"""Pure chroma-derived musical features, shared by the Phase 2 baseline
computation (phase2.py) and the live streaming companion (streaming.py).

Both callers pass a normalized 12-bin chroma mean vector (chroma.mean(axis=1)).
Kept dependency-light (numpy only) so it imports in the companion venv. Both
scripts run as `python earshot/companion/<script>.py`, so this sibling module
is on sys.path[0] and imports via `from chroma_features import ...`.
"""
from __future__ import annotations

import numpy as np


def chord_change_rate(prev_chroma: np.ndarray, cur_chroma: np.ndarray) -> float:
    """How much the harmonic content moved between two chroma mean vectors.

    Returns 1 - cosine_similarity, range ~0 (identical) .. 1 (orthogonal).
    Returns 0.0 if either vector has zero energy (silence) — no movement claim.
    """
    a = np.asarray(prev_chroma, dtype=float).flatten()
    b = np.asarray(cur_chroma, dtype=float).flatten()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na <= 0 or nb <= 0:
        return 0.0
    cos = float(np.dot(a, b) / (na * nb))
    cos = max(-1.0, min(1.0, cos))
    return round(1.0 - cos, 4)


def harmonic_tension(chroma_mean: np.ndarray) -> float:
    """Normalized Shannon entropy of the chroma distribution, range 0..1.

    Flat/ambiguous chroma (many pitch classes equally present) -> high tension;
    one or two dominant pitch classes (clear tonality) -> low. Returns 0.0 for
    a zero-energy (silent) vector.
    """
    v = np.asarray(chroma_mean, dtype=float).flatten()
    v = np.clip(v, 0.0, None)
    s = v.sum()
    if s <= 0:
        return 0.0
    p = v / s
    nz = p[p > 0]
    ent = float(-(nz * np.log(nz)).sum())
    max_ent = np.log(len(v))  # log(12)
    # max(0.0, …) avoids a confusing -0.0 for a single-pitch-class vector.
    return round(max(0.0, ent / max_ent), 4) if max_ent > 0 else 0.0
