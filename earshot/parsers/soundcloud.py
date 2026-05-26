"""SoundCloud profile/track parser using SSR hydration.

SoundCloud's HTML pages embed a `window.__sc_hydration = [...]` array carrying
typed blobs. Two blob kinds matter for Phase 1:

  - hydratable: "user"  — creator/profile fields (description, follower count,
                          track_count, playlist_count, creator_subscription,
                          avatar, city, etc.)
  - hydratable: "sound" — track fields (title, genre, tag_list, description,
                          duration, full_duration, playback_count, license,
                          artwork_url, waveform_url, release_date)

This path avoids yt-dlp's api-v2 rate-limit (which 403s after ~80 rapid
calls — observed in /tmp/earshot_phase1_test/soundcloud/). For enumerating
the entire track surface of a profile, yt-dlp is still the canonical tool;
this parser handles the profile-level read + arbitrary-track lookups.

URL discipline: pass /<username> for profile, /<username>/<track-slug> for
a single track. SoundCloud serves the same hydration on either.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from earshot.http_client import fetch


_HYDRATION_RE = re.compile(r"window\.__sc_hydration\s*=\s*(\[[\s\S]*?\]);")


def _extract_hydration(html: str) -> list[dict]:
    m = _HYDRATION_RE.search(html)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except Exception:
        return []


def _by_kind(hydration: list[dict], kind: str) -> dict | None:
    for h in hydration:
        if h.get("hydratable") == kind and isinstance(h.get("data"), dict):
            return h["data"]
    return None


def _user_slug_from_url(url: str) -> str | None:
    p = urlparse(url).path.strip("/")
    if not p:
        return None
    return p.split("/", 1)[0] or None


def _summarize_user(u: dict) -> dict:
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "full_name": u.get("full_name"),
        "permalink": u.get("permalink"),
        "permalink_url": u.get("permalink_url"),
        "uri": u.get("uri"),
        "city": u.get("city"),
        "country_code": u.get("country_code"),
        "created_at": u.get("created_at"),
        "verified": u.get("verified"),
        "avatar_url": u.get("avatar_url"),
        "description": u.get("description"),
        "followers_count": u.get("followers_count"),
        "followings_count": u.get("followings_count"),
        "track_count": u.get("track_count"),
        "playlist_count": u.get("playlist_count"),
        "likes_count": u.get("likes_count"),
        "comments_count": u.get("comments_count"),
        "creator_subscription": u.get("creator_subscription"),
        "creator_subscriptions": u.get("creator_subscriptions"),
        "badges": u.get("badges"),
    }


def _summarize_sound(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "title": s.get("title"),
        "permalink_url": s.get("permalink_url"),
        "created_at": s.get("created_at"),
        "release_date": s.get("release_date"),
        "duration_ms": s.get("duration"),
        "full_duration_ms": s.get("full_duration"),
        "genre": s.get("genre"),
        "tag_list": s.get("tag_list"),
        "description": s.get("description"),
        "license": s.get("license"),
        "playback_count": s.get("playback_count"),
        "comment_count": s.get("comment_count"),
        "likes_count": s.get("likes_count"),
        "reposts_count": s.get("reposts_count"),
        "artwork_url": s.get("artwork_url"),
        "waveform_url": s.get("waveform_url"),
        "purchase_url": s.get("purchase_url"),
        "label_name": s.get("label_name"),
        "user_id": s.get("user_id"),
    }


def ingest(url_normalized: str, artist_id: str) -> dict:
    """Run SoundCloud Phase 1 ingest against a profile or track URL.

    For a profile URL: returns user-level data only. The full track surface
    is NOT enumerated here — yt-dlp is the canonical enumerator for that
    (its api-v2 access yields stream URLs and complete pagination). The
    orchestrator can call yt-dlp out-of-band or use a future tool for the
    track enumeration step. This parser deliberately does the cheap,
    rate-limit-tolerant read.

    For a track URL: returns user + sound.
    """
    errors: list[str] = []
    html = fetch(url_normalized)
    hydration = _extract_hydration(html)
    if not hydration:
        return {
            "status": "error",
            "parser": "soundcloud",
            "source_url": url_normalized,
            "artist_id": artist_id,
            "error": "no __sc_hydration block found in SSR HTML",
        }

    user = _by_kind(hydration, "user")
    sound = _by_kind(hydration, "sound")

    # Refine artist_id from the actual permalink slug once we know it.
    refined_artist_id = artist_id
    if user and user.get("permalink"):
        refined_artist_id = f"soundcloud-{user['permalink']}"
    elif (slug := _user_slug_from_url(url_normalized)):
        refined_artist_id = f"soundcloud-{slug}"

    result: dict[str, Any] = {
        "status": "complete",
        "parser": "soundcloud",
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": url_normalized,
        "artist_id": refined_artist_id,
        "page_kind": "track" if sound else ("profile" if user else "unknown"),
        "errors": errors,
    }

    if user:
        result["user"] = _summarize_user(user)
    else:
        errors.append("no `user` hydration blob")
    if sound:
        result["sound"] = _summarize_sound(sound)

    if not user and not sound:
        result["status"] = "error"
        result["error"] = "neither user nor sound hydration blob present"

    return result
