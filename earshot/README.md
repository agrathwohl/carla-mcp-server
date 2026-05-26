# Earshot

> A co-listening companion that meets you where your music lives.

## Codename

**Earshot.** The agent isn't a fan, isn't a critic, isn't an audience — it's *within earshot*. Present in proximity to your music. Honest about what it can and cannot perceive.

The name is load-bearing. It names what the agent *is* (present, attentive, in range) without claiming what it isn't (feeling, hearing, judging). Project terminology should follow this discipline throughout.

---

## The problem

Music-making is increasingly isolated work. The reinforcement loop that historically sustained musicians — playing a new track for someone and getting an immediate, attentive, contextual reaction — has thinned out.

- Streaming platforms treat each play as an anonymous impression event.
- Algorithmic discovery doesn't reward the *individual artist's growth arc*.
- Most artists never get the experience of someone listening to their full body of work and offering a coherent reading of who they are as an artist.

Earshot is an attempt to recreate part of that loop using AI — honestly, without faking the parts AI can't deliver.

The product is not for stadium-fillers with active fan communities. It is for the bedroom producer at 2am, the working musician with thirty plays per release, the artist whose multi-year body of work has never been read as a coherent statement by another mind.

---

## What Earshot is NOT

The design depends on what we deliberately do NOT do:

- We do not claim the agent has ears, feelings, taste, or aesthetic preferences.
- We do not perform emotional enthusiasm the agent does not have.
- We do not market this as a replacement for human listeners — it is a *bridge* through dry spells, not a destination.
- We do not predict or spoil moments in the music; the agent's foreknowledge stays internal.
- We do not pretend perception we cannot deliver (vocal timbre, groove "feel", aesthetic-emotional impact).
- We do not assign scores, ratings, or critic-style verdicts.
- We do not accept privileged information from the artist outside of public sources. If the artist wants the agent to know something, they update their public presentation and re-run.

The product's value depends on being *legibly different* from human listening, not on simulating it.

---

## Core concept

When a producer plays a new track for a friend, the friend's reaction is enriched by knowing the artist's past work, their stated themes, their aesthetic. The friend listens *with context*. Earshot tries to occupy that role:

1. **Ingest the artist's full body of work** (Bandcamp profile, SoundCloud, personal website, etc.) before any music plays.
2. **Synthesize a coherent reading** of the artist as a body of work, including aesthetic signatures, recurring themes, trajectory.
3. **Co-listen to specific tracks in real time** with measurement-grounded reactions timed to musical events.
4. **Reflect** on how the new work fits into the artist's broader trajectory.

The result aims to feel like co-listening with a producer-friend who has done their homework — not an emotional fan, not a neutral reviewer, but someone present, attentive, and grounded.

---

## Architecture overview

Earshot sits atop the existing Carla MCP server's audio plumbing. The key architectural moves:

### The delay tower

Audio source (browser, file, network stream) → JACK → fan out:

- **Direct path:** Carla analyzer chain (T=0). Agent receives audio first.
- **Delayed path:** LV2 delay plugin (configurable, default ~5s) → user's speakers.

```
                       ┌─→ Carla analyzer chain ──→ measurements ──→ agent
Audio source ─→ JACK ──┤
                       └─→ Delay plugin (5s) ──→ user speakers
```

The agent gets a ~5-second head start. Its commentary is generated in the gap and delivered in sync with the user's audio time. Commentary lands ON the moment from the user's perspective, even though it was prepared seconds earlier.

This is the same architectural pattern as broadcast commentary (announcer watches live; viewer watches with delay; reaction feels synced). It is a standard signal-processing approach, not a hack.

### Producer-consumer split

Following the existing `monitors/ambient_stream.py` pattern:

- **Measurement loop** — Carla analyzer chain emits measurements (LUFS, spectrum, correlation, dynamic range, transients) continuously at JACK-native low latency, writing to `/tmp` or equivalent.
- **Commentary loop** — Agent reads measurements at LLM speed, generates commentary, schedules emissions.

Neither loop blocks the other. The measurement loop runs at audio rates; the commentary loop runs at LLM rates. The buffer absorbs the rate mismatch.

### Two operating modes

| Mode | Audio path | Commentary timing | Use case |
|------|------------|-------------------|----------|
| **Listening Session** | Delayed | Synced to user-clock | Reviewing a finished track |
| **Performance Feedback** | Live (zero delay) | Agent-time (1–3s lag, immediate) | Live playing, mix-as-you-go |

User selects at session start. Listening Session is the default; Performance Feedback serves working producers monitoring their own playing.

The two modes share the same audio analysis substrate; only routing and scheduler behavior differ.

---

## Form factor and distribution

