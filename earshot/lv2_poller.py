"""LV2 parameter poller — fills the ambient stream from the analyzer chain.

Phase 3's measurement substrate has two writer paths:

  1. **This module** — polls the loaded LV2 plugins via the existing
     `capture_plugin_parameters` MCP tool and emits per-parameter entries
     into the ambient stream at ~4 Hz.
  2. **The streaming-librosa companion** (Phase C, separate process) — runs
     online tempo/key/LUFS analysis on a JACK port and writes to the same
     ambient stream.

Both writers append to the same JSONL file with wall-clock timestamps so
consumers (drift + prediction comparators, output scheduler) can align
measurements across sources without their own coordinator.

Postmortem discipline anchors:
  - Rule #3 (orchestrate, don't reimplement): we call `capture_plugin_parameters`
    rather than building a parallel parameter reader.
  - Rule #21 (reuse batch tools in loops over rebuilding their primitives):
    each loop iteration is one capture call; we don't reimplement param polling.
  - Rule #32 (idempotency): start() is a no-op if already running; stop() is
    safe to call before start.

Wall-clock alignment:
  `capture_plugin_parameters` returns `time_ms` per sample relative to the
  *start of that capture*. For cross-source ambient-stream alignment we need
  wall-clock per sample. The poller records wall-clock at capture-end and
  back-computes:  sample_ts_ms = capture_end_ms - capture_duration_ms + sample.time_ms.
  Accurate to scheduler/network jitter (~milliseconds).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from earshot.ambient_stream import AmbientStreamWriter, now_ms

logger = logging.getLogger(__name__)


class LV2Poller:
    """Background asyncio task that drives one ambient-stream writer.

    Lifecycle:
        poller = LV2Poller(analysis_tools, [0,1,2,3,4], writer, interval_ms=250)
        await poller.start()    # idempotent
        # ... session runs ...
        await poller.stop()     # idempotent
    """

    def __init__(
        self,
        analysis_tools,
        plugin_ids: list[int],
        writer: AmbientStreamWriter,
        *,
        interval_ms: int = 250,
    ):
        """
        Args:
            analysis_tools: The carla-mcp-server `AnalysisTools` instance —
                            held by reference so we can invoke
                            `analysis_tools.execute("capture_plugin_parameters", ...)`
                            inside the loop. Phase J's session_start tool passes
                            `server.analysis_tools` directly.
            plugin_ids:     The plugin IDs to poll. Mixed audio meters and
                            spectrum analyzers both work; non-numeric params
                            are filtered out at emit time.
            writer:         Pre-opened ambient-stream writer for this session.
                            The poller does NOT take ownership — caller closes
                            on session end.
            interval_ms:    Polling cadence. 250 ms = 4 Hz, the README's
                            initial target. Lower values increase data volume
                            and CPU; higher values miss faster transients.
        """
        if not plugin_ids:
            raise ValueError("plugin_ids must be non-empty")
        if interval_ms < 50:
            raise ValueError(f"interval_ms < 50 risks starving the event loop ({interval_ms})")
        self.analysis_tools = analysis_tools
        self.plugin_ids = list(plugin_ids)
        self.writer = writer
        self.interval_ms = int(interval_ms)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._iterations = 0
        self._entries_written = 0
        self._errors = 0

    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start the polling loop. Idempotent (no-op if already running)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"lv2_poller_{id(self):x}")
        logger.info("LV2Poller started: plugins=%s interval=%dms", self.plugin_ids, self.interval_ms)

    async def stop(self) -> None:
        """Stop the polling loop. Idempotent."""
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
        logger.info(
            "LV2Poller stopped: iterations=%d entries=%d errors=%d",
            self._iterations, self._entries_written, self._errors,
        )

    def stats(self) -> dict:
        """Lightweight diagnostics for the session machinery to surface."""
        return {
            "running": self._running,
            "iterations": self._iterations,
            "entries_written": self._entries_written,
            "errors": self._errors,
            "plugin_ids": self.plugin_ids,
            "interval_ms": self.interval_ms,
        }

    # ------------------------------------------------------------------
    async def _loop(self) -> None:
        while self._running:
            iteration_started_ms = now_ms()
            try:
                # NOTE: we pass capture_duration_ms hoping the underlying
                # implementation blocks for that long, but empirically (Phase M
                # first-run, 2026-05-25) the call returns in ~25 ms regardless.
                # We pace the loop explicitly below rather than trust the
                # dependency's blocking behavior.
                result = await self.analysis_tools.execute(
                    "capture_plugin_parameters",
                    {
                        "plugin_ids": self.plugin_ids,
                        "capture_duration_ms": self.interval_ms,
                        "sampling_interval_ms": self.interval_ms,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._errors += 1
                logger.warning("LV2Poller iteration failed: %s", e)
                await asyncio.sleep(self.interval_ms / 1000.0)
                continue

            try:
                self._emit(result, iteration_started_ms)
            except Exception as e:
                self._errors += 1
                logger.warning("LV2Poller emit failed: %s", e)

            self._iterations += 1
            # Explicit pacing: sleep whatever's left of the configured interval
            # after the capture+emit. If the iteration overran (slow analysis
            # call, big emit), skip the sleep entirely — better to fall behind
            # gracefully than introduce negative sleeps.
            elapsed_ms = now_ms() - iteration_started_ms
            sleep_ms = self.interval_ms - elapsed_ms
            if sleep_ms > 0:
                await asyncio.sleep(sleep_ms / 1000.0)

    def _emit(self, capture_result: dict, capture_started_ms: int) -> None:
        """Translate one capture result into ambient-stream entries.

        We emit one entry per numeric `param_N` per sample per plugin. The
        named-key duplicates (e.g. "dBTP - momentaty" which shadows the L
        channel when R also has the same name — bug we hit earlier in this
        codebase) are filtered out by skipping non-`param_` keys. Consumers
        resolve param_N → semantic meaning via the plugin's metadata
        (fetched once at session start via `get_plugin_info`).
        """
        plugins = capture_result.get("plugins") or {}
        for plugin_id, plugin_data in plugins.items():
            history = plugin_data.get("history") or []
            for sample in history:
                # `time_ms` in the sample is offset within the capture window;
                # convert to wall-clock using capture_started_ms as the base.
                offset_ms = sample.get("time_ms", 0)
                sample_ts_ms = capture_started_ms + int(offset_ms)
                values = sample.get("values") or {}
                for k, v in values.items():
                    if not k.startswith("param_"):
                        continue  # skip name-keyed duplicates
                    if not isinstance(v, (int, float)):
                        continue  # skip non-numeric (string enums etc.)
                    try:
                        self.writer.append(
                            ts_ms=sample_ts_ms,
                            source="lv2",
                            metric_type=f"plugin_{plugin_id}.{k}",
                            value=float(v),
                        )
                        self._entries_written += 1
                    except Exception as e:
                        self._errors += 1
                        logger.debug("LV2Poller skip entry %s.%s: %s", plugin_id, k, e)
