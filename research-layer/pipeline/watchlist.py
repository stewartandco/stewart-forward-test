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
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

VALID_CLASSES = {"arxiv", "aggregator", "blog", "ssrn", "central_bank", "github"}
REQUIRED_FIELDS = ("id", "class", "name", "url", "feed", "poll_minutes",
                   "added_by", "verified_date", "notes")
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref")
AUTO_ADMIT_MIN_CITERS = 2
DEFAULT_POLL_MINUTES_AUTO = 360


class WatchlistError(ValueError):
    pass


def _atomic_write_text(path: str | Path, text: str) -> None:
    """Write-then-rename so a crash mid-write never leaves a truncated file.
    `.with_name` (not `.with_suffix`) so a `.jsonl` path keeps its real
    suffix and only grows a `.tmp` tail."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        # a partial write or a failed replace must not leave a stray .tmp
        # file behind for the next run to trip over
        tmp.unlink(missing_ok=True)
        raise


def write_watchlist_doc(path: str | Path, doc: dict) -> None:
    _atomic_write_text(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def write_discovery(path: str | Path, entries: list[dict]) -> None:
    _atomic_write_text(
        path, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


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


def proposal_citers(e: dict) -> set[str]:
    """Distinct citing sources for a discovery-queue proposal: `cited_by`
    when stamped, else the first path segment of `found_in` (legacy
    entries); 'scout' is never a citer for endorsement-counting purposes."""
    citers = set(e.get("cited_by") or [e.get("found_in", "").split("/", 1)[0]])
    return citers - {"scout"}


def is_mechanically_admissible(e: dict) -> bool:
    """D27 case 1/2 mechanical bar: scout-researched, or cited by
    >= AUTO_ADMIT_MIN_CITERS distinct non-scout sources."""
    found_in = e.get("found_in", "")
    raw_citers = set(e.get("cited_by") or [found_in.split("/", 1)[0]])
    is_scout = found_in.startswith("scout/") or "scout" in raw_citers
    return is_scout or len(proposal_citers(e)) >= AUTO_ADMIT_MIN_CITERS


def watchlist_domains(doc: dict) -> set[str]:
    """Every domain already represented on the watchlist: source ids plus
    the domain of each source's url and feed."""
    domains = {s["id"] for s in doc["sources"]}
    domains |= {discovery_domain(u) for s in doc["sources"]
               for u in (s["url"], s.get("feed")) if u}
    return domains


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
        # accumulate distinct citing sources on open proposals (D27 evidence).
        # A 'timed_out' domain is a dead end -- process_admissions never
        # looks at it again -- UNLESS a genuinely new citer shows up, in
        # which case it re-opens for review by flipping back to 'proposed'
        # (a repeat of the same citer that timed it out leaves it dead).
        status = existing.get("status")
        if status in ("proposed", "timed_out"):
            cited_by = existing.setdefault(
                "cited_by", [existing.get("found_in", "").split("/", 1)[0]])
            if citer and citer not in cited_by:
                cited_by.append(citer)
                if status == "timed_out":
                    existing["status"] = "proposed"
                    existing["status_reason"] = "re-cited after timeout"
                    existing["status_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                write_discovery(path, entries)
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


def flip_entries(entries: list[dict], domain: str, status: str, *,
                 reason: str, only_from: str | None = None) -> bool:
    """In-memory: flip EVERY entry for this domain to `status` and record
    why. `only_from`, when given, restricts the flip to entries whose
    current status equals it (others for the same domain are left alone).
    Returns False when nothing matched. Caller owns writing `entries` back."""
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
    return hit


def set_discovery_status(path: str | Path, domain: str, status: str, *,
                         reason: str, only_from: str | None = None) -> bool:
    """Flip EVERY discovery-queue entry for this domain to `status` and
    record why. `only_from`, when given, restricts the flip to entries whose
    current status equals it (others for the same domain are left alone).
    Returns False when nothing matched."""
    entries = load_discovery(path)
    hit = flip_entries(entries, domain, status, reason=reason, only_from=only_from)
    if hit:
        write_discovery(path, entries)
    return hit