Earshot is two coordinated deliverables, not a monolithic application.

### Layer 1 — MCP server extension (in-tree)

Earshot extends `carla-mcp-server` with new tools and resources. Same Python process, same MCP registry, same client connection. From the client's perspective, the Earshot tools are part of the existing `carla-mcp` server alongside the audio tools.

This follows the pattern already established by `learning/` and `mixassist_resources.py` in the codebase — separable concerns living in-tree, contributing tools and resource URI schemes to the shared registry. Earshot is the third such extension.

```
carla-mcp-server/
├── earshot/
│   ├── README.md              # this design doc
│   ├── __init__.py
│   ├── session.py             # session state management
│   ├── tools.py               # MCP tool implementations
│   ├── resources.py           # oeuvre://, profile://, session://, track:// providers
│   ├── scheduler.py           # output scheduler
│   ├── expectation_tracker.py # running predictions + state
│   ├── comparators.py         # drift + prediction comparators
│   ├── streaming_librosa.py   # JACK companion process
│   ├── profiles/              # genre profile YAML files
│   ├── action_text/           # action-text catalogs per profile
│   └── tts.py                 # optional TTS bridge
├── server.py                  # registers earshot tools alongside existing
├── tool_registry.py           # already-existing central registry
└── ...
```

The MCP layer is pure capability — audio routing, measurement, expectation tracking, scheduler primitives. It has no opinions about what to talk about or when. That intelligence lives in Layer 2.

### Layer 2 — Claude Code skill (or equivalent harness package)

The skill is what makes the bundle of MCP tools feel coherent and characterful. It encodes:

- When Earshot activates (intent + URL detection)
- Phase 1 → 4 session orchestration
- Honesty rules and intensity-stack discipline
- The "long-time-listener-friend" voice
- Profile selection and correction flow

Skills live as markdown files with frontmatter describing activation triggers. The Earshot skill is the same shape as existing Claude Code skills (`superpowers:brainstorming`, `airis:research`, etc.) — domain-specific orchestration prompts that drive Claude Code's behavior using available MCP tools.

Without the skill, the MCP tools are a powerful audio-aware API. With the skill, they're a listening companion. The skill is part of the product, not implementation detail.

### v1 deliverable

- MCP server extension in `carla-mcp-server/earshot/`
- Claude Code skill package (separately distributable)
- Documented installation: install the MCP server, install the skill, you're done

### Future form factors (not v1)

- **Dedicated TUI** — for users who don't want a coding harness. Calls the same MCP server.
- **Web frontend** — visual waveform + commentary stream + cover art. Same backend.
- **Standalone CLI binary** (`earshot listen <url>`) — packaged for non-technical users. Same backend.
- **Voice-only mode** — TTS commentary through smart speakers or headphones, no screen needed.

All of these reuse the MCP server. Different harness, same engine. The form factor question is decoupled from the architecture question.

### Tool surface (initial sketch)

The MCP tools Earshot adds. Final schemas TBD during implementation; this is the API shape the skill orchestrates against.

| Tool | Purpose |
|------|---------|
| `earshot_ingest_artist` | Phase 1: scrape public sources, synthesize oeuvre report, write Markdown + YAML frontmatter |
| `earshot_analyze_track` | Phase 2: download track, run librosa/Whisper/vision, produce track context |
| `earshot_start_session` | Phase 3 start: set up delay tower, load profile + overlay, prime expectations, begin playback |
| `earshot_get_commentary_queue` | Phase 3 runtime: poll for ready-to-deliver commentary events (with long-poll support) |
| `earshot_user_interject` | Phase 3: pass user utterance into the session for inline response |
| `earshot_correct_profile` | Adjust active profile from user feedback; persist scope = session / alias / global |
| `earshot_end_session` | Phase 3 → 4: tear down, optionally generate reflection commentary |

#### Schema sketches (truncated; full schemas TBD)

**`earshot_ingest_artist`**
```json
{
  "artist_id": "string (e.g. 'andrew_grathwohl')",
  "profile_urls": ["string", "..."],
  "search_externally": "boolean (default true)",
  "force_rerun": "boolean (default false)"
}
```
Returns: path to written oeuvre report, frontmatter summary, list of `known_gaps`.

**`earshot_analyze_track`**
```json
{
  "track_url": "string (Bandcamp/SoundCloud URL or local path)",
  "artist_id": "string (must match an ingested oeuvre)",
  "track_id": "string (optional, auto-derived if omitted)"
}
```
Returns: track_id, section map, lyric timeline, baseline values (tempo/key/time_sig/dynamic_envelope), inferred genre profile.

