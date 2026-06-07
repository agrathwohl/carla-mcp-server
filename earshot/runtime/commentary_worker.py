"""Headless commentary worker — closes the orchestrator loop in-process.

Replaces the human-in-the-loop orchestrator (earshot_get_commentary_queue +
earshot_submit_commentary). Drains the session's CommentaryQueue: fulfills each
ProseRequest via the Anthropic Messages API (Claude Haiku, over httpx — no SDK
dependency), validates against the same HonestyValidator the manual path uses,
and pushes the resulting CommentaryEmission back. Scheduler action-text and the
worker's own prose then drain when they come due and are written, at their
ts_user_clock, to a plain-text feed the user tails in a separate terminal.

Output goes ONLY to the feed file. The server speaks MCP over stdout, so the
worker must never print there.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

from earshot.ambient_stream import now_ms
from earshot.honesty import DEFAULT_VALIDATOR
from earshot.scheduler.commentary import (
    CommentaryEmission,
    CommentaryQueue,
    ProseRequest,
)
from earshot.scheduler.intensity import IntensityLevel

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "You are Earshot, a co-listening companion reacting in real time to a track "
    "playing right now. You are handed one measured musical or mix event that "
    "just happened. Write ONE short spoken-style reaction — at most two "
    "sentences — about that moment.\n\n"
    "Hard rules:\n"
    "- Present tense, about what is happening NOW. Never reference anything that "
    "has not happened yet (no 'about to', 'next section', 'coming up'): the "
    "listener has not heard it.\n"
    "- Ground every claim in the numbers given (dB, BPM, key, onset rate). Be "
    "specific, not vague.\n"
    "- No marketing superlatives (amazing, incredible, stunning). No claims about "
    "your own feelings ('I feel', 'I love').\n"
    "- Only let enthusiasm rise if the event's `warrant` is >= 1.0 and the "
    "magnitude is genuinely large; otherwise stay measured and plain.\n"
    "- If a `lyric` is given, it is the words playing right now — you may quote or "
    "react to it. If `artist_context` is given you may lightly ground the "
    "reaction in who made it, but do not recite a biography.\n"
    "- Output ONLY the line itself. No preamble, no quotes around it, no labels."
)


def _fmt_event(ctx: dict) -> str:
    keys = (
        "domain", "dimension", "music_event", "magnitude", "baseline",
        "current", "threshold", "warrant", "lyric", "track_time_s",
        "expected", "actual",
    )
    lines = [f"{k}: {ctx[k]}" for k in keys if ctx.get(k) is not None]
    art = ctx.get("artist_context")
    if art:
        lines.append(f"artist_context: {art}")
    sec = ctx.get("section")
    if isinstance(sec, dict) and sec.get("index") is not None:
        lines.append(f"section_index: {sec['index']}")
    return "\n".join(lines)


class CommentaryWorker:
    """One per session. Spawned by start_session when auto_orchestrate is set."""

    def __init__(
        self,
        *,
        session_id: str,
        queue: CommentaryQueue,
        playback_start_ms: int,
        delay_buffer_ms: int,
        feed_path: Path,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        poll_interval_s: float = 0.25,
    ):
        self.session_id = session_id
        self.queue = queue
        self.playback_start_ms = int(playback_start_ms)
        self.delay_buffer_ms = int(delay_buffer_ms)
        self.feed_path = Path(feed_path)
        self._jsonl_path = self.feed_path.with_suffix(".jsonl")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.poll_interval_s = poll_interval_s

        self._task: Optional[asyncio.Task] = None
        self._inflight: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(3)
        self._running = False
        self._client: Optional[httpx.AsyncClient] = None
        self._stats = {
            "prose_fulfilled": 0,
            "prose_rejected": 0,
            "llm_errors": 0,
            "emissions_written": 0,
        }

    async def start(self) -> None:
        if self._running:
            return
        if not self.api_key:
            raise RuntimeError(
                "auto_orchestrate=True but ANTHROPIC_API_KEY is not set; the headless "
                "commentary worker cannot run. Set the key or call with "
                "auto_orchestrate=False to drive prose manually."
            )
        self.feed_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self._running = True
        self._write_header()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        inflight = list(self._inflight)
        for t in inflight:
            t.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()

    async def _run(self) -> None:
        while self._running:
            try:
                ready = await self.queue.drain_ready(now_ms())
                for item in ready:
                    if isinstance(item, ProseRequest):
                        t = asyncio.create_task(self._fulfill(item))
                        self._inflight.add(t)
                        t.add_done_callback(self._inflight.discard)
                    elif isinstance(item, CommentaryEmission):
                        self._write_feed(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "CommentaryWorker(%s): _run loop error (continuing)", self.session_id)
            await asyncio.sleep(self.poll_interval_s)

    async def _fulfill(self, req: ProseRequest) -> None:
        ctx = req.context or {}
        prose = await self._call_haiku(ctx)
        if not prose:
            return
        authoritative = self.queue.warrant_for(req.source_event_id)
        effective_warrant = min(float(ctx.get("warrant", 0.0)), authoritative)
        result = DEFAULT_VALIDATOR.validate(prose, warrant=effective_warrant)
        if not result.ok:
            self._stats["prose_rejected"] += 1
            logger.info(
                "CommentaryWorker(%s): prose rejected (%s): %r",
                self.session_id, ", ".join(result.reasons), prose,
            )
            return
        dims = ctx.get("dimensions")
        if dims is None:
            d = ctx.get("dimension")
            dims = [d] if d else []
        emission = CommentaryEmission(
            level=req.intensity_target,
            content=prose,
            ts_user_clock_ms=int(req.ts_target_user_clock_ms),
            source_event_id=req.source_event_id,
            dimensions=list(dims),
            score=float(ctx.get("score", 0.0)),
            created_at_ms=now_ms(),
        )
        await self.queue.push(emission)
        self._stats["prose_fulfilled"] += 1

    async def _call_haiku(self, ctx: dict) -> Optional[str]:
        payload = {
            "model": self.model,
            "max_tokens": 160,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": _fmt_event(ctx)}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            async with self._sem:
                resp = await self._client.post(ANTHROPIC_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self._stats["llm_errors"] += 1
            logger.warning("CommentaryWorker(%s): Haiku call failed: %s",
                           self.session_id, e)
            return None
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return text.strip() or None

    def _track_time_s(self, ts_user_clock_ms: int) -> float:
        t = (ts_user_clock_ms - self.playback_start_ms - self.delay_buffer_ms) / 1000.0
        return t if t > 0 else 0.0

    def _track_clock(self, ts_user_clock_ms: int) -> str:
        t = self._track_time_s(ts_user_clock_ms)
        return f"{int(t) // 60:d}:{int(t) % 60:02d}"

    def _write_header(self) -> None:
        with self.feed_path.open("a", encoding="utf-8") as fp:
            fp.write(f"\n=== earshot feed :: {self.session_id} ===\n")

    def _write_feed(self, em: CommentaryEmission) -> None:
        ts = em.ts_user_clock_ms
        with self.feed_path.open("a", encoding="utf-8") as fp:
            fp.write(f"[{self._track_clock(ts)}] {em.level.name:11s} {em.content}\n")
        record = {
            "level": int(em.level),
            "level_name": em.level.name,
            "content": em.content,
            "ts_user_clock_ms": ts,
            "track_time_s": round(self._track_time_s(ts), 3),
            "dimensions": list(em.dimensions),
            "source_event_id": em.source_event_id,
            "score": em.score,
        }
        with self._jsonl_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._stats["emissions_written"] += 1

    def stats(self) -> dict:
        return dict(self._stats)
