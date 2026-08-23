"""Verified-source watchlist (D23) and the off-list discovery queue.

The watchlist is the standing corpus designation: a source may be polled ONLY
when Coen added it AND stamped verified_date (his one-time verification pass is
the permanent Tier 3 corpus gate). Anything discovered off-list is queued as a
proposal in sources/discovery_queue.jsonl and never fetched.

D27 case 3 adds a mechanical middle ground: `auto-d27-probation`/
`auto-d27-promoted` provenance lets a source poll on probationary terms before
Coen's own verification pass, tracked via the `tier` field.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

VALID_CLASSES = {"arxiv", "aggregator", "blog", "ssrn", "central_bank", "github"}
REQUIRED_FIELDS = ("id", "class", "name", "url", "feed", "poll_minutes",
                   "added_by", "verified_date", "notes")
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref")


class WatchlistError(ValueError):
    pass


def load_watchlist(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise WatchlistError("watchlist needs a 'sources' array")
    seen_ids: set[str] = set()
    for src in sources:
        for field in REQUIRED_FIELDS:
            if field not in src:
                raise WatchlistError(f"source {src.get('id', '?')!r} missing {field!r}")
        if src["id"] in seen_ids:
            raise WatchlistError(f"duplicate source id {src['id']!r}")
        seen_ids.add(src["id"])
        if src["class"] not in VALID_CLASSES:
            raise WatchlistError(f"source {src['id']!r} has unknown class {src['class']!r}")
        if not isinstance(src["poll_minutes"], int) or src["poll_minutes"] <= 0:
            raise WatchlistError(f"source {src['id']!r} poll_minutes must be a positive int")
        if not str(src["url"]).startswith(("http://", "https://")):
            raise WatchlistError(f"source {src['id']!r} url must be http(s)")
        if src["feed"] is not None and not str(src["feed"]).startswith(("http://", "https://")):
            raise WatchlistError(f"source {src['id']!r} feed must be http(s) or null")
        if src.get("tier") not in (None, "verified", "probation"):
            raise WatchlistError(f"source {src['id']!r} has unknown tier {src['tier']!r}")
    return sources


POLLABLE_PROVENANCE = {"coen", "auto-d27", "auto-d27-probation", "auto-d27-promoted"}


def pollable(sources: list[dict]) -> list[dict]:
    """The verified gate: entries carrying one of the four honest provenance
    values - 'coen' (his own verification pass), 'auto-d27' (D27 mechanical
    admission: scout-researched or 2+ distinct citers), or the D27 case-3
    probation pair 'auto-d27-probation' / 'auto-d27-promoted'. Nothing else
    polls."""
    return [s for s in sources
            if s["added_by"] in POLLABLE_PROVENANCE and s["verified_date"]]


def tier_of(source: dict) -> str:
    """'verified' unless the entry says otherwise (legacy entries carry no tier)."""
    return source.get("tier") or "verified"


def remove_source(path: str | Path, source_id: str) -> dict | None:
    """Delete one watchlist entry by id; returns it, or None if absent."""
    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    keep, removed = [], None
    for s in doc.get("sources", []):
        if s["id"] == source_id and removed is None:
            removed = s
        else:
            keep.append(s)
    if removed is not None:
        doc["sources"] = keep
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    return removed


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                       if not k.lower().startswith(TRACKING_PARAMS)])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), query, ""))


# Platforms that are never research SOURCES (social, video, retail, share
# infrastructure) - links to them are noise, not proposals for Coen.
JUNK_DOMAINS = {
    "twitter.com", "x.com", "facebook.com", "linkedin.com", "instagram.com",
    "youtube.com", "youtu.be", "reddit.com", "t.me", "wa.me", "threads.net",
    "amazon.com", "apple.com", "spotify.com", "podcasts.apple.com",
    "play.google.com", "google.com", "goo.gl", "bit.ly", "feedburner.com",
    "wordpress.com", "wp.com", "gravatar.com", "creativecommons.org",
    "mailchi.mp", "eepurl.com", "substackcdn.com",
}


def discovery_domain(url: str) -> str:
    netloc = urlsplit(url).netloc.lower().split(":")[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def entry_domain(e: dict) -> str:
    """A discovery-queue entry's domain: the stamped field, falling back to
    deriving it from the entry's url for older entries that predate it."""
    return e.get("domain") or discovery_domain(e["url"])


def load_discovery(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def queue_discovery(path: str | Path, url: str, found_in: str, reason: str) -> bool:
    """Append an off-list source proposal (Tier 3, never fetched).

    A proposal is a DOMAIN, not a post: further URLs on an already-queued
    domain are not new proposals, and junk platforms (social/video/retail)
    never queue. Returns False when nothing was written.
    """
    domain = discovery_domain(url)
    if not domain or domain in JUNK_DOMAINS \
            or any(domain.endswith("." + j) for j in JUNK_DOMAINS):
        return False
    citer = found_in.split("/", 1)[0]
    entries = load_discovery(path)
    existing = next((e for e in entries if entry_domain(e) == domain), None)
    if existing is not None:
        # accumulate distinct citing sources on open proposals (D27 evidence)
        if existing.get("status") == "proposed":
            cited_by = existing.setdefault(
                "cited_by", [existing.get("found_in", "").split("/", 1)[0]])
            if citer and citer not in cited_by:
                cited_by.append(citer)
                Path(path).write_text(
                    "".join(json.dumps(e, ensure_ascii=False) + "\n"
                            for e in entries),
                    encoding="utf-8")
        return False
    entry = {
        "url": url,
        "domain": domain,
        "normalized": normalize_url(url),
        "found_in": found_in,
        "reason": reason,
        "cited_by": [citer] if citer else [],
        "tier": 3,
        "status": "proposed",
        "queued_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def set_discovery_status(path: str | Path, domain: str, status: str, *,
                         reason: str, only_from: str | None = None) -> bool:
    """Flip EVERY discovery-queue entry for this domain to `status` and
    record why. `only_from`, when given, restricts the flip to entries whose
    current status equals it (others for the same domain are left alone).
    Returns False when nothing matched.

    Not safe to call while another function holds an in-memory copy of the
    queue and will write it back (load once, write once in any pass that
    also calls this)."""
    entries = load_discovery(path)
    hit = False
    for e in entries:
        if entry_domain(e) != domain:
            continue
        if only_from is not None and e.get("status") != only_from:
            continue
        e["status"] = status
        e["status_reason"] = reason
        e["status_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hit = True
    if hit:
        Path(path).write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
            encoding="utf-8")
    return hit