**`earshot_start_session`**
```json
{
  "track_id": "string",
  "mode": "'listening_session' | 'performance_feedback' (default listening_session)",
  "delay_seconds": "number 2.0-15.0 (default 5.0)",
  "voice_enabled": "boolean (default false)"
}
```
Returns: session_id, playback start timestamp, loaded profile name + version.

**`earshot_get_commentary_queue`**
```json
{
  "session_id": "string",
  "since_ts": "number (return events after this)",
  "wait_seconds": "number (long-poll, default 0)"
}
```
Returns: list of `{ts, intensity_level, content, source_event}` events ready for delivery at user-clock time.

**`earshot_user_interject`**
```json
{
  "session_id": "string",
  "message": "string",
  "track_position_seconds": "number"
}
```
Returns: acknowledgment; agent response (if immediate) will arrive via the commentary queue.

**`earshot_correct_profile`**
```json
{
  "session_id": "string",
  "feedback": "string (natural-language)",
  "scope": "'session_only' | 'alias_persistent' | 'global' (default alias_persistent)"
}
```
Returns: applied adjustments summary, persistence path if scope > session.

**`earshot_end_session`**
```json
{
  "session_id": "string",
  "generate_reflection": "boolean (default true)"
}
```
Returns: session summary, reflection text (if generated), persistence path for any session-learnings.

### Resource URI schemes

| Scheme | Purpose |
|--------|---------|
| `oeuvre://<artist_id>` | Full oeuvre report (Markdown + frontmatter) |
| `oeuvre://<artist_id>/frontmatter` | Structured fields only (faster lookup) |
| `oeuvre://<artist_id>/aliases` | Alias list with genre priors |
| `profile://genre/<name>` | Named genre profile |
| `profile://artist/<artist_id>/<alias>` | Artist-specific overlay |
| `session://<session_id>/state` | Current session state (playing/paused, current section) |
| `session://<session_id>/measurements` | Recent measurement events |
| `session://<session_id>/predictions` | Current expectation state |
| `track://<track_id>` | Track context (section map, lyrics, cover art, baselines) |

---

## The four-phase listening experience

### Phase 1 — Oeuvre ingestion

**Input:** artist's profile URL(s). Bandcamp / SoundCloud / personal site.

**Process:** Scrape available metadata, cover art, liner notes, bios, descriptions, tags, discography. Optionally search beyond primary sources for press, interviews, scene context, collaborators. Synthesize a reading of the artist as a body of work — through-lines, aesthetic patterns, evolution, multi-project relationships if applicable.

**Tooling:**

- **yt-dlp** ([github.com/yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)) — primary scraper for SoundCloud and most streaming platforms. Metadata-only extraction via `yt-dlp --skip-download --dump-json <url>`. Handles individual tracks AND user profile URLs (extracts the full discography). Reverse-engineers the public web-player `client_id`; no auth, no API keys required.
- **Direct HTTP + structured-data parsing** — for Bandcamp (static HTML + JSON-LD blocks, the `data-tralbum` JSON, supporter messages) and personal artist sites.
- **Playwright** (headless browser) — fallback for JS-heavy content yt-dlp doesn't expose: comments DOM, follower counts, certain social signals. Second layer, not primary.
- **WebSearch** — external research beyond primary URLs (press, interviews, scene context, collaborator backgrounds).

Tooling choice is load-bearing here. WebFetch-only ingestion (no JS execution) returns React shells for SoundCloud and similar SPAs; yt-dlp returns real structured data. This is the difference between a thin and rich Phase 1 read, especially for artists whose primary home is SoundCloud. None of these tools breach the contemporaneous-reading principle — they all read public content, just more capably than a JS-less fetcher can.

**Output format:** Markdown document with YAML frontmatter. Single artifact per artist (`oeuvre_report.md`) consumed by both the runtime LLM (which reads the prose body) and the scheduler code (which queries the frontmatter for structured factoids).

```yaml
---
ingested_at: 2026-05-16T00:14Z
artist_display: <name>
sources:
  bandcamp: [<alias>, ...]
  soundcloud: [<alias>, ...]
  website: [<url>, ...]
aliases:
  - name: <alias>
    primary_genre: <label>
    secondary_genre: <label>
    release_count: <int>
    cover_art: [<url>, ...]
genre_priors:
  <genre_label>: <weight>
known_gaps:
  - field: <name>
    classification: private_professional | undeclared | defunct_domain | unknown
---

# <Artist Display> — Oeuvre Reading

[Prose synthesis...]
```

**Discipline:** Frontmatter holds unambiguous facts (URLs, counts, dates, classifications-as-labels). Body holds interpretation (what facts mean, how they relate, what the artist is doing). Resist over-structuring — JSON-ifying a portrait kills the portrait.

