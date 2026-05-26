"""Phase L — session reflection writer.

Reads a completed (or in-progress) session's persisted artifacts and
produces:

  1. `reflection_data.json` — a structured machine-readable summary of
     what the session actually contained. Suitable for the orchestrator
     to consume programmatically or feed back into an LLM call.
  2. `reflection.md` — a markdown skeleton with quick-stats, dimension
     summaries, and a marker comment indicating where the orchestrator
     should append a prose synthesis. The skeleton is intentionally
     terse — the orchestrator owns the editorial voice; this module
     only owns the data.

Inputs (per-session artifacts under ~/.carla-mcp/earshot/sessions/{id}/):
  - `ambient.jsonl`    — measurement stream from LV2 poller + librosa
                         companion + user interjections.
  - `commentary.jsonl` — every CommentaryQueue push captured by
                         CommentaryLogger (Phase L instrumentation in
                         scheduler/commentary.py).

Honesty discipline (postmortem rule #19):
  Statistics are computed deterministically from the JSONL files; no
  inference, no synthesis. The .md body for prose lives BELOW a marker
  comment and is left blank — the orchestrator fills it. If the
  orchestrator doesn't, the .md remains a quick-stats document. We don't
  fabricate a synthesis.

The module is sync and pure-Python; callers (EarshotTools.earshot_reflect_session)
wrap it in their own async layer.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

from earshot.ambient_stream import session_dir

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# JSONL helpers
# ----------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping malformed lines (logged at debug).

    Matches AmbientStreamReader._parse_line behavior — bad lines don't
    abort the read; they just don't make it into the result.
    """
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                logger.debug("reflection: skipping malformed line: %s", e)
                continue
            if isinstance(entry, dict):
                out.append(entry)
    return out


# ----------------------------------------------------------------------
# Data assembly
# ----------------------------------------------------------------------

def compute_reflection_data(session_id: str) -> dict:
    """Build a structured summary dict from the session's JSONL artifacts.

    Returns a dict with the following top-level keys:

      session_id, duration_ms, ambient_entries_total, ambient_by_source,
      ambient_by_type, user_interjections, commentary_total,
      emissions_total, prose_requests_total, emissions_by_level,
      emissions_by_dimension, commentary_timeline,
      artifacts: { ambient_path, commentary_path }

    The data structure is the orchestrator's input for any prose synthesis
    pass — every claim made in `reflection.md`'s body should be
    grounded in this dict.
    """
    sdir = session_dir(session_id)
    ambient_path = sdir / "ambient.jsonl"
    commentary_path = sdir / "commentary.jsonl"

    ambient = _read_jsonl(ambient_path)
    commentary = _read_jsonl(commentary_path)

    # --- Ambient analysis ---
    source_counts = Counter(e.get("source", "?") for e in ambient)
    type_counts = Counter(e.get("type", "?") for e in ambient)
    interjections = [e for e in ambient if e.get("source") == "user"]

    ambient_ts = [int(e["ts_ms"]) for e in ambient
                  if isinstance(e.get("ts_ms"), int)]
    if ambient_ts:
        ambient_start_ms = min(ambient_ts)
        ambient_end_ms = max(ambient_ts)
        duration_ms = max(0, ambient_end_ms - ambient_start_ms)
    else:
        ambient_start_ms = ambient_end_ms = 0
        duration_ms = 0

    # --- Commentary analysis ---
    emissions = [c for c in commentary if c.get("kind") == "emission"]
    prose_requests = [c for c in commentary if c.get("kind") == "prose_request"]
    level_counts = Counter(e.get("level_name", "?") for e in emissions)

    # Dimensions tag (which musical axes drove the most commentary).
    dim_counts: Counter = Counter()
    for e in emissions:
        for d in (e.get("dimensions") or []):
            dim_counts[d] += 1

    # Compact timeline — chronological flatten for the orchestrator's
    # narrative pass. ts_user_clock_ms for emissions (= when it reached
    # the user); ts_target_user_clock_ms for prose requests; fall back
    # to logged_at_ms when neither is present.
    timeline = []
    for c in commentary:
        ts = (c.get("ts_user_clock_ms")
              or c.get("ts_target_user_clock_ms")
              or c.get("logged_at_ms")
              or 0)
        entry = {
            "ts_ms": ts,
            "kind": c.get("kind"),
        }
        if c.get("kind") == "emission":
            entry["level_name"] = c.get("level_name")
            entry["content"] = c.get("content")
            entry["dimensions"] = c.get("dimensions") or []
            entry["score"] = c.get("score")
            entry["source_event_id"] = c.get("source_event_id")
        elif c.get("kind") == "prose_request":
            entry["intensity_target_name"] = c.get("intensity_target_name")
            entry["request_id"] = c.get("request_id")
            entry["source_event_id"] = c.get("source_event_id")
            entry["context_event_type"] = (
                (c.get("context") or {}).get("event_type")
                or (c.get("context") or {}).get("kind")
            )
        timeline.append(entry)
    timeline.sort(key=lambda x: x.get("ts_ms") or 0)

    return {
        "session_id": session_id,
        "duration_ms": duration_ms,
        "ambient_window_ms": [ambient_start_ms, ambient_end_ms],
        "ambient_entries_total": len(ambient),
        "ambient_by_source": dict(source_counts),
        "ambient_by_type": dict(type_counts),
        "user_interjections": [
            {
                "ts_ms": i.get("ts_ms"),
                "value": i.get("value"),
            } for i in interjections
        ],
        "commentary_total": len(commentary),
        "emissions_total": len(emissions),
        "prose_requests_total": len(prose_requests),
        "emissions_by_level": dict(level_counts),
        "emissions_by_dimension": dict(dim_counts),
        "commentary_timeline": timeline,
        "artifacts": {
            "ambient_path": str(ambient_path),
            "commentary_path": str(commentary_path),
            "ambient_exists": ambient_path.exists(),
            "commentary_exists": commentary_path.exists(),
        },
    }


