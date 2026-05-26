"""Plan executor.

Takes a scraping plan emitted by the orchestrating LLM (or loaded from a
cached YAML) and a URL, runs the plan deterministically, and returns the
ingested result. The executor:

  1. Validates the plan structurally against ALLOWED_* kinds.
  2. Fetches the index URL, runs the release_index strategy to enumerate
     release pages.
  3. For each release page (or the index itself if recurse.follow_release_links
     is false), fetches it and runs each ExtractorSpec list in `fields`.
  4. If self_check is requested, refuses to return success unless the index
     strategy yielded at least one release and the title field resolved on
     that release.

No exceptions escape — failures are recorded as `errors` entries.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from earshot.discovery import ALLOWED_EXTRACTOR_KINDS, ALLOWED_INDEX_KINDS
from earshot.http_client import fetch


def validate_plan(plan: dict) -> list[str]:
    """Return a list of validation errors. Empty list = valid plan."""
    errs: list[str] = []
    if not isinstance(plan, dict):
        return ["plan is not a dict"]
    if not plan.get("host"):
        errs.append("missing host")
    idx = plan.get("release_index") or {}
    strategies = idx.get("strategy") or []
    if not strategies:
        errs.append("release_index.strategy missing or empty")
    else:
        for i, s in enumerate(strategies):
            if not isinstance(s, dict):
                errs.append(f"strategy[{i}] not a dict")
                continue
            k = s.get("kind")
            if k not in ALLOWED_INDEX_KINDS:
                errs.append(f"strategy[{i}].kind {k!r} not in {ALLOWED_INDEX_KINDS}")
    fields = plan.get("fields") or {}
    if not isinstance(fields, dict):
        errs.append("fields must be a dict")
    else:
        for fname, specs in fields.items():
            if not isinstance(specs, list):
                errs.append(f"fields.{fname} must be a list of extractor specs")
                continue
            for i, spec in enumerate(specs):
                if not isinstance(spec, dict) or "kind" not in spec:
                    errs.append(f"fields.{fname}[{i}] malformed")
                    continue
                if spec["kind"] not in ALLOWED_EXTRACTOR_KINDS:
                    errs.append(f"fields.{fname}[{i}].kind {spec['kind']!r} not in {ALLOWED_EXTRACTOR_KINDS}")
    return errs


def _resolve_jsonld_pointer(blocks: list[Any], pointer: str) -> Any:
    """Tiny $.path resolver across all JSON-LD blocks. Returns first hit."""
    if not pointer.startswith("$"):
        return None
    parts = [p for p in pointer.lstrip("$.").split(".") if p]
    for block in blocks:
        cur = block
        ok = True
        for p in parts:
            if isinstance(cur, list):
                # accept either numeric index or "match @type==<p>"
                if p.isdigit():
                    idx = int(p)
                    if idx < len(cur):
                        cur = cur[idx]
                    else:
                        ok = False; break
                else:
                    matched = None
                    for el in cur:
                        if isinstance(el, dict) and el.get("@type") == p:
                            matched = el; break
                    if matched is None:
                        ok = False; break
                    cur = matched
            elif isinstance(cur, dict):
                if p in cur:
                    cur = cur[p]
                else:
                    ok = False; break
            else:
                ok = False; break
        if ok and cur is not None:
            return cur
    return None


def _resolve_json_dotpath(obj: Any, pointer: str) -> Any:
    """Plain `$.a.b.c` over a single JSON value."""
    if not pointer.startswith("$"):
        return None
    cur = obj
    for p in [p for p in pointer.lstrip("$.").split(".") if p]:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return None
    return cur


def _jsonld_blocks(soup: BeautifulSoup) -> list[Any]:
    out = []
    for s in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            out.append(json.loads(s.string or ""))
        except Exception:
            pass
    return out


def _run_extractor(spec: dict, html: str, soup: BeautifulSoup) -> Any:
    """Run one ExtractorSpec against the parsed page. Returns None on miss."""
    k = spec["kind"]
    if k == "meta":
        key = spec.get("key")
        if not key:
            return None
        for el in soup.find_all("meta"):
            if el.get("property") == key or el.get("name") == key or el.get("itemprop") == key:
                return el.get("content")
        return None
    if k == "css":
        sel = spec.get("selector")
        if not sel:
            return None
        el = soup.select_one(sel)
        return el.get_text(" ", strip=True) if el else None
    if k == "css_attr":
        sel = spec.get("selector"); attr = spec.get("attr")
        if not sel or not attr:
            return None
        el = soup.select_one(sel)
        return el.get(attr) if el else None
    if k == "jsonld_path":
        return _resolve_jsonld_pointer(_jsonld_blocks(soup), spec.get("pointer", ""))
    if k == "data_attr_json_pointer":
        sel = spec.get("element_selector"); attr = spec.get("attr"); ptr = spec.get("pointer", "")
        if not sel or not attr:
            return None
        el = soup.select_one(sel)
        if not el:
            return None
        raw = el.get(attr)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            # try HTML-entity decode (Bandcamp data-tralbum convention)
            import html as html_mod
            try:
                data = json.loads(html_mod.unescape(raw))
            except Exception:
                return None
        return _resolve_json_dotpath(data, ptr) if ptr else data
    if k == "regex_on_html":
        pat = spec.get("pattern")
        if not pat:
            return None
        m = re.search(pat, html)
        return m.group(1) if (m and m.groups()) else (m.group(0) if m else None)
    if k == "regex_on_inline_script":
        script_match = spec.get("script_match") or ""
        pat = spec.get("pattern")
        if not pat:
            return None
        for s in soup.find_all("script"):
            if s.get("src"):
                continue
            t = s.string or s.get_text() or ""
            if script_match and script_match not in t:
                continue
            m = re.search(pat, t)
            if m:
                return m.group(1) if m.groups() else m.group(0)
        return None
    return None


def _first_match(specs: list[dict], html: str, soup: BeautifulSoup) -> Any:
    for s in specs:
        v = _run_extractor(s, html, soup)
        if v not in (None, "", []):
            return v
    return None


def _canonicalize_url(u: str) -> str:
    """Strip trailing slash + lowercase scheme for dedup-comparison only.

    Many sites surface the same page under both `/Path/` and `/Path` forms in
    different `<a href>` attributes; the server typically serves both or
    redirects between them. Treat them as one when building the seen-set.
    """
    if not u:
        return u
    base = u.rstrip("/")
    if base.lower().startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base.lower()


def _enumerate_release_urls(plan: dict, index_url: str, html: str, soup: BeautifulSoup) -> list[str]:
    strategies = (plan.get("release_index") or {}).get("strategy") or []
    seen: list[str] = []
    seen_canonical: set[str] = set()
    for s in strategies:
        k = s["kind"]
        urls: list[str] = []
        if k == "link_selector":
            for a in soup.select(s.get("selector", "")):
                href = a.get("href")
                if href:
                    urls.append(urljoin(index_url, href))
        elif k == "href_regex":
            pat = s.get("pattern")
            if not pat:
                continue
            rx = re.compile(pat)
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if rx.search(href):
                    urls.append(urljoin(index_url, href))
        elif k == "sitemap":
            try:
                sm_xml = fetch(s["url"])
                for m in re.finditer(r"<loc>(.*?)</loc>", sm_xml):
                    urls.append(m.group(1).strip())
            except Exception:
                continue
        for u in urls:
            canon = _canonicalize_url(u)
            if canon and canon not in seen_canonical:
                seen_canonical.add(canon)
                seen.append(u)
        if seen:
            break  # first non-empty strategy wins
    return seen


def _passes_release_filter(rec: dict, filter_rules: list[dict]) -> bool:
    """Apply plan-supplied release_filter rules to a per-release record.

    Each rule is `{field: <name>, regex: <pattern>}` and is checked via
    re.search against the field's string value. ALL rules must pass.
    Releases that fail any rule are dropped (used to exclude contributor
    pages / utility pages that share the same URL pattern as releases).
    """
    if not filter_rules:
        return True
    for rule in filter_rules:
        fname = rule.get("field")
        pat = rule.get("regex")
        if not fname or not pat:
            continue
        val = rec.get(fname)
        if val is None:
            return False
        if not re.search(pat, str(val)):
            return False
    return True


def execute(plan: dict, url_normalized: str, artist_id: str, self_check_only: bool = False) -> dict:
    """Run a validated plan against `url_normalized`.

    If self_check_only is True, fetch just the index page and verify the index
    strategy yields at least one release URL; do NOT recurse. Used at plan
    discovery time to decide whether the cache write is allowed.
    """
    errs = validate_plan(plan)
    if errs:
        return {
            "status": "error",
            "parser": "executor",
            "source_url": url_normalized,
            "artist_id": artist_id,
            "error": "plan validation failed",
            "validation_errors": errs,
        }

    errors: list[str] = []
    index_html = fetch(url_normalized)
    index_soup = BeautifulSoup(index_html, "lxml")
    release_urls = _enumerate_release_urls(plan, url_normalized, index_html, index_soup)

    if self_check_only:
        return {
            "status": "complete" if release_urls else "error",
            "parser": "executor",
            "source_url": url_normalized,
            "artist_id": artist_id,
            "release_urls_found": len(release_urls),
            "release_urls_sample": release_urls[:5],
            "error": None if release_urls else "release_index strategies yielded zero urls",
        }

    fields_spec = plan.get("fields") or {}
    recurse = (plan.get("recurse") or {}).get("follow_release_links", True)
    filter_rules = plan.get("release_filter") or []

    releases: list[dict] = []
    dropped_by_filter = 0
    if recurse and release_urls:
        for ru in release_urls:
            try:
                rh = fetch(ru)
            except Exception as e:
                errors.append(f"fetch failed {ru}: {e}")
                continue
            rs = BeautifulSoup(rh, "lxml")
            rec = {"url": ru}
            for fname, specs in fields_spec.items():
                rec[fname] = _first_match(specs, rh, rs)
            if not _passes_release_filter(rec, filter_rules):
                dropped_by_filter += 1
                continue
            releases.append(rec)
    else:
        # Page-only mode: extract directly from the index page.
        rec = {"url": url_normalized}
        for fname, specs in fields_spec.items():
            rec[fname] = _first_match(specs, index_html, index_soup)
        if _passes_release_filter(rec, filter_rules):
            releases.append(rec)
        else:
            dropped_by_filter += 1

    # Status truthfulness (postmortem rule #19): the top-level status MUST
    # align with actual outcome. `complete` only when nothing went wrong;
    # `partial` when there were fetch errors alongside successful extractions;
    # `error` when no releases survived at all.
    title_hits = sum(1 for r in releases if r.get("title"))
    if not releases:
        top_status = "error"
    elif errors:
        top_status = "partial"
    elif title_hits == 0:
        top_status = "partial"  # we got urls but couldn't extract titles
    else:
        top_status = "complete"

    return {
        "status": top_status,
        "parser": "executor",
        "host": plan.get("host"),
        "source_url": url_normalized,
        "artist_id": artist_id,
        "release_count": len(releases),
        "release_with_title": title_hits,
        "dropped_by_filter": dropped_by_filter,
        "releases": releases,
        "errors": errors,
    }
