"""URL normalization, host classification, and dispatch routing.

Single source of truth for *where* a URL gets handled. Three outcomes per URL:

  - built_in   : a hand-coded parser claims this host
  - cached_plan: a discovery plan exists on disk for this host
  - unknown    : neither — requires LLM-driven discovery

Each artist_id is derived deterministically from the canonical host so that
re-ingesting the same artist via a different URL on the same host reuses
the existing oeuvre file (postmortem rule #32: idempotency).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlunparse

# Where the per-host plans and per-artist oeuvre reports live. Mirrors the
# `learning/` storage convention already used by carla-mcp-server.
EARSHOT_HOME = Path.home() / ".carla-mcp" / "earshot"
PLAN_DIR = EARSHOT_HOME / "plans"
OEUVRE_DIR = EARSHOT_HOME / "oeuvre"
TRACKS_DIR = EARSHOT_HOME / "tracks"        # Phase 2 baselines (context.json)
SESSIONS_DIR = EARSHOT_HOME / "sessions"    # Phase 3 ambient streams + logs


# Built-in parser host patterns. Each entry: (matcher fn, parser module name)
# Order matters — first match wins.
def _is_bandcamp(host: str) -> bool:
    return host.endswith(".bandcamp.com") or host == "bandcamp.com"


def _is_soundcloud(host: str) -> bool:
    return host == "soundcloud.com" or host.endswith(".soundcloud.com")


BUILT_IN_HOSTS: tuple[tuple, ...] = (
    (_is_bandcamp, "bandcamp"),
    (_is_soundcloud, "soundcloud"),
)


@dataclass
class Dispatch:
    url_normalized: str
    host: str
    artist_id: str
    route: Literal["built_in", "cached_plan", "unknown"]
    parser_name: str | None       # set when route == built_in
    plan_path: Path | None        # set when route == cached_plan


def _slugify_artist(host: str, path: str = "") -> str:
    """Derive a stable artist_id from a host (and optionally a URL path).

    For platform hosts (bandcamp/soundcloud) we identify the artist by the
    portion of the URL that names them:
      - Bandcamp puts the artist in the subdomain → strip ".bandcamp.com".
      - SoundCloud puts the artist in the first path segment → use it
        prefixed with "soundcloud-" so the same user can be reconciled
        across platforms by the orchestrator if needed.
    For arbitrary hosts the whole host becomes the id.
    """
    if host.endswith(".bandcamp.com"):
        return host[: -len(".bandcamp.com")]
    if host == "soundcloud.com":
        # First non-empty path segment is the username; e.g.
        # https://soundcloud.com/sonicmultiplicities/freiburg → "sonicmultiplicities".
        seg = ""
        if path:
            parts = [p for p in path.strip("/").split("/") if p]
            if parts:
                seg = parts[0]
        return f"soundcloud-{seg}" if seg else "soundcloud-unknown"
    # Strip leading www.
    h = host[4:] if host.startswith("www.") else host
    # Collapse non-alphanumerics to hyphens
    return re.sub(r"[^a-z0-9]+", "-", h.lower()).strip("-")


def normalize_url(url: str) -> str:
    """Apply per-platform URL hygiene before dispatch.

    Implements the URL-discipline gaps from postmortem rules #14 and #15:
      - Bandcamp `https://artist.bandcamp.com/` → `…/music` (root → music)
      - Trailing slash on path roots collapsed
      - http → https where the host is known to redirect anyway
    """
    if not url:
        raise ValueError("empty url")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path or "/"

    # Bandcamp: root URL serves a thin landing; /music has the grid.
    if _is_bandcamp(host) and path in ("", "/"):
        path = "/music"

    return urlunparse((parsed.scheme.lower(), host, path, parsed.params, parsed.query, parsed.fragment))


def plan_path_for(host: str) -> Path:
    """Return where a cached plan for this host would live."""
    safe = re.sub(r"[^a-z0-9.-]+", "_", host.lower())
    return PLAN_DIR / f"{safe}.yaml"


def oeuvre_path_for(artist_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", artist_id)
    return OEUVRE_DIR / f"{safe}.md"


def dispatch(url: str, force_rediscover: bool = False) -> Dispatch:
    """Decide how to ingest a URL. Pure: no I/O beyond cache existence checks."""
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    OEUVRE_DIR.mkdir(parents=True, exist_ok=True)

    url_norm = normalize_url(url)
    parsed = urlparse(url_norm)
    host = parsed.netloc
    artist_id = _slugify_artist(host, parsed.path)

    for matcher, parser_name in BUILT_IN_HOSTS:
        if matcher(host):
            return Dispatch(
                url_normalized=url_norm,
                host=host,
                artist_id=artist_id,
                route="built_in",
                parser_name=parser_name,
                plan_path=None,
            )

    pp = plan_path_for(host)
    if pp.exists() and not force_rediscover:
        return Dispatch(
            url_normalized=url_norm,
            host=host,
            artist_id=artist_id,
            route="cached_plan",
            parser_name=None,
            plan_path=pp,
        )

    return Dispatch(
        url_normalized=url_norm,
        host=host,
        artist_id=artist_id,
        route="unknown",
        parser_name=None,
        plan_path=None,
    )