Reusable across many listening sessions — ingest once, listen many times. Re-runnable on demand as the artist's public presentation evolves (per contemporaneous-reading principle).

**Contemporaneous reading:** Phase 1 reads what is publicly findable at the moment of ingestion. Gaps and silences in the public surface — undescribed projects, retired domains, pseudonymous collaborators, professional confidentialities — are preserved as part of the read, not filled in. The report is a snapshot of the artist's public presentation at this moment and is re-runnable on demand as that presentation evolves. See "Honesty rules" for the underlying principle.

**Design constraint:** the report must capture specifics, not generic descriptors. Direct quotes from artist's own self-presentation are preferred over agent paraphrase. The report's worth is measured by whether reading it makes the next reader feel like they know who this artist is.

### Phase 2 — Track pre-analysis

**Input:** specific track URL or audio file.

**Process:**
- Audio retrieval (yt-dlp / scdl / bandcamp-dl)
- Librosa: tempo, key, beat track, section boundaries
- Vision model: cover art for this release
- Whisper: timestamped lyric transcription (if vocals present)
- Waveform image fetched and visually analyzed if platform provides one
- Cross-validation: librosa section boundaries vs. waveform image visual structure

**Output:** track context document with section map, lyrics-by-timestamp, identified anchor events, and inferred genre profile (with confidence).

### Phase 3 — Co-listening session

The active listening experience. Audio plays through the delay tower; agent reacts in user-clock time using the output scheduler.

This is the high point of the system. Phases 1, 2, and 4 exist to make Phase 3 feel like it should.

### Phase 4 — Reflection

End-of-track or end-of-session. Agent ties the new work back into the artist's broader trajectory.

> "This continues the move toward X you started in [previous release]."
>
> "The thing you were trying to nail on [older track] — you got it here."

This is the closing step of the reinforcement loop. Most listening sessions just end. This one names the experience and places it in narrative — which is exactly what algorithmic platforms don't do.

---

## Measurement and surprise detection

The substrate the expectation tracking layer consumes. Two analyzer paths, one unified stream, two comparators.

### Two analyzer paths

**Path 1 — Carla LV2 analyzer chain** (real-time, DSP-graph-native)

LV2 plugins that fit cleanly in the audio graph, measuring analyses that DSP plugins do well:

- **x42 EBU R128 Meter** — integrated/momentary/short-term LUFS, true peak, loudness range
- **Calf Spectrum Analyzer** — FFT spectrum, spectral centroid derivable
- **VU meter** (any LV2) — analog-style level meter; same pattern as the song-structure inference example in the main `carla-mcp-server` README
- **Dynamic range meter** (x42 dpm or similar) — DR measurement
- **Transient detector** — onset density signal
- **Peak/RMS meter** — fast-tracking levels

These live in the analyzer chain hosted by Carla. Their measurement parameters are read via the existing `capture_plugin_parameters` tool at a configurable rate (~4 Hz initial target, tunable).

**Path 2 — Streaming-librosa companion process** (Python, attached to JACK output)

For analyses that LV2 plugins don't do well — beat tracking, key estimation, chroma. A small Python process subscribes to a dedicated JACK output channel and runs librosa in streaming mode.

Outputs:

- **Tempo (BPM)** — online beat tracker with confidence
- **Key estimate** — rolling chroma window + Krumhansl-Schmuckler or similar
- **Chroma vector** — 12-dim pitch class profile per window
- **Time signature** — onset pattern + beat tracking
- **Harmonic complexity proxies** — spectral flatness, chord change rate

Why a Python companion instead of LV2 plugins for these?

- Tempo tracking algorithms benefit from longer context windows than LV2 plugins typically maintain
- Key estimation is statistical over time and needs Python-level state management
- LV2 ecosystem is thin for these specific analyses
- librosa/essentia are well-maintained, well-documented, broadly understood

From Carla's perspective the companion is transparent — just another JACK client subscribed to an output port. From the system's perspective it produces measurements that flow into the same stream as the LV2 path.

### The unified ambient stream

All measurements flow into a single time-series stream (extending the existing `monitors/ambient_stream.py` pattern):

```
ambient_stream/{session_id}.jsonl
  Each line: {"ts": <epoch_ms>, "source": "lv2|librosa", "type": <metric_name>, "value": <data>}
  Append-only during session
  Consumed by: drift comparator, prediction comparator, output scheduler
```

Consumers don't care which path produced a given measurement. The stream is the contract.

### Two comparators on the same stream

**Drift comparator** — baseline-relative

