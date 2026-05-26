"""Shared HTTP fetch helper.

Bandcamp serves different HTML to clients without a full browser-shaped header
set (observed: data-tralbum attribute disappears entirely when httpx sends its
default Accept header). The headers below mirror what Chrome sends and have
been verified to recover the rich SSR payloads on:

  - bandcamp.com (data-tralbum, data-band, music-grid)
  - soundcloud.com (window.__sc_hydration array)

These same headers were used during the Phase 1 scrape that produced
/tmp/earshot_phase1_test/releases_deep.json.
"""
from __future__ import annotations

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def fetch(url: str, timeout: float = 30.0) -> str:
    """Synchronous GET with browser-mimic headers. Raises on non-2xx."""
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        r = client.get(url, headers=BROWSER_HEADERS)
        r.raise_for_status()
        return r.text