# ----------------------------------------------------------------------
# Markdown skeleton
# ----------------------------------------------------------------------

def _build_markdown_skeleton(data: dict) -> str:
    """Render a deterministic markdown summary from the data dict.

    The body intentionally does NOT include any synthesized prose. The
    orchestrator appends below the marker comment when it produces one.
    """
    duration_s = data["duration_ms"] / 1000.0
    lines: list[str] = [
        "---",
        f"session_id: {data['session_id']}",
        f"duration_seconds: {duration_s:.1f}",
        f"ambient_entries: {data['ambient_entries_total']}",
        f"commentary_total: {data['commentary_total']}",
        "synthesis_status: pending_orchestrator",
        "---",
        "",
        f"# Session reflection — {data['session_id']}",
        "",
        "## Quick stats",
        "",
        f"- Duration: {duration_s:.1f} s ({duration_s / 60:.1f} min)",
        f"- Ambient entries: {data['ambient_entries_total']}",
        f"- Commentary items: {data['commentary_total']} "
        f"({data['emissions_total']} emissions, "
        f"{data['prose_requests_total']} prose requests)",
        f"- User interjections: {len(data['user_interjections'])}",
        "",
    ]

    if data["ambient_by_source"]:
        lines.extend(["## Ambient stream by source", ""])
        for source, n in sorted(data["ambient_by_source"].items()):
            lines.append(f"- `{source}`: {n} entries")
        lines.append("")

    if data["emissions_by_level"]:
        lines.extend(["## Emissions by intensity level", ""])
        # Sort by intensity ordering (SILENT, ACTION_TEXT, ...) when known.
        level_order = {
            "SILENT": 1, "ACTION_TEXT": 2, "EXCLAMATION": 3,
            "OBSERVATION": 4, "CONSIDERED": 5, "REFLECTION": 6,
        }
        ordered = sorted(
            data["emissions_by_level"].items(),
            key=lambda kv: level_order.get(kv[0], 99),
        )
        for level, n in ordered:
            lines.append(f"- `{level}`: {n}")
        lines.append("")

    if data["emissions_by_dimension"]:
        lines.extend(["## Emissions by dimension", ""])
        for dim, n in sorted(data["emissions_by_dimension"].items(),
                             key=lambda kv: -kv[1]):
            lines.append(f"- `{dim}`: {n}")
        lines.append("")

    if data["user_interjections"]:
        lines.extend(["## User interjections", ""])
        for i in data["user_interjections"]:
            val = i.get("value") or {}
            text = val.get("text", "") if isinstance(val, dict) else str(val)
            kind = val.get("kind", "") if isinstance(val, dict) else ""
            track_t = val.get("track_time_s") if isinstance(val, dict) else None
            t_str = f" (t={track_t}s)" if track_t is not None else ""
            kind_str = f"_{kind}_ " if kind else ""
            lines.append(f"- {kind_str}{text}{t_str}")
        lines.append("")

    lines.extend([
        "## Open for orchestrator",
        "",
        "<!-- The orchestrator should read reflection_data.json (next to this",
        "     file) and append a prose synthesis BELOW this comment. Suggested",
        "     prompts to explore:",
        "       - Where did the agent's predictions diverge from what arrived?",
        "       - Which of the user's interjections did the agent fail to anticipate?",
        "       - What does the dimension breakdown say about THIS track's character?",
        "       - What would the agent listen for differently next time?",
        "     The synthesis_status frontmatter field should be flipped to",
        "     'complete' once prose is written. -->",
        "",
    ])
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Public entrypoint
# ----------------------------------------------------------------------

def write_reflection_artifacts(
    session_id: str,
    output_dir: Optional[Path] = None,
) -> dict:
    """Compute reflection data + write both artifacts. Returns paths + data.

    When `output_dir` is None (default), writes alongside the session's
    ambient stream at `~/.carla-mcp/earshot/sessions/{id}/`. Override only
    for tests / one-off exports.
    """
    sdir = output_dir if output_dir is not None else session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)

    data = compute_reflection_data(session_id)

    data_path = sdir / "reflection_data.json"
    data_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    md_path = sdir / "reflection.md"
    md_path.write_text(_build_markdown_skeleton(data), encoding="utf-8")

    logger.info(
        "wrote reflection artifacts for %s: %d ambient, %d commentary",
        session_id,
        data["ambient_entries_total"],
        data["commentary_total"],
    )
    return {
        "reflection_data_path": str(data_path),
        "reflection_md_path": str(md_path),
        "data": data,
    }
