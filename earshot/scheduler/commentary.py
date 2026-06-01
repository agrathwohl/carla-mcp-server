"""Commentary data model + output queue.

The scheduler produces two kinds of items into the per-session commentary
queue:

  - **CommentaryEmission** — ready-to-deliver content. Level 2 (action-text)
    is composed by the scheduler from the active profile's catalog; levels
    3–6 (prose) arrive via `earshot_submit_commentary` after the orchestrator
    fulfills a ProseRequest.
  - **ProseRequest** — a scheduler signal that "this event warrants prose
    at intensity N; orchestrator, please generate and submit it." The
    orchestrator reads requests via `earshot_get_commentary_queue` and
    responds via `earshot_submit_commentary`.

Anti-spoiler timing:
  Every CommentaryEmission carries `ts_user_clock_ms` = the wall-clock at
  which it should land for the user. The delay tower buffers audio so the
  agent's commentary (prepared from T+0 measurements) lands in sync with
  the user's T+delay_buffer audio. `CommentaryQueue.drain_ready(now_ms)`
  only returns emissions whose ts_user_clock_ms has arrived.

The queue is a per-session asyncio-aware structure. The scheduler
enqueues; `earshot_get_commentary_queue` drains. ProseRequests live in
the same queue alongside emissions because the orchestrator polls one
endpoint to get both — keeps the agent loop simple.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Union

from earshot.scheduler.intensity import IntensityLevel

logger = logging.getLogger(__name__)


# Atomicity ceiling matches AmbientStreamWriter.MAX_LINE_BYTES. Commentary
# lines should be far under this in practice (one emission's content is
# typically <200 chars) but defensive bookkeeping prevents a future
# verbose-prose emission from silently corrupting the JSONL.
_COMMENTARY_MAX_LINE_BYTES = 4000


# ----------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class CommentaryEmission:
    """A ready-to-deliver piece of commentary.

    Level determines render style:
      - SILENT: never queued (the scheduler emits nothing).
      - ACTION_TEXT: prefix-cued line (e.g. `>>> head bobbing`); the prefix
        is part of `content`. The orchestrator may render this as a visual
        annotation rather than spoken text.
      - EXCLAMATION / OBSERVATION / CONSIDERED / REFLECTION: prose. Spoken
        via TTS if enabled, otherwise rendered as text.

    Required fields are all required-by-construction; postmortem rule #19.
    """
    level: IntensityLevel
    content: str
    ts_user_clock_ms: int      # when this should land for the user
    source_event_id: str       # ID of the comparator event that spawned this
    dimensions: list[str]      # Dimension.value strings (what this is about)
    score: float               # the comparator score that triggered this
    created_at_ms: int         # scheduler wall-clock when emitted
    kind: str = field(default="emission", init=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level"] = int(self.level)
        d["level_name"] = self.level.name
        return d


@dataclass(frozen=True)
class ProseRequest:
    """Scheduler signal that orchestrator should generate prose for an event.

    The orchestrator reads these via `earshot_get_commentary_queue` and
    fulfils them by calling `earshot_submit_commentary` with the generated
    text. The submitted CommentaryEmission then enters the same queue.

    `context` carries everything the orchestrator needs to compose the
    prose without separate lookups: which event, what magnitude, what
    dimensions, what section the playback is in, what was expected vs
    actual. The orchestrator MAY consult other resources (oeuvre report,
    Phase 2 baseline) but the context dict alone is enough for a
    minimum-viable response.
    """
    request_id: str
    intensity_target: IntensityLevel
    source_event_id: str
    context: dict             # event payload + section info + baseline excerpt
    ts_target_user_clock_ms: int    # when the prose should ideally land
    created_at_ms: int
    kind: str = field(default="prose_request", init=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["intensity_target"] = int(self.intensity_target)
        d["intensity_target_name"] = self.intensity_target.name
        return d


# Union of items that can ride in the commentary queue.
CommentaryItem = Union[CommentaryEmission, ProseRequest]


# ----------------------------------------------------------------------
# Commentary logger — Phase L prerequisite
# ----------------------------------------------------------------------

class CommentaryLogger:
    """Append-only JSONL log of every CommentaryItem pushed to the queue.

    The session's in-memory CommentaryQueue gets drained (and forgotten)
    over the session's lifetime. Without a persistent log, Phase L's
    reflection writer would have no record of what the agent actually
    said. This logger captures emissions + prose requests at push time.

    Path convention:
        ~/.carla-mcp/earshot/sessions/{session_id}/commentary.jsonl

    Concurrency:
        Single-writer per session (the scheduler + orchestrator both push
        through the same CommentaryQueue instance on the same event loop).
        Uses O_APPEND + line-buffering for the same multi-writer-safe
        contract as AmbientStreamWriter, even though commentary isn't
        actually multi-written today — keeps the schema portable.

    Why not log inside Scheduler._emit_action_text / _emit_prose_request:
        Orchestrator-submitted prose lands via
        `earshot_submit_commentary` -> `state.commentary_queue.push()`.
        Instrumenting at the queue captures both source paths through
        one call site.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.path, "a", buffering=1, encoding="utf-8")
        self._closed = False
        self._logged_count = 0
        self._skipped_oversize = 0
        logger.info("CommentaryLogger opened: %s", self.path)

    def log(self, item: CommentaryItem) -> None:
        """Append one item's JSON representation. Silently drops on close
        rather than raising — the queue's push path shouldn't fail just
        because the session is tearing down."""
        if self._closed:
            return
        try:
            entry = item.to_dict()
        except Exception as e:
            logger.warning("CommentaryLogger.log: to_dict failed: %s", e)
            return
        # Stamp the wall-clock log-time so the reflection writer can
        # distinguish "when this was queued" from "when it should land
        # for the user" (ts_user_clock_ms on emissions).
        entry["logged_at_ms"] = int(time.time() * 1000)
        try:
            line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.warning("CommentaryLogger.log: json.dumps failed: %s", e)
            return
        if len(line.encode("utf-8")) + 1 > _COMMENTARY_MAX_LINE_BYTES:
            self._skipped_oversize += 1
            return
        try:
            self._fp.write(line + "\n")
            self._logged_count += 1
        except Exception as e:
            logger.warning("CommentaryLogger.log: write failed: %s", e)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._fp.flush()
            try:
                os.fsync(self._fp.fileno())
            except OSError as e:
                logger.debug("CommentaryLogger fsync: %s", e)
        finally:
            self._fp.close()
            self._closed = True
            logger.info("CommentaryLogger closed: %s (%d items logged)",
                        self.path, self._logged_count)

    def stats(self) -> dict:
        return {
            "path": str(self.path),
            "items_logged": self._logged_count,
            "items_skipped_oversize": self._skipped_oversize,
            "closed": self._closed,
        }


