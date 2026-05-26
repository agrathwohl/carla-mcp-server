"""DOM summary generator + plan schema for unknown-host discovery.

When the orchestrating LLM has to plan how to scrape an unknown site, it
does NOT receive the raw HTML (200KB+ wastes tokens and degrades planning).
It receives a compressed summary that exposes every structural anchor the
plan-executor can later target:

  - URL + host
  - <head> meta tags + every JSON-LD block (verbatim — usually small)
  - heading outline (h1/h2/h3 only)
  - link inventory (deduped, grouped by domain, with sample anchor text)
  - embed inventory (iframe srcs, audio/video tags with their srcs)
  - framework fingerprints (Astro islands, Next __NEXT_DATA__, React, static)
  - every HTML attribute carrying JSON (data-tralbum/data-band/astro-island
    props/__sc_hydration/__NEXT_DATA__/etc.) — caps each at 4KB
  - script tag inventory (src urls + first 200 chars of inline scripts)
  - first 1-2KB of visible body text

By design every shape the LLM can REFERENCE in a plan, the executor can
REACH using the same primitives that produced the summary. Symmetry.

The plan schema is documented at the bottom of this file. The orchestrator
emits a JSON plan matching that schema; the executor validates and runs it.
"""
from __future__ import annotations

import collections
import json
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

# Soft caps so the summary stays small even on pathologically large pages.
MAX_INLINE_SCRIPT_PREVIEW = 200
MAX_BODY_TEXT_HEAD = 2000
MAX_JSON_ATTR_PREVIEW = 4096
MAX_LINKS_PER_DOMAIN = 12
MAX_LINK_DOMAINS = 30


def _detect_frameworks(html: str, soup: BeautifulSoup) -> dict:
    fp = {
        "astro_islands_count": len(soup.find_all("astro-island")),
        "next_data_present": bool(soup.find("script", id="__NEXT_DATA__")),
        "next_chunks_count": html.count("/_next/"),
        "astro_assets_count": html.count("/_astro/"),
        "react_root": bool(soup.find(id="root")) or bool(soup.find(id="__next")),
        "gatsby_root": bool(soup.find(id="___gatsby")),
        "sc_hydration": "window.__sc_hydration" in html,
        "bandcamp_data_tralbum": "data-tralbum=" in html,
        "bandcamp_data_band": "data-band=" in html,
        "vue_present": "data-v-" in html,
        "svelte_present": "svelte" in html,
        "mathjax": "mathjax" in html.lower(),
        "wordpress": "wp-content" in html or "wp-includes" in html,
    }
    return {k: v for k, v in fp.items() if v}


def _link_inventory(soup: BeautifulSoup, base_host: str) -> dict:
    by_domain: dict[str, list[dict]] = collections.defaultdict(list)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        text = a.get_text(" ", strip=True)[:80]
        parsed = urlparse(href if "://" in href else "//" + href, scheme="https")
        host = (parsed.netloc or base_host).lower()
        if len(by_domain[host]) < MAX_LINKS_PER_DOMAIN:
            by_domain[host].append({"href": href, "text": text})
    # Order domains by link count, cap total
    ordered = sorted(by_domain.items(), key=lambda kv: -len(kv[1]))[:MAX_LINK_DOMAINS]
    return {host: links for host, links in ordered}


def _embed_inventory(soup: BeautifulSoup) -> dict:
    return {
        "iframes": [i.get("src") for i in soup.find_all("iframe") if i.get("src")],
        "audio_sources": [
            (a.get("src") or [s.get("src") for s in a.find_all("source")])
            for a in soup.find_all("audio")
        ],
        "video_sources": [
            (v.get("src") or [s.get("src") for s in v.find_all("source")])
            for v in soup.find_all("video")
        ],
    }


def _json_carrying_attrs(soup: BeautifulSoup) -> list[dict]:
    """Find every HTML attribute that holds JSON (heuristic).

    Looks for known carriers (data-tralbum, data-band, astro-island props) plus
    any attribute on any element whose value starts with `{` or `[` and parses
    as JSON. Each entry is capped to MAX_JSON_ATTR_PREVIEW chars.
    """
    out: list[dict] = []
    for el in soup.find_all(True):
        for attr, val in el.attrs.items():
            if not isinstance(val, str):
                continue
            stripped = val.strip()
            if not stripped or stripped[0] not in "{[":
                continue
            preview = stripped[:MAX_JSON_ATTR_PREVIEW]
            parses = True
            try:
                json.loads(preview if len(stripped) <= MAX_JSON_ATTR_PREVIEW else stripped)
            except Exception:
                parses = False
            if parses:
                out.append({
                    "element": el.name,
                    "attribute": attr,
                    "value_preview": preview,
                    "value_length": len(stripped),
                    "siblings_hint": [c.name for c in el.parent.find_all(True, recursive=False)[:5]] if el.parent else None,
                })
    return out


