"""Scheduler — comparator events → commentary queue.

The editorial state machine. Drains the session's `EventQueue` (drift /
prediction-error / boundary-approaching), maps each event to an
IntensityLevel via the active profile's thresholds, applies profile
gating (silence zones, density multipliers) + anti-spoiler timing, then:

  - **Level SILENT**: emits nothing.
  - **Level ACTION_TEXT**: composes a line from the profile's catalog,
    pushes a CommentaryEmission with `ts_user_clock_ms` = the moment
    the prepared commentary should land for the user.
  - **Levels EXCLAMATION..REFLECTION**: pushes a ProseRequest for the
    orchestrator to fulfill via `earshot_submit_commentary`.
  - **BoundaryApproachingEvent**: always becomes a ProseRequest (orchestrator
    refreshes predictions; never user-facing — see honesty discipline).

The scheduler enforces three editorial rules in code:

  1. **Anti-spoiler timing** — every Emission's `ts_user_clock_ms` is
     `now_ms + delay_buffer_ms`. The delay tower's buffer means
     measurements come in at T+0 (agent's clock) but the user hears at
     T+delay_buffer; commentary scheduled to ts_user_clock lands ON the
     moment from the user's view.
  2. **Honesty validation** — any scheduler-composed content passes
     through `HonestyValidator.validate()` before queueing. Failures
     drop to SILENT for that event, logged.
  3. **Silence-as-default** — events whose normalized score doesn't
     reach the profile's `action_text` threshold produce no output.
     This is the README's explicit silence discipline.

Lifecycle:
    sch = Scheduler(event_queue, commentary_queue, profile, baseline,
                    delay_buffer_ms=5000)
    await sch.start()
    # ... session runs ...
    await sch.stop()
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import Optional

from earshot.ambient_stream import now_ms
from earshot.comparators.events import (
    BoundaryApproachingEvent,
    ComparatorEvent,
    Dimension,
    DriftEvent,
    EventQueue,
    PredictionErrorEvent,
    StructuralEvent,
    domain_for,
)
from earshot.honesty import DEFAULT_VALIDATOR, HonestyValidator
from earshot.phase2_baseline import Phase2Baseline
from earshot.profiles import Profile
from earshot.scheduler.commentary import (
    CommentaryEmission,
    CommentaryQueue,
    ProseRequest,
)
from earshot.scheduler.intensity import IntensityLevel, score_to_level

logger = logging.getLogger(__name__)


class Scheduler:
    """The editorial state machine. One per session."""

    def __init__(
        self,
        *,
        event_queue: EventQueue,
        commentary_queue: CommentaryQueue,
        profile: Profile,
        baseline: Phase2Baseline,
        playback_start_ms: int,
        delay_buffer_ms: int = 5000,
        validator: Optional[HonestyValidator] = None,
        artist_context: Optional[str] = None,
    ):
        if delay_buffer_ms < 0:
            raise ValueError(f"delay_buffer_ms must be ≥ 0, got {delay_buffer_ms}")
        self.event_queue = event_queue
        self.commentary_queue = commentary_queue
        self.profile = profile
        self.baseline = baseline
        self.playback_start_ms = int(playback_start_ms)
        self.delay_buffer_ms = int(delay_buffer_ms)
        self.validator = validator or DEFAULT_VALIDATOR
        # Static artist-background digest (from the oeuvre deep-research report),
        # attached to every prose request so the orchestrator can ground its
        # reaction in who made the track. None when no oeuvre report exists.
        self.artist_context = artist_context

        # Global emission cooldown — bounds the USER-FACING rate so a noisy
        # comparator stream (e.g. continuous dims drifting against a global-mean
        # baseline) doesn't bury the listener. After a drift/prediction line is
        # emitted, further ones are suppressed for emission_cooldown_ms UNLESS
        # the new event's score clears cooldown_override_ratio × the last emitted
        # score — so a genuinely bigger moment still interrupts. Structural
        # (section) + boundary events bypass this; they're sparse and important.
        self.emission_cooldown_ms = 7000
        self.cooldown_override_ratio = 1.8
        self._last_emit_ms = 0
        self._last_emit_score = 0.0

        # Per-event-type history of recent action-text choices, used to
        # avoid repeating the same phrase within
        # `profile.action_text_repeat_window_seconds`. Each entry is
        # `(ts_ms, (prefix, text))`; we prune expired entries at lookup
        # time so the suppression is actually time-windowed (the configured
        # contract) rather than count-windowed.
        self._recent_choices: dict[str, list[tuple[int, tuple[str, str]]]] = {}

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {
            "events_in": 0,
            "emissions_out": 0,
            "prose_requests_out": 0,
            "silent": 0,
            "suppressed_silence_zone": 0,
            "suppressed_cooldown": 0,
            "suppressed_honesty": 0,
            "suppressed_no_catalog": 0,
            "by_level": {l.name: 0 for l in IntensityLevel},
        }

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"scheduler_{id(self):x}")
        logger.info(
            "Scheduler started: profile=%s delay_buffer=%dms",
            self.profile.name, self.delay_buffer_ms,
        )

    async def stop(self) -> None:
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
        logger.info("Scheduler stopped: %s", self._stats)

    def stats(self) -> dict:
        return {
            **self._stats,
            "running": self._running,
            "profile": self.profile.name,
            "commentary_queue": self.commentary_queue.stats(),
        }

    # ------------------------------------------------------------------
    async def _loop(self) -> None:
        while self._running:
            try:
                event = await self.event_queue.pop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Scheduler: event_queue.pop raised: %s", e)
                await asyncio.sleep(0.1)
                continue
            self._stats["events_in"] += 1
            try:
                await self._process(event)
            except Exception as e:
                logger.warning("Scheduler: process failed for event %r: %s", event, e)

    # ------------------------------------------------------------------
    async def _process(self, event: ComparatorEvent) -> None:
        # Boundary events always route to the orchestrator — no user-facing
        # emission, ever (anti-spoiler discipline).
        if isinstance(event, BoundaryApproachingEvent):
            await self._emit_prose_request(
                intensity=IntensityLevel.OBSERVATION,  # arbitrary; orchestrator-routed
                source_event=event,
                context={
                    "kind": "boundary_approaching",
                    "upcoming_section_index": event.upcoming_section_index,
                    "boundary_track_time_s": event.boundary_track_time_s,
                    "eta_ms": event.eta_ms,
                },
                ts_target_user_clock_ms=event.ts_ms + self.delay_buffer_ms,
            )
            return

        # Structural build/release events (music-domain). No per-sample score
        # path (they'd otherwise fall to SILENT via _normalized_score); intensity
        # is set by the energy jump. Hybrid-by-intensity: a modest section change
        # takes the fast catalog line ("this section opens up"); a big jump
        # escalates to an LLM-composed reaction.
        if isinstance(event, StructuralEvent):
            score = abs(event.energy_delta_db) / 6.0
            ts_user_clock = event.ts_ms + self.delay_buffer_ms
            if abs(event.energy_delta_db) >= 6.0:
                level = IntensityLevel.CONSIDERED
                self._stats["by_level"][level.name] += 1
                await self._emit_prose_request(
                    intensity=level,
                    source_event=event,
                    context=self._prose_context(
                        event, score, event.track_time_s, None,
                        domain="music", warrant=max(0.0, score - 1.0),
                    ),
                    ts_target_user_clock_ms=ts_user_clock,
                )
            else:
                level = IntensityLevel.ACTION_TEXT
                self._stats["by_level"][level.name] += 1
                await self._emit_action_text(
                    event=event, score=score, level=level,
                    ts_user_clock=ts_user_clock, track_time_s=event.track_time_s,
                )
            return

        # Drift + Prediction-error events compute a normalized score, then
        # gate through profile rules → level selection → emit or suppress.
        score = self._normalized_score(event)
        track_time_s = self._track_time(event.ts_ms)

        # Silence-zone check (profile-defined windows where we stay quiet).
        if self.profile.in_silence_zone(track_time_s):
            self._stats["suppressed_silence_zone"] += 1
            self._stats["by_level"]["SILENT"] += 1
            return

        # Density gate — multiply score by the section's density factor.
        sec = self.baseline.section_at(track_time_s)
        sec_label = "default"  # Phase H profiles may key by section labels
        density = self.profile.density_for(sec_label)
        score *= density

        level = score_to_level(score, self.profile.thresholds)
        self._stats["by_level"][level.name] += 1
        if level == IntensityLevel.SILENT:
            self._stats["silent"] += 1
            return

        # Global emission cooldown (drift/prediction only — structural + boundary
        # events returned earlier and intentionally bypass this). Suppress unless
        # the cooldown has elapsed OR this event is a notably bigger moment than
        # the last thing we said. This is what keeps a noisy comparator stream
        # from burying the listener with ~one line every couple of seconds.
        since_last = event.ts_ms - self._last_emit_ms
        if (self._last_emit_ms
                and since_last < self.emission_cooldown_ms
                and score < self._last_emit_score * self.cooldown_override_ratio):
            self._stats["suppressed_cooldown"] += 1
            return
        self._last_emit_ms = event.ts_ms
        self._last_emit_score = score

        ts_user_clock = event.ts_ms + self.delay_buffer_ms
        domain = self._event_domain(event)
        warrant = max(0.0, score - 1.0)   # score is already magnitude/threshold

        if level == IntensityLevel.ACTION_TEXT:
            await self._emit_action_text(
                event=event, score=score, level=level,
                ts_user_clock=ts_user_clock, track_time_s=track_time_s,
            )
        else:
            # Levels 3-6: prose path — orchestrator fulfills.
            await self._emit_prose_request(
                intensity=level,
                source_event=event,
                context=self._prose_context(event, score, track_time_s, sec,
                                            domain=domain, warrant=warrant),
                ts_target_user_clock_ms=ts_user_clock,
            )

    # ------------------------------------------------------------------
    def _normalized_score(self, event: ComparatorEvent) -> float:
        """Convert event-specific magnitude into "magnitudes over threshold".

        For PredictionErrorEvent the comparator already supplied a
        normalized `score` (magnitude / threshold). For DriftEvent we
        derive: magnitude / threshold. Categorical drift (key) is
        treated as score=2.0 (well above ACTION_TEXT, well below
        REFLECTION) since the magnitude is always 1.0 and there's no
        meaningful threshold-ratio.
        """
        if isinstance(event, PredictionErrorEvent):
            return float(event.score)
        if isinstance(event, DriftEvent):
            if event.dimension == Dimension.KEY:
                return 2.0
            if event.threshold and float(event.threshold) > 0:
                return float(event.magnitude) / float(event.threshold)
            return float(event.magnitude)
        return 0.0

    def _track_time(self, ts_ms: int) -> float:
        return (ts_ms - self.playback_start_ms) / 1000.0

    # ------------------------------------------------------------------
    async def _emit_action_text(
        self,
        *,
        event: ComparatorEvent,
        score: float,
        level: IntensityLevel,
        ts_user_clock: int,
        track_time_s: float,
    ) -> None:
        """Compose a level-2 emission from the profile's catalog."""
        catalog_key = self._catalog_key_for(event)
        entries = self.profile.catalog_for(catalog_key)
        if not entries:
            self._stats["suppressed_no_catalog"] += 1
            logger.debug(
                "Scheduler: no catalog entries for %r (event=%r); silent",
                catalog_key, event,
            )
            return

        choice = self._select_action_text(catalog_key, entries)
        if choice is None:
            # All entries exhausted within the repeat window — stay silent
            # rather than re-use one. The window expires; the catalog
            # cycles eventually.
            self._stats["suppressed_no_catalog"] += 1
            return

        content = f"{choice['prefix']} {choice['text']}"
        # Belt-and-suspenders honesty check — the catalog SHOULD be clean,
        # but if a future profile slips in a violation, catch it.
        result = self.validator.validate(content)
        if not result.ok:
            self._stats["suppressed_honesty"] += 1
            logger.warning(
                "Scheduler: catalog entry failed honesty check: %r -- %s",
                content, result.reasons,
            )
            return

        emission = CommentaryEmission(
            level=level,
            content=content,
            ts_user_clock_ms=ts_user_clock,
            source_event_id=self._event_id(event),
            dimensions=self._dimensions_of(event),
            score=round(score, 4),
            created_at_ms=now_ms(),
        )
        await self.commentary_queue.push(emission)
        self._stats["emissions_out"] += 1
        logger.info(
            "Scheduler emitted action-text: %r (score=%.2f track_time=%.1fs)",
            content, score, track_time_s,
        )

    async def _emit_prose_request(
        self,
        *,
        intensity: IntensityLevel,
        source_event: ComparatorEvent,
        context: dict,
        ts_target_user_clock_ms: int,
    ) -> None:
        request = ProseRequest(
            request_id=str(uuid.uuid4()),
            intensity_target=intensity,
            source_event_id=self._event_id(source_event),
            context=context,
            ts_target_user_clock_ms=ts_target_user_clock_ms,
            created_at_ms=now_ms(),
        )
        await self.commentary_queue.push(request)
        self._stats["prose_requests_out"] += 1
        logger.info(
            "Scheduler emitted prose request: intensity=%s for event=%s",
            intensity.name, type(source_event).__name__,
        )

    # ------------------------------------------------------------------
    def _select_action_text(
        self, catalog_key: str, entries: list[dict]
    ) -> Optional[dict]:
        """Pick an entry that hasn't been used within the profile's
        configured repeat window for this catalog_key.

        Suppression is *time*-windowed (the documented contract), not
        count-windowed. Entries older than
        `profile.action_text_repeat_window_seconds` are pruned at lookup
        time and become eligible again. Returns None when every entry
        for this key has been used within the window — the scheduler
        then suppresses rather than re-using.
        """
        window_ms = self.profile.action_text_repeat_window_seconds * 1000
        wall = now_ms()
        cutoff = wall - window_ms
        recent = self._recent_choices.setdefault(catalog_key, [])
        # Prune expired entries in-place so subsequent calls see them
        # as available again.
        recent[:] = [(ts, k) for ts, k in recent if ts >= cutoff]
        used_keys = {k for _, k in recent}
        available = [e for e in entries if (e["prefix"], e["text"]) not in used_keys]
        if not available:
            return None
        choice = random.choice(available)
        recent.append((wall, (choice["prefix"], choice["text"])))
        return choice

    def _catalog_key_for(self, event: ComparatorEvent) -> str:
        """Map an event to its catalog key (e.g. 'drift_tempo').

        Falls back to 'generic' for events the catalog doesn't recognize.
        """
        if isinstance(event, DriftEvent):
            dim = event.dimension.value if hasattr(event.dimension, "value") else str(event.dimension)
            return f"drift_{dim}"
        if isinstance(event, PredictionErrorEvent):
            if event.dimensions:
                dim = event.dimensions[0]
                dim_val = dim.value if hasattr(dim, "value") else str(dim)
                return f"prediction_error_{dim_val}"
        if isinstance(event, StructuralEvent):
            return event.kind.replace("section_", "structural_")  # structural_build|structural_release
        return "generic"

    def _event_domain(self, event: ComparatorEvent) -> str:
        """"mix" or "music" for an event — routes phrasing + grounding source."""
        if isinstance(event, StructuralEvent):
            return "music"
        if isinstance(event, DriftEvent):
            return domain_for(event.dimension)
        if isinstance(event, PredictionErrorEvent):
            # The prediction comparator emits one dimension per event today, so
            # routing by dimensions[0] is exact. (If a future change batches
            # mixed-domain dimensions into one event, this would route by the
            # first — keep PredictionErrorEvent.dimensions homogeneous in domain.)
            return domain_for(event.dimensions[0]) if event.dimensions else "mix"
        return "mix"

    def _music_event_phrase(self, event: ComparatorEvent) -> str:
        """Short, factual descriptor of a MUSIC-domain event for the orchestrator.
        Deterministic and enthusiasm-free — the LLM adds the warranted reaction."""
        if isinstance(event, StructuralEvent):
            verb = "opened up" if event.kind == "section_build" else "pulled back"
            return f"section {verb} ({event.energy_delta_db:+.0f} dB vs the last)"
        if isinstance(event, DriftEvent):
            dim = event.dimension
            up = None
            try:
                up = float(event.current) > float(event.baseline)
            except (TypeError, ValueError):
                up = None
            if dim == Dimension.KEY:
                return "the harmony shifted"
            if dim == Dimension.TEMPO:
                return "the pulse pushed" if up else "the pulse eased"
            if dim == Dimension.ONSET_DENSITY:
                return "the rhythm thickened" if up else "the rhythm thinned"
            if dim == Dimension.CHORD_CHANGE_RATE:
                return "the changes sped up" if up else "the harmony settled"
            if dim == Dimension.HARMONIC_TENSION:
                return "tension rising" if up else "it resolved"
            return f"{dim.value} moved"
        if isinstance(event, PredictionErrorEvent) and event.dimensions:
            return f"{event.dimensions[0].value} diverged from what I expected"
        return "something shifted in the music"

    def _dimensions_of(self, event: ComparatorEvent) -> list[str]:
        if isinstance(event, DriftEvent):
            return [event.dimension.value if hasattr(event.dimension, "value") else str(event.dimension)]
        if isinstance(event, PredictionErrorEvent):
            return [
                (d.value if hasattr(d, "value") else str(d))
                for d in event.dimensions
            ]
        return []

    def _event_id(self, event: ComparatorEvent) -> str:
        """Stable-ish id for source-event reference. We don't store IDs on
        events themselves (they're frozen dataclasses); use ts_ms+type."""
        return f"{type(event).__name__}_{event.ts_ms}"

    def _prose_context(
        self, event: ComparatorEvent, score: float, track_time_s: float,
        sec: Optional[dict], *, domain: str = "mix", warrant: float = 0.0,
    ) -> dict:
        """Build the context dict an orchestrator needs to compose prose.

        `domain` ("mix"|"music") selects the grounding source: MIX events carry
        MixAssist hints; MUSIC events carry a factual music-event descriptor and
        NO MixAssist (it's a mixing dataset). `warrant` is echoed so the
        orchestrator can pass it to earshot_submit_commentary for the honesty
        escape valve."""
        ctx = {
            "event_type": type(event).__name__,
            "track_time_s": round(track_time_s, 3),
            "score": round(score, 4),
            "section": sec,
            "domain": domain,
            "warrant": round(warrant, 3),
        }
        if isinstance(event, DriftEvent):
            ctx.update({
                "dimension": event.dimension.value,
                "magnitude": event.magnitude,
                "baseline": event.baseline,
                "current": event.current,
                "threshold": event.threshold,
                # Calibration EMA-tracked offset that was subtracted from raw
                # drift to get magnitude (None until comparators emit it).
                "calibration_offset": event.calibration_offset,
            })
        elif isinstance(event, PredictionErrorEvent):
            ctx.update({
                "dimensions": [
                    (d.value if hasattr(d, "value") else str(d))
                    for d in event.dimensions
                ],
                "expected": event.expected,
                "actual": event.actual,
                "section_index": event.section_index,
            })
        # Grounding source by domain:
        #  - MIX  -> MixAssist hints (the orchestrator queries those resources
        #    before composing, grounding in the 640-conversation mixing dataset;
        #    reference as "in my experience", not "according to the dataset").
        #  - MUSIC -> a factual music-event descriptor; NO MixAssist (it's a
        #    mixing dataset, not music-content), the LLM composes the reaction.
        if domain == "mix":
            ctx["mixassist_hints"] = _mixassist_hints_for(event)
        else:
            ctx["music_event"] = self._music_event_phrase(event)
        # Lyric + artist grounding (both domains). `lyric` is anti-spoiler-safe:
        # only words already sung at track_time_s, which is what the listener
        # hears when this commentary lands after the delay tower. Omitted when
        # instrumental / no word timing / no oeuvre report, to keep context lean.
        lyric = self.baseline.recent_lyrics(track_time_s)
        if lyric:
            ctx["lyric"] = lyric
        if self.artist_context:
            ctx["artist_context"] = self.artist_context
        return ctx


