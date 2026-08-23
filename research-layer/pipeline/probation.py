"""D27 case 3: mechanical admission of single-citation source proposals.

Pre-filter (no cost) -> source screen (one Sonnet call) -> probation by yield.
Pure functions over dicts; all I/O (fetch, LLM client, clock) is injected so the
state machine is fully testable offline. Spec:
docs/2026-08-23-source-probation-filter-design.md
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .feeds import (fetch_url, discover_feed, parse_feed, article_links,
                    html_to_text)
from .watchlist import JUNK_DOMAINS, discovery_domain

WINDOW_1 = 40
WINDOW_2 = 80
PROMOTE_KEEPS = 2
TIMEOUT_DAYS = 90
PRIORITY_CAP = 40
BLOCKED_SUBDOMAINS = ("store.", "shop.", "cms.", "app.", "login.", "my.")
MIN_INDEX_ITEMS = 5
MAX_MALFORMED_RUNS = 3
KEEP_STATUSES = ("screen_keep", "screen_keep_low", "extracted", "paywalled",
                 "fetch_failed", "thin_content", "extract_failed")
SCREENED_STATUSES = KEEP_STATUSES + ("screen_kill",)
PROVENANCE_PROBATION = "auto-d27-probation"
PROVENANCE_PROMOTED = "auto-d27-promoted"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------- pre-filter ----------------

def prefilter(url: str, fetch=fetch_url) -> dict:
    """Deterministic, no tokens. Returns {ok, reason, feed, titles, about}."""
    domain = discovery_domain(url)
    if not domain or domain in JUNK_DOMAINS or \
            any(domain.endswith("." + j) for j in JUNK_DOMAINS):
        return {"ok": False, "reason": "junk domain", "feed": None, "titles": [], "about": ""}
    if any(domain.startswith(p) for p in BLOCKED_SUBDOMAINS):
        return {"ok": False, "reason": f"blocked subdomain {domain.split('.')[0]}.",
                "feed": None, "titles": [], "about": ""}
    status, html, final = fetch(url)
    if status != 200:
        status, html, final = fetch(url)          # one retry
        if status != 200:
            return {"ok": False, "reason": f"unreachable: http {status}",
                    "feed": None, "titles": [], "about": ""}
    about = html_to_text(html)[:300]
    feed = discover_feed(html, final)
    titles: list[str] = []
    if feed:
        fstatus, ftext, _ = fetch(feed)
        if fstatus == 200:
            titles = [it["title"] for it in parse_feed(ftext, domain)][:10]
        else:
            feed = None
    if not titles:
        titles = [t or link for link, t in article_links(html, final, cap=25)][:10]
        if len(titles) < MIN_INDEX_ITEMS:
            return {"ok": False,
                    "reason": f"no feed and only {len(titles)} index items (< {MIN_INDEX_ITEMS})",
                    "feed": None, "titles": titles, "about": about}
    return {"ok": True, "reason": "feed" if feed else "index", "feed": feed,
            "titles": titles, "about": about}
