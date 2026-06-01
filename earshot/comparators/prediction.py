"""Prediction comparator — measurements vs running expectations → PredictionErrorEvent.

The prediction comparator is the engagement substrate per the README's
"Expectation tracking" section: the agent's *interest* in any musical
moment is grounded in the magnitude of its prediction error, not in
performed enthusiasm. When the model was meaningfully wrong about what
came next, that's worth talking about; when it was right, silence.

Structurally near-identical to DriftComparator:
  - reads ambient stream tail
  - maintains rolling windows per dimension
  - compares windowed estimate against a *reference*
  - emits events when divergence exceeds threshold

The difference is the reference. Drift compares to Phase 2's static
baseline; prediction compares to the agent's *current running expectation
state*, which is updated at section boundaries by an LLM call on the
orchestrator side. That expectation state lives behind an
`ExpectationProvider` interface — Phase G implements it as part of the
expectation tracker; today we ship a `NoExpectations` default that
returns None and effectively suppresses event emission until Phase G
wires in the real provider.

This keeps Phase F's deliverable shippable now without blocking on G:
the comparator's full event-emission and queue-integration mechanics
get exercised by tests against a stub provider, and Phase G's expectation
tracker just becomes an `ExpectationProvider` impl that the session
machinery passes in instead of `NoExpectations()`.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from statistics import median
from typing import Optional, Protocol

from earshot.ambient_stream import AmbientStreamReader
from earshot.comparators.events import (
    ComparatorThresholds,
    Dimension,
    EventQueue,
    PredictionErrorEvent,
)
from earshot.phase2_baseline import Phase2Baseline

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Expectation provider — Phase G replaces NoExpectations with the real one
# ----------------------------------------------------------------------

class ExpectationProvider(Protocol):
    """Returns "what the agent expected at moment t" per dimension.

    Phase G's tracker implements this interface; today's session uses
    `NoExpectations()` which makes the comparator inert (no events). When
    G ships, the session_start tool swaps in the real tracker and this
    comparator immediately starts emitting prediction_error events.
    """

    def expected(self, dim: Dimension, track_time_s: float) -> Optional[float]:
        ...

    def current_section_index(self, track_time_s: float) -> Optional[int]:
        ...


class NoExpectations:
    """Default provider. Always returns None — no events fire.

    Useful for:
      - Bringing up Phase F before Phase G is built (no expectations yet)
      - Sessions where the user opted out of prediction commentary
      - Smoke tests that exercise the comparator's plumbing without
        needing a real LLM-driven tracker
    """

    def expected(self, dim: Dimension, track_time_s: float) -> Optional[float]:
        return None

    def current_section_index(self, track_time_s: float) -> Optional[int]:
        return None


class Phase2BackedExpectations:
    """Minimal expectation provider that uses Phase 2 baseline as the prediction.

    NOT the real Phase G tracker — that one refreshes predictions at
    section boundaries via LLM calls. This stub treats the baseline as
    "what we expect", which means the prediction comparator behaves
    identically to the drift comparator. Useful for plumbing tests but
    NOT a substitute for Phase G in production.

    Phase J's session_start should NOT wire this in; it's documentation
    for what the contract looks like.
    """

    def __init__(self, baseline: Phase2Baseline):
        self.baseline = baseline

    def expected(self, dim: Dimension, track_time_s: float) -> Optional[float]:
        # Delegate to the canonical mapping on Phase2Baseline.
        return self.baseline.value_for(dim, track_time_s)

    def current_section_index(self, track_time_s: float) -> Optional[int]:
        sec = self.baseline.section_at(track_time_s)
        return sec.get("index") if sec else None


# ----------------------------------------------------------------------
# Comparator
# ----------------------------------------------------------------------

DEFAULT_WINDOW_SAMPLES = {
    Dimension.DYNAMIC_ENVELOPE: 16,
    Dimension.TEMPO: 12,
    Dimension.LUFS_INTEGRATED: 8,
    Dimension.SPECTRAL_CENTROID: 16,
    Dimension.ONSET_DENSITY: 12,
}


class PredictionComparator:
    """Background task: ambient stream → PredictionErrorEvents on the queue.

    Lifecycle mirrors DriftComparator. The semantic difference is the
    reference (provider vs baseline) and the event type (PredictionErrorEvent
    vs DriftEvent).
    """

    def __init__(
        self,
        baseline: Phase2Baseline,
        reader: AmbientStreamReader,
        queue: EventQueue,
        *,
        playback_start_ms: int,
        semantics: dict[str, Dimension],
        expectations: Optional[ExpectationProvider] = None,
        thresholds: Optional[ComparatorThresholds] = None,
        window_samples: Optional[dict[Dimension, int]] = None,
        debounce_seconds: float = 4.0,
        calibration_alpha: float = 0.01,
        calibration_warmup_samples: int = 16,
    ):
        if playback_start_ms <= 0:
            raise ValueError(f"playback_start_ms must be positive, got {playback_start_ms}")
        if not semantics:
            raise ValueError("semantics mapping cannot be empty")
        if not (0.0 < calibration_alpha < 1.0):
            raise ValueError(f"calibration_alpha must be in (0, 1), got {calibration_alpha}")
        self.baseline = baseline
        self.reader = reader
        self.queue = queue
        self.playback_start_ms = playback_start_ms
        self.semantics = dict(semantics)
        self.expectations: ExpectationProvider = expectations or NoExpectations()
        self.thresholds = thresholds or ComparatorThresholds()
        self.window_samples = {**DEFAULT_WINDOW_SAMPLES, **(window_samples or {})}
        self.debounce_seconds = float(debounce_seconds)

        # Calibration: same approach as DriftComparator — cancel steady-state
        # offset between current and expectation. The PredictionComparator's
        # reference is the LLM-supplied expectation (or NoExpectations), not
        # the Phase 2 baseline, but the offset-cancellation principle is the
        # same: a constant level offset isn't a "prediction error", only a
        # CHANGE in the relationship is.
        self.calibration_alpha = float(calibration_alpha)
        self.calibration_warmup_samples = int(calibration_warmup_samples)
        self._calibration_offset: dict[Dimension, float] = {}
        self._calibration_count: dict[Dimension, int] = {}
        # Unlike the drift baseline (a continuous interpolated envelope), the
        # per-section `expected` value steps at each section boundary. The
        # calibration offset tracks one section's (current - expected)
        # relationship; carrying it across a boundary makes magnitude spike
        # on the step alone (false positive). We reset calibration for a
        # dimension whenever its containing section changes.
        self._last_section: dict[Dimension, int] = {}

        self._windows: dict[Dimension, deque] = {
            dim: deque(maxlen=self.window_samples.get(dim, 16))
            for dim in Dimension
        }
        self._last_fired_ms: dict[Dimension, int] = {}

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {
            "entries_seen": 0,
            "entries_mapped": 0,
            "events_emitted": 0,
            "events_suppressed_no_expectation": 0,
            "events_suppressed_calibration_warmup": 0,
            "events_debounced": 0,
        }

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name=f"prediction_comparator_{id(self):x}"
        )
        logger.info(
            "PredictionComparator started: dims=%s, provider=%s",
            sorted({d.value for d in self.semantics.values()}),
            type(self.expectations).__name__,
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
        logger.info("PredictionComparator stopped: %s", self._stats)

    def stats(self) -> dict:
        return {**self._stats, "running": self._running, "queue": self.queue.stats()}

    # ------------------------------------------------------------------
    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
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
                logger.warning("PredictionComparator: reader raised: %s", e)
                await asyncio.sleep(0.5)
                continue
            if entry is sentinel:
                return
            self._stats["entries_seen"] += 1
            try:
                await self._process(entry)
            except Exception as e:
                logger.warning("PredictionComparator: process failed for %s: %s",
                               entry.get("type"), e)

    async def _process(self, entry: dict) -> None:
        ambient_type = entry.get("type")
        dim = self.semantics.get(ambient_type)
        if dim is None:
            return
        self._stats["entries_mapped"] += 1

        value = entry.get("value")
        if not isinstance(value, (int, float)):
            return  # categorical prediction error handled at expectation-tracker level
        ts_ms = int(entry.get("ts_ms", 0))
        track_time_s = (ts_ms - self.playback_start_ms) / 1000.0
        if track_time_s < 0:
            return

        window = self._windows[dim]
        window.append((ts_ms, float(value)))
        if len(window) < max(3, window.maxlen // 2):
            return

        expected = self.expectations.expected(dim, track_time_s)
        if expected is None:
            self._stats["events_suppressed_no_expectation"] += 1
            return

        # The per-section `expected` value steps at section boundaries; reset
        # this dimension's calibration when the section changes so the stale
        # offset doesn't manufacture a false prediction error on the step
        # alone. After a reset the warmup gate re-engages, suppressing events
        # until the new section's offset re-establishes (~warmup samples).
        section_index = self.expectations.current_section_index(track_time_s)
        if section_index is not None and self._last_section.get(dim) != section_index:
            self._last_section[dim] = section_index
            self._calibration_offset.pop(dim, None)
            self._calibration_count.pop(dim, None)

        windowed_current = median(v for _, v in window)
        # Calibrated offset: same pattern as DriftComparator. A constant gain
        # mismatch between source-of-prediction (LLM, baseline) and live signal
        # shouldn't fire prediction-error events on its own.
        raw_err = windowed_current - expected
        prev_offset = self._calibration_offset.get(dim, raw_err)
        new_offset = (
            self.calibration_alpha * raw_err
            + (1.0 - self.calibration_alpha) * prev_offset
        )
        self._calibration_offset[dim] = new_offset
        self._calibration_count[dim] = self._calibration_count.get(dim, 0) + 1
        if self._calibration_count[dim] < self.calibration_warmup_samples:
            self._stats["events_suppressed_calibration_warmup"] += 1
            return
        magnitude = abs(raw_err - new_offset)
        threshold = self.thresholds.threshold_for(dim)
        if threshold is None or magnitude <= threshold:
            return

        last_fired = self._last_fired_ms.get(dim)
        if last_fired is not None and (ts_ms - last_fired) < self.debounce_seconds * 1000:
            self._stats["events_debounced"] += 1
            return

        # Score normalization: magnitude / threshold yields 1.0 right at the
        # threshold edge, 2.0 at twice the threshold etc. The scheduler uses
        # this as the intensity gate per the README's "error magnitude IS the
        # rule" principle.
        score = round(magnitude / threshold, 3) if threshold > 0 else magnitude

        # section_index was already resolved above (for the calibration reset)
        # and reused here so the same track-time isn't looked up twice.
        # PredictionErrorEvent's schema carries `dimensions: list` and dict-shaped
        # `expected`/`actual` so a single emission can describe correlated error
        # across multiple dimensions ("tempo went up AND dynamics dropped at the
        # same moment" → one event, weighted-sum score). Phase F emits one
        # dimension per event for simplicity; cross-dimension aggregation lives
        # in the scheduler (Phase I) or a future F3 refinement that buffers
        # sub-events for N milliseconds before flushing one combined event.
        event = PredictionErrorEvent(
            dimensions=[dim],
            score=score,
            expected={dim.value: expected},
            actual={dim.value: round(windowed_current, 4)},
            ts_ms=ts_ms,
            track_time_s=round(track_time_s, 3),
            section_index=section_index,
        )
        await self.queue.push(event)
        self._last_fired_ms[dim] = ts_ms
        self._stats["events_emitted"] += 1
        logger.info(
            "prediction_error fired: %s score=%.2f expected=%s current=%.3f at t=%.1fs",
            dim.value, score, expected, windowed_current, track_time_s,
        )