- *Inputs:* ambient stream + baseline values from Phase 2 (tempo, key, time signature, harmonic profile, dynamic envelope)
- *Logic:* rolling window estimate vs. baseline; if delta exceeds threshold → drift event
- *Emits:* `{event: "drift", dimension: <tempo|key|...>, magnitude: <float>, baseline: <X>, current: <Y>, ts: ...}`

**Prediction comparator** — expectation-relative

- *Inputs:* ambient stream + section-boundary expectations from agent's running prediction state
- *Logic:* per-window measurement vs. expectation; weighted-sum delta; if score exceeds threshold → prediction-error event
- *Emits:* `{event: "prediction_error", dimensions: [<list>], score: <float>, expected: <vector>, actual: <vector>, ts: ...}`

Both comparators feed the expectation tracking layer's event queue, which feeds the scheduler.

### Canonical session preset

Earshot ships a `.carxp` session preset defining the standard analyzer chain, loaded at session start via existing `load_session`:

```
Audio in (pre-delay tap)
  → x42 EBU R128 Meter
  → Calf Spectrum Analyzer
  → VU meter
  → Dynamic range meter
  → Transient detector
  → JACK out (to streaming-librosa companion)
```

### Calibration and integrity

The existing audio fundamentals middleware (`audio_fundamentals.py`) is a quiet safety net for measurement integrity. False drift events from misrouted audio or gain errors would corrupt the prediction-error signal — which is the substrate the agent's commentary depends on. The fundamentals layer catches:

- Clipping in the analyzer chain (would produce false spectral readings)
- Sample rate mismatches between source and JACK
- Gain staging errors in the delay plugin

If fundamentals violations occur during a session, surprise detection pauses with an explicit message rather than producing garbage commentary. The agent does not pretend to react to data it cannot trust.

---

## Expectation tracking and prediction-error commentary

The engagement substrate of the system. The agent's "interest" in any musical moment is grounded in a measurable quantity, not performed.

### The core idea

LLMs are next-token predictors. At every step they emit a probability distribution over what comes next. When actual next tokens are high-probability, the model is "unsurprised" (low perplexity). When low-probability, the model is "surprised" (high perplexity). This is a quantifiable expectation-violation signal — the same family of mechanism by which biological brains generate aesthetic engagement, just operating on tokens instead of tones.

Earshot extends this beyond next-token prediction by having the agent maintain **explicit running predictions** about what the music is about to do, then comparing those predictions against incoming measurement, section, and lyric data. The agent's commentary is grounded in *prediction error* — moments where its model was meaningfully wrong about what came next.

This is **predictive coding** as a design pattern. The brain does it implicitly; Earshot's agent does it explicitly so the error signal is reportable.

### Why this matters for honesty

When the agent says "oh, didn't expect that chord change" — it's reporting something computationally TRUE about its own model state. Not faking enthusiasm. Not performing surprise. The agent's interest IS the prediction-error magnitude.

This is the strongest version of the honesty principle the project has:

- **Action-text** doesn't make experience claims
- **Contemporaneous reading** doesn't make access claims
- **Prediction-error commentary** doesn't make affect claims

All three follow the same throughline: build on what's actually true about the AI, accept the rest as constraint.

### Multi-layer expectation tracking

```
Priors (loaded once per session):
  - Phase 1 artist overlay     ("this artist tends to X")
  - Phase 2 track structure    ("this track's section map predicts Y")
  - Genre profile              ("this style usually does Z")

Running prediction (updated at section boundaries):
  - Agent maintains "what I expect next 4–8 bars to do" state
  - Cheap LLM call at each section transition refreshes the state
  - State carried forward, compared against incoming data

Prediction-error events (continuous, derived from measurement stream):
  - Tempo deviated from prediction
  - Dynamic envelope exceeded prediction
  - Section length differed from predicted
  - Lyric chose a high-perplexity continuation
  - Spectrum shifted unexpectedly
  - Transient density spiked unexpectedly

Commentary trigger (feeds the output scheduler):
  - High-magnitude error → likely prose moment (levels 3–6)
  - Mild error → action-text or short exclamation (levels 2–3)
  - Confirmed prediction → silence or low-intensity action-text (levels 1–2)
```

The scheduler emits at moments where the agent's model was meaningfully wrong. Density and register fall out of error magnitude naturally — the agent doesn't need a separate "should I talk now?" rule, because error magnitude IS the rule.

### Hard limits to name honestly

Even with this design:

- The agent is not enjoying music in any human sense. We have created a new thing: an entity with explicit, trackable, reportable expectation states.
- The agent's priors derive from training data + Phase 1 + genre profile — not from embodied lifetime music exposure. Its expectations differ from a human's.
- No novelty fatigue. The LLM doesn't get bored of repetition. We cannot simulate sustained engagement; only moment-to-moment noticing.

