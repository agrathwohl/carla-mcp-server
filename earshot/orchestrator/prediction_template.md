# Orchestrator-side prediction template (Phase G)

This is **prompt structure documentation for the harness**, not code that runs
inside the MCP server. The orchestrator (Claude Code, or any equivalent
harness that drives the Earshot tools) reads this template to know how to
respond when a `boundary_approaching` event arrives in the commentary queue.

## Trigger

The session's commentary queue emits a `BoundaryApproachingEvent` with shape:

```json
{
  "event": "boundary_approaching",
  "upcoming_section_index": 3,
  "boundary_track_time_s": 456.13,
  "eta_ms": 5000,
  "ts_ms": 1779200000000,
  "track_time_s": 451.13
}
```

When the orchestrator sees this, it must compose a `predictions` dict and call:

```python
earshot_refresh_expectations(
    session_id=<the active session>,
    section_index=3,
    predictions={...},
    source="orchestrator",
)
```

Latency budget: `eta_ms` tells you how long until the new section actually
arrives. Use it. If your LLM round-trip would land predictions after the
boundary has already passed, **still submit them** — the tracker's
`confidence_at()` decays the score but a late prediction is better than no
prediction. The prediction comparator weights events accordingly.

## Inputs the orchestrator should consult

Before calling `earshot_refresh_expectations`, the orchestrator should read
the following resources to ground its predictions in real material:

1. **Oeuvre report** — `oeuvre://{artist_id}` (or read directly from
   `~/.carla-mcp/earshot/oeuvre/{artist_id}.{json,md}`). Gives the artist's
   tendencies, recurring patterns, aesthetic signatures.
2. **Phase 2 baseline** — `~/.carla-mcp/earshot/tracks/{track_id}/context.json`.
   Read the upcoming section's structural data: how long is it, where does
   it sit relative to the section map's larger arc, what was the dynamic
   range of comparable sections earlier in the track.
3. **Current expectation state** — call
   `earshot_get_session_state` (Phase J, future) or query the tracker via
   the registry. Useful for "what did I predict for the LAST section, and
   how wrong was I?" — calibrate against your own track record.
4. **Recent ambient stream signal** — the last ~10–20 seconds of measurements
   give context for what the player is doing right now (calm? heated?
   accelerating?). Phase J will expose a snapshot helper; for now the
   orchestrator can read the JSONL directly at
   `~/.carla-mcp/earshot/sessions/{session_id}/ambient.jsonl`.

## Output schema

`predictions` is a JSON dict keyed by `Dimension.value` strings, mapped to
the expected scalar (or categorical) for that dimension during the upcoming
section. Unknown keys are tolerated (forward-compatible) but ignored by
the comparator. **All dimensions are optional**; only emit predictions you
have grounded reason to believe in.

| Key | Type | Meaning | Example |
|---|---|---|---|
| `tempo` | float (BPM) | Expected dominant tempo through the section | `103.5` |
| `dynamic_envelope` | float (dBFS) | Expected RMS level (mid-section, not peak) | `-18.0` |
| `lufs_integrated` | float (LUFS) | Expected integrated loudness through the section | `-12.0` |
| `spectral_centroid` | float (Hz) | Expected mean brightness | `2400.0` |
| `onset_density` | float (Hz) | Expected onset events per second | `1.8` |
| `key` | object | Expected tonality (categorical) | `{"tonic": "D", "mode": "minor"}` |

## Prompt structure (suggested for harness LLM call)

```
You are the expectation generator for an Earshot listening session.
A section boundary is approaching in {eta_seconds} seconds.

Context:
  - Track: {track_id} by {artist_id}
  - Approaching section: index {upcoming_section_index},
    starts at track-time {boundary_track_time_s}s,
    duration {section_duration_s}s
  - Baseline values for this section from Phase 2 pre-analysis:
    {baseline_section_snapshot}
  - Recent ambient measurements (last 10 s):
    {ambient_tail_snapshot}
  - Artist's oeuvre tendencies relevant to this kind of section:
    {oeuvre_excerpt}

Predict the expected values for the next section across dimensions where
you have grounded reason to believe. Be honest about uncertainty: omit
dimensions you can't predict confidently rather than guessing.

Output STRICTLY a JSON object matching the predictions schema. No prose.
```

## Honesty rules that apply here

From `earshot/README.md` § "Honesty rules":

- **No performed enthusiasm in the prediction itself.** Predictions are
  measurements-grounded forecasts, not editorial opinion.
- **Acknowledge limits explicitly via omission.** If you can't predict
  tempo (e.g. the artist's catalog includes both metric and ametric
  pieces and you don't know which is coming), don't emit a `tempo` field.
  The comparator suppresses prediction-error events for unpredicted
  dimensions; you don't hurt the system by being silent.
- **Don't fabricate "expected" values just to make the dict populated.**
  An empty predictions dict is valid input.

## What this template intentionally does NOT cover

- How to author the actual commentary text in response to a fired
  prediction-error event (that's Phase I — the output scheduler).
- The user-facing "agent voice" tone (that's the skill.md `voice_persona`
  setting plus the action-text catalogs).
- Cross-section state carryover (e.g. "section 3 typically grows from
  section 2") — that's an orchestrator-side rendering choice, not in
  the predictions schema.

The predictions schema is deliberately narrow: it accepts numbers (or one
categorical), the comparator does the rest. Editorial intelligence lives
elsewhere.
