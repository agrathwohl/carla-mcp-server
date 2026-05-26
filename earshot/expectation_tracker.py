"""Phase G — in-memory expectation tracker.

The agent maintains explicit running predictions about what the next 4–8
bars will do, refreshed at section boundaries via LLM calls on the
orchestrator side. This module is the in-tree home for that state.

It implements the `ExpectationProvider` protocol (defined in
`earshot.comparators.prediction`), so swapping `NoExpectations()` for an
`ExpectationTracker` instance in `PredictionComparator` immediately
unlocks the prediction-error commentary path with no comparator changes.

Lifecycle (one ExpectationTracker per session):
  - Phase J's `earshot_start_session` instantiates a tracker per session
    and registers it in the SessionRegistry (Phase G).
  - The boundary detector (Phase G) fires `BoundaryApproachingEvent`s
    ahead of upcoming section boundaries.
  - The orchestrator handles those events by calling the
    `earshot_refresh_expectations` tool, which routes to `refresh()`.
  - The prediction comparator queries `expected()` and `current_section_index()`
    via the ExpectationProvider protocol.

Confidence decay:
  Predictions are time-sensitive — a refresh from 60 seconds ago for the
  current section is more useful than one from a section ago, but less
  useful than one from 5 seconds ago. `confidence_at(t_s)` returns a 0..1
  scalar that decays linearly to zero over `decay_seconds` from refresh
  time. The scheduler weights prediction-error events by this confidence,
  so stale predictions don't drown out current observation. Phase I will
  consume the score.

Concurrency:
  `refresh()` is async-safe (uses asyncio.Lock). The reader methods
  (`expected`, `current_section_index`, `confidence_at`) are sync — they
  just read snapshot state. Race condition between a refresh and a read
  results in reading either pre-refresh or post-refresh state, never a
  torn intermediate.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from earshot.ambient_stream import now_ms
from earshot.comparators.events import Dimension
from earshot.phase2_baseline import Phase2Baseline

logger = logging.getLogger(__name__)


# Default confidence-decay horizon. After this many seconds since refresh,
# confidence reaches zero. Picked to be a few times the typical LLM round
# trip + a buffer — empirically tunable.
DEFAULT_DECAY_SECONDS = 30.0


@dataclass
class ExpectationState:
    """Snapshot of the agent's predictions for one section.

    Mutable container; the tracker owns instances and updates them under lock.
    """
    section_index: int
    # Per-dimension expected values. Keys are Dimension *enum values* (strings)
    # so the dict can come straight from JSON via the MCP tool.
    predictions: dict[str, Any] = field(default_factory=dict)
    refreshed_at_ms: int = 0
    # Source label — useful for diagnostics; lets us see which orchestrator
    # call (or fallback path) produced this state.
    source: str = "orchestrator"


class ExpectationTracker:
    """Implements the ExpectationProvider protocol (see prediction.py).

    Stores per-section expectations keyed by section_index. Returns the
    expectation for whichever section contains the queried track time.
    """

    def __init__(
        self,
        baseline: Phase2Baseline,
        *,
        decay_seconds: float = DEFAULT_DECAY_SECONDS,
    ):
        self.baseline = baseline
        self.decay_seconds = float(decay_seconds)
        self._states: dict[int, ExpectationState] = {}
        self._lock = asyncio.Lock()
        self._refresh_count = 0

    # ------------------------------------------------------------------
    # ExpectationProvider protocol — read paths (no lock; snapshot reads)
    # ------------------------------------------------------------------
    def expected(self, dim: Dimension, track_time_s: float) -> Optional[float]:
        """Expected value for `dim` at `track_time_s`, or None.

        Looks up which Phase 2 section contains the time, then returns the
        prediction stored for that section under `dim`'s enum value.
        """
        sec = self.baseline.section_at(track_time_s)
        if not sec:
            return None
        state = self._states.get(sec["index"])
        if not state:
            return None
        v = state.predictions.get(dim.value)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            # Categorical predictions (e.g. key) aren't floats; the
            # prediction comparator currently only consumes numeric dims.
            # Callers that want categorical can read the raw dict via
            # state_for_section().
            return None

    def current_section_index(self, track_time_s: float) -> Optional[int]:
        """Which Phase 2 section contains `track_time_s`."""
        sec = self.baseline.section_at(track_time_s)
        return sec.get("index") if sec else None

    # ------------------------------------------------------------------
    # Additional API beyond the ExpectationProvider protocol
    # ------------------------------------------------------------------
    def confidence_at(self, track_time_s: float) -> float:
        """0..1 confidence in the predictions covering `track_time_s`.

        Decays linearly from 1.0 (just-refreshed) to 0.0 over
        `decay_seconds`. Returns 0 when there are no predictions for the
        containing section. The scheduler (Phase I) uses this to weight
        prediction-error events: fresh predictions → strong evidence,
        stale ones → soft evidence.
        """
        sec_idx = self.current_section_index(track_time_s)
        if sec_idx is None:
            return 0.0
        state = self._states.get(sec_idx)
        if not state:
            return 0.0
        age_s = (now_ms() - state.refreshed_at_ms) / 1000.0
        if age_s <= 0:
            return 1.0
        if age_s >= self.decay_seconds:
            return 0.0
        return 1.0 - (age_s / self.decay_seconds)

    def state_for_section(self, section_index: int) -> Optional[ExpectationState]:
        """Raw state for a section (read-only snapshot)."""
        s = self._states.get(section_index)
        if s is None:
            return None
        # Return a shallow copy so callers can't mutate our state.
        return ExpectationState(
            section_index=s.section_index,
            predictions=dict(s.predictions),
            refreshed_at_ms=s.refreshed_at_ms,
            source=s.source,
        )

    def known_sections(self) -> list[int]:
        return sorted(self._states.keys())

    # ------------------------------------------------------------------
    # Write path (the orchestrator calls this via earshot_refresh_expectations)
    # ------------------------------------------------------------------
    async def refresh(
        self,
        section_index: int,
        predictions: dict[str, Any],
        *,
        source: str = "orchestrator",
    ) -> ExpectationState:
        """Install new predictions for `section_index`.

        Args:
            section_index: which Phase 2 section these predictions apply to.
                           Must be a valid section index per the baseline.
            predictions:   dict of `Dimension.value` → expected value. Unknown
                           keys are allowed (forward-compatible) but logged.
            source:        diagnostic label; default 'orchestrator'.

        Returns the installed ExpectationState (copy).

        Raises:
            ValueError: if section_index isn't a valid section in the baseline.
        """
        if not isinstance(section_index, int) or section_index < 0:
            raise ValueError(f"section_index must be non-negative int, got {section_index!r}")
        valid_indexes = {s["index"] for s in self.baseline.section_map}
        if valid_indexes and section_index not in valid_indexes:
            raise ValueError(
                f"section_index {section_index} not in baseline sections "
                f"{sorted(valid_indexes)}"
            )
        if not isinstance(predictions, dict):
            raise ValueError(f"predictions must be dict, got {type(predictions).__name__}")

        # Identify any unknown dimension keys (forward-compatible, not fatal).
        known = {d.value for d in Dimension}
        unknown = set(predictions.keys()) - known
        if unknown:
            logger.warning(
                "ExpectationTracker.refresh: unknown dim keys %s ignored at runtime",
                sorted(unknown),
            )

        async with self._lock:
            state = ExpectationState(
                section_index=section_index,
                predictions=dict(predictions),  # defensive copy
                refreshed_at_ms=now_ms(),
                source=source,
            )
            self._states[section_index] = state
            self._refresh_count += 1
        logger.info(
            "ExpectationTracker refreshed section=%d dims=%s",
            section_index, sorted(predictions.keys()),
        )
        # Return a copy so callers can't mutate our state.
        return ExpectationState(
            section_index=state.section_index,
            predictions=dict(state.predictions),
            refreshed_at_ms=state.refreshed_at_ms,
            source=state.source,
        )

    def stats(self) -> dict:
        return {
            "sections_with_predictions": len(self._states),
            "known_sections": self.known_sections(),
            "refresh_count": self._refresh_count,
            "decay_seconds": self.decay_seconds,
        }