# ----------------------------------------------------------------------
# Commentary queue
# ----------------------------------------------------------------------

class CommentaryQueue:
    """Per-session output queue: scheduler in, orchestrator drains out.

    Holds both CommentaryEmissions (ready-to-deliver content) and
    ProseRequests (orchestrator-action items). `drain_ready(now_ms)`
    returns emissions whose ts_user_clock_ms <= now AND all pending
    prose requests (which the orchestrator should handle ASAP regardless
    of ts_target). `wait_for_items(since_ms, wait_seconds)` supports the
    long-poll semantics of `earshot_get_commentary_queue`.

    Unlike the EventQueue (Phase F, internal scheduler intake), the
    CommentaryQueue is the public output surface — anything that lands
    here has already passed honesty validation and timing computation.
    """

    def __init__(self, *, logger: Optional[CommentaryLogger] = None):
        # We keep items in a list (sorted by ts_user_clock_ms for emissions)
        # because we need both FIFO consumption AND time-gated filtering.
        # An asyncio.Queue alone doesn't support peek/filter; a list +
        # asyncio.Event for "new item arrived" gives us both.
        self._items: list[CommentaryItem] = []
        self._lock = asyncio.Lock()
        self._notify = asyncio.Event()
        self._pushed_emissions = 0
        self._pushed_requests = 0
        self._delivered = 0
        # Scheduler-assigned warrant per ProseRequest source_event_id. The
        # honesty escape valve must be grounded in the MEASUREMENT (the
        # scheduler's warrant), not in a value the orchestrator-LLM asserts
        # for itself — otherwise "warranted enthusiasm" is circular. submit
        # caps the LLM's echoed warrant against this authoritative record.
        self._prose_warrants: dict = {}
        # Optional Phase L persistence — when supplied, every push() also
        # writes the item to commentary.jsonl. Session lifecycle code
        # constructs and closes the logger; the queue just holds the ref.
        self._logger = logger

    # ------------------------------------------------------------------
    async def push(self, item: CommentaryItem) -> None:
        """Add an emission or prose request to the queue.

        Side effect (Phase L): when a CommentaryLogger is wired, also
        persists the item to commentary.jsonl. The log write happens
        OUTSIDE the asyncio lock so synchronous file I/O doesn't block
        other queue operations.
        """
        async with self._lock:
            self._items.append(item)
            if isinstance(item, CommentaryEmission):
                self._pushed_emissions += 1
            elif isinstance(item, ProseRequest):
                self._pushed_requests += 1
                # Record the authoritative (scheduler-measured) warrant so a
                # later submission can't self-authorize enthusiasm above it.
                if item.source_event_id:
                    self._prose_warrants[item.source_event_id] = float(
                        (item.context or {}).get("warrant", 0.0))
            self._notify.set()
        # Log after releasing the lock; logger is single-threaded-safe
        # (one event loop) and small writes don't justify holding the lock.
        if self._logger is not None:
            self._logger.log(item)

    async def drain_ready(self, now_ms: int) -> list[CommentaryItem]:
        """Return + remove all items deliverable at `now_ms`.

        Rules:
          - ProseRequests: always returned (orchestrator handles ASAP).
          - CommentaryEmissions: returned only if ts_user_clock_ms <= now_ms.
        Items that aren't ready stay in the queue for the next call.
        """
        ready: list[CommentaryItem] = []
        async with self._lock:
            remaining: list[CommentaryItem] = []
            for it in self._items:
                if isinstance(it, ProseRequest):
                    ready.append(it)
                elif isinstance(it, CommentaryEmission):
                    if it.ts_user_clock_ms <= now_ms:
                        ready.append(it)
                    else:
                        remaining.append(it)
                else:
                    remaining.append(it)  # unknown type — leave it
            self._items = remaining
            self._delivered += len(ready)
            if not self._items:
                self._notify.clear()
        return ready

    async def wait_for_items(
        self, *, since_ms: int, wait_seconds: float, now_ms_fn
    ) -> list[CommentaryItem]:
        """Long-poll: drain_ready, or block up to wait_seconds for new items.

        Args:
            since_ms:     orchestrator's last-seen ts; emissions returned
                          must have ts_user_clock_ms >= since_ms.
            wait_seconds: how long to block if nothing's ready yet.
                          0 = non-blocking, just drain whatever's ready.
            now_ms_fn:    callable returning current wall-clock ms (injected
                          to make this testable without monkey-patching time).
        """
        deadline = now_ms_fn() + int(wait_seconds * 1000)
        while True:
            ready = await self.drain_ready(now_ms_fn())
            # Re-filter emissions by since_ms (orchestrator may have already
            # seen some). ProseRequests ALWAYS pass through — drain_ready has
            # already removed them from the queue, and they carry no
            # "already delivered" semantics; dropping one here would lose it
            # permanently and the orchestrator would never fulfil it.
            filtered = []
            for it in ready:
                if isinstance(it, CommentaryEmission):
                    if it.ts_user_clock_ms >= since_ms:
                        filtered.append(it)
                else:
                    filtered.append(it)
            if filtered or wait_seconds <= 0:
                return filtered
            remaining_s = (deadline - now_ms_fn()) / 1000.0
            if remaining_s <= 0:
                return []
            try:
                await asyncio.wait_for(self._notify.wait(), timeout=min(remaining_s, 1.0))
            except asyncio.TimeoutError:
                # Could be due to remaining_s OR our 1s cap; loop to re-check.
                continue

    def snapshot(self) -> list[CommentaryItem]:
        """Peek (no removal) — for diagnostics."""
        return list(self._items)

    def warrant_for(self, source_event_id: str) -> float:
        """Scheduler-assigned warrant for a ProseRequest's source event, or 0.0.

        Authoritative: the honesty escape valve uses this (capped against the
        orchestrator's echoed value) so enthusiasm can never be granted above
        what the measurement actually warranted."""
        return float(self._prose_warrants.get(source_event_id, 0.0))

    def stats(self) -> dict:
        return {
            "queued": len(self._items),
            "pushed_emissions": self._pushed_emissions,
            "pushed_requests": self._pushed_requests,
            "delivered": self._delivered,
        }
