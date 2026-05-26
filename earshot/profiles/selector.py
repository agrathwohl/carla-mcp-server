"""Profile selector — Phase 2 baseline + oeuvre hint -> profile name.

A *rule-of-thumb* classifier. It returns a non-fallback profile name only
when the inputs match a confident bucket; everything else falls back to
'experimental'. The orchestrator can always override via the
`profile_name` arg to `earshot_start_session`.

Why a heuristic and not a model:
  Genre classification is a notoriously fuzzy task; an ML classifier here
  would add a large dependency for marginal accuracy gains and would
  produce mistakes that look authoritative. A small set of explicit rules
  is more honest — it falls back to 'experimental' whenever the music
  doesn't clearly fit a bucket, which is the safe default.

Inputs:
  - `baseline`: a Phase2Baseline instance (typed).
  - `oeuvre_hint`: an optional free-form string (e.g. a genre label from
    the Phase 1 oeuvre report or user input). Stronger signal than
    baseline alone when provided.

Output:
  One of: 'edm', 'ambient', 'jazz', 'experimental'. Adding new profiles
  to the catalog should pair with new conditions here.

Tuning principle:
  Bias toward 'experimental'. False-positive specific profiles can
  produce catalog mismatches (e.g. 'edm' commentary on an ambient track);
  false-negative profile selection just yields the well-behaved fallback.
"""
from __future__ import annotations

import logging
from typing import Optional

from earshot.phase2_baseline import Phase2Baseline

logger = logging.getLogger(__name__)


# Substring patterns mapping common genre labels to profile names. Order
# matters when a label matches multiple — first wins. Lowercased before
# matching.
_OEUVRE_HINT_MAP = [
    # Most specific / unambiguous patterns first.
    ("ambient", "ambient"),
    ("drone", "ambient"),
    ("dark ambient", "ambient"),
    ("generative", "ambient"),
    ("field recording", "ambient"),
    ("edm", "edm"),
    ("house", "edm"),
    ("techno", "edm"),
    ("trance", "edm"),
    ("dubstep", "edm"),
    ("drum and bass", "edm"),
    ("drum & bass", "edm"),
    ("dnb", "edm"),
    ("electro", "edm"),
    ("jazz", "jazz"),
    ("bebop", "jazz"),
    ("hard bop", "jazz"),
    ("fusion", "jazz"),
    ("improvised", "jazz"),
    ("free improv", "jazz"),
]


def select_profile(
    baseline: Optional[Phase2Baseline] = None,
    oeuvre_hint: Optional[str] = None,
) -> str:
    """Return a profile name from the available signals.

    Both args are optional but at least one is required to do anything
    other than return the fallback.
    """
    # Strongest signal: explicit oeuvre hint (a genre label from Phase 1
    # or user input). Try substring matches in the order defined above.
    if oeuvre_hint:
        hint = oeuvre_hint.lower()
        for substr, profile_name in _OEUVRE_HINT_MAP:
            if substr in hint:
                logger.info("select_profile: matched oeuvre hint %r -> %s",
                            substr, profile_name)
                return profile_name

    # Baseline-derived heuristic. Each profile has a narrow window — if any
    # condition is missing, fall through to 'experimental' rather than
    # forcing a fit.
    if baseline is None:
        return "experimental"

    tempo = baseline.tempo_bpm
    onset_rate = baseline.onset_rate_hz
    lufs = baseline.loudness.integrated_lufs
    dr = baseline.dynamic_range_db

    # EDM: 120-150 BPM, loud (LUFS >= -10), dense onsets (>= 4/s).
    # All three must hold; partial matches stay in 'experimental'.
    if (120 <= tempo <= 150
            and onset_rate >= 4
            and lufs is not None and lufs >= -10):
        logger.info("select_profile: baseline matches edm (tempo=%.1f rate=%.1f lufs=%.1f)",
                    tempo, onset_rate, lufs)
        return "edm"

    # Ambient: slow (<90 BPM), sparse onsets (<2/s), narrow DR (<8 dB).
    if (0 < tempo < 90
            and onset_rate < 2
            and dr > 0 and dr < 8):
        logger.info("select_profile: baseline matches ambient (tempo=%.1f rate=%.1f dr=%.1f)",
                    tempo, onset_rate, dr)
        return "ambient"

    # Jazz is deliberately NOT covered by baseline-only inference. Tempo
    # rubato + improvisation density is too close to many other genres to
    # classify confidently without a label hint. Require an explicit
    # oeuvre_hint match to land in 'jazz'.

    return "experimental"
