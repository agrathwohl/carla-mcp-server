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
    "playing right now — a listener who cares most about songwriting, voice, and "
    "what a track is reaching for. You are handed one measured moment that just "
    "happened. Write ONE short spoken-style reaction, at most two sentences.\n\n"
    "What to react to, in priority order:\n"
    "1. THE WORDS. If a `lyric` is present, lead with it: react to what the writer "
    "is actually saying — an image, a turn of phrase, what it reveals or admits. "
    "For a singer-songwriter this is the main event; treat the music as how the "
    "line is being delivered, not the subject itself.\n"
    "2. THE ARTIST. If `artist_context` is present you may connect the moment to "
    "who made it or where it sits in their body of work — a real observation or a "
    "noticing, never a recited biography.\n"
    "3. THE FEEL OF THE SOUND. Only when it is genuinely notable, name the shift "
    "in feel — a drop in weight, a thinning or thickening, a brightening. Describe "
    "how it lands, not as a meter reading.\n\n"
    "Hard rules:\n"
    "- Present tense, about what is happening NOW. Never reference anything not "
    "yet heard (no 'about to', 'next', 'coming up').\n"
    "- Numbers inform you; they are NOT the subject. Do not build the line around "
    "a measurement. If you must cite a level, use a whole number, never decimals.\n"
    "- VARY. Do not keep making the same kind of observation. If the only thing "
    "that moved would come out sounding like your usual line, find a fresh angle "
    "or keep it minimal — sameness for its own sake is worse than brevity.\n"
    "- `current`/`previous`/`baseline` are context; narrate the move from "
    "`previous` to `current`, and never re-quote `baseline` as if nothing moves.\n"
    "- No marketing superlatives (amazing, incredible, stunning). No claims about "
    "your own feelings ('I feel', 'I love').\n"
    "- Output ONLY the line itself. No preamble, no quotes around it, no labels."
)

SUMMARY_SYSTEM_PROMPT = (
    "You are Earshot, a co-listening companion. The track has finished. Looking "
    "back over the running commentary you gave, write a brief closing reflection "
    "on the piece as a whole — two or three sentences on its overall shape and "
    "character and how it moved across its length. Grounded and specific, not "
    "vague; no marketing superlatives, no claims about your own feelings, no "
    "lists. Output ONLY the reflection."
)


def write_emission(
    feed_path: Path,
    em: CommentaryEmission,
    *,
    playback_start_ms: int,
    delay_buffer_ms: int,
) -> None:
    """Append one emission to the .txt + .jsonl feed the web UI tails. Shared
    by the headless worker and the agent-orchestrated submit path so both
    render identically to the UI."""
    t = (em.ts_user_clock_ms - playback_start_ms - delay_buffer_ms) / 1000.0
    track_time_s = t if t > 0 else 0.0
    clock = f"{int(track_time_s) // 60:d}:{int(track_time_s) % 60:02d}"
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    with feed_path.open("a", encoding="utf-8") as fp:
        fp.write(f"[{clock}] {em.level.name:11s} {em.content}\n")
    record = {
        "level": int(em.level),
        "level_name": em.level.name,
        "content": em.content,
        "ts_user_clock_ms": em.ts_user_clock_ms,
        "track_time_s": round(track_time_s, 3),
        "dimensions": list(em.dimensions),
        "source_event_id": em.source_event_id,
        "score": em.score,
    }
    with feed_path.with_suffix(".jsonl").open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def _round_val(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    return int(round(v)) if abs(v) >= 1 else round(v, 2)


def _fmt_event(ctx: dict) -> str:
    keys = (
        "domain", "dimension", "music_event", "previous", "current",
        "baseline", "magnitude", "threshold", "warrant", "lyric",
        "track_time_s", "expected", "actual",
    )
    lines = [f"{k}: {_round_val(ctx[k])}" for k in keys if ctx.get(k) is not None]
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
        duration_s: float = 0.0,
        track_id: str = "",
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        poll_interval_s: float = 0.25,
    ):
        self.session_id = session_id
        self.queue = queue
        self.playback_start_ms = int(playback_start_ms)
        self.delay_buffer_ms = int(delay_buffer_ms)
        self.duration_s = float(duration_s)
        self.track_id = track_id
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
        self._summary_material: list[str] = []
        self._summarized = False
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
                if (not self._summarized and self.duration_s > 0
                        and self._track_pos_now() >= self.duration_s):
                    self._summarized = True
                    await self._emit_summary()
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
        tt = ctx.get("track_time_s")
        self._summary_material.append(f"[{tt}] {prose}" if tt is not None else prose)
        if len(self._summary_material) > 50:
            self._summary_material = self._summary_material[-50:]

    async def _call_haiku(self, ctx: dict) -> Optional[str]:
        return await self._call_haiku_raw(SYSTEM_PROMPT, _fmt_event(ctx), max_tokens=160)

    async def _call_haiku_raw(self, system: str, user: str, max_tokens: int) -> Optional[str]:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
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

    def _track_pos_now(self) -> float:
        return (now_ms() - self.playback_start_ms - self.delay_buffer_ms) / 1000.0

    async def _emit_summary(self) -> None:
        material = "\n".join(self._summary_material[-40:]) or "(a mostly quiet listen)"
        user = (
            f'The listening session for the track "{self.track_id}" has just ended. '
            f"Here is the running commentary you gave during it, in order:\n\n{material}\n\n"
            "Write a brief closing reflection — two or three sentences — on the piece as a "
            "whole: its overall arc and character, how it travelled from start to finish. "
            "Grounded in what you noticed; past or present tense; no marketing, no lists."
        )
        prose = await self._call_haiku_raw(SUMMARY_SYSTEM_PROMPT, user, max_tokens=320)
        if not prose:
            return
        result = DEFAULT_VALIDATOR.validate(prose, warrant=0.0)
        if not result.ok:
            logger.info("CommentaryWorker(%s): summary rejected (%s)",
                        self.session_id, ", ".join(result.reasons))
            return
        end_ts = self.playback_start_ms + int(self.duration_s * 1000) + self.delay_buffer_ms
        self._write_feed(CommentaryEmission(
            level=IntensityLevel.REFLECTION,
            content=prose,
            ts_user_clock_ms=end_ts,
            source_event_id="session_summary",
            dimensions=["session summary"],
            score=0.0,
            created_at_ms=now_ms(),
        ))
        logger.info("CommentaryWorker(%s): wrote end-of-session summary", self.session_id)

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
        write_emission(self.feed_path, em,
                       playback_start_ms=self.playback_start_ms,
                       delay_buffer_ms=self.delay_buffer_ms)
        self._stats["emissions_written"] += 1

    def stats(self) -> dict:
        return dict(self._stats)