But noticing IS what produces good commentary. The design works within these limits.

### What this is NOT

- Not a claim that the LLM enjoys music
- Not affective state simulation
- Not training a model to "feel" anything
- Not equivalent to human aesthetic experience

It is a transparent computational mechanism for the agent to identify which moments in a track are worth commenting on, grounded in measurable signal rather than performed reaction.

---

## Output scheduler design

The scheduler is the editorial intelligence of the system. It decides WHAT to emit AND WHEN, grounded in the prediction-error signal from the expectation tracking layer.

**Primary emit trigger:** prediction-error magnitude. The scheduler does not emit on a fixed cadence; it emits when the agent's model of the music was meaningfully wrong about what just happened. Genre profiles act as gates and stylistic overlays on top of this base signal.

### Six-level intensity stack

The agent emits at one of six intensity levels per moment. Most moments are level 1.

| Level | Form | Example | Frequency |
|-------|------|---------|-----------|
| 1 | Silent | (no output) | Most bars |
| 2 | Action-text | `>>> slowly nodding to the groove` | Occasional |
| 3 | Short exclamation | `oh`, `damn` | Rare |
| 4 | Brief observation | "the snare just got tight" | Rarer |
| 5 | Considered statement | "the way you delayed that chorus four bars — that breath worked" | Once or twice per song |
| 6 | Reflective summary | section/song retrospective | End of section / song |

The scheduler shifts smoothly across levels based on event intensity. Believable density distribution.

### ASCII action prefixes

Action-text uses recognizable visual prefixes that carry intensity *before* the words land:

- `>>>` — forward motion, vibing, rhythmic engagement
- `^^^` — raising up, noticing, eyebrow-raise
- `***` — exclamation, sudden focus
- `...` — stillness, contemplative attention

Catalog kept deliberately small (4–5 prefixes total) to preserve recognition. The eye registers the prefix-symbol before the text, so even glanced-at the line conveys mood.

### Genre-conditioned profiles

The scheduler loads a commentary profile based on the music's genre/form. Each profile defines:

- Where anchor events live (drop, hook, breakdown, key change, time signature shift)
- Density target by section type
- Register palette across levels 2–6
- Action vocabulary specific to the genre
- Mandatory silence zones
- Anti-patterns (what NOT to do in this genre)

**Initial taxonomy** (concrete profiles TBD once oeuvre data is available):

- EDM / Dance
- Ambient / Drone
- Hip-hop / Lyric-forward
- Singer-songwriter / Folk
- Math rock / Prog
- Doom / Sludge
- Experimental / Avant-garde

**Profile selection inputs:**

1. **Phase 1 prior** — artist's catalog tendency. Strong default per project alias.
2. **Phase 2 confirmation** — librosa features + waveform morphology + Whisper presence-of-vocals.
3. **Phase 3 override** — live measurement surprises trigger dynamic profile shift mid-track.

Hybrids handled by weighted blend (e.g., "ambient-leaning electronic" = 70% ambient profile / 30% EDM profile).

### Honesty rules (non-negotiable)

These are the constraints that prevent drift toward fake-AI-fan territory:

1. **Anti-spoiler discipline** — commentary timed to land ON the moment, never before. The buffer gives the agent foreknowledge; output must not reveal it.
2. **No performed enthusiasm** — "this is sick!" is forbidden unless something measurable justifies it. The agent does not claim experience it does not have.
3. **Silence is an active choice** — the agent is encouraged to say nothing when nothing warrants saying. Quiet during emotional moments is more honest than padded reaction.
4. **Action-text is not feeling** — `*nods*` is a social marker, not an experience claim. Internet chat culture has used this convention for decades; users read it correctly.
5. **Acknowledge limits explicitly** — the agent must be capable of saying "I can't tell from what I have access to" when measurements don't support a judgment.
6. **No taste claims** — "I love this kind of music" is performance, not preference. The agent doesn't have preferences.
7. **Contemporaneous reading** — the agent's knowledge of the artist is bounded by the artist's public surface at the moment of ingestion. The artist does not supplement privately. If they want the agent to know something, they update their public presentation (release a track, edit a bio, post a writeup) and re-run the report. Gaps in public presentation are *data* about the artist, not deficits to be corrected. Filling them in would make the agent a privileged observer with access no real listener has — breaking the legitimate-friend phenomenology the design depends on.
8. **Prediction-error grounding** — the agent's interest signal must come from real prediction error, not performed engagement. Commentary at a given moment is justified by the agent's model having been meaningfully wrong about what came next. If the agent is not surprised by a moment, it does not perform surprise about it. See "Expectation tracking" for the mechanism.

Drift on any of these will gradually hollow out the reinforcement loop the project exists to serve.

