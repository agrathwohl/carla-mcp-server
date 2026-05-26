"""Phase F — Comparators.

Turn ambient-stream measurements into reportable events for the output
scheduler. Two comparator kinds:

  - **DriftComparator** (`drift.py`) — measurements vs Phase 2 baseline.
    Emits `DriftEvent` when a windowed measurement diverges from the
    pre-recorded baseline by more than the configured per-dimension
    threshold.

  - **PredictionComparator** (`prediction.py`) — measurements vs the agent's
    current running expectations. Emits `PredictionErrorEvent` when the
    agent's model was meaningfully wrong about what just happened.

Both feed a single `EventQueue` (one per session) that the scheduler
consumes. See `events.py` for the wire format the scheduler expects.

Phase F deliverables — workflow_earshot_completion.md tasks F1–F5.
"""

from earshot.comparators.events import (
    BoundaryApproachingEvent,
    DriftEvent,
    PredictionErrorEvent,
    EventQueue,
    ComparatorThresholds,
    Dimension,
)

__all__ = [
    "BoundaryApproachingEvent",
    "DriftEvent",
    "PredictionErrorEvent",
    "EventQueue",
    "ComparatorThresholds",
    "Dimension",
]
