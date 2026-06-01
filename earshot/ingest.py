"""EarshotTools — the MCP-registered front door for Phase 1 ingestion.

One tool, three execution modes (see earshot/__init__.py for the why):

  - built_in    : known host → hand-coded parser runs, returns complete.
  - cached_plan : unknown host with a cached plan → executor runs, returns
                  complete (or error if the plan's selectors stale-failed).
  - unknown     : unknown host with no cached plan → tool returns
                  `status: needs_plan` with a DOM summary + plan schema.
                  The orchestrator emits a plan and re-invokes the tool
                  with `plan=<dict>`. The executor self-checks the plan
                  against the source page; on success the plan is cached
                  to ~/.carla-mcp/earshot/plans/<host>.yaml and a full
                  ingest runs.

Idempotency (postmortem rule #32): if a cached oeuvre report already exists
for the artist and `force_rerun=False`, the existing path is returned
without re-fetching. Re-ingestion is explicit, not implicit.

Honesty (postmortem rules #2, #19): top-level `status` only takes the
value `complete` when the parser/executor reports `complete`. Partial
results (some releases failed, plan validated but missed fields) surface
as `partial` with the per-item errors enumerated. Any non-recoverable
condition surfaces as `error`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from earshot import dispatch as dsp
from earshot import discovery, executor
from earshot.discovery import PLAN_SCHEMA_DOC
from earshot.http_client import fetch
from earshot.parsers import PARSERS
from utils.async_helpers import run_blocking

# Scraping a 16-release Bandcamp catalog can take 30+ seconds end-to-end
# (one HTTP GET per release page). The default 30s `run_blocking` timeout
# would cut us off mid-discography. 5 minutes is generous enough for the
# largest realistic artist surface while still bounded for the orchestrator.
INGEST_TIMEOUT_SECONDS = 300.0

logger = logging.getLogger(__name__)


# Canonical Earshot LV2 chain — URI substrings → (param name keyword → Dimension).
# Phase K uses this for autoderive_semantics; tracks the URIs of the saved
# analyzer chain (see ~/.carla-mcp/sessions/earshot_analyzer_chain.carxp).
# Add new entries when the chain grows; matching is case-insensitive substring.
_CANONICAL_CHAIN_MAP = {
    # gareus.org TPnRMS Meter (Stereo) — RMS params map to dynamic_envelope.
    # dBTP variants are true-peak in dBFS; left out because LUFS_INTEGRATED
    # is a longer-window measure than dBTP. Phase C's companion provides
    # actual LUFS via pyloudnorm.
    "tpnrmsstereo": [("rms", "dynamic_envelope")],
    "tpnrms": [("rms", "dynamic_envelope")],
}


def _derive_canonical_semantics(plugin_map: dict) -> dict:
    """Inspect plugin URIs + parameter names to build an ambient-type ->
    Dimension mapping for the canonical chain.

    Returns a dict keyed by `plugin_{id}.param_{N}` (the exact format the
    LV2Poller emits into the ambient stream), values are Dimension enum
    value strings (e.g. "dynamic_envelope"). The dict can be passed
    straight into earshot_start_session(semantics=...).

    Plugins/parameters not in the canonical map are silently skipped — the
    caller can extend the returned dict manually for custom chains.
    """
    out: dict = {}
    for plugin_id, p in plugin_map.items():
        uri = (p.get("uri") or "").lower()
        matched_rules = None
        for uri_substr, rules in _CANONICAL_CHAIN_MAP.items():
            if uri_substr in uri:
                matched_rules = rules
                break
        if matched_rules is None:
            continue
        for keyword, dimension_value in matched_rules:
            kw_lower = keyword.lower()
            for param in p.get("params", []):
                pname = (param.get("name") or "").lower()
                if kw_lower in pname:
                    key = f"plugin_{plugin_id}.param_{param['param_index']}"
                    out[key] = dimension_value
                    break  # one param per (plugin, keyword) — first match wins
    return out


async def _await_non_silence_then_start(
    *,
    session_id: str,
    reader,
    monitor_type: str,
    silence_threshold_db: float,
    timeout_s: float,
    gated: list,
) -> None:
    """Phase M sync helper — wait for first non-silent ambient entry on
    `monitor_type`, then rewrite playback_start_ms on each gated component
    and start them.

    Why this exists: when audio routing is external (PulseAudio → JACK →
    Carla chain), the user presses "play" some indeterminate time AFTER
    `earshot_start_session` returns. Anchoring playback_start_ms to
    wall-clock-at-session-start makes `track_time_s` wrong by that gap,
    so every drift-vs-baseline lookup compares the wrong slice of Phase 2.
    This watcher anchors playback_start_ms to the first sample whose
    value crosses out of silence.

    Times out after `timeout_s`; on timeout starts the gated components
    with their original playback_start_ms (session still functional, just
    desynced).

    KNOWN LIMITATION (sync + companion): When the streaming companion is
    also spawned, it received --playback-start-ms at subprocess launch
    and tags its ambient entries with that original wall-clock. After
    sync re-anchors the live playback_start_ms to a later moment, the
    companion's entries land with track_time_s < 0 from the comparators'
    POV and get silently dropped. Comparators handle this safely (they
    skip negative track_time entries), but the companion's lookahead is
    effectively wasted until its logical clock catches up to the new
    playback_start_ms. Fix path: signal the companion via SIGUSR1 or a
    control file once sync fires; or have the companion wait for the
    same non-silence signal. Out of scope for this change.
    """
    from earshot.ambient_stream import now_ms as _now_ms

    started_at_ms = _now_ms()
    sentinel = object()
    loop = asyncio.get_event_loop()

    iterator = reader.tail(
        since_ms=None,           # only future entries; ignore historical silence
        poll_interval_s=0.05,
        stop_fn=lambda: False,
    )
    detected_ts_ms = None
    try:
        while True:
            entry = await loop.run_in_executor(None, next, iterator, sentinel)
            if entry is sentinel:
                break
            elapsed_ms = _now_ms() - started_at_ms
            if elapsed_ms > timeout_s * 1000:
                logger.warning(
                    "playback_sync[%s]: timed out after %.1fs without non-silence; "
                    "starting gated components with original playback_start_ms",
                    session_id, timeout_s,
                )
                break
            if entry.get("type") != monitor_type:
                continue
            value = entry.get("value")
            if not isinstance(value, (int, float)):
                continue
            if value > silence_threshold_db:
                ts = entry.get("ts_ms")
                if not isinstance(ts, (int, float)):
                    continue
                detected_ts_ms = int(ts)
                logger.info(
                    "playback_sync[%s]: non-silence at ts_ms=%d (value=%.2f dB on %s); "
                    "anchoring playback_start_ms here",
                    session_id, detected_ts_ms, value, monitor_type,
                )
                break
    except asyncio.CancelledError:
        logger.info("playback_sync[%s]: cancelled before sync detected", session_id)
        raise

    # Apply (or skip on timeout) the new playback_start_ms, then start gated.
    if detected_ts_ms is not None:
        # Every component that caches playback_start_ms needs the update.
        # The Scheduler is already running (it wasn't gated — drains an
        # empty event_queue while sync is pending), so we mutate its
        # attribute live. That's race-free at the GIL level (single
        # attribute assignment is atomic in CPython); the next event
        # the scheduler processes will use the new value.
        from earshot.session_registry import registry
        state = registry.get(session_id)
        if state is not None:
            state.playback_start_ms = detected_ts_ms
            if state.scheduler is not None:
                state.scheduler.playback_start_ms = detected_ts_ms
        for comp in gated:
            comp.playback_start_ms = detected_ts_ms
    for comp in gated:
        try:
            await comp.start()
        except Exception as e:
            logger.warning("playback_sync[%s]: failed to start %s: %s",
                           session_id, type(comp).__name__, e)


class EarshotTools:
    """Earshot Phase 1 ingestion. Mirrors the SessionTools / PluginTools shape
    so the existing tool_registry handler-routing in server.py works without
    bespoke wiring."""

    def __init__(self, carla_controller=None, analysis_tools=None):
        # carla_controller is accepted for API parity with the other tool
        # classes; Phase 1 doesn't touch Carla. Keeping the same constructor
        # signature means server._execute_tool's routing branch is trivial.
        # analysis_tools is the project's AnalysisTools instance — Phase J's
        # earshot_start_session wires it into LV2Poller when plugin_ids are
        # supplied so the comparators can tail the live analyzer chain.
        self.carla = carla_controller
        self.analysis_tools = analysis_tools
        dsp.EARSHOT_HOME.mkdir(parents=True, exist_ok=True)
        dsp.PLAN_DIR.mkdir(parents=True, exist_ok=True)
        dsp.OEUVRE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("EarshotTools initialized (storage=%s)", dsp.EARSHOT_HOME)

    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch to the right tool method. Currently registered tools:
        `earshot_ingest_artist` (Phase 1), `earshot_analyze_track` (Phase 2),
        `earshot_refresh_expectations` (Phase G),
        `earshot_submit_commentary` + `earshot_get_commentary_queue` (Phase I),
        `earshot_start_session` + `earshot_user_interject` +
        `earshot_correct_profile` + `earshot_end_session` (Phase J),
        `earshot_load_analyzer_chain` + `earshot_wire_delay_tower` (Phase K),
        `earshot_reflect_session` (Phase L)."""
        # Strip server-injected context that aren't real tool args.
        arguments = {k: v for k, v in (arguments or {}).items()
                     if k not in ("session_context", "performance_metrics")}
        if tool_name == "earshot_ingest_artist":
            return await self.earshot_ingest_artist(**arguments)
        if tool_name == "earshot_analyze_track":
            return await self.earshot_analyze_track(**arguments)
        if tool_name == "earshot_refresh_expectations":
            return await self.earshot_refresh_expectations(**arguments)
        if tool_name == "earshot_submit_commentary":
            return await self.earshot_submit_commentary(**arguments)
        if tool_name == "earshot_get_commentary_queue":
            return await self.earshot_get_commentary_queue(**arguments)
        if tool_name == "earshot_start_session":
            return await self.earshot_start_session(**arguments)
        if tool_name == "earshot_user_interject":
            return await self.earshot_user_interject(**arguments)
        if tool_name == "earshot_correct_profile":
            return await self.earshot_correct_profile(**arguments)
        if tool_name == "earshot_end_session":
            return await self.earshot_end_session(**arguments)
        if tool_name == "earshot_load_analyzer_chain":
            return await self.earshot_load_analyzer_chain(**arguments)
        if tool_name == "earshot_wire_delay_tower":
            return await self.earshot_wire_delay_tower(**arguments)
        if tool_name == "earshot_load_source":
            return await self.earshot_load_source(**arguments)
        if tool_name == "earshot_stop_source":
            return await self.earshot_stop_source(**arguments)
        if tool_name == "earshot_play":
            return await self.earshot_play(**arguments)
        if tool_name == "earshot_stop":
            return await self.earshot_stop(**arguments)
        if tool_name == "earshot_reflect_session":
            return await self.earshot_reflect_session(**arguments)
        raise ValueError(f"Unknown earshot tool: {tool_name}")

    # ------------------------------------------------------------------
    # Tool implementation
    # ------------------------------------------------------------------
    async def earshot_ingest_artist(
        self,
        url: str,
        artist_id: str | None = None,
        plan: dict | None = None,
        force_rediscover: bool = False,
        force_rerun: bool = False,
        **_kw,
    ) -> dict:
        """Phase 1 oeuvre ingest. Returns one of three envelope shapes.

        Args:
            url:                The artist's profile URL on any host.
            artist_id:          Optional override; defaults to host-derived slug.
            plan:               Orchestrator-supplied scraping plan. Required
                                only when continuing a previous `needs_plan`
                                response for an unknown host.
            force_rediscover:   Ignore any cached plan and run discovery again.
            force_rerun:        Ignore any cached oeuvre report and refetch.

        Returns:
            envelope with `status` of `complete | partial | needs_plan | error`.
        """
        try:
            disp = dsp.dispatch(url, force_rediscover=force_rediscover)
        except Exception as e:
            return {"status": "error", "error": f"URL normalization failed: {e}", "source_url": url}

        effective_artist_id = artist_id or disp.artist_id

        # Idempotency: if we've ingested this artist before and the caller
        # didn't request a re-run, return the existing artifact path.
        oeuvre_path = dsp.oeuvre_path_for(effective_artist_id)
        if oeuvre_path.exists() and not force_rerun:
            return {
                "status": "complete",
                "cached": True,
                "artist_id": effective_artist_id,
                "host": disp.host,
                "source_url": disp.url_normalized,
                "oeuvre_report_path": str(oeuvre_path),
                "note": "Existing oeuvre report served from cache. Pass force_rerun=True to re-ingest.",
            }

        # ------------------------------------------------------------------
        # If the orchestrator supplied a plan, the prior call must have been
        # `needs_plan` for this host. Validate, self-check, cache, then run.
        # ------------------------------------------------------------------
        if plan is not None:
            return await self._run_with_supplied_plan(plan, disp, effective_artist_id)

        # ------------------------------------------------------------------
        # Built-in parsers — sync I/O isolated via run_blocking (postmortem
        # rule #3 / codebase consistency: SessionTools/PluginTools route all
        # blocking work through utils.async_helpers).
        # ------------------------------------------------------------------
        if disp.route == "built_in":
            parser_mod = PARSERS[disp.parser_name]
            try:
                result = await run_blocking(
                    parser_mod.ingest,
                    disp.url_normalized,
                    effective_artist_id,
                    timeout=INGEST_TIMEOUT_SECONDS,
                    description=f"earshot.parsers.{disp.parser_name}.ingest",
                )
            except Exception as e:
                logger.exception("built-in parser %s failed", disp.parser_name)
                return {
                    "status": "error",
                    "host": disp.host,
                    "parser": disp.parser_name,
                    "source_url": disp.url_normalized,
                    "artist_id": effective_artist_id,
                    "error": f"built-in parser raised: {e}",
                }
            self._write_structured_artifact(effective_artist_id, result)
            result["oeuvre_report_path"] = str(dsp.oeuvre_path_for(effective_artist_id))
            return result

        # ------------------------------------------------------------------
        # Cached plan path
        # ------------------------------------------------------------------
        if disp.route == "cached_plan" and disp.plan_path is not None:
            try:
                cached_plan = yaml.safe_load(disp.plan_path.read_text(encoding="utf-8"))
            except Exception as e:
                return {
                    "status": "error",
                    "host": disp.host,
                    "source_url": disp.url_normalized,
                    "artist_id": effective_artist_id,
                    "error": f"cached plan unreadable: {e}",
                    "plan_path": str(disp.plan_path),
                }
            result = await run_blocking(
                executor.execute,
                cached_plan,
                disp.url_normalized,
                effective_artist_id,
                timeout=INGEST_TIMEOUT_SECONDS,
                description=f"earshot.executor.execute (cached plan {disp.host})",
            )
            result["plan_source"] = "cached"
            result["plan_path"] = str(disp.plan_path)
            self._write_structured_artifact(effective_artist_id, result)
            result["oeuvre_report_path"] = str(dsp.oeuvre_path_for(effective_artist_id))
            return result

        # ------------------------------------------------------------------
        # Unknown host, no plan supplied — return discovery envelope.
        # Orchestrator must call back with `plan=<dict>`.
        # ------------------------------------------------------------------
        try:
            html = await run_blocking(
                fetch,
                disp.url_normalized,
                timeout=60.0,
                description=f"earshot.discovery fetch {disp.url_normalized}",
            )
        except Exception as e:
            return {
                "status": "error",
                "host": disp.host,
                "source_url": disp.url_normalized,
                "artist_id": effective_artist_id,
                "error": f"fetch of {disp.url_normalized} failed: {e}",
            }
        summary = discovery.generate_summary(disp.url_normalized, html)
        return {
            "status": "needs_plan",
            "host": disp.host,
            "source_url": disp.url_normalized,
            "artist_id": effective_artist_id,
            "dom_summary": summary,
            "plan_schema_doc": PLAN_SCHEMA_DOC,
            "next_call_instructions": (
                "Examine dom_summary and emit a scraping plan matching the "
                "schema described in plan_schema_doc. Re-invoke earshot_ingest_artist "
                f"with the same url and plan=<your plan dict>. The plan must set "
                f"host: \"{disp.host}\". Plans that pass self-check are cached at "
                f"{dsp.plan_path_for(disp.host)} and reused on subsequent runs."
            ),
        }

    # ------------------------------------------------------------------
    # Phase 2 — track pre-analysis (companion subprocess)
    # ------------------------------------------------------------------
    async def earshot_analyze_track(
        self,
        track_url: str,
        artist_id: str,
        track_id: str | None = None,
        force_rerun: bool = False,
        **_kw,
    ) -> dict:
        """Phase 2 pre-analysis: download (if URL) + librosa/essentia/Whisper.

        Heavy lifting runs in the companion venv at
        `~/.carla-mcp/earshot/companion/.venv/bin/python` against
        `earshot/companion/phase2.py`. The project-side wrapper:

          - normalizes inputs
          - computes a deterministic track_id if absent
          - checks the on-disk cache (idempotency, postmortem rule #32)
          - dispatches via subprocess wrapped in run_blocking
          - reads back the context.json artifact
          - returns the envelope to the caller

        The companion venv must exist. If it doesn't, return an error envelope
        rather than falling back to library-less analysis (postmortem rule #19:
        success bool must mean contract fulfilled).
        """
        if not track_url:
            return {"status": "error", "error": "track_url is required"}
        if not artist_id:
            return {"status": "error", "error": "artist_id is required"}

        if not track_id:
            tail = track_url.rstrip("/").rsplit("/", 1)[-1] or "track"
            tail = re.sub(r"[^A-Za-z0-9_.-]+", "-", tail).strip("-")
            track_id = tail[:80] or "track"

        track_root = dsp.TRACKS_DIR / track_id
        context_path = track_root / "context.json"

        if context_path.exists() and not force_rerun:
            try:
                cached = json.loads(context_path.read_text())
            except Exception as e:
                return {"status": "error", "error": f"cached context unreadable: {e}",
                        "context_path": str(context_path)}
            return {
                "status": "complete",
                "cached": True,
                "track_id": track_id,
                "artist_id": artist_id,
                "context_path": str(context_path),
                "baseline_summary": cached.get("baseline"),
                "note": "Cached Phase 2 artifact served. Pass force_rerun=True to re-analyze.",
            }

        companion_python = Path.home() / ".carla-mcp/earshot/companion/.venv/bin/python"
        if not companion_python.exists():
            return {
                "status": "error",
                "error": (
                    f"companion python not found at {companion_python}. "
                    "Set up the companion venv with essentia/faster-whisper/yt-dlp "
                    "before invoking Phase 2."
                ),
            }

        phase2_script = Path(__file__).resolve().parent / "companion" / "phase2.py"
        if not phase2_script.exists():
            return {"status": "error", "error": f"phase2 script missing at {phase2_script}"}

        track_root.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(companion_python),
            str(phase2_script),
            "--track-url", track_url,
            "--track-id", track_id,
            "--artist-id", artist_id,
            "--output", str(context_path),
        ]
        # NixOS HTTPS gotcha (postmortem rule #13): ensure cert bundle is set.
        env = {**os.environ, "SSL_CERT_FILE": os.environ.get("SSL_CERT_FILE", "/etc/ssl/certs/ca-bundle.crt")}

        def _run_subprocess():
            return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)

        try:
            proc = await run_blocking(
                _run_subprocess,
                timeout=920.0,
                description=f"earshot.companion.phase2 {track_id}",
            )
        except Exception as e:
            return {"status": "error", "error": f"companion subprocess raised: {e}",
                    "track_id": track_id}

        # Companion prints a single JSON line to stdout describing its outcome.
        stdout = (proc.stdout or "").strip()
        stderr_tail = (proc.stderr or "")[-2000:]
        try:
            companion_result = json.loads(stdout.splitlines()[-1]) if stdout else {}
        except Exception:
            companion_result = {"status": "error", "error": "companion stdout not parseable JSON",
                                "stdout_tail": stdout[-500:]}

        if proc.returncode != 0 or companion_result.get("status") != "complete":
            return {
                "status": "error",
                "track_id": track_id,
                "artist_id": artist_id,
                "returncode": proc.returncode,
                "companion_result": companion_result,
                "stderr_tail": stderr_tail,
            }

        # Confirm artifact actually landed on disk (postmortem rule #2:
        # don't trust success bools without verifying the contract).
        if not context_path.exists():
            return {
                "status": "error",
                "track_id": track_id,
                "artist_id": artist_id,
                "error": "companion claimed success but context.json missing",
                "companion_result": companion_result,
            }

        try:
            context = json.loads(context_path.read_text())
        except Exception as e:
            return {"status": "error", "error": f"wrote context but cannot reread: {e}"}

        return {
            "status": "complete",
            "cached": False,
            "track_id": track_id,
            "artist_id": artist_id,
            "context_path": str(context_path),
            "duration_s": context.get("duration_s"),
            "baseline_summary": context.get("baseline"),
            "section_count": len(context.get("section_map") or []),
            "transcript_segment_count": context.get("transcript_segment_count"),
        }

    # ------------------------------------------------------------------
    # Phase G — expectation refresh (orchestrator → tracker round-trip)
    # ------------------------------------------------------------------
    async def earshot_refresh_expectations(
        self,
        session_id: str,
        section_index: int,
        predictions: dict,
        source: str = "orchestrator",
        **_kw,
    ) -> dict:
        """Install fresh per-section predictions in the session's tracker.

        Called by the orchestrator in response to a BoundaryApproachingEvent
        that arrived via the commentary queue. Predictions are typed as a
        dict of `Dimension.value` → expected value, e.g.
        ``{"tempo": 103.5, "dynamic_envelope": -18.0, "lufs_integrated": -12.0}``.

        The prediction comparator (Phase F) picks up the new state on its
        next sample and starts evaluating prediction errors against it
        immediately. Stale predictions decay per the tracker's confidence
        model (see expectation_tracker.py).
        """
        from earshot.session_registry import registry

        if not session_id:
            return {"status": "error", "error": "session_id is required"}
        state = registry.get(session_id)
        if state is None:
            return {
                "status": "error",
                "error": f"unknown session_id {session_id!r}; was earshot_start_session called?",
                "known_sessions": registry.list_sessions(),
            }
        try:
            installed = await state.tracker.refresh(
                section_index, predictions, source=source
            )
        except ValueError as e:
            return {
                "status": "error",
                "error": str(e),
                "session_id": session_id,
            }
        return {
            "status": "complete",
            "session_id": session_id,
            "section_index": installed.section_index,
            "refreshed_at_ms": installed.refreshed_at_ms,
            "dims_installed": sorted(installed.predictions.keys()),
            "tracker_stats": state.tracker.stats(),
        }

    # ------------------------------------------------------------------
    # Phase I — commentary queue endpoints (orchestrator-facing)
    # ------------------------------------------------------------------
    async def earshot_submit_commentary(
        self,
        session_id: str,
        ts_target_user_clock_ms: int,
        intensity: int,
        content: str,
        source_event_id: str = "",
        dimensions: Optional[list] = None,
        score: float = 0.0,
        warrant: float = 0.0,
        **_kw,
    ) -> dict:
        """Orchestrator → scheduler: prose response to a ProseRequest.

        The orchestrator reads a ProseRequest from
        `earshot_get_commentary_queue`, composes prose at the requested
        intensity, and submits via this tool. The scheduler validates
        against honesty rules (anti-spoiler, no marketing language, no
        feeling claims) before queueing. Rejected submissions return
        `status: "error"` with the validation reasons so the orchestrator
        can revise.

        Args:
            session_id:               active session
            ts_target_user_clock_ms:  when this should land for the user
                                      (matches ts_target from the ProseRequest)
            intensity:                IntensityLevel value (3-6 for prose)
            content:                  the prose text to deliver
            source_event_id:          for traceability
            dimensions:               list of Dimension.value strings
            score:                    the comparator score that triggered this
            warrant:                  the ProseRequest's `warrant` (event-score
                                      multiple). Echo it back so a genuinely
                                      surprising event permits warranted
                                      enthusiasm past the honesty filter.
                                      Default 0.0 = no enthusiasm latitude.
        """
        from earshot.session_registry import registry
        from earshot.scheduler.commentary import CommentaryEmission
        from earshot.scheduler.intensity import IntensityLevel
        from earshot.honesty import DEFAULT_VALIDATOR
        from earshot.ambient_stream import now_ms

        if not session_id:
            return {"status": "error", "error": "session_id is required"}
        state = registry.get(session_id)
        if state is None:
            return {"status": "error", "error": f"unknown session_id {session_id!r}"}
        if state.commentary_queue is None:
            return {"status": "error",
                    "error": f"session {session_id!r} has no commentary_queue (Phase J not yet wired?)"}
        try:
            level = IntensityLevel(intensity)
        except ValueError:
            return {"status": "error", "error": f"invalid intensity {intensity!r} (1-6)"}
        if level == IntensityLevel.SILENT:
            return {"status": "error",
                    "error": "cannot submit SILENT-level content; silence is suppression, not emission"}

        # Honesty gate — postmortem rule #19: don't queue dishonest content.
        # `warrant` permits warranted enthusiasm (a real, surprising measured
        # event); anti-spoiler/taste/marketing stay blocked regardless.
        # The effective warrant is CAPPED at the scheduler-assigned value for
        # this event (recorded when the ProseRequest was queued) so the
        # orchestrator-LLM cannot self-authorize enthusiasm above what the
        # measurement warranted — that would make the grounding circular.
        authoritative = state.commentary_queue.warrant_for(source_event_id)
        effective_warrant = min(float(warrant), authoritative)
        result = DEFAULT_VALIDATOR.validate(content, warrant=effective_warrant)
        if not result.ok:
            return {
                "status": "rejected",
                "session_id": session_id,
                "reasons": result.reasons,
                "note": "Revise the prose to address the listed honesty rule violations and resubmit.",
            }

        emission = CommentaryEmission(
            level=level,
            content=content,
            ts_user_clock_ms=int(ts_target_user_clock_ms),
            source_event_id=source_event_id,
            dimensions=list(dimensions or []),
            score=float(score),
            created_at_ms=now_ms(),
        )
        await state.commentary_queue.push(emission)
        return {
            "status": "complete",
            "session_id": session_id,
            "level": level.name,
            "ts_user_clock_ms": emission.ts_user_clock_ms,
        }

    async def earshot_get_commentary_queue(
        self,
        session_id: str,
        since_ts_ms: int = 0,
        wait_seconds: float = 0.0,
        **_kw,
    ) -> dict:
        """Scheduler → orchestrator: drain ready commentary items.

        Returns CommentaryEmissions whose ts_user_clock_ms <= now (they're
        ready to deliver to the user) AND any pending ProseRequests
        (orchestrator handles ASAP). Long-poll: if `wait_seconds > 0` and
        nothing is ready, blocks up to that long waiting for new items.

        The orchestrator polls this in a loop, rendering emissions to the
        user (visual annotation for action-text, TTS for prose) and
        responding to ProseRequests via `earshot_submit_commentary`.

        Args:
            session_id:    active session
            since_ts_ms:   only return items with target ts >= this
                           (orchestrator's last-seen marker)
            wait_seconds:  0 = non-blocking; >0 = long-poll up to this many seconds
        """
        from earshot.session_registry import registry
        from earshot.ambient_stream import now_ms

        if not session_id:
            return {"status": "error", "error": "session_id is required"}
        state = registry.get(session_id)
        if state is None:
            return {"status": "error", "error": f"unknown session_id {session_id!r}"}
        if state.commentary_queue is None:
            return {"status": "error",
                    "error": f"session {session_id!r} has no commentary_queue (Phase J not yet wired?)"}

        items = await state.commentary_queue.wait_for_items(
            since_ms=int(since_ts_ms),
            wait_seconds=float(wait_seconds),
            now_ms_fn=now_ms,
        )
        return {
            "status": "complete",
            "session_id": session_id,
            "count": len(items),
            "items": [it.to_dict() for it in items],
            "queue_stats": state.commentary_queue.stats(),
        }

    # ------------------------------------------------------------------
    # Phase K — analyzer chain loader (delay tower lives in the .carxp)
    # ------------------------------------------------------------------
    async def earshot_load_analyzer_chain(
        self,
        carxp_path: Optional[str] = None,
        skip_if_loaded: bool = True,
        include_params: bool = False,
        **_kw,
    ) -> dict:
        """Phase K — bring up Earshot's canonical analyzer chain.

        Loads `~/.carla-mcp/sessions/earshot_analyzer_chain.carxp` (or the
        supplied path), introspects the loaded plugins, and returns a
        plugin map + a derived `semantics` dict ready to pass to
        `earshot_start_session`.

        The delay-tower configuration (art_delay_stereo's time / feedback /
        mix params) lives baked into the .carxp itself — re-saving the
        project is the supported way to retune it. This tool intentionally
        does NOT re-configure delay params; that would silently diverge
        from the on-disk source of truth.

        Args:
            carxp_path:      Override the default chain path.
            skip_if_loaded:  When True (default) AND plugins already loaded,
                             introspect what's there without re-loading
                             (preserves any live param tweaks).
            include_params:  When True, include the full per-plugin parameter
                             list in the response. Defaults to False because
                             LSP-family plugins (e.g. art_delay_stereo) carry
                             700+ params each — including them all blows up
                             the MCP tool response well past the response
                             size ceiling. The params are still read
                             internally for semantics derivation.
        """
        from utils.async_helpers import run_blocking

        if self.carla is None:
            return {"status": "error", "error": "carla controller not wired"}
        if not self.carla.engine_running:
            try:
                self.carla.start_engine()
            except Exception as e:
                return {"status": "error", "error": f"carla engine start failed: {e}"}

        if carxp_path is None:
            carxp_path = str(Path.home() / ".carla-mcp" / "sessions" /
                             "earshot_analyzer_chain.carxp")

        plugin_count = await run_blocking(
            self.carla.host.get_current_plugin_count,
            timeout=5.0,
            description="get plugin count",
        )

        loaded_via_carxp = False
        if plugin_count == 0 or not skip_if_loaded:
            if not os.path.exists(carxp_path):
                return {
                    "status": "error",
                    "error": f"chain carxp not found: {carxp_path}",
                    "remediation": (
                        "Save your analyzer chain via Carla's File > Save As, "
                        "or supply `carxp_path` pointing at an existing project."
                    ),
                }
            success = await run_blocking(
                self.carla.load_project,
                carxp_path,
                timeout=30.0,
                description="load analyzer chain carxp",
            )
            if not success:
                return {
                    "status": "error",
                    "error": f"carla.load_project returned False for {carxp_path}",
                }
            loaded_via_carxp = True
            plugin_count = await run_blocking(
                self.carla.host.get_current_plugin_count,
                timeout=5.0,
                description="get plugin count after load",
            )

        # Introspect every loaded plugin.
        plugin_map: dict = {}
        for plugin_id in range(plugin_count):
            info = await run_blocking(
                self.carla.host.get_plugin_info,
                plugin_id,
                timeout=5.0,
                description=f"get_plugin_info[{plugin_id}]",
            )
            if not info:
                continue
            param_count = await run_blocking(
                self.carla.host.get_parameter_count,
                plugin_id,
                timeout=5.0,
                description=f"get_parameter_count[{plugin_id}]",
            ) or 0
            params: list = []
            for p in range(param_count):
                p_info = await run_blocking(
                    self.carla.host.get_parameter_info,
                    plugin_id, p,
                    timeout=2.0,
                    description=f"get_parameter_info[{plugin_id},{p}]",
                )
                if p_info:
                    params.append({
                        "param_index": p,
                        "name": p_info.get("name", ""),
                        "symbol": p_info.get("symbol", ""),
                    })
            # For LV2, info["label"] holds the URI per carla_controller.py:462.
            uri_or_label = info.get("label", "") or ""
            plugin_map[plugin_id] = {
                "name": info.get("name", f"plugin_{plugin_id}"),
                "uri": uri_or_label,
                "audio_ins": info.get("audioIns", 0),
                "audio_outs": info.get("audioOuts", 0),
                "param_count": len(params),
                "params": params,
            }

        semantics_map = _derive_canonical_semantics(plugin_map)

        # Strip the verbose per-plugin params list from the return payload
        # unless the caller explicitly requested it. Semantics derivation
        # already consumed the params; downstream callers (start_session)
        # only need the param_count + plugin metadata.
        if not include_params:
            for p in plugin_map.values():
                p.pop("params", None)

        return {
            "status": "complete",
            "loaded_from_carxp": loaded_via_carxp,
            "carxp_path": carxp_path if loaded_via_carxp else None,
            "plugin_count": plugin_count,
            "plugins": plugin_map,
            "semantics": semantics_map,
            "next_call_hint": (
                "Pass `semantics=<the semantics dict>` and "
                "`plugin_ids=<list(plugins.keys())>` to earshot_start_session "
                "to enable drift + prediction tracking via the LV2 chain."
            ),
        }

    async def earshot_wire_delay_tower(
        self,
        source_ports: list,
        sink_ports: Optional[list] = None,
        chain_entry_plugin_id: int = 0,
        delay_plugin_id: int = 4,
        disconnect_source_from_sink: bool = True,
        **_kw,
    ) -> dict:
        """Phase K — wire the analyzer chain + delay tower into the live
        audio path so the user hears audio ON A DELAY while the analyzer
        sees raw audio.

        Architecture (per Earshot README §"co-listening"):
            SOURCE ─┬─ analyzer chain entry (raw — agent's clock)
                    └─ delay tower input ─ delay tower output ─ SYSTEM PLAYBACK (user's clock = T + delay)

        Without this routing, `delay_buffer_ms` is just a math constant
        used in `ts_user_clock = event.ts_ms + delay_buffer_ms` but the
        user actually hears audio in real-time — the anti-spoiler
        discipline collapses because the agent's measurements arrive at
        the SAME wall-clock as the user's perception of the audio.

        After this routing:
          - Agent sees the source at T (no delay)
          - User hears the source at T + (art_delay_stereo's delay time)
          - Commentary scheduled to land at ts_user_clock = event.ts_ms +
            delay_buffer_ms actually arrives "on the moment" from the
            user's perspective.

        Side effect: when `disconnect_source_from_sink=True` (default), any
        existing direct `source -> sink` connections are torn down so the
        user doesn't hear undelayed + delayed audio mixed. To restore the
        direct path later, the caller has to reconnect manually (this tool
        is intentionally not stateful — it doesn't remember prior routes).

        Args:
            source_ports:       JACK output ports producing the audio,
                                e.g. ["PulseAudio_JACK_Sink:front-left",
                                      "PulseAudio_JACK_Sink:front-right"].
            sink_ports:         Where the user's ear is. Defaults to
                                ["system:playback_1", "system:playback_2"].
            chain_entry_plugin_id: Plugin index of the analyzer chain's
                                first node (default 0 = LSP autogain).
            delay_plugin_id:    Plugin index of the delay tower
                                (default 4 = LSP art_delay_stereo).
            disconnect_source_from_sink: When True, tear down any direct
                                source->sink connections so the user only
                                hears the delayed path.
        """
        from utils.async_helpers import run_blocking

        if not source_ports:
            return {"status": "error", "error": "source_ports is required"}
        if sink_ports is None:
            sink_ports = ["system:playback_1", "system:playback_2"]
        if len(source_ports) != 2 or len(sink_ports) != 2:
            return {"status": "error",
                    "error": "source_ports and sink_ports must each have exactly 2 elements (L, R)"}
        if self.carla is None or not self.carla.engine_running:
            return {"status": "error", "error": "Carla engine not running"}

        # Resolve plugin display names — we need them to build JACK port
        # names like "CarlaMCP_default.0/<plugin name>:<port>".
        def _plugin_name(pid: int) -> Optional[str]:
            info = self.carla.host.get_plugin_info(pid)
            return info.get("name") if info else None

        chain_name = await run_blocking(
            _plugin_name, chain_entry_plugin_id, timeout=2.0,
            description=f"get_plugin_info[{chain_entry_plugin_id}]")
        delay_name = await run_blocking(
            _plugin_name, delay_plugin_id, timeout=2.0,
            description=f"get_plugin_info[{delay_plugin_id}]")
        if not chain_name or not delay_name:
            return {"status": "error",
                    "error": f"plugin name lookup failed (chain={chain_name!r}, delay={delay_name!r})"}

        # LSP plugins expose audio I/O as "Input L"/"Input R"/"Output L"/
        # "Output R" with spaces. JACK quoting handled by subprocess (no
        # shell). gareus plugins use "InL"/"OutL" without spaces, but the
        # canonical chain's entry (autogain) and delay (art_delay_stereo)
        # are both LSP, so we hard-code LSP port names here.
        client_prefix = "CarlaMCP_default.0"
        chain_in_L = f"{client_prefix}/{chain_name}:Input L"
        chain_in_R = f"{client_prefix}/{chain_name}:Input R"
        delay_in_L = f"{client_prefix}/{delay_name}:Input L"
        delay_in_R = f"{client_prefix}/{delay_name}:Input R"
        delay_out_L = f"{client_prefix}/{delay_name}:Output L"
        delay_out_R = f"{client_prefix}/{delay_name}:Output R"

        def _jack_connect(src: str, dst: str) -> tuple:
            proc = subprocess.run(
                ["jack_connect", src, dst],
                capture_output=True, text=True, timeout=5.0,
            )
            # jack_connect returns 1 if the connection already exists;
            # that's not really an error for our purposes.
            already = "already" in (proc.stderr or "").lower()
            return (proc.returncode == 0 or already, proc.stderr.strip())

        def _jack_disconnect(src: str, dst: str) -> tuple:
            proc = subprocess.run(
                ["jack_disconnect", src, dst],
                capture_output=True, text=True, timeout=5.0,
            )
            return (proc.returncode == 0, proc.stderr.strip())

        def _list_connections(port: str) -> list:
            proc = subprocess.run(
                ["jack_lsp", "-c", port],
                capture_output=True, text=True, timeout=5.0,
            )
            if proc.returncode != 0:
                return []
            # jack_lsp -c output: line for the port, then indented lines for connected peers
            lines = (proc.stdout or "").splitlines()
            connections = []
            saw_self = False
            for line in lines:
                if not saw_self and line.strip() == port:
                    saw_self = True
                    continue
                if saw_self and (line.startswith(" ") or line.startswith("\t")):
                    connections.append(line.strip())
                else:
                    saw_self = False
            return connections

        operations: list = []
        errors: list = []

        # Step 1: tear down any direct source -> sink connections so the
        # user only hears the delayed path. We discover them rather than
        # assuming a specific topology — JACK auto-graph rules vary by setup.
        if disconnect_source_from_sink:
            for src in source_ports:
                peers = await run_blocking(
                    _list_connections, src, timeout=5.0,
                    description=f"jack_lsp -c {src}")
                for peer in peers:
                    if peer in sink_ports or any(
                        peer.startswith(s.split(":")[0] + ":") for s in sink_ports
                    ):
                        ok, err = await run_blocking(
                            _jack_disconnect, src, peer, timeout=5.0,
                            description=f"disconnect {src} -> {peer}")
                        operations.append({"op": "disconnect", "src": src, "dst": peer, "ok": ok})
                        if not ok and err:
                            errors.append(f"disconnect {src}->{peer}: {err}")

        # Step 2: connect source -> analyzer chain entry (so analyzer reads
        # the source) AND source -> delay tower input (so delay buffers it).
        connections_to_make = [
            (source_ports[0], chain_in_L),
            (source_ports[1], chain_in_R),
            (source_ports[0], delay_in_L),
            (source_ports[1], delay_in_R),
            # Step 3: delay tower output -> system playback.
            (delay_out_L, sink_ports[0]),
            (delay_out_R, sink_ports[1]),
        ]
        for src, dst in connections_to_make:
            ok, err = await run_blocking(
                _jack_connect, src, dst, timeout=5.0,
                description=f"connect {src} -> {dst}")
            operations.append({"op": "connect", "src": src, "dst": dst, "ok": ok})
            if not ok and err:
                errors.append(f"connect {src}->{dst}: {err}")

        return {
            "status": "complete" if not errors else "partial",
            "chain_plugin_name": chain_name,
            "delay_plugin_name": delay_name,
            "operations": operations,
            "errors": errors,
            "topology": {
                "agent_reads": [
                    f"{src} -> {chain_in_L if i == 0 else chain_in_R}"
                    for i, src in enumerate(source_ports)
                ],
                "user_hears": [
                    f"{src} -> delay -> {sink_ports[i]}"
                    for i, src in enumerate(source_ports)
                ],
            },
            "note": (
                "Routed the delay-tower path: agent's analyzer chain reads the "
                "undelayed source; the user-facing path goes through the delay "
                "plugin. Direct source->sink links were torn down when "
                "disconnect_source_from_sink=True. IMPORTANT: the ts_user_clock "
                "anti-spoiler math is only physically correct if the delay plugin "
                "is wet-only (Dry enable=0) with a delay line active and a non-zero "
                "time, baked into the loaded .carxp. If Dry enable=1 the plugin "
                "sums dry+wet and the user hears the source undelayed AND delayed "
                "(doubled audio). Verify/retune those params in the .carxp."
            ),
        }

    # ------------------------------------------------------------------
    # Phase N — programmatic audio source via Carla's internal audiofile plugin
    # ------------------------------------------------------------------
    # audiofile parameter indices (from Carla native-plugins/audio-file.cpp):
    #   0 = Loop Mode (boolean), 1 = Host Sync (boolean).
    _AUDIOFILE_PARAM_LOOP = 0
    _AUDIOFILE_PARAM_HOSTSYNC = 1

    # Canonical map of the streaming companion's ambient `type`s -> comparator
    # Dimension values (see earshot/companion/streaming.py emit() calls and the
    # Dimension enum in comparators/events.py). Merged into the LV2-chain
    # semantics by earshot_play so the music-domain dims (tempo/key/harmonic/
    # onset) actually drive drift + prediction, not just dynamics. Without this
    # the companion writes these entries to the ambient stream but no comparator
    # is mapped to them, so every event ends up domain="mix".
    _COMPANION_SEMANTICS = {
        "tempo_bpm": "tempo",
        "key": "key",
        "onset_density": "onset_density",
        "harmonic_tension": "harmonic_tension",
        "chord_change_rate": "chord_change_rate",
        "lufs_integrated": "lufs_integrated",
        "spectral_centroid": "spectral_centroid",
    }

    async def _load_audiofile_source(
        self, file_path: str, *, loop: bool = False, host_sync: bool = True,
    ) -> dict:
        """Load Carla's internal `audiofile` player on `file_path`, set the
        file, configure loop / host-sync, activate, and WAIT for its JACK
        output ports to appear (Carla exposes them a process-cycle after
        add_plugin, so an instant query races and finds none).

        Returns {plugin_id, name, output_ports, file_verified} or {error}.
        Caller owns transport rolling + wiring.
        """
        from utils.async_helpers import run_blocking

        if self.carla is None:
            return {"error": "carla controller not wired"}
        if not file_path or not os.path.exists(file_path):
            return {"error": f"audio file not found: {file_path!r}"}
        if not self.carla.engine_running:
            try:
                self.carla.start_engine()
            except Exception as e:
                return {"error": f"carla engine start failed: {e}"}

        pid = await run_blocking(
            self.carla.add_internal_plugin, "audiofile", "EarshotSource",
            timeout=15.0, description="load audiofile internal plugin")
        if pid is None:
            return {"error": ("audiofile internal plugin failed to load — the "
                              "label may differ in this Carla build (see server log)")}

        # set_custom_data is void in this binding; set then verify via read-back
        # (get_plugin_file uses the same custom-data type as set_plugin_file).
        await run_blocking(self.carla.set_plugin_file, pid, file_path,
                           timeout=10.0, description="set audiofile file")
        verified = await run_blocking(
            self.carla.get_plugin_file, pid,
            timeout=5.0, description="verify file set")
        file_verified = (verified == file_path)

        # Loop off (play once, matches the single-pass Phase 2 baseline) +
        # host-sync on (server transport is authoritative for deterministic t=0).
        await run_blocking(self.carla.set_parameter, pid,
                           self._AUDIOFILE_PARAM_LOOP, 1.0 if loop else 0.0,
                           timeout=3.0, description="set loop mode")
        await run_blocking(self.carla.set_parameter, pid,
                           self._AUDIOFILE_PARAM_HOSTSYNC, 1.0 if host_sync else 0.0,
                           timeout=3.0, description="set host sync")
        await run_blocking(self.carla.set_plugin_active, pid, True,
                           timeout=5.0, description="activate source")

        info = await run_blocking(self.carla.host.get_plugin_info, pid,
                                  timeout=5.0, description="source plugin info")
        pname = (info.get("name") if info else None) or "EarshotSource"

        def _jack_lines():
            proc = subprocess.run(["jack_lsp"], capture_output=True, text=True, timeout=5.0)
            return (proc.stdout or "").splitlines()

        def _audio_outs(lines):
            # audio output ports of the source: name match + suffix has "out"
            # but is not an events/CV port (events-out, "Play status").
            outs = []
            for ln in lines:
                if pname not in ln:
                    continue
                suf = ln.rsplit(":", 1)[-1].lower()
                if "out" in suf and "event" not in suf and "status" not in suf:
                    outs.append(ln)
            return outs

        # Poll for ports. Carla exposes them a process-cycle after load, but
        # under CPU/jackd load (e.g. immediately after a Phase 2 essentia run)
        # the internal audiofile's JACK port registration can lag several
        # seconds. Poll generously — the loop breaks the instant the ports
        # appear, so a fast (idle-system) load pays no extra latency.
        out_ports = []
        for _ in range(60):  # up to ~6s under load
            lines = await run_blocking(_jack_lines, timeout=6.0, description="jack_lsp poll")
            out_ports = _audio_outs(lines)
            if out_ports:
                break
            await asyncio.sleep(0.1)

        return {
            "plugin_id": pid, "name": pname,
            "output_ports": out_ports, "file_verified": file_verified,
        }

    async def earshot_load_source(
        self,
        file_path: str,
        to_speakers: bool = True,
        autoplay: bool = True,
        loop: bool = False,
        **_kw,
    ) -> dict:
        """Low-level: load the audiofile source on a file and (optionally) wire
        its outputs straight to system playback + roll transport. Diagnostic /
        manual-playback path; `earshot_play` is the full co-listening entry."""
        from utils.async_helpers import run_blocking

        res = await self._load_audiofile_source(file_path, loop=loop)
        if "error" in res:
            return {"status": "error", "error": res["error"]}
        pid, pname, out_ports = res["plugin_id"], res["name"], res["output_ports"]
        if not out_ports:
            return {"status": "error", "plugin_id": pid,
                    "error": "source loaded but no JACK output ports appeared within ~2s"}

        connections = []
        if to_speakers:
            def _conn(src, dst):
                proc = subprocess.run(["jack_connect", src, dst],
                                      capture_output=True, text=True, timeout=5.0)
                return proc.returncode == 0 or "already" in (proc.stderr or "").lower()
            sinks = ["system:playback_1", "system:playback_2"]
            pairs = (list(zip(out_ports[:2], sinks)) if len(out_ports) >= 2
                     else [(out_ports[0], sinks[0]), (out_ports[0], sinks[1])])
            for src, dst in pairs:
                ok = await run_blocking(_conn, src, dst, timeout=5.0,
                                        description=f"connect {src} -> {dst}")
                connections.append({"src": src, "dst": dst, "ok": ok})

        if autoplay:
            await run_blocking(self.carla.transport_relocate, 0, timeout=3.0,
                               description="transport relocate 0")
            await run_blocking(self.carla.transport_play, timeout=3.0,
                               description="transport play")

        return {
            "status": "complete",
            "plugin_id": pid, "plugin_name": pname,
            "file_verified": res["file_verified"], "file_path": file_path,
            "output_ports": out_ports, "speaker_connections": connections,
            "loop": loop, "autoplay": autoplay,
            "note": f"Call earshot_stop_source(plugin_id={pid}) to stop + remove.",
        }

    async def earshot_stop_source(self, plugin_id: Optional[int] = None, **_kw) -> dict:
        """Pause transport and remove the audiofile source plugin."""
        from utils.async_helpers import run_blocking
        if self.carla is None:
            return {"status": "error", "error": "carla controller not wired"}
        await run_blocking(self.carla.transport_pause, timeout=3.0,
                           description="transport pause")
        removed = False
        if plugin_id is not None:
            removed = await run_blocking(
                self.carla.remove_plugin, int(plugin_id),
                timeout=5.0, description="remove source plugin")
        return {"status": "complete", "transport_paused": True, "source_removed": removed}

    async def earshot_play(
        self,
        track_id: str,
        file_path: Optional[str] = None,
        mode: str = "live",
        profile_name: str = "experimental",
        oeuvre_hint: Optional[str] = None,
        plugin_ids: Optional[list] = None,
        semantics: Optional[dict] = None,
        delay_seconds: float = 5.0,
        alias: Optional[str] = None,
        **_kw,
    ) -> dict:
        """Phase N — one-call co-listening: load the analyzer chain, load the
        track's audio into Carla's audiofile player, wire source -> chain (agent
        reads) + delay -> speakers (user hears delayed), roll transport from 0,
        and start the earshot session anchored to the EXACT transport start
        (deterministic — no non-silence watcher).

        The audio file: `file_path` if given, else `tracks/{track_id}/audio.*`.
        The session's baseline comes from `track_id` (must be Phase-2 analyzed).
        Plays the track ONCE (loop disabled) to match the single-pass baseline.
        """
        from utils.async_helpers import run_blocking
        from earshot.ambient_stream import now_ms
        from earshot.session_registry import registry

        if self.carla is None:
            return {"status": "error", "error": "carla controller not wired"}

        # Resolve the audio file.
        if file_path is None:
            cand = list((dsp.TRACKS_DIR / track_id).glob("audio.*"))
            if not cand:
                return {"status": "error",
                        "error": (f"no audio for track {track_id!r}; pass file_path= or "
                                  "persist the Phase 2 source to tracks/{id}/audio.*"),
                        "track_id": track_id}
            file_path = str(cand[0])
        if not os.path.exists(file_path):
            return {"status": "error", "error": f"audio file not found: {file_path!r}"}

        # 1. Ensure the analyzer chain (+ baked delay) is loaded.
        chain = await self.earshot_load_analyzer_chain()
        if chain.get("status") != "complete":
            return {"status": "error", "error": "analyzer chain load failed",
                    "chain_result": chain}
        # Build the comparator semantics map. earshot_play ALWAYS spawns the
        # streaming companion below, so its music dims (tempo/key/harmonic/onset)
        # are always merged in — otherwise the companion would emit those types
        # to the ambient stream with no comparator mapped to them. An explicit
        # `semantics` arg overrides the LV2-chain dynamics/spectrum dims, but the
        # companion dims are still added (the caller can't sensibly opt out of
        # the music dims while the companion is running).
        chain_semantics = {
            **(semantics or chain.get("semantics") or {}),
            **self._COMPANION_SEMANTICS,
        }
        # Poll the analyzer plugins but NOT the delay tower (plugin 4, 737 params
        # — polling it would bloat the ambient stream). Matches the proven
        # [0,1,2,3] config from earlier live sessions.
        chain_plugin_ids = plugin_ids or [
            int(k) for k in (chain.get("plugins") or {}).keys() if int(k) != 4
        ]

        # 2. Load the audiofile source (loop off, host-sync on, ports polled).
        src = await self._load_audiofile_source(file_path, loop=False, host_sync=True)
        if "error" in src:
            return {"status": "error", "error": src["error"], "file_path": file_path}
        src_pid, src_ports = src["plugin_id"], src["output_ports"]
        if len(src_ports) < 2:
            # Roll back the loaded source — same reason as the wiring-failure
            # path below: a leftover audiofile would be polled as a fake
            # analyzer plugin on the next earshot_play. (Transport not rolling.)
            await run_blocking(self.carla.remove_plugin, src_pid, timeout=5.0,
                               description="rollback remove source (<2 outputs)")
            return {"status": "error", "plugin_id": src_pid,
                    "error": f"source exposed <2 audio outputs: {src_ports}"}

        # 3. Wire source -> chain entry + delay -> speakers (reuse the tested
        #    delay-tower routing with the audiofile outputs as the source).
        wire = await self.earshot_wire_delay_tower(
            source_ports=src_ports[:2], disconnect_source_from_sink=False)
        if wire.get("status") not in ("complete", "partial"):
            # Remove the loaded source so it doesn't orphan a Carla slot — a
            # leftover audiofile at the chain's tail would be polled as a fake
            # analyzer plugin on the next earshot_play. (Transport not yet
            # rolling, so no pause needed.)
            await run_blocking(self.carla.remove_plugin, src_pid, timeout=5.0,
                               description="rollback remove source (wiring failed)")
            return {"status": "error", "error": "delay-tower wiring failed",
                    "wire_result": wire, "source_plugin_id": src_pid}

        # 4. Roll transport from 0; capture the exact start as the session anchor.
        # The anchor is taken just before transport_play, so the first ~15ms of
        # the track (one JACK buffer + scheduling) carry a slightly-negative
        # track_time — the comparators already skip negative track_time, and
        # capturing here is as accurate as possible given JACK schedules audio
        # asynchronously anyway.
        await run_blocking(self.carla.transport_relocate, 0, timeout=3.0,
                           description="transport relocate 0")
        playback_start_ms = now_ms()
        await run_blocking(self.carla.transport_play, timeout=3.0,
                           description="transport play")

        # 5. Start the session anchored to the exact transport start — no
        #    non-silence watcher needed (we KNOW t=0).
        session = await self.earshot_start_session(
            track_id=track_id, mode=mode, profile_name=profile_name,
            oeuvre_hint=oeuvre_hint, plugin_ids=chain_plugin_ids,
            semantics=chain_semantics, delay_seconds=delay_seconds, alias=alias,
            sync_to_audio=False, playback_start_ms=playback_start_ms,
            # Spawn the streaming companion on the same file so tempo/key/
            # harmonic/onset land in the ambient stream and feed the music-domain
            # comparators (their semantics were merged in above).
            companion_audio_file=file_path,
        )
        if session.get("status") != "complete":
            # roll back the audio so we don't leave it playing into a dead session
            await run_blocking(self.carla.transport_pause, timeout=3.0, description="rollback pause")
            await run_blocking(self.carla.remove_plugin, src_pid, timeout=5.0, description="rollback remove source")
            return {"status": "error", "error": "session start failed",
                    "session_result": session, "source_plugin_id": src_pid}

        # Record the source on the session so end_session tears it down.
        state = registry.get(session["session_id"])
        if state is not None:
            state.source_plugin_id = src_pid

        return {
            "status": "complete",
            "session_id": session["session_id"],
            "track_id": track_id,
            "file_path": file_path,
            "file_verified": src["file_verified"],
            "source_plugin_id": src_pid,
            "source_ports": src_ports,
            "playback_start_ms": playback_start_ms,
            "mode": mode,
            "profile": session.get("profile"),
            "baseline_summary": session.get("baseline_summary"),
            "components_started": session.get("components_started"),
            "note": ("Playing once through the chain + delay; agent reads raw, you "
                     "hear it delayed by delay_seconds. Call earshot_stop(session_id) "
                     "to stop playback + tear down."),
        }

    async def earshot_stop(self, session_id: str, **_kw) -> dict:
        """Stop a co-listening session started by earshot_play: end the session
        (which also pauses transport + removes the audiofile source) and return
        the session summary."""
        return await self.earshot_end_session(session_id=session_id)

    # ------------------------------------------------------------------
    # Phase L — session reflection
    # ------------------------------------------------------------------
    async def earshot_reflect_session(
        self,
        session_id: str,
        **_kw,
    ) -> dict:
        """Phase L — read a session's artifacts and write reflection_data.json
        + reflection.md to its directory.

        Works whether the session is active or already torn down — reads
        from disk, not the in-memory registry. The .md is a quick-stats
        skeleton with a marker indicating where the orchestrator should
        append a prose synthesis (no LLM call happens here; postmortem
        rule #7 keeps inference out of in-tree code).
        """
        from earshot.reflection import write_reflection_artifacts
        from utils.async_helpers import run_blocking

        if not session_id:
            return {"status": "error", "error": "session_id is required"}

        # write_reflection_artifacts is synchronous + reads JSONL files;
        # offload to a thread so a multi-megabyte ambient stream doesn't
        # stall the event loop.
        try:
            result = await run_blocking(
                write_reflection_artifacts,
                session_id,
                timeout=60.0,
                description=f"earshot.reflection {session_id}",
            )
        except Exception as e:
            return {"status": "error", "error": f"reflection failed: {e}"}

        data = result["data"]
        return {
            "status": "complete",
            "session_id": session_id,
            "reflection_data_path": result["reflection_data_path"],
            "reflection_md_path": result["reflection_md_path"],
            "summary": {
                "duration_ms": data["duration_ms"],
                "ambient_entries_total": data["ambient_entries_total"],
                "commentary_total": data["commentary_total"],
                "emissions_total": data["emissions_total"],
                "prose_requests_total": data["prose_requests_total"],
                "user_interjections": len(data["user_interjections"]),
                "ambient_by_source": data["ambient_by_source"],
                "emissions_by_level": data["emissions_by_level"],
                "emissions_by_dimension": data["emissions_by_dimension"],
            },
            "next_call_hint": (
                "Read reflection_data.json + the .md skeleton. Compose prose "
                "synthesis grounded in the data dict; append below the marker "
                "comment in the .md and flip synthesis_status frontmatter to "
                "'complete'."
            ),
        }

    # ------------------------------------------------------------------
    # Phase J — session lifecycle (start / interject / correct_profile / end)
    # ------------------------------------------------------------------
    @staticmethod
    def _artist_context_digest(artist_id: str) -> Optional[str]:
        """Compact artist-background digest from the oeuvre deep-research report,
        attached to prose requests so commentary can reference who made the
        track. Prefers the orchestrator-written prose synthesis (.md body) when
        present; otherwise builds a digest from the structured .json scrape.
        Returns None when no oeuvre data exists for the artist."""
        if not artist_id:
            return None
        # Artist context is optional grounding — never let a read/parse failure
        # escape and crash session construction. Worst case: return None.
        try:
            # Prefer a real prose synthesis if the orchestrator has written one
            # (the stub .md only carries frontmatter + a placeholder comment).
            md_path = dsp.oeuvre_path_for(artist_id)
            if md_path.exists():
                body = md_path.read_text(encoding="utf-8")
                after_fm = body.split("---", 2)[-1].strip()
                if after_fm and "Prose synthesis lives here" not in after_fm:
                    return after_fm[:1500]
            # Fall back to a digest of the structured scrape.
            json_path = dsp.OEUVRE_DIR / f"{artist_id}.json"
            if not json_path.exists():
                return None
            d = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("_artist_context_digest(%r) failed: %s", artist_id, e)
            return None
        parts = [f"Artist: {d.get('page_title') or artist_id}"]
        desc = (d.get("profile_description") or "").strip()
        if desc:
            parts.append(desc[:500])
        tags = d.get("tag_freq") or {}
        if isinstance(tags, dict) and tags:
            top = sorted(tags.items(), key=lambda kv: kv[1], reverse=True)[:6]
            parts.append("Tags: " + ", ".join(t for t, _ in top))
        rc, tc = d.get("release_count"), d.get("track_count")
        if rc or tc:
            parts.append(f"Catalog: {rc or '?'} releases, {tc or '?'} tracks")
        digest = " | ".join(parts).strip()
        return digest or None

    async def earshot_start_session(
        self,
        track_id: str,
        mode: str = "preview",
        delay_seconds: float = 5.0,
        voice_enabled: bool = False,
        profile_name: str = "experimental",
        oeuvre_hint: Optional[str] = None,
        plugin_ids: Optional[list] = None,
        semantics: Optional[dict] = None,
        alias: Optional[str] = None,
        boundary_lookahead_seconds: float = 5.0,
        poll_interval_ms: int = 250,
        companion_audio_file: Optional[str] = None,
        companion_chunk_seconds: float = 2.0,
        companion_lookahead_seconds: float = 8.0,
        sync_to_audio: bool = True,
        sync_monitor_type: Optional[str] = None,
        sync_silence_threshold_db: float = -65.0,
        sync_timeout_s: float = 300.0,
        playback_start_ms: Optional[int] = None,
        **_kw,
    ) -> dict:
        """Bring up a Phase 3 co-listening session.

        `playback_start_ms`: when the caller controls playback (earshot_play
        rolls Carla transport), it passes the exact start wall-clock so
        track_time is anchored deterministically — no non-silence watcher.
        When None (default), it's set to now_ms() and the sync watcher runs.

        Component graph (started in dependency order; consumers first so
        producers never fire into an unprepared queue):
            tracker → comparators → boundary_detector → scheduler → lv2_poller

        Args:
            track_id:                Phase 2 baseline must exist for this id.
            mode:                    `preview` | `live` — diagnostic label.
            delay_seconds:           delay-tower buffer; commentary lands at
                                     `event.ts_ms + delay_seconds*1000` on the
                                     user's clock (anti-spoiler discipline).
            voice_enabled:           TTS rendering preference for the orchestrator.
            profile_name:            profile YAML stem under earshot/profiles/.
            plugin_ids:              when provided + analysis_tools is wired,
                                     spawns an LV2Poller polling those plugins
                                     at `poll_interval_ms`. Without this the
                                     ambient stream still works (only the
                                     companion / external producers write).
            semantics:               map of ambient `type` → Dimension.value
                                     (e.g. `{"plugin_2.param_0": "dynamic_envelope"}`).
                                     Required for comparators to fire — when
                                     absent, drift+prediction tracking is skipped
                                     but the boundary detector + scheduler still
                                     run (boundary events still reach the
                                     orchestrator for prediction refreshes).
            alias:                   explicit session_id; otherwise generated.
            boundary_lookahead_seconds: how far ahead of a section boundary the
                                       detector fires (orchestrator LLM budget).
            poll_interval_ms:        LV2 polling cadence; default 250 ms (~4 Hz).

        Returns the registered session_id + a manifest of which components
        actually started (postmortem rule #19: don't claim everything is wired
        when it isn't).
        """
        # Lazy imports — keep import cost out of the top of the module and
        # avoid the comparators package being eagerly loaded for Phase 1/2
        # use cases.
        from earshot.ambient_stream import (
            AmbientStreamReader,
            AmbientStreamWriter,
            now_ms,
        )
        from earshot.boundary_detector import BoundaryDetector
        from earshot.comparators.drift import DriftComparator
        from earshot.comparators.events import Dimension, EventQueue
        from earshot.comparators.prediction import PredictionComparator
        from earshot.expectation_tracker import ExpectationTracker
        from earshot.lv2_poller import LV2Poller
        from earshot.phase2_baseline import Phase2BaselineLoader
        from earshot.profiles import list_profiles, load_profile
        from earshot.scheduler.commentary import CommentaryLogger, CommentaryQueue
        from earshot.scheduler.core import Scheduler
        from earshot.session_registry import SessionState, registry

        # 1. Input validation
        if not track_id:
            return {"status": "error", "error": "track_id is required"}
        if delay_seconds < 0:
            return {"status": "error", "error": "delay_seconds must be >= 0"}
        if mode not in ("preview", "live"):
            return {"status": "error",
                    "error": f"invalid mode {mode!r}; must be 'preview' or 'live'"}

        # 2. Baseline (errors propagate; postmortem rule #19)
        if not Phase2BaselineLoader.exists(track_id):
            return {
                "status": "error",
                "error": f"Phase 2 baseline missing for {track_id!r}",
                "remediation": "Run earshot_analyze_track first.",
            }
        try:
            baseline = Phase2BaselineLoader.load(track_id)
        except (FileNotFoundError, ValueError) as e:
            return {"status": "error", "error": str(e)}

        # 3. Profile — `profile_name="auto"` runs the Phase H selector against
        # the baseline + optional oeuvre hint; any other value is loaded literally.
        if profile_name == "auto":
            from earshot.profiles.selector import select_profile
            profile_name = select_profile(baseline=baseline, oeuvre_hint=oeuvre_hint)
            logger.info("earshot_start_session: auto-selected profile=%s "
                        "(oeuvre_hint=%r)", profile_name, oeuvre_hint)
        try:
            profile = load_profile(profile_name)
        except FileNotFoundError as e:
            return {
                "status": "error",
                "error": str(e),
                "available_profiles": list_profiles(),
            }

        # 4. session_id — generated unless alias supplied; refuse to overwrite.
        session_id = alias or f"earshot_{track_id}_{uuid.uuid4().hex[:8]}"
        if registry.get(session_id) is not None:
            return {
                "status": "error",
                "error": (f"session {session_id!r} already exists; "
                          "call earshot_end_session first"),
            }

        # 5. Component graph construction (everything except .start() calls)
        # Use the caller-supplied anchor (earshot_play, deterministic) when
        # given; otherwise anchor to now and let the sync watcher align.
        if playback_start_ms is None:
            playback_start_ms = now_ms()
        else:
            playback_start_ms = int(playback_start_ms)
            sync_to_audio = False  # explicit anchor overrides the watcher
        delay_buffer_ms = int(delay_seconds * 1000)

        writer = AmbientStreamWriter(session_id)
        reader = AmbientStreamReader(session_id)
        event_queue = EventQueue()
        # Phase L — open the commentary log alongside the ambient stream
        # so every push() to the queue gets persisted for reflection. The
        # writer's session directory already exists by this point.
        commentary_log_path = writer.path.parent / "commentary.jsonl"
        commentary_logger = CommentaryLogger(commentary_log_path)
        commentary_queue = CommentaryQueue(logger=commentary_logger)
        tracker = ExpectationTracker(baseline)

        # Comparators only when semantics provided — without a type→Dimension
        # map their constructors refuse (correctly: would emit zero events).
        drift_comp = None
        pred_comp = None
        if semantics:
            try:
                semantics_typed = {k: Dimension(v) for k, v in semantics.items()}
            except ValueError as e:
                writer.close()
                return {
                    "status": "error",
                    "error": f"invalid semantics dimension value: {e}",
                    "valid_dimensions": [d.value for d in Dimension],
                }
            drift_comp = DriftComparator(
                baseline, reader, event_queue,
                playback_start_ms=playback_start_ms,
                semantics=semantics_typed,
            )
            pred_comp = PredictionComparator(
                baseline, reader, event_queue,
                playback_start_ms=playback_start_ms,
                semantics=semantics_typed,
                expectations=tracker,
            )

        boundary_det = BoundaryDetector(
            baseline, event_queue,
            playback_start_ms=playback_start_ms,
            lookahead_seconds=boundary_lookahead_seconds,
        )
        scheduler = Scheduler(
            event_queue=event_queue,
            commentary_queue=commentary_queue,
            profile=profile,
            baseline=baseline,
            playback_start_ms=playback_start_ms,
            delay_buffer_ms=delay_buffer_ms,
            artist_context=self._artist_context_digest(baseline.artist_id),
        )

        # LV2 poller only when explicitly requested AND analysis_tools wired.
        lv2_poller = None
        if plugin_ids:
            if self.analysis_tools is None:
                writer.close()
                return {
                    "status": "error",
                    "error": ("plugin_ids supplied but analysis_tools not wired; "
                              "server must construct EarshotTools(analysis_tools=...)"),
                }
            lv2_poller = LV2Poller(
                self.analysis_tools, plugin_ids, writer,
                interval_ms=poll_interval_ms,
            )

        # Phase C — streaming librosa companion (file-driven). Optional;
        # spawns only when a file path is supplied. The companion writes
        # to the same ambient JSONL as the LV2 poller; multi-writer
        # atomicity is guaranteed below PIPE_BUF (AmbientStreamWriter
        # docstring) and the companion's emit() helper enforces the same
        # 4KB line ceiling.
        companion_process = None
        if companion_audio_file:
            companion_python = Path.home() / ".carla-mcp/earshot/companion/.venv/bin/python"
            companion_script = Path(__file__).resolve().parent / "companion" / "streaming.py"
            if not companion_python.exists():
                writer.close()
                return {
                    "status": "error",
                    "error": (f"companion python missing at {companion_python}; "
                              "set up the companion venv before passing companion_audio_file"),
                }
            if not companion_script.exists():
                writer.close()
                return {"status": "error",
                        "error": f"streaming companion script missing at {companion_script}"}
            if not os.path.exists(companion_audio_file):
                writer.close()
                return {"status": "error",
                        "error": f"companion_audio_file not found: {companion_audio_file}"}
            companion_env = {
                **os.environ,
                "SSL_CERT_FILE": os.environ.get(
                    "SSL_CERT_FILE", "/etc/ssl/certs/ca-bundle.crt"
                ),
                "TF_CPP_MIN_LOG_LEVEL": "3",
            }
            companion_cmd = [
                str(companion_python),
                str(companion_script),
                "--audio-file", companion_audio_file,
                "--session-id", session_id,
                "--playback-start-ms", str(playback_start_ms),
                "--chunk-seconds", str(companion_chunk_seconds),
                "--lookahead-seconds", str(companion_lookahead_seconds),
            ]
            try:
                # CRITICAL: capture stdout/stderr. The main server runs MCP
                # over stdio; if the subprocess inherits stdout it would
                # corrupt the JSON-RPC stream. Pipes are NOT drained while
                # the session runs — librosa's per-chunk output is small
                # (one JSON line at exit) and pyloudnorm/numpy warnings
                # land on stderr's pipe buffer (64 KiB default on Linux,
                # plenty for a session of typical duration).
                companion_process = subprocess.Popen(
                    companion_cmd,
                    env=companion_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception as e:
                writer.close()
                return {"status": "error",
                        "error": f"companion subprocess spawn failed: {e}"}

        # 6. Register + start. Register first so cleanup paths can find the
        # state via the registry even if a later start() raises.
        state = SessionState(
            session_id=session_id,
            baseline=baseline,
            tracker=tracker,
            queue=event_queue,
            playback_start_ms=playback_start_ms,
            profile=profile,
            scheduler=scheduler,
            commentary_queue=commentary_queue,
            writer=writer,
            reader=reader,
            lv2_poller=lv2_poller,
            drift_comparator=drift_comp,
            prediction_comparator=pred_comp,
            boundary_detector=boundary_det,
            companion_process=companion_process,
            commentary_logger=commentary_logger,
        )
        registry.register(state)

        # 7. Start tasks in dependency order. If any fail, stop everything
        # we did start, close the writer, unregister.
        #
        # Sync gating: the comparators + boundary_detector all compute
        # `track_time_s = (ts_ms - playback_start_ms) / 1000`. If we anchor
        # playback_start_ms to wall-clock-at-session-start, but the user
        # presses play 30 seconds later, every event's track_time is off by
        # 30 seconds and the baseline lookup compares wrong slices. To
        # avoid that, when `sync_to_audio=True` AND we have a stream the
        # watcher can observe (LV2 poller writing something semantically
        # mapped), we gate those three components until the first
        # non-silent sample arrives — that sample's ts_ms becomes the new
        # playback_start_ms.
        sync_effective = bool(
            sync_to_audio and lv2_poller is not None and semantics
        )
        gated_components: list = []
        started_components: list = []
        sync_task = None
        try:
            # Consumers first so producers never push into a stopped consumer.
            if sync_effective:
                # Hold drift/pred/boundary for the watcher; start everything else.
                if drift_comp is not None:
                    gated_components.append(drift_comp)
                if pred_comp is not None:
                    gated_components.append(pred_comp)
                gated_components.append(boundary_det)
            else:
                if drift_comp is not None:
                    await drift_comp.start()
                    started_components.append(drift_comp)
                if pred_comp is not None:
                    await pred_comp.start()
                    started_components.append(pred_comp)
            await scheduler.start()
            started_components.append(scheduler)
            if not sync_effective:
                # Producers last.
                await boundary_det.start()
                started_components.append(boundary_det)
            if lv2_poller is not None:
                await lv2_poller.start()
                started_components.append(lv2_poller)
            # Spawn the sync watcher LAST, after the poller is producing.
            if sync_effective:
                monitor_type = sync_monitor_type or next(iter(semantics))
                sync_task = asyncio.create_task(
                    _await_non_silence_then_start(
                        session_id=session_id,
                        reader=reader,
                        monitor_type=monitor_type,
                        silence_threshold_db=sync_silence_threshold_db,
                        timeout_s=sync_timeout_s,
                        gated=gated_components,
                    ),
                    name=f"playback_sync_{session_id}",
                )
                state.playback_sync_task = sync_task
        except Exception as e:
            logger.exception("earshot_start_session: failed mid-startup; rolling back")
            if sync_task is not None and not sync_task.done():
                sync_task.cancel()
                try:
                    await sync_task
                except (asyncio.CancelledError, Exception):
                    pass
            for comp in reversed(started_components):
                try:
                    await comp.stop()
                except Exception as rb_err:
                    logger.warning("rollback stop failed for %s: %s",
                                   type(comp).__name__, rb_err)
            if companion_process is not None:
                try:
                    companion_process.terminate()
                    companion_process.wait(timeout=2.0)
                except Exception as rb_err:
                    logger.warning("rollback companion terminate failed: %s", rb_err)
            try:
                commentary_logger.close()
            except Exception as rb_err:
                logger.warning("rollback commentary_logger close failed: %s", rb_err)
            writer.close()
            registry.unregister(session_id)
            return {
                "status": "error",
                "error": f"component startup failed: {e}",
            }

        logger.info("earshot_start_session: %s up (mode=%s, profile=%s)",
                    session_id, mode, profile.name)
        return {
            "status": "complete",
            "session_id": session_id,
            "track_id": track_id,
            "mode": mode,
            "profile": profile.name,
            "delay_buffer_ms": delay_buffer_ms,
            "voice_enabled": voice_enabled,
            "playback_start_ms": playback_start_ms,
            "baseline_loaded": True,
            "baseline_summary": {
                "duration_s": baseline.duration_s,
                "tempo_bpm": baseline.tempo_bpm,
                "key": f"{baseline.key.tonic} {baseline.key.mode}",
                "section_count": len(baseline.section_map),
                "lufs_integrated": baseline.loudness.integrated_lufs,
            },
            "components_started": {
                "writer": True,
                "scheduler": True,
                "boundary_detector": not sync_effective,
                "expectation_tracker": True,
                "drift_comparator": drift_comp is not None and not sync_effective,
                "prediction_comparator": pred_comp is not None and not sync_effective,
                "lv2_poller": lv2_poller is not None,
                "streaming_companion": companion_process is not None,
            },
            "sync_to_audio": sync_effective,
            "sync_monitor_type": (
                (sync_monitor_type or (next(iter(semantics)) if semantics else None))
                if sync_effective else None
            ),
            "ambient_stream_path": str(writer.path),
            "companion_pid": (companion_process.pid
                              if companion_process is not None else None),
        }

    async def earshot_user_interject(
        self,
        session_id: str,
        text: str,
        kind: str = "comment",
        **_kw,
    ) -> dict:
        """Record a user interjection during a live session.

        The user types or speaks something mid-listen; we log it to the
        session's ambient stream with `source="user"`. Comparators don't
        currently act on user entries (no semantics mapping for user text),
        but the entries are durable for Phase L reflection and any future
        scheduler integration that wants to react to user input.

        `kind` is a free-form label (typical values: "comment", "correction",
        "question") that orchestrator-side handlers can branch on.
        """
        from earshot.ambient_stream import now_ms
        from earshot.session_registry import registry

        if not session_id:
            return {"status": "error", "error": "session_id is required"}
        if not text or not isinstance(text, str):
            return {"status": "error", "error": "text must be a non-empty string"}
        state = registry.get(session_id)
        if state is None:
            return {"status": "error", "error": f"unknown session_id {session_id!r}"}
        if state.writer is None:
            return {"status": "error",
                    "error": f"session {session_id!r} has no ambient stream writer"}

        ts_ms = now_ms()
        track_time_s = (ts_ms - state.playback_start_ms) / 1000.0
        try:
            state.writer.append(
                ts_ms=ts_ms,
                source="user",
                metric_type="interjection",
                value={"text": text, "kind": kind, "track_time_s": round(track_time_s, 3)},
            )
        except (ValueError, RuntimeError) as e:
            return {"status": "error", "error": f"failed to log interjection: {e}"}
        return {
            "status": "complete",
            "session_id": session_id,
            "ts_ms": ts_ms,
            "track_time_s": round(track_time_s, 3),
            "kind": kind,
        }

    async def earshot_correct_profile(
        self,
        session_id: str,
        profile_name: str,
        **_kw,
    ) -> dict:
        """Swap the session's active profile mid-listen.

        Used when the auto-selected profile doesn't match the music — the
        user (or the orchestrator on the user's behalf) can switch to a
        better one without restarting the session. Affects all future
        scheduler decisions (thresholds, density, silence zones, catalog)
        from the next event onward. Already-queued commentary is not revoked.
        """
        from earshot.profiles import list_profiles, load_profile
        from earshot.session_registry import registry

        if not session_id:
            return {"status": "error", "error": "session_id is required"}
        if not profile_name:
            return {"status": "error", "error": "profile_name is required"}
        state = registry.get(session_id)
        if state is None:
            return {"status": "error", "error": f"unknown session_id {session_id!r}"}
        if state.scheduler is None:
            return {"status": "error",
                    "error": f"session {session_id!r} has no scheduler"}

        try:
            new_profile = load_profile(profile_name)
        except FileNotFoundError as e:
            return {
                "status": "error",
                "error": str(e),
                "available_profiles": list_profiles(),
            }

        old_name = state.profile.name if state.profile else "<none>"
        state.profile = new_profile
        state.scheduler.profile = new_profile
        logger.info("earshot_correct_profile: %s %s -> %s",
                    session_id, old_name, new_profile.name)
        return {
            "status": "complete",
            "session_id": session_id,
            "previous_profile": old_name,
            "active_profile": new_profile.name,
        }

    async def earshot_end_session(
        self,
        session_id: str,
        **_kw,
    ) -> dict:
        """Shut down a session and return a summary.

        Stop order is reverse of start: producers first (lv2_poller,
        boundary_detector) so no new events land in the queues, then the
        scheduler (drains in-flight events on cancel), then the comparators,
        then close the writer. The ambient stream JSONL stays on disk for
        Phase L's reflection writer to read.
        """
        from earshot.session_registry import registry

        if not session_id:
            return {"status": "error", "error": "session_id is required"}
        state = registry.get(session_id)
        if state is None:
            return {"status": "error", "error": f"unknown session_id {session_id!r}"}

        # Capture stats BEFORE stopping (stop() logs final stats but doesn't
        # return them; we surface them via the envelope so the orchestrator
        # has a session manifest without polling each component).
        summary = {
            "session_id": session_id,
            "profile": state.profile.name if state.profile else None,
            "playback_start_ms": state.playback_start_ms,
            "tracker": state.tracker.stats(),
            "event_queue": state.queue.stats(),
        }
        if state.commentary_queue is not None:
            summary["commentary_queue"] = state.commentary_queue.stats()
        if state.scheduler is not None:
            summary["scheduler"] = state.scheduler.stats()
        if state.drift_comparator is not None:
            summary["drift_comparator"] = state.drift_comparator.stats()
        if state.prediction_comparator is not None:
            summary["prediction_comparator"] = state.prediction_comparator.stats()
        if state.boundary_detector is not None:
            summary["boundary_detector"] = state.boundary_detector.stats()
        if state.lv2_poller is not None:
            summary["lv2_poller"] = state.lv2_poller.stats()
        if state.companion_process is not None:
            summary["companion_pid"] = state.companion_process.pid
            summary["companion_returncode_at_stop"] = state.companion_process.poll()
        if state.commentary_logger is not None:
            summary["commentary_logger"] = state.commentary_logger.stats()

        # Stop in reverse dependency order. Each stop() is idempotent.
        stop_errors: list[str] = []

        async def _safe_stop(comp, label: str):
            if comp is None:
                return
            try:
                await comp.stop()
            except Exception as e:
                logger.warning("earshot_end_session: %s.stop raised: %s", label, e)
                stop_errors.append(f"{label}: {e}")

        # Producers first so no new ambient entries arrive after consumers stop.
        # Companion is a producer too (writes the same JSONL); terminate it
        # with SIGTERM (its handler flips a flag the main loop polls).
        if state.companion_process is not None:
            try:
                if state.companion_process.poll() is None:
                    state.companion_process.terminate()
                    try:
                        state.companion_process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        # Refused SIGTERM; SIGKILL to guarantee cleanup.
                        state.companion_process.kill()
                        state.companion_process.wait(timeout=2.0)
            except Exception as e:
                logger.warning("earshot_end_session: companion terminate raised: %s", e)
                stop_errors.append(f"companion_process: {e}")
        # Cancel the sync watcher first (if it's still waiting for non-silence,
        # we don't want it to start the gated components mid-teardown).
        if state.playback_sync_task is not None and not state.playback_sync_task.done():
            state.playback_sync_task.cancel()
            try:
                await state.playback_sync_task
            except (asyncio.CancelledError, Exception) as e:
                logger.debug("earshot_end_session: sync task cancel: %s", e)
        await _safe_stop(state.lv2_poller, "lv2_poller")
        await _safe_stop(state.boundary_detector, "boundary_detector")
        await _safe_stop(state.scheduler, "scheduler")
        await _safe_stop(state.prediction_comparator, "prediction_comparator")
        await _safe_stop(state.drift_comparator, "drift_comparator")

        if state.writer is not None:
            try:
                state.writer.close()
            except Exception as e:
                logger.warning("earshot_end_session: writer.close raised: %s", e)
                stop_errors.append(f"writer: {e}")
        if state.commentary_logger is not None:
            try:
                state.commentary_logger.close()
            except Exception as e:
                logger.warning("earshot_end_session: commentary_logger.close raised: %s", e)
                stop_errors.append(f"commentary_logger: {e}")

        # Phase N — stop any audio this session was driving. ALWAYS pause the
        # transport (cheap, and a session wired by hand without a recorded
        # source at least goes quiet); then, if earshot_play recorded the
        # audiofile source, remove that plugin — dropping its JACK ports is what
        # actually cuts the speaker feed, independent of transport state.
        if self.carla is not None:
            from utils.async_helpers import run_blocking
            try:
                await run_blocking(self.carla.transport_pause, timeout=3.0,
                                   description="end_session transport pause")
            except Exception as e:
                logger.warning("earshot_end_session: transport pause raised: %s", e)
                stop_errors.append(f"transport_pause: {e}")
            if state.source_plugin_id is not None:
                try:
                    await run_blocking(self.carla.remove_plugin, int(state.source_plugin_id),
                                       timeout=5.0, description="end_session remove source")
                except Exception as e:
                    logger.warning("earshot_end_session: source remove raised: %s", e)
                    stop_errors.append(f"source_plugin: {e}")

        registry.unregister(session_id)
        logger.info("earshot_end_session: %s torn down (%d stop errors)",
                    session_id, len(stop_errors))

        envelope = {
            "status": "complete" if not stop_errors else "partial",
            "session_id": session_id,
            "summary": summary,
        }
        if stop_errors:
            envelope["stop_errors"] = stop_errors
        return envelope

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _run_with_supplied_plan(self, plan: dict, disp: dsp.Dispatch, artist_id: str) -> dict:
        # Sanity: host in plan must match the URL's host.
        if plan.get("host") != disp.host:
            return {
                "status": "error",
                "host": disp.host,
                "source_url": disp.url_normalized,
                "artist_id": artist_id,
                "error": f"plan.host={plan.get('host')!r} does not match url host {disp.host!r}",
            }
        # Validate
        validation_errs = executor.validate_plan(plan)
        if validation_errs:
            return {
                "status": "error",
                "host": disp.host,
                "source_url": disp.url_normalized,
                "artist_id": artist_id,
                "error": "plan validation failed",
                "validation_errors": validation_errs,
            }
        # Self-check: index strategy must yield at least one release URL.
        self_check = await run_blocking(
            executor.execute,
            plan,
            disp.url_normalized,
            artist_id,
            self_check_only=True,
            timeout=60.0,
            description=f"earshot.executor self-check {disp.host}",
        )
        if self_check.get("status") != "complete":
            return {
                "status": "error",
                "host": disp.host,
                "source_url": disp.url_normalized,
                "artist_id": artist_id,
                "error": "plan self-check failed; not caching",
                "self_check": self_check,
            }
        # Cache the plan (idempotent: overwrite existing file).
        plan_with_meta = dict(plan)
        plan_with_meta["discovered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        dsp.plan_path_for(disp.host).write_text(yaml.safe_dump(plan_with_meta, sort_keys=False), encoding="utf-8")
        # Run the full ingest.
        result = await run_blocking(
            executor.execute,
            plan,
            disp.url_normalized,
            artist_id,
            timeout=INGEST_TIMEOUT_SECONDS,
            description=f"earshot.executor.execute (fresh plan {disp.host})",
        )
        result["plan_source"] = "freshly_discovered"
        result["plan_path"] = str(dsp.plan_path_for(disp.host))
        self._write_structured_artifact(artist_id, result)
        result["oeuvre_report_path"] = str(dsp.oeuvre_path_for(artist_id))
        return result

    @staticmethod
    def _write_structured_artifact(artist_id: str, result: dict) -> None:
        """Persist the structured ingestion result alongside the (orchestrator-
        written) prose oeuvre report. The orchestrator overwrites the .md
        body when it generates the synthesis; until then the .json is the
        authoritative record of what was scraped."""
        json_path = dsp.OEUVRE_DIR / f"{artist_id}.json"
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        # Stub markdown if none exists so the path is real (postmortem rule #2:
        # `oeuvre_report_path` returned to the caller must point at a real file).
        # Only stub for a COMPLETE ingest — a partial/error result must not leave
        # the marker that the idempotency gate (earshot_ingest_artist) reads as
        # "already done", or a failed scrape would never be retried.
        md_path = dsp.oeuvre_path_for(artist_id)
        if result.get("status") == "complete" and not md_path.exists():
            frontmatter = [
                "---",
                f"artist_id: {artist_id}",
                f"ingested_at: {result.get('ingested_at')}",
                f"source_url: {result.get('source_url')}",
                f"parser: {result.get('parser')}",
                f"release_count: {result.get('release_count')}",
                "synthesis_status: pending_orchestrator",
                "---",
                "",
                "<!-- Prose synthesis lives here. The orchestrator (Claude Code) is",
                "     responsible for reading the .json sibling file and writing this",
                "     body. Until then this file exists as a marker for idempotency. -->",
                "",
            ]
            md_path.write_text("\n".join(frontmatter), encoding="utf-8")
