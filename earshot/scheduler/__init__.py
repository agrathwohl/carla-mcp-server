"""Phase I — output scheduler.

The editorial brain of Earshot. Drains the comparators' EventQueue
(drift / prediction-error / boundary-approaching events) and emits
commentary onto the session's CommentaryQueue. The orchestrator reads
the commentary queue via `earshot_get_commentary_queue` and either
relays action-text directly to the user or fulfils prose requests by
generating text and submitting via `earshot_submit_commentary`.

This is the load-bearing piece per the README — the part that determines
whether Earshot has a voice or is just a meter dashboard with text
annotations. The scheduler's job:

  1. Map each comparator event to an intensity level (1-6) via the
     active profile's thresholds — "error magnitude IS the rule" per the
     README, no separate "should I talk now?" heuristic.
  2. Anti-spoiler discipline: compute `ts_user_clock` so commentary
     lands ON the moment from the user's perspective (using the
     delay tower's buffer for headroom).
  3. Silence-as-default: events that don't clear the threshold for any
     level produce no output — explicit silence, not absence of work.
  4. Honesty rules: scheduler-emitted action-text + orchestrator-submitted
     prose both pass through the honesty validator before queueing.
  5. Action-text catalog selection for level 2 (no LLM round-trip).
  6. Prose requests for levels 3-6 (orchestrator handles the LLM call).

See `earshot/README.md` § "Output scheduler design" for the full spec.
"""

from earshot.scheduler.commentary import (
    CommentaryEmission,
    CommentaryQueue,
    ProseRequest,
)
from earshot.scheduler.intensity import IntensityLevel, score_to_level

# Scheduler intentionally NOT re-exported here. It depends on the profiles
# loader (earshot.profiles.Profile), and earshot.profiles in turn pulls
# IntensityThresholds from earshot.scheduler.intensity. Eagerly re-exporting
# Scheduler from this package's __init__.py would create a circular import
# whenever something does `from earshot.profiles import ...` before
# `from earshot.scheduler import ...`. Callers that need the class import
# it directly: `from earshot.scheduler.core import Scheduler`.

__all__ = [
    "CommentaryEmission",
    "CommentaryQueue",
    "IntensityLevel",
    "ProseRequest",
    "score_to_level",
]
