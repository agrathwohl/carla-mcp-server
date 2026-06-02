"""Drift comparator — measurements vs Phase 2 baseline → DriftEvent.

Reads the ambient stream tail, maintains a rolling window per dimension,
and emits a DriftEvent whenever the windowed estimate diverges from the
Phase 2 baseline value at the corresponding track time by more than the
configured threshold.

Wall-clock alignment is the binding contract here. The ambient stream
carries `ts_ms` (epoch milliseconds — the shared time axis across all
producers); the session machinery (Phase J) supplies `playback_start_ms`
at session start. Track time at any moment is `(ts_ms - playback_start_ms) / 1000`,
which is the key passed to `Phase2Baseline.rms_at()` / `section_at()`.

Semantics layer:
  Ambient entries are typed as `plugin_3.param_5` (raw LV2 references)
  or `librosa.tempo_bpm` (companion-emitted). The drift comparator does
  NOT know what these mean by itself. The caller supplies a `semantics`
  mapping from ambient `type` → `Dimension` so the comparator can pick
  up only the entries it knows how to evaluate. Phase J will populate
  this from `get_plugin_info` introspection.

Rolling-window discipline:
  A single noisy sample is NOT drift — natural music dynamics traverse
  large ranges within seconds (see SM012's 96 dB measured range).
  Drift = a *sustained* deviation. The comparator buffers the last N
  samples per dimension and uses the median as the "current" estimate
  to compare against baseline. Median (vs mean) is robust to per-sample
  spikes typical of percussive transients hitting the analyzer chain.

Debounce:
  Once a drift event fires for a dimension, no further events for that
  dimension fire until `debounce_seconds` have elapsed OR the drift
  resolves (estimate returns within threshold of baseline). Otherwise
  a 30-second drift would emit a hundred events.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from statistics import median
from typing import Any, Optional

from earshot.ambient_stream import AmbientStreamReader
from earshot.comparators.events import (
    ComparatorThresholds,
    Dimension,
    DriftEvent,
    EventQueue,
)
from earshot.phase2_baseline import Phase2Baseline

logger = logging.getLogger(__name__)


# Window sizes per dimension (in samples). All assume the LV2 poller
# emits at ~4 Hz; tune in Phase J if a different cadence is configured.
DEFAULT_WINDOW_SAMPLES = {
    Dimension.DYNAMIC_ENVELOPE: 16,     # ~4 s
    Dimension.TEMPO: 12,                # ~3 s (companion-emitted at ~1 Hz → 12 s, fine)
    Dimension.KEY: 8,                   # categorical
    Dimension.LUFS_INTEGRATED: 8,
    Dimension.SPECTRAL_CENTROID: 16,
    Dimension.ONSET_DENSITY: 12,
}


class DriftComparator:
    """Background task: ambient stream → DriftEvents on the EventQueue.

    Lifecycle:
        comp = DriftComparator(baseline, reader, queue,
                                playback_start_ms=..., semantics=...)
        await comp.start()    # idempotent
        # ... session runs ...
        await comp.stop()     # idempotent

    The comparator does NOT own the AmbientStreamReader (the session
    machinery does; multiple consumers tail the same stream) nor the
    EventQueue (the scheduler drains it). It only contributes events.
    """

    def __init__(
        self,
        baseline: Phase2Baseline,
        reader: AmbientStreamReader,
        queue: EventQueue,
        *,
        playback_start_ms: int,
        semantics: dict[str, Dimension],
        thresholds: Optional[ComparatorThresholds] = None,
        window_samples: Optional[dict[Dimension, int]] = None,
        debounce_seconds: float = 10.0,
        calibration_alpha: float = 0.03,
        calibration_warmup_samples: int = 16,
    ):
        if playback_start_ms <= 0:
            raise ValueError(f"playback_start_ms must be positive, got {playback_start_ms}")
        if not semantics:
            raise ValueError("semantics mapping cannot be empty (would yield zero drift events)")
        if not (0.0 < calibration_alpha < 1.0):
            raise ValueError(f"calibration_alpha must be in (0, 1), got {calibration_alpha}")
        self.baseline = baseline
        self.reader = reader
        self.queue = queue
        self.playback_start_ms = playback_start_ms
        self.semantics = dict(semantics)
        self.thresholds = thresholds or ComparatorThresholds()
        self.window_samples = {**DEFAULT_WINDOW_SAMPLES, **(window_samples or {})}
        self.debounce_seconds = float(debounce_seconds)

        # Calibration: per-dimension running EMA of (current - baseline). This
        # cancels out steady-state offsets (e.g. playback volume attenuation,
        # gain mismatches between the source master and the live signal). After
        # calibration: real_drift = (current - baseline) - calibration_offset,
        # so only *changes* in the relationship fire events, not constant offset.
        # `alpha` controls how quickly calibration follows a fader move:
        #   alpha=0.03 → ~33-sample memory → at 4 Hz ≈ 8s to half-track a change.
        # (Higher than the original 0.01 so sustained section-level shifts get
        # absorbed into the offset instead of firing for ~25s; the per-dim
        # debounce + the scheduler's emission cooldown bound the rate further.)
        # Warmup gates event emission until we've seen enough samples to trust
        # the offset estimate.
        self.calibration_alpha = float(calibration_alpha)
        self.calibration_warmup_samples = int(calibration_warmup_samples)
        self._calibration_offset: dict[Dimension, float] = {}
        self._calibration_count: dict[Dimension, int] = {}

        # Per-dimension rolling windows of (ts_ms, value).
        self._windows: dict[Dimension, deque] = {
            dim: deque(maxlen=self.window_samples.get(dim, 16))
            for dim in Dimension
        }
        # Last drift-event wall-clock ms per dimension (for debounce).
        self._last_fired_ms: dict[Dimension, int] = {}

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {
            "entries_seen": 0,
            "entries_mapped": 0,
            "events_emitted": 0,
            "events_debounced": 0,
            "events_suppressed_calibration_warmup": 0,
        }

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"drift_comparator_{id(self):x}")
        logger.info(
            "DriftComparator started: dims=%s, thresholds=%s, playback_start=%d",
            sorted({d.value for d in self.semantics.values()}),
            self.thresholds,
            self.playback_start_ms,
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("DriftComparator stopped: %s", self._stats)

    def stats(self) -> dict:
        return {**self._stats, "running": self._running, "queue": self.queue.stats()}

    # ------------------------------------------------------------------
    async def _loop(self) -> None:
        """Tail the ambient stream and process each new entry.

        The reader's tail() is a sync generator (it uses time.sleep).
        Wrapping next() in run_in_executor offloads the wait to a thread
        so the asyncio event loop stays free for the poller, scheduler,
        and other comparators.
        """
        loop = asyncio.get_event_loop()
        # `stop_fn` lets the generator exit cleanly when we signal stop.
        # Start tail with since_ms=playback_start_ms so we get backfill
        # if any entries pre-dated the comparator's startup.
        iterator = self.reader.tail(
            since_ms=self.playback_start_ms,
            poll_interval_s=0.05,
            stop_fn=lambda: not self._running,
        )
        sentinel = object()
        while self._running:
            try:
                entry = await loop.run_in_executor(None, next, iterator, sentinel)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("DriftComparator: reader raised: %s", e)
                await asyncio.sleep(0.5)
                continue
            if entry is sentinel:
                # Generator exhausted (file removed, or stop_fn returned True).
                logger.debug("DriftComparator: stream tail ended")
                return
            self._stats["entries_seen"] += 1
            try:
                await self._process(entry)
            except Exception as e:
                logger.warning("DriftComparator: process failed for entry %s: %s",
                               entry.get("type"), e)

    # ------------------------------------------------------------------
    async def _process(self, entry: dict) -> None:
        ambient_type = entry.get("type")
        dim = self.semantics.get(ambient_type)
        if dim is None:
            return  # unmapped entry, ignore
        self._stats["entries_mapped"] += 1

        value = entry.get("value")
        ts_ms = int(entry.get("ts_ms", 0))
        track_time_s = (ts_ms - self.playback_start_ms) / 1000.0
        if track_time_s < 0:
            return  # entry pre-dates session start somehow

        # Categorical (KEY): take latest value, compare directly to baseline.
        if dim == Dimension.KEY:
            await self._maybe_fire_categorical(dim, value, ts_ms, track_time_s)
            return

        # Numeric: maintain rolling window, compute median, compare to baseline.
        if not isinstance(value, (int, float)):
            return
        window = self._windows[dim]
        window.append((ts_ms, float(value)))
        if len(window) < max(3, window.maxlen // 2):
            return  # not enough samples yet

        windowed_current = median(v for _, v in window)
        baseline_value = self._baseline_for(dim, track_time_s)
        if baseline_value is None:
            return  # baseline unknown at this time

        # Raw difference (signed); the calibration EMA tracks its long-term
        # mean, which we then subtract so a constant offset (volume attenuation
        # etc.) doesn't register as drift.
        raw_drift = windowed_current - baseline_value
        prev_offset = self._calibration_offset.get(dim, raw_drift)
        new_offset = (
            self.calibration_alpha * raw_drift
            + (1.0 - self.calibration_alpha) * prev_offset
        )
        self._calibration_offset[dim] = new_offset
        self._calibration_count[dim] = self._calibration_count.get(dim, 0) + 1

        if self._calibration_count[dim] < self.calibration_warmup_samples:
            self._stats["events_suppressed_calibration_warmup"] += 1
            return  # not enough samples to trust the calibration yet

        # Calibrated magnitude: how much current diverges from baseline AFTER
        # accounting for the steady-state offset. Constant attenuation → ~0.
        magnitude = abs(raw_drift - new_offset)
        threshold = self.thresholds.threshold_for(dim)
        if threshold is None or magnitude <= threshold:
            return  # within tolerance

        # Debounce: don't re-fire for same dim within debounce_seconds.
        last_fired = self._last_fired_ms.get(dim)
        if last_fired is not None and (ts_ms - last_fired) < self.debounce_seconds * 1000:
            self._stats["events_debounced"] += 1
            return

        event = DriftEvent(
            dimension=dim,
            magnitude=round(magnitude, 4),
            baseline=baseline_value,
            current=round(windowed_current, 4),
            threshold=threshold,
            ts_ms=ts_ms,
            track_time_s=round(track_time_s, 3),
            window_size_samples=len(window),
            calibration_offset=round(new_offset, 4),
        )
        await self.queue.push(event)
        self._last_fired_ms[dim] = ts_ms
        self._stats["events_emitted"] += 1
        logger.info(
            "drift fired: %s magnitude=%.3f (raw=%.3f offset=%.3f) "
            "baseline=%s current=%.3f at t=%.1fs",
            dim.value, magnitude, raw_drift, new_offset,
            baseline_value, windowed_current, track_time_s,
        )

    # ------------------------------------------------------------------
    async def _maybe_fire_categorical(
        self, dim: Dimension, value: Any, ts_ms: int, track_time_s: float
    ) -> None:
        """Categorical drift handler — KEY today, room for future categoricals.

        Treats value as a dict like `{"tonic": "D", "mode": "minor"}` or a
        bare string. Fires when value differs from baseline AND the
        threshold's `key_change_fires` knob is True.
        """
        if dim != Dimension.KEY:
            return
        if not self.thresholds.key_change_fires:
            return
        baseline_key = (self.baseline.key.tonic, self.baseline.key.mode)
        if isinstance(value, dict):
            current_key = (value.get("tonic"), value.get("mode"))
        elif isinstance(value, str) and " " in value:
            # e.g. "D minor"
            parts = value.split(" ", 1)
            current_key = (parts[0], parts[1])
        else:
            return  # unrecognized shape
        if current_key == baseline_key or None in current_key:
            return

        last_fired = self._last_fired_ms.get(dim)
        if last_fired is not None and (ts_ms - last_fired) < self.debounce_seconds * 1000:
            self._stats["events_debounced"] += 1
            return

        event = DriftEvent(
            dimension=dim,
            magnitude=1.0,  # categorical: change = magnitude 1
            baseline=f"{baseline_key[0]} {baseline_key[1]}",
            current=f"{current_key[0]} {current_key[1]}",
            threshold=None,
            ts_ms=ts_ms,
            track_time_s=round(track_time_s, 3),
            window_size_samples=1,
        )
        await self.queue.push(event)
        self._last_fired_ms[dim] = ts_ms
        self._stats["events_emitted"] += 1
        logger.info(
            "key drift fired: baseline=%s current=%s at t=%.1fs",
            baseline_key, current_key, track_time_s,
        )

    # ------------------------------------------------------------------
    def _baseline_for(self, dim: Dimension, track_time_s: float) -> Optional[float]:
        """Pull the baseline value for `dim` at `track_time_s`.

        Thin wrapper over Phase2Baseline.value_for() — the canonical
        Dimension → baseline-value mapping lives there so adding a new
        dimension only touches one file.
        """
        return self.baseline.value_for(dim, track_time_s)
