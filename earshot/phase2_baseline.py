"""Phase 2 baseline loader — in-memory accessor for the track context artifact.

At session start, the comparators (Phase F) and the eventual session machinery
(Phase J) need to query Phase 2's pre-analysis output by track_id. This module
provides:

  - `Phase2BaselineLoader.load(track_id)` → `Phase2Baseline` instance.
  - `Phase2BaselineLoader.exists(track_id)` → bool, for fast availability check.
  - `Phase2Baseline` — wraps the parsed context dict with typed properties and
    time-domain query helpers (`rms_at`, `section_at`, `lyric_at`, etc.).

The loader is intentionally synchronous and pure-Python — no async, no Carla,
no MCP. Callers (comparators, session start) wrap it in their own concurrency
model. Loading a typical context.json is ~5 ms; not worth threading.

Path convention: `~/.carla-mcp/earshot/tracks/{track_id}/context.json`.
This mirrors the output path the companion Phase 2 script writes to
(see `earshot/companion/phase2.py`), so the contract is single-sourced.

Postmortem rule #19 (status truthfulness): `load()` raises FileNotFoundError
when the artifact is absent rather than returning a partial/synthetic baseline.
The session-start tool (Phase J) should catch this and refuse to start a
session against an un-analyzed track — `earshot_analyze_track` must be called
first.
"""
from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from earshot import dispatch as dsp


def _context_path(track_id: str) -> Path:
    return dsp.TRACKS_DIR / track_id / "context.json"


@dataclass(frozen=True)
class Loudness:
    """EBU R128 loudness measurements from essentia.standard.LoudnessEBUR128."""
    integrated_lufs: Optional[float] = None
    lra_lu: Optional[float] = None
    momentary_lufs_max: Optional[float] = None
    momentary_lufs_min: Optional[float] = None
    shortterm_lufs_max: Optional[float] = None
    error: Optional[str] = None  # set when LoudnessEBUR128 raised


@dataclass(frozen=True)
class KeyEstimate:
    """Key estimation result, either essentia.KeyExtractor or KS fallback."""
    tonic: str
    mode: str
    confidence: float
    method: str
    fallback_reason: Optional[str] = None