def _inline_script_blobs(soup: BeautifulSoup) -> list[dict]:
    """Capture inline-script signals (e.g. window.__sc_hydration assigns)."""
    out = []
    for s in soup.find_all("script"):
        if s.get("src"):
            continue
        text = (s.string or s.get_text() or "").strip()
        if not text:
            continue
        head = text[:MAX_INLINE_SCRIPT_PREVIEW]
        # Detect window.* assigns
        win_assign = None
        m = re.match(r"^\s*window\.([A-Za-z_$][\w$]*)\s*=", text)
        if m:
            win_assign = m.group(1)
        out.append({
            "head": head,
            "length": len(text),
            "window_assignment_to": win_assign,
        })
    return out


def _jsonld(soup: BeautifulSoup) -> list[Any]:
    blocks = []
    for s in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            blocks.append(json.loads(s.string or ""))
        except Exception:
            pass
    return blocks


def _meta_tags(soup: BeautifulSoup) -> dict:
    out = {}
    for m in soup.find_all("meta"):
        key = m.get("property") or m.get("name") or m.get("itemprop")
        val = m.get("content")
        if key and val:
            out[key] = val[:500]
    return out


def _heading_outline(soup: BeautifulSoup) -> list[dict]:
    out = []
    for h in soup.find_all(re.compile(r"^h[1-3]$")):
        out.append({"level": h.name, "text": h.get_text(" ", strip=True)[:200]})
    return out[:50]


def generate_summary(url: str, html: str) -> dict:
    """Produce a compressed DOM summary suitable for handing to the LLM."""
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(url)
    body = soup.find("body")
    body_text = body.get_text(" ", strip=True) if body else ""
    return {
        "url": url,
        "host": parsed.netloc,
        "html_bytes": len(html),
        "title": soup.title.get_text(strip=True) if soup.title else None,
        "frameworks": _detect_frameworks(html, soup),
        "meta": _meta_tags(soup),
        "jsonld": _jsonld(soup),
        "headings": _heading_outline(soup),
        "links_by_domain": _link_inventory(soup, parsed.netloc),
        "embeds": _embed_inventory(soup),
        "json_carrying_attrs": _json_carrying_attrs(soup),
        "inline_scripts": _inline_script_blobs(soup),
        "body_text_chars": len(body_text),
        "body_text_head": body_text[:MAX_BODY_TEXT_HEAD],
    }


# -----------------------------------------------------------------------------
# Plan schema — what the orchestrating LLM emits in response to a summary.
# Kept as a Python dict (rather than JSON schema strings) so the executor can
# import it directly and the orchestrator can read the same source-of-truth.
# -----------------------------------------------------------------------------

PLAN_SCHEMA_DOC = """\
A scraping plan is a YAML/JSON document with these fields. All extractor
specs are tried in order; first non-empty match wins.

host: <string>                       # must match the URL's host
plan_version: 1
release_index:
  strategy:
    - kind: link_selector            # CSS selector returning <a> elements
      selector: "ol#music-grid li a"
    - kind: href_regex               # regex over all <a href=> on the page
      pattern: "^/(album|track)/[^/]+/?$"
    - kind: sitemap                  # absolute URL of sitemap to fetch
      url:  "https://host/sitemap.xml"
fields:                              # per-release page extractors
  title:        [ <ExtractorSpec>, ... ]
  artist:       [ ... ]
  release_date: [ ... ]
  description:  [ ... ]
  cover_art:    [ ... ]
  tracks:       [ ... ]             # may return list-of-dicts
  audio_source: [ ... ]             # the playable / downloadable URL
recurse:
  follow_release_links: bool
  max_depth: int                    # 0 = page-only, 1 = page + its release links
release_filter:                     # optional; drops over-matched non-release URLs
  - { field: description, regex: "Catalog number" }   # ALL rules must match
transport: static_http              # only static_http supported in v0
confidence:                         # planner self-assessment 0.0..1.0
  release_index: float
  fields_overall: float

ExtractorSpec is one of:
  { kind: meta,                  key: "og:image" }
  { kind: css,                   selector: "h1.trackTitle" }
  { kind: css_attr,              selector: "img.cover", attr: "src" }
  { kind: jsonld_path,           pointer: "$.datePublished" }
  { kind: data_attr_json_pointer,
    element_selector: "astro-island[component-url*=AudioPlayer]",
    attr: "props",
    pointer: "$.streamUrl" }
  { kind: regex_on_html,         pattern: "streamUrl\\\\s*:\\\\s*\\"([^\\"]+)\\"" }
  { kind: regex_on_inline_script,
    script_match: "window\\\\.__data",
    pattern: "..." }
"""

ALLOWED_EXTRACTOR_KINDS = {
    "meta",
    "css",
    "css_attr",
    "jsonld_path",
    "data_attr_json_pointer",
    "regex_on_html",
    "regex_on_inline_script",
}
ALLOWED_INDEX_KINDS = {"link_selector", "href_regex", "sitemap"}
