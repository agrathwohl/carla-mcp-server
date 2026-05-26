"""Bandcamp artist/discography parser.

Implements Phase 1 ingestion against Bandcamp's stable SSR schema:

  - data-band   : HTML attribute (HTML-entity-encoded JSON) — label-level
                  identity, URLs, external sites list (Twitter/SC/site).
  - data-tralbum: HTML attribute — per-release JSON containing artist,
                  current.title, current.release_date, trackinfo[], item_type.
  - <ol id="music-grid"> <li class="music-grid-item"> : the discography grid
                  on /music. Each item carries the release path and artist
                  override.
  - <div class="tralbum-about|tralbum-credits|lyricsText"> : per-release
                  liner notes, credits, lyrics.

URL discipline: callers must pass a normalized URL (the dispatcher rewrites
artist.bandcamp.com/ → /music for us). Both /track/<slug> and /album/<slug>
release URLs are recognized.

Returns a dict shaped for the EarshotTools tool envelope. No exceptions
escape the parser — failures are encoded as `errors` entries on the result
(postmortem rule #2: top-level success bool must mean contract fulfilled).
"""
from __future__ import annotations

import html as html_mod
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from earshot.http_client import fetch


def _decode_attr_json(raw: str) -> dict | None:
    """data-tralbum / data-band values are HTML-entity-encoded JSON."""
    if not raw:
        return None
    try:
        return json.loads(html_mod.unescape(raw))
    except Exception:
        try:
            return json.loads(raw)
        except Exception:
            return None


