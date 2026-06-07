"""Event schemas + queue infrastructure for Phase F comparators.

Two event types reach the scheduler:

  - **DriftEvent** — emitted by the drift comparator when a measurement
    diverges from its Phase-2 baseline value beyond threshold.
  - **PredictionErrorEvent** — emitted by the prediction comparator when
    incoming measurement diverges from the agent's expectation state.

Both share the same time axis (wall-clock `ts_ms`) and carry the
`track_time_s` derived from the session's `playback_start_ms`, so the
scheduler can apply the anti-spoiler discipline ("commentary lands ON
the moment from the user's perspective") via the delay tower's buffer.

Schema is `dataclass`-based rather than dict to make the contract
between comparator and scheduler typed; `to_dict()` returns a JSON-
serializable form for the ambient stream log + the MCP queue endpoint.

Postmortem rule #19 — typed schemas + a single `EventQueue` interface
mean the scheduler never sees ambiguous "is this drift or prediction?"
states; the discriminator is the dataclass type, not a string field.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Dimensions
# ----------------------------------------------------------------------

class Dimension(str, Enum):
    """Measurement dimensions tracked by comparators.

    Values are stable strings (subclassing str) so they serialize cleanly
    via dataclasses.asdict() — no custom JSON encoder required.
    """
    TEMPO = "tempo"
    KEY = "key"
    DYNAMIC_ENVELOPE = "dynamic_envelope"  # RMS / LUFS short-term
    LUFS_INTEGRATED = "lufs_integrated"
    SPECTRAL_CENTROID = "spectral_centroid"
    ONSET_DENSITY = "onset_density"
    CHORD_CHANGE_RATE = "chord_change_rate"   # chroma movement vs previous frame
    HARMONIC_TENSION = "harmonic_tension"     # chroma ambiguity (entropy), 0..1


# Which dimensions are mix-engineering metrics vs musical-content metrics.
# The scheduler routes by domain: MIX -> MixAssist-grounded phrasing,
# MUSIC -> orchestrator-LLM reaction (MixAssist is a mixing dataset, not
# music-content). Structural events (section build/release) are MUSIC.
DIMENSION_DOMAIN: dict = {
    Dimension.DYNAMIC_ENVELOPE: "mix",
    Dimension.LUFS_INTEGRATED: "mix",
    Dimension.SPECTRAL_CENTROID: "mix",
    Dimension.TEMPO: "music",
    Dimension.KEY: "music",
    Dimension.ONSET_DENSITY: "music",
    Dimension.CHORD_CHANGE_RATE: "music",
    Dimension.HARMONIC_TENSION: "music",
}


def domain_for(dimension) -> str:
    """Return "mix" or "music" for a Dimension. Defaults to "mix" (fail-safe:
    an unknown dimension keeps current MixAssist behavior) with a warning."""
    d = DIMENSION_DOMAIN.get(dimension)
    if d is None:
        logger.warning("domain_for: unknown dimension %r, defaulting to 'mix'", dimension)
        return "mix"
    return d


# ----------------------------------------------------------------------
# Threshold configuration
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class ComparatorThresholds:
    """Per-dimension drift-detection thresholds.

    These are absolute differences (current vs baseline). When the
    rolling-windowed measurement exceeds threshold the drift event fires.

    Defaults from workflow_earshot_completion.md Phase F2:
      tempo:               ±3 BPM
      key:                 categorical — any tonic/mode change fires
      dynamic_envelope:    ±6 dB on RMS
      lufs_integrated:     ±3 LU
      spectral_centroid:   ±400 Hz (about 1/3 octave at 2 kHz)
      onset_density:       ±50% relative deviation

    `key_change_fires` is the categorical knob — set False to suppress
    key-drift events when working on tracks with deliberate modulations.
    """
    tempo_bpm: float = 3.0
    dynamic_envelope_db: float = 6.0
    lufs_integrated_lu: float = 3.0
    spectral_centroid_hz: float = 400.0
    onset_density_relative: float = 0.5  # 50% deviation
    chord_change_rate_abs: float = 0.20  # absolute change in the 0..1 rate
    harmonic_tension_abs: float = 0.15   # absolute change in 0..1 entropy
    key_change_fires: bool = True

    def threshold_for(self, dimension: Dimension) -> Optional[float]:
        """Numeric threshold for a dimension; None if categorical (e.g. key)."""
        if dimension == Dimension.TEMPO:
            return self.tempo_bpm
        if dimension == Dimension.DYNAMIC_ENVELOPE:
            return self.dynamic_envelope_db
        if dimension == Dimension.LUFS_INTEGRATED:
            return self.lufs_integrated_lu
        if dimension == Dimension.SPECTRAL_CENTROID:
            return self.spectral_centroid_hz
        if dimension == Dimension.ONSET_DENSITY:
            return self.onset_density_relative
        if dimension == Dimension.CHORD_CHANGE_RATE:
            return self.chord_change_rate_abs
        if dimension == Dimension.HARMONIC_TENSION:
            return self.harmonic_tension_abs
        return None  # key is categorical


# ----------------------------------------------------------------------
# Event types
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class DriftEvent:
    """A measurement has diverged from its Phase 2 baseline.

    The scheduler (Phase I) reads `magnitude` to decide intensity-stack
    level — small drift = action-text level 2, large drift = prose 3-6.

    All fields are required by construction (postmortem rule #19: a
    zero-magnitude event for an arbitrary dimension would be meaningless).
    The `event` discriminator is the only init=False field — set on the
    class for callers that route by event-type-string instead of `isinstance`.
    """
    dimension: Dimension
    magnitude: float              # calibrated |current - baseline - offset|
    baseline: Any                 # type depends on dimension (float | str)
    current: Any                  # same shape as baseline
    threshold: Any                # the threshold that was exceeded
    ts_ms: int
    track_time_s: float
    window_size_samples: int      # how many samples in the rolling estimate
    # Running EMA of (current - baseline), subtracted from raw drift to
    # cancel out steady-state offsets (e.g. playback volume attenuation).
    # None by default for comparators that don't do calibration and for
    # the categorical KEY dimension where it doesn't apply.
    calibration_offset: Optional[float] = None
    # The last value this comparator REPORTED for this dimension (the running
    # "current state"), so prose narrates previous -> current instead of
    # re-quoting the static baseline every time. None on the first report.
    previous: Any = None
    event: str = field(default="drift", init=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict() preserves enum→str via str-subclass; just ensure JSON-safe
        d["dimension"] = self.dimension.value if isinstance(self.dimension, Enum) else str(self.dimension)
        return d


@dataclass(frozen=True)
class BoundaryApproachingEvent:
    """A section boundary is coming up — orchestrator should refresh predictions.

    Phase G's boundary detector fires this ~lookahead_seconds before the
    actual boundary so the orchestrator has time to do an LLM round-trip,
    compose predictions, and push them via `earshot_refresh_expectations`
    before the prediction comparator starts evaluating the new section.

    The scheduler (Phase I) routes this event to the orchestrator's commentary
    queue; the orchestrator handles it by calling `earshot_refresh_expectations`.
    If the predictions arrive after the boundary, the tracker's `confidence_at`
    score decays — late predictions are still used but weighted lower.

    All fields are required by construction (postmortem rule #19); an event
    with `upcoming_section_index=-1` would mean "no upcoming section" which
    is contradictory.
    """
    upcoming_section_index: int
    boundary_track_time_s: float    # when the next section begins
    eta_ms: int                     # how many wall-clock ms until the boundary
    ts_ms: int                      # wall-clock when this event fired
    track_time_s: float             # current track time when fired
    event: str = field(default="boundary_approaching", init=False)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PredictionErrorEvent:
    """A measurement violated the agent's running expectation.

    This is the engagement substrate of the system per the README §
    "Expectation tracking" — the agent's interest IS the prediction-error
    magnitude. The scheduler uses `score` as the primary intensity gate.

    `dimensions` is plural because a single prediction window can be wrong
    along multiple axes simultaneously (e.g. tempo went up AND dynamics
    dropped). `score` is the weighted-sum across them.

    Required by construction: dimensions, score, expected, actual, ts_ms,
    track_time_s. `section_index` is genuinely optional — emissions can
    happen outside the section_map's range when playback overshoots.
    """
    dimensions: list[Dimension]
    score: float                 # weighted-sum across dimensions
    expected: dict               # {dim_name: value}
    actual: dict
    ts_ms: int
    track_time_s: float
    section_index: Optional[int] = None  # None when no section context
    event: str = field(default="prediction_error", init=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dimensions"] = [
            (dim.value if isinstance(dim, Enum) else str(dim))
            for dim in self.dimensions
        ]
        # Enum values inside expected/actual dicts are stringified.
        d["expected"] = {k: _coerce(v) for k, v in self.expected.items()}
        d["actual"] = {k: _coerce(v) for k, v in self.actual.items()}
        return d


@dataclass(frozen=True)
class StructuralEvent:
    """A section transition with a measured energy change — build or release.

    Emitted by the boundary detector when the section just entered differs in
    mean RMS from the prior one beyond threshold. Always MUSIC-domain. Fires
    AT the crossing (ts anchored to the new section's start) so the reaction
    lands on the user's clock as the section begins (anti-spoiler-correct).
    """
    kind: str                    # "section_build" | "section_release"
    from_section: int
    to_section: int
    energy_delta_db: float       # to_section mean RMS minus from_section mean RMS
    ts_ms: int
    track_time_s: float
    event: str = field(default="structural", init=False)

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    return v


# Union type used by the queue + scheduler.
ComparatorEvent = Union[DriftEvent, PredictionErrorEvent, BoundaryApproachingEvent, StructuralEvent]


# ----------------------------------------------------------------------
# Event queue
# ----------------------------------------------------------------------

class EventQueue:
    """Async queue for comparator events, drained by the scheduler.

    Thin wrapper around `asyncio.Queue` so we can add per-session
    diagnostics (counts by type, oldest unread timestamp) without the
    callers caring about the underlying implementation. Phase J creates
    one of these per session; F1/F3 push; Phase I pops.
    """

    def __init__(self, maxsize: int = 0):
        self._q: asyncio.Queue[ComparatorEvent] = asyncio.Queue(maxsize=maxsize)
        self._pushed_drift = 0
        self._pushed_prediction = 0
        self._pushed_boundary = 0
        self._pushed_structural = 0
        self._popped = 0

    async def push(self, event: ComparatorEvent) -> None:
        await self._q.put(event)
        if isinstance(event, DriftEvent):
            self._pushed_drift += 1
        elif isinstance(event, PredictionErrorEvent):
            self._pushed_prediction += 1
        elif isinstance(event, BoundaryApproachingEvent):
            self._pushed_boundary += 1
        elif isinstance(event, StructuralEvent):
            self._pushed_structural += 1

    async def pop(self) -> ComparatorEvent:
        ev = await self._q.get()
        self._popped += 1
        return ev

    def qsize(self) -> int:
        return self._q.qsize()

    def stats(self) -> dict:
        return {
            "queued": self._q.qsize(),
            "pushed_drift": self._pushed_drift,
            "pushed_prediction": self._pushed_prediction,
            "pushed_boundary": self._pushed_boundary,
            "pushed_structural": self._pushed_structural,
            "popped": self._popped,
        }