class Phase2Baseline:
    """Read-only accessor over a single track_id's Phase 2 context.json.

    Construct via `Phase2BaselineLoader.load()`. The constructor is public for
    testability but in production code prefer the loader factory so missing
    artifacts surface as FileNotFoundError rather than silently degrading.
    """

    def __init__(self, context_data: dict):
        self._raw = context_data
        b = context_data.get("baseline") or {}

        # Identity + duration
        self.track_id: str = context_data.get("track_id", "")
        self.artist_id: str = context_data.get("artist_id", "")
        self.ingested_at: str = context_data.get("ingested_at", "")
        self.source_path: str = context_data.get("source_path", "")
        self.duration_s: float = float(context_data.get("duration_s", 0.0))
        self.sample_rate: int = int(context_data.get("sample_rate", 44100))

        # Tempo
        self.tempo_bpm: float = float(b.get("tempo_bpm", 0.0))
        self.tempo_confidence: float = float(b.get("tempo_confidence", 0.0))
        self.tempo_steadiness: float = float(b.get("tempo_steadiness_0to1", 0.0))
        self.inter_beat_interval_mean_s: float = float(b.get("inter_beat_interval_mean_s", 0.0))
        self.inter_beat_interval_cv: float = float(b.get("inter_beat_interval_cv", 0.0))
        self.beat_count: int = int(b.get("beat_count", 0))

        # Key
        kdata = b.get("key") or {}
        self.key = KeyEstimate(
            tonic=kdata.get("tonic", "?"),
            mode=kdata.get("mode", "?"),
            confidence=float(kdata.get("confidence", 0.0)),
            method=kdata.get("method", "unknown"),
            fallback_reason=kdata.get("fallback_reason"),
        )

        # Loudness
        ldata = b.get("loudness") or {}
        self.loudness = Loudness(
            integrated_lufs=ldata.get("integrated_lufs"),
            lra_lu=ldata.get("lra_lu"),
            momentary_lufs_max=ldata.get("momentary_lufs_max"),
            momentary_lufs_min=ldata.get("momentary_lufs_min"),
            shortterm_lufs_max=ldata.get("shortterm_lufs_max"),
            error=ldata.get("error"),
        )

        # Onsets + spectral + dynamic range
        self.onset_count: int = int(b.get("onset_count", 0))
        self.onset_rate_hz: float = float(b.get("onset_rate_hz", 0.0))
        self.spectral_centroid_mean_hz: float = float(b.get("spectral_centroid_mean_hz", 0.0))
        self.spectral_centroid_std_hz: float = float(b.get("spectral_centroid_std_hz", 0.0))
        self.dynamic_range_db: float = float(b.get("dynamic_range_db", 0.0))

        # New harmonic features (additive). None when the key is ABSENT
        # (pre-2026-05-31 artifact) -> value_for returns None -> comparators skip
        # gracefully. A genuine computed 0.0 (e.g. a perfectly static track) is
        # kept as 0.0, NOT conflated with "absent".
        self.chord_change_rate_mean: Optional[float] = (
            float(b["chord_change_rate_mean"]) if "chord_change_rate_mean" in b else None)
        self.harmonic_tension_mean: Optional[float] = (
            float(b["harmonic_tension_mean"]) if "harmonic_tension_mean" in b else None)
        # Per-section means keyed by section index (JSON keys may be str or int):
        # {index: {"chord_change_rate": x, "harmonic_tension": y}}
        self._section_harmonic: dict = context_data.get("section_harmonic") or {}

        # Time-series — pre-sorted for bisect-based queries.
        # RMS envelope: list of {"t_s": float, "rms_db": float}, sorted by t_s.
        rms = context_data.get("rms_envelope_db") or []
        self._rms_times: list[float] = [r["t_s"] for r in rms]
        self._rms_db: list[float] = [r["rms_db"] for r in rms]

        # Section map: list of {"index","start_s","end_s","duration_s"}, sorted.
        # `section_error` is set by the companion when librosa.segment.agglomerative
        # raised. When sections are absent, callers can distinguish "never
        # computed" (section_error is None) from "computed and failed".
        self._sections: list[dict] = context_data.get("section_map") or []
        self._section_starts: list[float] = [s["start_s"] for s in self._sections]
        self.section_error: Optional[str] = context_data.get("section_error")

        # Beat times — top-50 sample only; for nearest-beat queries this is
        # under-resolved. A future Phase B enhancement could store the full
        # beat array if downstream comparators need it.
        self._beat_times: list[float] = list(context_data.get("beat_times_s_sample") or [])

        # Lyric segments — sorted by start_s.
        self._transcript: list[dict] = list(context_data.get("transcript") or [])
        # Word-level lyric timing (Phase 2 with word_timestamps=True). Each:
        # {"start_s", "end_s", "word"}. Enables recent_lyrics() to surface only
        # words already sung at a given time — anti-spoiler-safe, unlike the
        # coarse segments which can run minutes ahead. Empty for older
        # context.json files transcribed before word timing was enabled.
        self._transcript_words: list[dict] = list(
            context_data.get("transcript_words") or [])

    # ------------------------------------------------------------------
    # Time-domain query helpers
    # ------------------------------------------------------------------
    def rms_at(self, t_s: float) -> Optional[float]:
        """Linearly-interpolated RMS dB at time `t_s`. None if out of range.

        The Phase 2 envelope is sampled at 1-second windows; this method
        interpolates between adjacent samples for sub-second precision.
        """
        if not self._rms_times or t_s < self._rms_times[0] or t_s > self._rms_times[-1]:
            return None
        i = bisect_left(self._rms_times, t_s)
        if i >= len(self._rms_times):
            return self._rms_db[-1]
        if self._rms_times[i] == t_s or i == 0:
            return self._rms_db[i]
        t_lo, t_hi = self._rms_times[i - 1], self._rms_times[i]
        v_lo, v_hi = self._rms_db[i - 1], self._rms_db[i]
        span = t_hi - t_lo
        if span <= 0:
            return v_lo
        frac = (t_s - t_lo) / span
        return v_lo + frac * (v_hi - v_lo)

    def section_at(self, t_s: float) -> Optional[dict]:
        """Section dict containing time `t_s`. None if out of range / no sections.

        Returns a copy of the entry from `section_map` (don't mutate it).
        """
        if not self._sections:
            return None
        i = bisect_left(self._section_starts, t_s)
        # bisect_left gives the insert position; the containing section is i-1
        # (unless t_s == start exactly, in which case it's index i).
        if i < len(self._section_starts) and self._section_starts[i] == t_s:
            return dict(self._sections[i])
        i -= 1
        if i < 0 or i >= len(self._sections):
            return None
        sec = self._sections[i]
        if t_s > sec["end_s"]:
            return None
        return dict(sec)

    def next_boundary(self, t_s: float) -> Optional[dict]:
        """The next section boundary STRICTLY AFTER `t_s`.

        Returns `{'start_s': <next section start>, 'section_index': <index of
        the section the boundary opens>}` or None when there are no boundaries
        remaining (`t_s` is in or past the final section).

        Used by Phase G's boundary detector to schedule
        BoundaryApproachingEvent emissions ahead of the orchestrator's
        prediction-refresh round-trip.
        """
        if not self._sections:
            return None
        # bisect_left returns the position where t_s would be inserted to
        # keep _section_starts sorted; that's the first start STRICTLY > t_s
        # except when t_s equals a start exactly (then it's that start; we
        # want the NEXT one, so add one).
        i = bisect_left(self._section_starts, t_s)
        while i < len(self._section_starts) and self._section_starts[i] <= t_s:
            i += 1
        if i >= len(self._section_starts):
            return None
        return {
            "start_s": self._section_starts[i],
            "section_index": self._sections[i]["index"],
        }

    def lyric_at(self, t_s: float) -> Optional[dict]:
        """Active lyric segment at time `t_s`. None when nothing is sung."""
        for seg in self._transcript:
            if seg["start_s"] <= t_s <= seg["end_s"]:
                return dict(seg)
        return None

    def recent_lyrics(self, t_s: float, window_s: float = 8.0) -> Optional[str]:
        """Words sung in the `window_s` seconds up to and including `t_s`,
        joined into a string. ANTI-SPOILER: only words whose end_s <= t_s are
        included, so this never reveals lyrics the listener hasn't heard yet
        (the commentary that quotes it lands, after the delay tower, exactly
        when the listener's ear is at t_s). Returns None when there are no
        word timings (instrumental passage, or context.json predates word
        timing)."""
        if not self._transcript_words:
            return None
        lo = t_s - max(0.0, window_s)
        words = [
            w["word"] for w in self._transcript_words
            if w.get("end_s") is not None and w.get("start_s") is not None
            and w["end_s"] <= t_s and w["start_s"] >= lo
        ]
        if not words:
            return None
        return " ".join(words).strip() or None

    def nearest_beat(self, t_s: float) -> Optional[float]:
        """Nearest beat-time sample to `t_s`. Limited by top-50 stored sample.

        For comparators that need full beat-level precision, a future Phase B
        enhancement should store all beat times rather than a 50-sample head.
        """
        if not self._beat_times:
            return None
        return min(self._beat_times, key=lambda b: abs(b - t_s))

    def value_for(self, dimension, t_s: float) -> Optional[float]:
        """Canonical baseline lookup: Dimension → expected scalar at time `t_s`.

        Used by both DriftComparator and any ExpectationProvider that wants
        to fall back to the Phase 2 baseline. Centralizing this mapping here
        ensures that adding a new Dimension only requires updating one place,
        not every comparator/provider that knows how to read it.

        `dimension` is `earshot.comparators.events.Dimension` but typed as
        Any here to avoid a circular import (comparators import Phase2Baseline).
        """
        # Resolve by enum value/string so this method doesn't depend on the
        # Dimension class being importable here.
        key = dimension.value if hasattr(dimension, "value") else str(dimension)
        if key == "dynamic_envelope":
            return self.rms_at(t_s)
        if key == "tempo":
            return self.tempo_bpm or None
        if key == "lufs_integrated":
            return self.loudness.integrated_lufs
        if key == "spectral_centroid":
            return self.spectral_centroid_mean_hz or None
        if key == "onset_density":
            return self.onset_rate_hz or None
        if key == "chord_change_rate":
            return self._harmonic_at("chord_change_rate", t_s, self.chord_change_rate_mean)
        if key == "harmonic_tension":
            return self._harmonic_at("harmonic_tension", t_s, self.harmonic_tension_mean)
        # `key` dimension is categorical; callers handle it separately.
        return None

    def _harmonic_at(self, feature: str, t_s: float, fallback: Optional[float]):
        """Per-section mean of a harmonic feature at time t_s, else the global
        mean, else None when the feature was never computed (old artifact).

        `fallback` is None on an old artifact and 0.0..1.0 on a re-analyzed
        track (including a valid 0.0), so it is returned directly — a computed
        zero is NOT treated as 'absent'."""
        sec = self.section_at(t_s)
        if sec is not None:
            per = (self._section_harmonic.get(str(sec["index"]))
                   or self._section_harmonic.get(sec["index"]))
            if per and feature in per:
                return float(per[feature])
        return fallback

    def section_mean_rms_db(self, section_index: int):
        """Mean RMS (dB) of the envelope samples within a section's span, or None."""
        if section_index < 0 or section_index >= len(self._sections):
            return None
        sec = self._sections[section_index]
        lo, hi = sec["start_s"], sec["end_s"]
        vals = [db for t, db in zip(self._rms_times, self._rms_db) if lo <= t < hi]
        if not vals:
            return None
        return sum(vals) / len(vals)

    # ------------------------------------------------------------------
    # Bulk accessors
    # ------------------------------------------------------------------
    @property
    def section_map(self) -> list[dict]:
        """Deep-copy of the section list. Mutating returned dicts is safe."""
        return [dict(s) for s in self._sections]

    @property
    def transcript(self) -> list[dict]:
        return [dict(s) for s in self._transcript]

    def as_dict(self) -> dict:
        """Return the raw context dict (useful for serialization / debugging)."""
        return self._raw

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"<Phase2Baseline track_id={self.track_id!r} "
            f"duration={self.duration_s:.1f}s "
            f"tempo={self.tempo_bpm:.1f}BPM "
            f"key={self.key.tonic} {self.key.mode} "
            f"sections={len(self._sections)}>"
        )


class Phase2BaselineLoader:
    """Factory for Phase2Baseline. Hides the on-disk path convention."""

    @staticmethod
    def load(track_id: str) -> Phase2Baseline:
        """Load and parse a track's Phase 2 context. Raises FileNotFoundError
        when the artifact is absent (signals 'run earshot_analyze_track first').
        """
        path = _context_path(track_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Phase 2 baseline missing for track_id={track_id!r}. "
                f"Run earshot_analyze_track first. Expected at: {path}"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"context.json malformed for {track_id!r}: {e}") from e
        return Phase2Baseline(data)

    @staticmethod
    def exists(track_id: str) -> bool:
        return _context_path(track_id).exists()

    @staticmethod
    def context_path(track_id: str) -> Path:
        return _context_path(track_id)