def _parse_grid(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []
    grid = soup.find("ol", id="music-grid")
    if not grid:
        return items
    for li in grid.find_all("li", class_="music-grid-item"):
        a = li.find("a")
        if not (a and a.get("href")):
            continue
        title_p = li.find("p", class_="title")
        artist_span = li.find("span", class_="artist-override")
        img = li.find("img")
        items.append({
            "href": a["href"],
            "title": title_p.get_text(" ", strip=True) if title_p else None,
            "artist_override": artist_span.get_text(" ", strip=True) if artist_span else None,
            "thumb": img.get("src") if img else None,
            "data_item_id": li.get("data-item-id"),
        })
    return items


def _parse_release_page(html: str, url: str) -> dict:
    """Pull data-tralbum + about/credits/lyrics for one release page."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, Any] = {"url": url}

    el = soup.find(attrs={"data-tralbum": True})
    if el:
        tral = _decode_attr_json(el.get("data-tralbum", ""))
        if tral:
            out["tralbum"] = tral

    el = soup.find(attrs={"data-band": True})
    if el:
        band = _decode_attr_json(el.get("data-band", ""))
        if band:
            out["band"] = band

    for selector, key in (
        ("div.tralbum-about", "about"),
        ("div.tralbum-credits", "credits"),
        ("div.lyricsText", "lyrics"),
    ):
        el = soup.select_one(selector)
        if el:
            out[key] = el.get_text("\n", strip=True)

    tags = [a.get_text(strip=True) for a in soup.select("a.tag")]
    if tags:
        out["tags"] = tags

    for prop, key in (("og:image", "cover_art"), ("og:title", "og_title"), ("og:description", "og_description")):
        m = soup.find("meta", property=prop)
        if m and m.get("content"):
            out[key] = m["content"]

    return out


def _flatten_release(raw: dict) -> dict:
    """Project a parsed release into the oeuvre tracklist shape."""
    tr = raw.get("tralbum") or {}
    cur = tr.get("current") or {}
    tracks = []
    for t in tr.get("trackinfo") or []:
        tracks.append({
            "title": t.get("title"),
            "duration_s": t.get("duration"),
            "track_num": t.get("track_num"),
            "lyrics": t.get("lyrics"),
        })
    return {
        "url": raw["url"],
        "item_type": tr.get("item_type"),
        "artist": tr.get("artist"),
        "title": cur.get("title"),
        "release_date": cur.get("release_date") or tr.get("album_release_date"),
        "cover_art": raw.get("cover_art"),
        "about": raw.get("about"),
        "credits": raw.get("credits"),
        "lyrics": raw.get("lyrics"),
        "tags": raw.get("tags") or [],
        "tracks": tracks,
        "total_duration_s": sum((t.get("duration_s") or 0) for t in tracks),
    }


def ingest(url_normalized: str, artist_id: str) -> dict:
    """Run a full Bandcamp Phase 1 ingest.

    Fetches /music, enumerates the grid, fetches every release page, and
    composes a structured result. The caller is responsible for any LLM
    synthesis of the oeuvre prose body — this function only returns
    structured data (frontmatter-shaped fields + per-release records).
    """
    errors: list[str] = []
    music_html = fetch(url_normalized)
    soup = BeautifulSoup(music_html, "lxml")

    band_el = soup.find(attrs={"data-band": True})
    band = _decode_attr_json(band_el.get("data-band", "")) if band_el else None

    grid_items = _parse_grid(soup)
    if not grid_items:
        errors.append("music-grid empty or absent on /music page")

    # Compose external-source links from data-band.sites so the orchestrator
    # can decide whether to recurse to SoundCloud / personal site.
    external_sites = []
    if band:
        for s in band.get("sites") or []:
            external_sites.append({"url": s.get("url"), "title": s.get("title")})

    # Profile-level metadata from OpenGraph fallback
    og_desc = (soup.find("meta", property="og:description") or {}).get("content") if soup.find("meta", property="og:description") else None
    page_title = soup.title.get_text(strip=True) if soup.title else None

    # Fetch each release. /track/X and /album/X both yield data-tralbum.
    releases: list[dict] = []
    for item in grid_items:
        rel_url = urljoin(url_normalized, item["href"])
        try:
            rh = fetch(rel_url)
        except Exception as e:
            errors.append(f"fetch failed {rel_url}: {e}")
            continue
        parsed = _parse_release_page(rh, rel_url)
        flat = _flatten_release(parsed)
        flat["grid_artist"] = item.get("artist_override")
        flat["grid_title"] = item.get("title")
        flat["grid_thumb"] = item.get("thumb")
        releases.append(flat)

    # Aggregate tag frequencies for genre_priors candidate generation. This
    # is structured data; the synthesizer LLM converts it to weighted priors.
    tag_freq: dict[str, int] = {}
    for r in releases:
        for t in r["tags"]:
            tag_freq[t] = tag_freq.get(t, 0) + 1

    artists_in_releases: dict[str, int] = {}
    for r in releases:
        a = r.get("artist") or r.get("grid_artist")
        if a:
            artists_in_releases[a] = artists_in_releases.get(a, 0) + 1

    total_dur = sum(r["total_duration_s"] for r in releases)

    # Postmortem rule #19: top-level status must align with actual outcome.
    # `complete` means every grid item was fetched and parsed without error.
    # Any failure → `partial` (with errors enumerated) or `error` (nothing usable).
    if not grid_items:
        top_status = "error"
    elif errors:
        top_status = "partial"
    else:
        top_status = "complete"

    # Host: prefer the data-band subdomain when present; fall back to the URL.
    if band and (band.get("url_hints") or {}).get("subdomain"):
        host_str = f"{band['url_hints']['subdomain']}.bandcamp.com"
    else:
        from urllib.parse import urlparse
        host_str = urlparse(url_normalized).netloc or "bandcamp.com"

    return {
        "status": top_status,
        "host": host_str,
        "parser": "bandcamp",
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artist_id": artist_id,
        "source_url": url_normalized,
        "page_title": page_title,
        "profile_description": og_desc,
        "band": band,                # full data-band payload, includes sites
        "external_sites": external_sites,
        "release_count": len(releases),
        "track_count": sum(len(r["tracks"]) for r in releases),
        "total_duration_s": total_dur,
        "tag_freq": tag_freq,
        "artists_in_releases": artists_in_releases,
        "releases": releases,
        "errors": errors,
    }