def _mixassist_hints_for(event: "ComparatorEvent") -> list:
    """Map a comparator event to MixAssist resource URIs that ground the
    eventual prose response in professional mixing knowledge.

    Hierarchy (per project CLAUDE.md token-budget guide):
      1. mixassist://advice/{topic}/top5  — curated best practices (<3K tokens)
      2. mixassist://search?q=...          — keyword search, top 10 (<5K tokens)
      Both stay well under the orchestrator's per-resource budget.

    Topics: drums, guitars, bass, vocals, keys, overall_mix.
    Dimensions that don't have a clean topic mapping fall back to search
    against the dimension keyword.
    """
    if isinstance(event, BoundaryApproachingEvent):
        return [
            "mixassist://search?q=transition",
            "mixassist://search?q=arrangement",
        ]
    # DriftEvent + PredictionErrorEvent share the dimension axis. Reduce to
    # a single primary Dimension for hint lookup.
    primary: Optional[Dimension] = None
    if isinstance(event, DriftEvent):
        primary = event.dimension
    elif isinstance(event, PredictionErrorEvent) and event.dimensions:
        primary = event.dimensions[0]
    if primary is None:
        return ["mixassist://advice/overall_mix/top5"]
    # Dimension → (topic, extra search terms). Topic queries the curated
    # top-5 advice for that topic; search terms broaden coverage when the
    # topic page might not address the specific axis.
    DIM_HINTS = {
        Dimension.DYNAMIC_ENVELOPE: (
            "overall_mix",
            ["dynamics", "compression"],
        ),
        Dimension.LUFS_INTEGRATED: (
            "overall_mix",
            ["loudness", "headroom"],
        ),
        Dimension.SPECTRAL_CENTROID: (
            "overall_mix",
            ["eq", "brightness", "tone"],
        ),
        Dimension.TEMPO: (
            None,  # no topic — tempo isn't a per-instrument concept
            ["tempo", "timing", "feel"],
        ),
        Dimension.KEY: (
            None,
            ["harmony", "key", "modulation"],
        ),
        Dimension.ONSET_DENSITY: (
            None,
            ["density", "arrangement", "groove"],
        ),
    }
    topic, terms = DIM_HINTS.get(primary, (None, []))
    hints: list = []
    if topic is not None:
        hints.append(f"mixassist://advice/{topic}/top5")
    for term in terms:
        hints.append(f"mixassist://search?q={term}")
    return hints