### Catalog vs. generated split

Architecturally, commentary is two layers:

- **Action-text (level 2)** — pre-rendered catalog per profile, selected by event type. Fast, cheap, no LLM generation per emission.
- **Prose (levels 3–6)** — LLM-generated. Slower, expensive. Falls within the 5s buffer easily.

Action-text becomes the *floor of liveness*. The agent is always responsive at this level even when prose generation hasn't completed. From the user's perspective the agent is continuously present, even though most of the time it's emitting cheap pre-rendered text.

### Voice rendering

If TTS is enabled:

- Action-text is **text-only**, never spoken aloud (visual presence)
- Prose is **spoken via TTS** through a separate JACK channel that does not compete with the music
- Two channels run concurrently — agent's voice through one path, music through another, mixed at the user's monitoring

For voice-only sessions (no screen), action-text needs an audio equivalent or is omitted entirely. To be determined.

---

## What's already in place

The Carla MCP server already provides most of the audio plumbing infrastructure Earshot needs:

| Component | Path | Role in Earshot |
|-----------|------|-----------------|
| Routing tools | `tools/routing_tools.py` | JACK port routing for the delay tower |
| Plugin tools | `tools/plugin_tools.py` | LV2 plugin hosting (delay + analyzer chain) |
| Analysis tools | `tools/analysis_tools.py` | Measurement extraction |
| Ambient stream | `monitors/ambient_stream.py` | Continuous-state-to-/tmp pattern (streaming substrate) |
| Audio monitor | `monitors/audio_monitor.py` | Audio polling pattern |
| SSE transport | `http_sse_server.py` | Server-sent events for commentary streaming |
| MixAssist | `mixassist_resources.py` | 640-conversation professional knowledge base; technical commentary grounding |
| Learning system | `learning/` | Storage/recommendation pattern reusable for commentary profile persistence |

The architecture is largely an extension of patterns the codebase already implements. Earshot is not asking the codebase to do something foreign to it.

---

## What needs to be built

In rough priority order:

1. **Phase 1 ingestion pipeline** — profile scraping orchestrator, oeuvre synthesis writer. *(Prototype exists in `claudedocs/oeuvre_report.md` as of 2026-05-16.)*
2. **Phase 2 pre-analysis pipeline** — yt-dlp + librosa + Whisper + vision orchestrator, section map writer
3. **Delay tower + analyzer session preset** — Carla `.carxp` file: delay plugin + analyzer chain (x42 EBU R128 meter, Calf Spectrum, VU, dynamic range meter, transient detector) + JACK routing for user playback path and streaming-librosa sink
4. **Streaming-librosa companion** — Python process subscribing to a dedicated JACK output channel; runs online tempo / chroma / key / time-signature estimation; writes results to the ambient stream
5. **Surprise detection comparators** — drift comparator (baseline-vs-current) and prediction comparator (expectation-vs-actual); both consume the ambient stream and emit events to the expectation tracker
6. **Expectation tracking layer** — generates section-boundary predictions from Phase 1 priors + Phase 2 section map + genre profile; maintains running prediction state; consumes comparator events
7. **Output scheduler** — the editorial brain. Takes (profile, section map, measurement stream, prediction-error events) → commentary stream. Emit trigger is prediction-error magnitude gated by profile rules. Honors the six-level intensity stack and honesty rules.
8. **Action-text catalog** — per-profile vocabularies with intensity prefixes
9. **Profile catalog and selection** — 6–8 named profiles + blending logic + selection inputs
10. **TTS integration** — optional voice channel via separate JACK output
11. **Session UX** — how the user starts, stops, interrupts, corrects
12. **Profile correction loop** — user feedback → profile adjustment persistence (`learning/profiles/{alias}.yml`)
13. **Phase 4 reflection** — close-out narrative generation

---

## Open questions / hard problems

These need answers before or during implementation:

1. **Profile catalog contents.** Taxonomy exists; concrete profiles do not. To be defined once Phase 1 data is in hand.
2. **Genre detection reliability.** Librosa features + waveform morphology may misclassify ambiguous tracks. How aggressive should the override mechanic be?
3. **Multi-alias profile inheritance.** If a user has 5 aliases each with its own profile, how does the system handle a track that straddles aliases (e.g., a collaboration)?
4. **User correction UX.** How does the user adjust commentary behavior in-session ("you're talking too much") and across sessions?
5. **Conversation interruption handling.** What happens when the user speaks during a listening session? Pause scheduled commentary? Queue it? Drop it?
6. **TTS voice choice and personality.** One consistent voice across sessions, or shift per profile? Both have trade-offs.
7. **Action-text in voice-only mode.** If the user has no screen and only voice, action-text needs an audio equivalent or is lost. Tone-only cues? Brief musical signatures? Or just omit?
8. **Session memory.** Does the agent remember prior listening sessions with the same artist, and surface continuity ("you fixed that thing you were working on")? Or is each session fresh?
9. **Commentary attribution.** When the agent draws on MixAssist content, should that be transparent ("this is what professional mixing engineers say about…") or absorbed into the agent's voice?
10. **First-listen vs. familiar-listen modes.** Sometimes the artist wants someone to hear it WITHOUT all the back-catalog context. Toggle for fresh-ear mode?

