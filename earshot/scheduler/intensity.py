"""Six-level intensity stack + score-to-level mapping.

The README defines six levels of emission, in increasing intensity:

  1. Silent (no output) — most bars
  2. Action-text (`>>> slowly nodding to the groove`) — occasional
  3. Short exclamation (`oh`, `damn`) — rare
  4. Brief observation ("the snare just got tight") — rarer
  5. Considered statement (multi-sentence about a craft move) — once or
     twice per song
  6. Reflective summary (section/song retrospective) — end of section / song

Mapping logic: a single normalized score (from prediction error or drift
magnitude, scaled by threshold) maps to a level via per-profile thresholds.
The README says "density and register fall out of error magnitude naturally"
— the scheduler doesn't need a separate gate; the score IS the gate.

Per-profile thresholds (the experimental fallback's defaults):
  score < 1.0     → silent     (the event didn't exceed its threshold)
  1.0 ≤ score < 1.5  → action-text
  1.5 ≤ score < 2.5  → exclamation
  2.5 ≤ score < 4.0  → brief observation
  4.0 ≤ score < 6.0  → considered statement
  6.0 ≤ score        → reflective summary

These shift per genre profile (Phase H). Slow contemplative material has
higher thresholds (longer silences); dense rhythmic material has lower
ones (more frequent action-text).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class IntensityLevel(IntEnum):
    """Six-level emission intensity. Higher = more verbose / more rare.

    IntEnum (not Enum) so callers can do arithmetic (`level + 1`) and
    compare across levels (`if level >= IntensityLevel.OBSERVATION`).
    """
    SILENT = 1
    ACTION_TEXT = 2
    EXCLAMATION = 3
    OBSERVATION = 4
    CONSIDERED = 5
    REFLECTION = 6


@dataclass(frozen=True)
class IntensityThresholds:
    """Score thresholds that gate each level.

    A score below `action_text` means SILENT. A score >= `reflection`
    means REFLECTION. In between, the highest threshold a score clears
    determines the level.

    Defaults match the README's intent: most events silent, action-text
    occasional, prose rare. Profile authors (Phase H) tune these per
    genre — slower / quieter profiles raise the bar (longer silences);
    denser profiles lower it (more frequent action-text).
    """
    action_text: float = 1.0      # ≥ this → at least ACTION_TEXT
    exclamation: float = 1.5
    observation: float = 2.5
    considered: float = 4.0
    reflection: float = 6.0


def score_to_level(score: float, thresholds: Optional[IntensityThresholds] = None) -> IntensityLevel:
    """Map a normalized score (0+) to an IntensityLevel using thresholds.

    Score is expected in units of "magnitudes over threshold" — e.g. a
    PredictionErrorEvent with magnitude exactly at threshold has score=1.0,
    twice the threshold = 2.0, etc. (See `PredictionComparator._process`
    for the normalization.) Drift events use absolute magnitude in raw
    units (BPM, dB, LUFS) and require their own per-dimension scaling
    before calling this. The scheduler does that scaling at event-receive
    time.

    Args:
        score: non-negative magnitude in threshold-multiples.
        thresholds: per-profile cutoffs; uses defaults if None.

    Returns:
        IntensityLevel — SILENT when score < action_text threshold.
    """
    if thresholds is None:
        thresholds = IntensityThresholds()
    if score < thresholds.action_text:
        return IntensityLevel.SILENT
    if score < thresholds.exclamation:
        return IntensityLevel.ACTION_TEXT
    if score < thresholds.observation:
        return IntensityLevel.EXCLAMATION
    if score < thresholds.considered:
        return IntensityLevel.OBSERVATION
    if score < thresholds.reflection:
        return IntensityLevel.CONSIDERED
    return IntensityLevel.REFLECTION
