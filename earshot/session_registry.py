"""Phase G — minimal session registry.

Runtime in-memory mapping of `session_id` → live per-session state. The
MCP server holds one of these (module-level singleton); Phase J's
`earshot_start_session` populates it; Phase G's `earshot_refresh_expectations`
and the eventual `earshot_get_commentary_queue` / `earshot_end_session`
tools look up state by session_id.

This file is intentionally minimal for Phase G — only the fields needed
to wire the refresh-expectations tool. Phase J will extend `SessionState`
with the poller, comparators, detector, and reader instances so end_session
can cleanly shut everything down.

Why module-level singleton:
  The MCP server is a single process. All tool invocations land in the
  same event loop and the same EarshotTools instance. A module-level
  singleton keeps the registry accessible from anywhere — tools, the
  scheduler (Phase I), tests — without threading it through every call
  signature. Per-test cleanup uses `registry.unregister()` or
  `registry.clear_all_for_tests()`.

Idempotency (postmortem rule #32):
  `register()` overwrites existing state for the same session_id with a
  warning; `unregister()` is a no-op if absent. `get()` returns None for
  unknown ids rather than raising — callers check explicitly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from earshot.comparators.events import EventQueue
from earshot.expectation_tracker import ExpectationTracker
from earshot.phase2_baseline import Phase2Baseline

if TYPE_CHECKING:
    import asyncio
    import subprocess
    # Forward references to keep this module's import cost low.
    from earshot.ambient_stream import AmbientStreamReader, AmbientStreamWriter
    from earshot.boundary_detector import BoundaryDetector
    from earshot.comparators.drift import DriftComparator
    from earshot.comparators.prediction import PredictionComparator
    from earshot.lv2_poller import LV2Poller
    from earshot.profiles import Profile
    from earshot.runtime.commentary_worker import CommentaryWorker
    from earshot.scheduler.commentary import CommentaryLogger, CommentaryQueue
    from earshot.scheduler.core import Scheduler

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Live runtime state for one Phase 3 listening session.

    Phase G defined the five required fields. Phase I added the
    scheduler-side components (profile, scheduler, commentary_queue).
    Phase J adds the remaining live-session components: the ambient
    stream writer/reader, the LV2 poller (optional, depends on a live
    Carla session), and the comparators + boundary detector.
    Postmortem rule #29 — every component is a named field, no
    "free-form extras dict".

    Phase C will eventually add a companion_process handle. Phase K
    will add source_loader + delay_tower. Those slots aren't here yet;
    add them when the phases that need them ship.
    """
    session_id: str
    baseline: Phase2Baseline
    tracker: ExpectationTracker
    queue: EventQueue
    playback_start_ms: int
    # Phase I additions — populated by Phase J's session_start.
    profile: Optional["Profile"] = None
    scheduler: Optional["Scheduler"] = None
    commentary_queue: Optional["CommentaryQueue"] = None
    # Phase J additions — populated by earshot_start_session.
    writer: Optional["AmbientStreamWriter"] = None
    reader: Optional["AmbientStreamReader"] = None
    lv2_poller: Optional["LV2Poller"] = None
    drift_comparator: Optional["DriftComparator"] = None
    prediction_comparator: Optional["PredictionComparator"] = None
    boundary_detector: Optional["BoundaryDetector"] = None
    # Phase C — streaming librosa companion subprocess (file-driven). Holds
    # the Popen handle so end_session can terminate it cleanly.
    companion_process: Optional["subprocess.Popen"] = None
    # Phase L — per-session commentary log writer. Wired into the
    # CommentaryQueue at start_session so every push is persisted to
    # commentary.jsonl for reflection-time reconstruction.
    commentary_logger: Optional["CommentaryLogger"] = None
    # Phase M sync watcher — async Task that waits for the first non-silent
    # ambient entry, then anchors playback_start_ms to that moment and
    # starts the gated components (drift/pred/boundary). When sync isn't
    # enabled this stays None and components start with the wall-clock-
    # at-session-start playback_start_ms.
    playback_sync_task: Optional["asyncio.Task"] = None
    # Phase N — Carla audiofile source plugin id, set by earshot_play. When
    # present, end_session pauses transport + removes the source so stopping
    # the session also stops the audio.
    source_plugin_id: Optional[int] = None
    # Phase O — headless commentary worker (auto-orchestrate). Drains prose
    # requests + fulfills them via Claude Haiku so no human orchestrator is
    # needed; end_session stops it.
    commentary_worker: Optional["CommentaryWorker"] = None


class SessionRegistry:
    """Map of session_id → SessionState. Singleton-per-process."""

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}

    def register(self, state: SessionState) -> None:
        """Install or replace a session's state.

        Overwriting is allowed (logged warn) so a re-start with the same
        session_id doesn't fail — Phase J handles cleanup of the previous
        state's tasks before re-registering.
        """
        if state.session_id in self._sessions:
            logger.warning(
                "SessionRegistry.register: overwriting existing session %s",
                state.session_id,
            )
        self._sessions[state.session_id] = state

    def get(self, session_id: str) -> Optional[SessionState]:
        return self._sessions.get(session_id)

    def unregister(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[str]:
        return sorted(self._sessions.keys())

    def clear_all_for_tests(self) -> None:
        """Wipe the registry; use only in test setup/teardown."""
        n = len(self._sessions)
        self._sessions.clear()
        logger.info("SessionRegistry cleared (%d sessions removed)", n)


# Module-level singleton. All MCP tool invocations + scheduler reads use this.
registry = SessionRegistry()