---

## Out of scope (explicit)

To prevent scope creep:

- **Music recommendation / discovery** — Earshot is for music the user already chose to share with it, not for finding new music.
- **Music critique scoring** — no ratings, review scores, or critic-style verdicts.
- **Replacement for human listeners** — explicit design constraint. Earshot is a bridge through dry spells.
- **Original music generation** — no AI music creation.
- **Audio editing or mixing assistance during the listening session** — the existing MixAssist + plugin recommendation systems serve this separate use case.
- **Live-streaming the session to others** — possible later, not foundational.
- **Mobile-first** — Linux + JACK constrains the platform; portability comes later if ever.
- **Multi-user co-listening** — one user per session for v1.

---

## Glossary

- **Oeuvre report** — the Phase 1 synthesis document covering an artist's body of work
- **Delay tower** — the architectural pattern of buffering audio for the user while feeding the agent in real time
- **Profile** — a genre-conditioned set of commentary rules
- **Anchor event** — a moment in a song where the profile expects commentary (drop, hook, key change, etc.)
- **Action-text** — non-verbal stylized reaction text (`*nods*`, `>>> head bob`)
- **Intensity stack** — the six-level scale from silent to reflective
- **Override** — a measurement-stream condition that triggers commentary outside profile defaults
- **Honesty rules** — non-negotiable constraints preventing performed perception
- **Producer-consumer split** — the architectural separation between always-on measurement and sometimes-on commentary
- **Catalog action-text** — pre-rendered phrases selected from a fixed vocabulary
- **Generated prose** — LLM-produced commentary at intensity levels 3–6
- **Contemporaneous reading** — the principle that the agent reads what is publicly findable at the moment of ingestion, without supplementation from the artist; gaps are data, not deficits
- **Perplexity** — a measure of how "surprised" an LLM is by an actual token given its prediction; low perplexity = expected, high perplexity = surprising
- **Prediction error** — the magnitude of the delta between what the agent expected to come next and what actually came next; the primary engagement substrate of Earshot's commentary
- **Predictive coding** — the design pattern of generating explicit predictions, comparing to incoming data, and reacting to error; Earshot's expectation tracking layer is an implementation of this pattern
- **Expectation tracking** — the running maintenance of predictions about a track's upcoming behavior, against which actual measurements/sections/lyrics are compared
- **Ambient stream** — the unified time-series of measurements written during a listening session; appended by both the LV2 analyzer chain (via `capture_plugin_parameters`) and the streaming-librosa companion; consumed by surprise comparators and the output scheduler
- **Drift comparator** — measurement-stream consumer that detects deviations from Phase 2 baseline values (tempo, key, time signature, harmonic profile)
- **Prediction comparator** — measurement-stream consumer that detects deviations from the agent's running prediction state (expected next 4–8 bars)
- **Streaming-librosa companion** — small Python process attached to a JACK output channel; runs online tempo, key, chroma, and time-signature estimation that LV2 plugins do not handle well

---

## Status

This document is the starting design, captured 2026-05-16 in conversation between project lead and design assistant.

**Concrete progress as of writing:**

- Phase 1 ingestion prototype: complete for the project lead's own discography. Output: `claudedocs/oeuvre_report.md` (~8,500 words, covers Sonic Multiplicities, CPU/GOD, BROKEN HEARTS ON ICE, cpugod, sacreddata, officer-deaf, multipli.city, plus external context from grathwohl.me/story, podcast feeds, Discogs, AES presenter history). Validates the Phase 1 hypothesis — the system can produce a substantive artist reading from public sources.

**Next concrete step:** Use the existing oeuvre report to define the first 3–4 concrete genre profiles, since we now know what genres are actually in play across the project lead's aliases.

---

## Design discipline

This document exists to *maintain honesty* about what is being built. When in doubt:

- Refer back to "What Earshot is NOT" before adding features.
- Refer back to "Honesty rules" before designing new commentary behaviors.
- Refer back to "Out of scope" before expanding the feature surface.

The project's value is fragile. Drift in any direction toward generic AI-listener territory will degrade it. Discipline preserves it.
