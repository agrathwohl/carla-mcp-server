"""Ambient stream — the unified measurement time-series for a Phase 3 session.

Every measurement source (LV2 polling, librosa companion, MIDI events,
user interjections) appends to a single per-session JSONL file. Comparators
(drift, prediction) and the output scheduler subscribe to this stream.

Schema (per line):
    {"ts_ms": <epoch ms>, "source": "lv2|librosa|midi|user|system",
     "type": "<measurement-name>", "value": <JSON-serializable>}

Path convention (per session):
    ~/.carla-mcp/earshot/sessions/{session_id}/ambient.jsonl

Concurrency model:
  - Multiple processes may write to the same file. Each entry is a single
    line < 4 KiB (PIPE_BUF on Linux), so individual writes are atomic when
    the file is opened with O_APPEND. CPython's `open(path, 'a')` sets
    O_APPEND on POSIX. Line-buffered output (`buffering=1`) flushes per
    line. fsync is optional; the reader follows whatever's been flushed.
  - No file-level lock is required for correctness under the line-atomicity
    assumption. We do NOT support writers emitting lines > 4 KiB.

Read model:
  - `snapshot(since_ms=None)` returns all entries seen so far.
  - `tail()` yields new entries as they arrive (polling-based). For sub-
    second responsiveness the reader uses a 50 ms poll interval; inotify
    would be principled but is overkill for the 4 Hz measurement cadence.

Postmortem rule #19 — Writer/Reader return values reflect actual outcome.
Bad input raises ValueError; partial reads (malformed lines) are skipped
with a warning rather than aborting the iteration.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from earshot import dispatch as dsp

logger = logging.getLogger(__name__)

# Sources that are recognized; entries with unknown sources are still
# accepted but logged as warnings.
KNOWN_SOURCES = {"lv2", "librosa", "midi", "user", "system"}

# Linux PIPE_BUF guarantees per-write atomicity below this size. JSONL lines
# above this length lose multi-writer atomicity. Writer raises if exceeded.
MAX_LINE_BYTES = 4000  # conservative — leave headroom under PIPE_BUF (4096)


def session_dir(session_id: str) -> Path:
    """Per-session storage root. Creating callers must mkdir it."""
    return dsp.SESSIONS_DIR / session_id


def ambient_path(session_id: str) -> Path:
    return session_dir(session_id) / "ambient.jsonl"


class AmbientStreamWriter:
    """Append-only JSONL writer for a single session's ambient stream.

    Instances hold an open file handle; use as a context manager or call
    `.close()` explicitly. Multiple concurrent writers (e.g. LV2 poller +
    librosa companion subprocess) writing to the same path are safe under
    the line-atomicity assumption documented at module level.
    """

    def __init__(self, session_id: str, *, fsync_every_n: int = 10):
        self.session_id = session_id
        self.path = ambient_path(session_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 'a' mode + buffering=1 (line-buffered) on POSIX gives O_APPEND
        # semantics; each write of a line < PIPE_BUF is atomic across
        # processes without explicit locks.
        self._fp = open(self.path, "a", buffering=1, encoding="utf-8")
        self._fsync_every_n = max(1, int(fsync_every_n))
        self._writes_since_fsync = 0
        self._closed = False
        logger.info("AmbientStreamWriter opened: %s", self.path)

    # ------------------------------------------------------------------
    def append(self, *, ts_ms: int, source: str, metric_type: str, value: Any) -> None:
        """Append one measurement to the stream.

        Args:
            ts_ms:       Wall-clock milliseconds since epoch (the common
                         time axis across all producers — see module docstring).
            source:      One of KNOWN_SOURCES (unknown is allowed but logged).
            metric_type: Measurement name, e.g. "plugin_3.param_5" or
                         "tempo_bpm" or "onset". Stored as `type` in JSON
                         to match the README spec; the keyword is renamed
                         here to avoid shadowing the builtin `type()`.
            value:       Any JSON-serializable value.

        Raises:
            ValueError: if metric_type is empty/non-string, ts_ms is non-int,
                        or the serialized line exceeds MAX_LINE_BYTES.
            RuntimeError: if called on a closed writer.
        """
        if self._closed:
            raise RuntimeError("AmbientStreamWriter is closed")
        if not isinstance(ts_ms, int):
            raise ValueError(f"ts_ms must be int, got {type(ts_ms).__name__}")
        if not isinstance(metric_type, str) or not metric_type:
            raise ValueError("metric_type must be a non-empty string")
        if source not in KNOWN_SOURCES:
            logger.warning("AmbientStreamWriter: unknown source %r (allowed)", source)

        entry = {"ts_ms": ts_ms, "source": source, "type": metric_type, "value": value}
        try:
            line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise ValueError(f"value not JSON-serializable: {e}") from e

        line_bytes = line.encode("utf-8")
        if len(line_bytes) + 1 > MAX_LINE_BYTES:  # +1 for newline
            raise ValueError(
                f"line {len(line_bytes)+1}B exceeds atomicity ceiling "
                f"{MAX_LINE_BYTES}B; multi-writer safety lost"
            )

        self._fp.write(line + "\n")
        self._writes_since_fsync += 1
        if self._writes_since_fsync >= self._fsync_every_n:
            self._fp.flush()
            try:
                os.fsync(self._fp.fileno())
            except OSError as e:
                logger.warning("fsync failed (continuing): %s", e)
            self._writes_since_fsync = 0

    def flush(self) -> None:
        """Force-flush buffered writes + fsync."""
        if self._closed:
            return
        self._fp.flush()
        try:
            os.fsync(self._fp.fileno())
        except OSError as e:
            logger.warning("fsync failed (continuing): %s", e)
        self._writes_since_fsync = 0

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush()
        finally:
            self._fp.close()
            self._closed = True
            logger.info("AmbientStreamWriter closed: %s", self.path)

    # ------------------------------------------------------------------
    def __enter__(self) -> "AmbientStreamWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class AmbientStreamReader:
    """Read-side accessor for a session's ambient stream.

    Two modes:
      - snapshot(since_ms=None) — read everything written so far, optionally
        filtered to entries with ts_ms >= since_ms.
      - tail(poll_interval_s=0.05) — generator that yields new entries as
        they arrive; runs until the caller stops iterating or the file is
        removed. Polling-based for simplicity (50 ms default).

    Malformed lines (truncated, non-JSON, missing keys) are skipped with a
    debug log entry rather than aborting the iteration. This matters because
    multi-writer atomicity is best-effort — if a line ever does exceed
    PIPE_BUF and gets interleaved, the reader should keep going.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.path = ambient_path(session_id)

    # ------------------------------------------------------------------
    def snapshot(self, *, since_ms: Optional[int] = None) -> list[dict]:
        """All entries currently on disk, optionally filtered by ts_ms."""
        if not self.path.exists():
            return []
        out: list[dict] = []
        with open(self.path, "r", encoding="utf-8") as fp:
            for line in fp:
                entry = _parse_line(line)
                if entry is None:
                    continue
                if since_ms is not None and entry.get("ts_ms", 0) < since_ms:
                    continue
                out.append(entry)
        return out

    def tail(self, *, since_ms: Optional[int] = None,
             poll_interval_s: float = 0.05,
             stop_fn=None) -> Iterator[dict]:
        """Generator yielding new entries as they're written.

        Stops when `stop_fn()` returns True (if supplied) or the file is
        deleted under us. The caller may also break out of the iteration
        at any time. Initial backfill (entries with ts_ms < since_ms or
        all if `since_ms` is None) is skipped via a seek-to-end pattern.
        """
        # Wait briefly for the file to exist (e.g. if session just started).
        deadline = time.time() + 5.0
        while not self.path.exists() and time.time() < deadline:
            if stop_fn and stop_fn():
                return
            time.sleep(poll_interval_s)
        if not self.path.exists():
            return

        with open(self.path, "r", encoding="utf-8") as fp:
            if since_ms is None:
                # No backfill requested: seek to end and only yield future lines.
                fp.seek(0, os.SEEK_END)
            # If since_ms is set, we start reading from the file's beginning
            # so historical lines can be returned; the in-loop ts_ms filter
            # below drops anything older than since_ms. New lines arriving
            # after we catch up to current EOF are yielded normally.
            buffer = ""
            while True:
                if stop_fn and stop_fn():
                    return
                chunk = fp.read()
                if not chunk:
                    time.sleep(poll_interval_s)
                    continue
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    entry = _parse_line(line + "\n")
                    if entry is None:
                        continue
                    if since_ms is not None and entry.get("ts_ms", 0) < since_ms:
                        continue
                    yield entry


def _parse_line(line: str) -> Optional[dict]:
    """Parse one JSONL line; return None on malformed input (logged debug)."""
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except json.JSONDecodeError as e:
        logger.debug("AmbientStreamReader: skipping malformed line: %s", e)
        return None
    if not isinstance(entry, dict):
        logger.debug("AmbientStreamReader: entry not a dict, skipping")
        return None
    return entry


# ----------------------------------------------------------------------
# Convenience helpers
# ----------------------------------------------------------------------

def now_ms() -> int:
    """Wall-clock milliseconds since epoch. Used as the canonical timestamp
    for all ambient stream entries across all producers."""
    return int(time.time() * 1000)
