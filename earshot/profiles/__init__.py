"""Profile loader for Phase I (scheduler) + Phase H (catalog).

Loads YAML profile + action-text catalog from disk into a typed Profile
object the scheduler queries at every event. Profiles live alongside
this module (`experimental.yaml`); action-text catalogs live in the
`action_text/` subdirectory.

Phase H (future) extends this with multiple genre-specific profiles
(`edm.yaml`, `ambient.yaml`, etc.) and a selector that chooses among
them based on Phase 1 priors + Phase 2 hints + user override. For
Phase I today, only the `experimental` fallback exists; that's the
single profile every session uses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from earshot.scheduler.intensity import IntensityThresholds

logger = logging.getLogger(__name__)

# Profile YAMLs sit alongside this module; catalogs in action_text/.
PROFILES_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class SilenceZone:
    """A track-time window during which the scheduler emits nothing."""
    start_s: float
    end_s: float
    reason: str = ""

    def contains(self, t_s: float) -> bool:
        return self.start_s <= t_s <= self.end_s


@dataclass(frozen=True)
class Profile:
    """One genre/aesthetic profile. Parsed from YAML.

    Phase H builds many of these; Phase I uses only `experimental` today.
    """
    name: str
    description: str
    thresholds: IntensityThresholds
    density_by_section: dict[str, float]
    silence_zones: list[SilenceZone]
    anti_patterns: list[str]
    action_text_repeat_window_seconds: int
    action_text_catalog: dict[str, list[dict]]
    # Original YAML path so callers can diagnose where this came from.
    source_path: Optional[Path] = None

    def density_for(self, section_label: str) -> float:
        """Density multiplier for a section type; default if not listed."""
        return self.density_by_section.get(
            section_label, self.density_by_section.get("default", 1.0)
        )

    def in_silence_zone(self, t_s: float) -> bool:
        return any(z.contains(t_s) for z in self.silence_zones)

    def catalog_for(self, event_type_key: str) -> list[dict]:
        """Catalog entries for an event-type key (e.g. 'drift_tempo'),
        falling back to 'generic' when no specific entries exist."""
        return self.action_text_catalog.get(
            event_type_key,
            self.action_text_catalog.get("generic", []),
        )


def load_profile(name: str) -> Profile:
    """Load a profile by name (the YAML filename without extension).

    Raises FileNotFoundError when the profile YAML doesn't exist —
    callers (Phase J's session_start) should fall back to 'experimental'.
    """
    yaml_path = PROFILES_DIR / f"{name}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"profile {name!r} not found at {yaml_path}. "
            f"Available: {[p.stem for p in PROFILES_DIR.glob('*.yaml')]}"
        )
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    # Action-text catalog reference — relative to profiles dir.
    catalog_rel = raw.get("action_text_catalog", f"action_text/{name}.yaml")
    catalog_path = PROFILES_DIR / catalog_rel
    if not catalog_path.exists():
        raise FileNotFoundError(
            f"profile {name!r} references action_text catalog {catalog_rel!r} "
            f"that doesn't exist at {catalog_path}"
        )
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError(f"action_text catalog for {name!r} not a mapping")

    th_raw = raw.get("thresholds") or {}
    thresholds = IntensityThresholds(
        action_text=float(th_raw.get("action_text", 1.0)),
        exclamation=float(th_raw.get("exclamation", 1.5)),
        observation=float(th_raw.get("observation", 2.5)),
        considered=float(th_raw.get("considered", 4.0)),
        reflection=float(th_raw.get("reflection", 6.0)),
    )

    zones_raw = raw.get("silence_zones") or []
    silence_zones = [
        SilenceZone(
            start_s=float(z["start_s"]),
            end_s=float(z["end_s"]),
            reason=z.get("reason", ""),
        )
        for z in zones_raw
    ]

    profile = Profile(
        name=raw.get("name", name),
        description=raw.get("description", "").strip(),
        thresholds=thresholds,
        density_by_section=dict(raw.get("density_by_section") or {"default": 1.0}),
        silence_zones=silence_zones,
        anti_patterns=list(raw.get("anti_patterns") or []),
        action_text_repeat_window_seconds=int(
            raw.get("action_text_repeat_window_seconds", 300)
        ),
        action_text_catalog=catalog,
        source_path=yaml_path,
    )
    logger.info(
        "loaded profile %r from %s (catalog: %d event-type keys)",
        profile.name, yaml_path, len(catalog),
    )
    return profile


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))
