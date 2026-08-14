"""Verified-source watchlist (D23) and the off-list discovery queue.

The watchlist is the standing corpus designation: a source may be polled ONLY
when Coen added it AND stamped verified_date (his one-time verification pass is
the permanent Tier 3 corpus gate). Anything discovered off-list is queued as a
proposal in sources/discovery_queue.jsonl and never fetched.
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
    return sources


def pollable(sources: list[dict]) -> list[dict]:
    """The verified gate: Coen-added AND verification-stamped, nothing else."""
    return [s for s in sources if s["added_by"] == "coen" and s["verified_date"]]


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
    known = {e.get("domain") or discovery_domain(e["url"])
             for e in load_discovery(path)}
    if domain in known:
        return False
    entry = {
        "url": url,
        "domain": domain,
        "normalized": normalize_url(url),
        "found_in": found_in,
        "reason": reason,
        "tier": 3,
        "status": "proposed",
        "queued_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True
