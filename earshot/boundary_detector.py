"""Phase G — section boundary detector.

Background async task that ticks ~2 Hz, computes current track time
relative to playback start, and fires a `BoundaryApproachingEvent` ~N
seconds before each upcoming section boundary. The orchestrator listens
for these events and calls `earshot_refresh_expectations` so the
expectation tracker has fresh predictions when the new section arrives.

Why a separate task and not piggybacked on the comparator loops:
  - The comparators are *measurement-driven* (they react when ambient
    entries arrive). The boundary detector is *clock-driven* (it ticks
    even during silence to keep the lookahead deadline honest).
  - Decoupling means clock cadence and measurement cadence can differ
    (~2 Hz lookahead vs ~4 Hz measurement) without one starving the other.
  - The detector has zero dependency on the ambient stream — it only needs
    `playback_start_ms` and the Phase 2 baseline. That's a simpler contract
    than the comparators which need a reader + semantics map + thresholds.

Lookahead tuning:
  Default 5.0 s gives the orchestrator a usable LLM round-trip budget
  (most provider calls land in 1–3 s, with up to 5 s for the slowest).
  Phase J can override per-session. The boundary detector debounces by
  upcoming-section-index so a single boundary fires exactly one event
  even if the lookahead window is wider than the tick interval.

Stop behavior:
  When `stop()` is called the task cancels cleanly. No pending events
  are emitted after stop. The event queue may still hold un-popped
  events; the scheduler (Phase I) drains it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from earshot.ambient_stream import now_ms
from earshot.comparators.events import BoundaryApproachingEvent, EventQueue
from earshot.phase2_baseline import Phase2Baseline

logger = logging.getLogger(__name__)


# Default cadence for the detector's tick. 0.5 s = 2 Hz. Fast enough that
# we never miss a lookahead deadline by more than half a tick (250 ms).
DEFAULT_TICK_INTERVAL_S = 0.5

# Default lookahead before a section boundary. 5 s is generous for typical
# LLM round-trip latencies; Phase J can override per-session.
DEFAULT_LOOKAHEAD_S = 5.0


class BoundaryDetector:
    """Tick-driven async task: emits BoundaryApproachingEvent ahead of section boundaries.

    Lifecycle:
        det = BoundaryDetector(baseline, queue, playback_start_ms=...)
        await det.start()
        # ... session runs ...
        await det.stop()
    """

    def __init__(
        self,
        baseline: Phase2Baseline,
        queue: EventQueue,
        *,
        playback_start_ms: int,
        lookahead_seconds: float = DEFAULT_LOOKAHEAD_S,
        tick_interval_s: float = DEFAULT_TICK_INTERVAL_S,
    ):
        if playback_start_ms <= 0:
            raise ValueError(f"playback_start_ms must be positive, got {playback_start_ms}")
        if lookahead_seconds <= 0:
            raise ValueError(f"lookahead_seconds must be > 0, got {lookahead_seconds}")
        if tick_interval_s <= 0:
            raise ValueError(f"tick_interval_s must be > 0, got {tick_interval_s}")
        if tick_interval_s > lookahead_seconds:
            raise ValueError(
                f"tick_interval_s={tick_interval_s} > lookahead_seconds={lookahead_seconds} "
                "would miss boundaries; tick must be finer than lookahead"
            )
        self.baseline = baseline
        self.queue = queue
        self.playback_start_ms = playback_start_ms
        self.lookahead_seconds = float(lookahead_seconds)
        self.tick_interval_s = float(tick_interval_s)

        # Section indexes we've already fired for; ensures each boundary
        # produces exactly one event no matter how many ticks fall inside
        # the lookahead window.
        self._fired_for_section: set[int] = set()

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {
            "ticks": 0,
            "events_emitted": 0,
            "events_debounced": 0,
            "ticks_past_end_of_track": 0,
        }

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name=f"boundary_detector_{id(self):x}"
        )
        logger.info(
            "BoundaryDetector started: lookahead=%.1fs tick=%.2fs playback_start=%d",
            self.lookahead_seconds, self.tick_interval_s, self.playback_start_ms,
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
        logger.info("BoundaryDetector stopped: %s", self._stats)

    def stats(self) -> dict:
        return {
            **self._stats,
            "running": self._running,
            "fired_for_sections": sorted(self._fired_for_section),
        }

    # ------------------------------------------------------------------
    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("BoundaryDetector tick failed: %s", e)
            await asyncio.sleep(self.tick_interval_s)

    async def _tick(self) -> None:
        self._stats["ticks"] += 1
        wall_ms = now_ms()
        track_time_s = (wall_ms - self.playback_start_ms) / 1000.0
        if track_time_s < 0:
            return  # playback hasn't started

        nxt = self.baseline.next_boundary(track_time_s)
        if nxt is None:
            self._stats["ticks_past_end_of_track"] += 1
            return

        eta_s = nxt["start_s"] - track_time_s
        if eta_s > self.lookahead_seconds:
            return  # boundary still too far away

        section_index = nxt["section_index"]
        if section_index in self._fired_for_section:
            self._stats["events_debounced"] += 1
            return

        event = BoundaryApproachingEvent(
            upcoming_section_index=section_index,
            boundary_track_time_s=round(nxt["start_s"], 3),
            eta_ms=int(eta_s * 1000),
            ts_ms=wall_ms,
            track_time_s=round(track_time_s, 3),
        )
        await self.queue.push(event)
        self._fired_for_section.add(section_index)
        self._stats["events_emitted"] += 1
        logger.info(
            "boundary_approaching fired: section %d in %.1fs (boundary at t=%.1fs)",
            section_index, eta_s, nxt["start_s"],
        )
