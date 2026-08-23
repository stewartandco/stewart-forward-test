"""D27 case 3: mechanical admission of single-citation source proposals.

Pre-filter (no cost) -> source screen (one Sonnet call) -> probation by yield.
Pure functions over dicts; all I/O (fetch, LLM client, clock) is injected so the
state machine is fully testable offline. Spec:
docs/2026-08-23-source-probation-filter-design.md
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .feeds import (fetch_url, discover_feed, parse_feed, article_links,
                    html_to_text)
from .watchlist import JUNK_DOMAINS, discovery_domain, DEFAULT_POLL_MINUTES_AUTO

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
        feed = None  # dead/empty/non-XML feed: fall back to index mode
        titles = [t or link for link, t in article_links(html, final, cap=25)][:10]
        if len(titles) < MIN_INDEX_ITEMS:
            return {"ok": False,
                    "reason": f"no feed and only {len(titles)} index items (< {MIN_INDEX_ITEMS})",
                    "feed": None, "titles": titles, "about": about}
    return {"ok": True, "reason": "feed" if feed else "index", "feed": feed,
            "titles": titles, "about": about}


# ---------------- yield ----------------

def source_stats(seen, source_id: str) -> dict:
    """Screened / kept counts for one source from the seen store's latest
    statuses. Post-keep states (extracted, paywalled, ...) are keeps."""
    screened = keeps = 0
    for e in seen._latest.values():
        if e["source_id"] != source_id or e["status"] not in SCREENED_STATUSES:
            continue
        screened += 1
        if e["status"] in KEEP_STATUSES:
            keeps += 1
    return {"screened": screened, "keeps": keeps}


def decide_probation(stats: dict, since: str, today: str) -> dict:
    """The state machine. Returns {action: promote|revoke|timeout|wait, reason}."""
    screened, keeps = stats["screened"], stats["keeps"]
    if keeps >= PROMOTE_KEEPS:
        return {"action": "promote", "reason": f"probation-yield {keeps}/{screened}"}
    try:
        age = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(since, "%Y-%m-%d")).days
    except ValueError:
        return {"action": "wait", "reason": f"bad probation_since {since!r}"}
    if age < 0:
        return {"action": "wait", "reason": f"probation_since {since} is in the future"}
    if age >= TIMEOUT_DAYS:
        return {"action": "timeout", "reason": "probation-timeout"}
    window = WINDOW_1 if keeps == 0 else WINDOW_2
    if screened >= window:
        return {"action": "revoke", "reason": f"probation-yield {keeps}/{window}"}
    return {"action": "wait", "reason": f"{keeps} keeps in {screened}/{window}"}


# ---------------- admissions (proposal -> blocked | probation) ----------------

def process_admissions(*, discovery_path, watchlist_path, actions, fetch=fetch_url,
                       screen, today: str | None = None) -> dict:
    """Case-3 admission pass. `screen(domain, titles, about) -> verdict|None`
    is injected (production binds relevance.screen_source). Returns the
    domains admitted / blocked / deferred this pass. Idempotent. The queue
    is loaded once and written once at the end (if anything changed); same
    for the watchlist (only when something was admitted)."""
    from .watchlist import (load_discovery, write_discovery, write_watchlist_doc,
                            flip_entries, entry_domain, is_mechanically_admissible,
                            watchlist_domains)
    today = today or _today()
    entries = load_discovery(discovery_path)
    doc = json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
    known = watchlist_domains(doc)
    out = {"admitted": [], "blocked": [], "deferred": []}
    queue_dirty = False
    for e in entries:
        if e.get("status") != "proposed" or is_mechanically_admissible(e):
            continue
        domain = entry_domain(e)
        if domain in known:
            e.pop("malformed_runs", None)
            flip_entries(entries, domain, "auto_admitted", reason="already on watchlist")
            queue_dirty = True
            continue
        pf = prefilter(e["url"], fetch)
        if not pf["ok"]:
            e.pop("malformed_runs", None)
            flip_entries(entries, domain, "blocked", reason=f"prefilter: {pf['reason']}")
            queue_dirty = True
            actions.event("source_auto_blocked", {"domain": domain, "rule": "prefilter",
                                                  "reason": pf["reason"], "url": e["url"]})
            out["blocked"].append(domain)
            continue
        verdict = screen(domain, pf["titles"], pf["about"])
        if verdict is None:
            n = int(e.get("malformed_runs", 0)) + 1
            if n >= MAX_MALFORMED_RUNS:
                e.pop("malformed_runs", None)
                flip_entries(entries, domain, "blocked", reason=f"source-screen malformed x{n}")
                actions.event("source_auto_blocked", {"domain": domain, "rule": "source-screen",
                                                      "reason": f"malformed x{n}", "url": e["url"]})
                out["blocked"].append(domain)
            else:
                e["malformed_runs"] = n
                actions.event("source_screen_malformed",
                              {"domain": domain, "run": n, "url": e["url"]})
                out["deferred"].append(domain)
            queue_dirty = True
            continue
        if not verdict["research_source"]:
            e.pop("malformed_runs", None)
            flip_entries(entries, domain, "blocked", reason=f"source-screen: {verdict['reason']}")
            queue_dirty = True
            actions.event("source_auto_blocked", {"domain": domain, "rule": "source-screen",
                                                  "reason": verdict["reason"], "url": e["url"]})
            out["blocked"].append(domain)
            continue
        entry = {
            "id": domain, "class": "blog", "name": domain, "url": e["url"],
            "feed": pf["feed"], "poll_minutes": DEFAULT_POLL_MINUTES_AUTO,
            "added_by": PROVENANCE_PROBATION, "verified_date": today,
            "tier": "probation", "probation_since": today,
            "notes": (f"probation from {today} per D27 case 3 (single citation; "
                      f"screen: {verdict['reason'][:120]}; classes "
                      f"{','.join(verdict['asset_classes']) or '-'}). Coen-revocable."),
        }
        doc["sources"].append(entry)
        known.add(domain)
        e.pop("malformed_runs", None)
        flip_entries(entries, domain, "probation",
                    reason=f"admitted on probation: {verdict['reason']}")
        queue_dirty = True
        actions.event("source_auto_admitted", {"domain": domain, "rule": "probation",
                                               "reason": verdict["reason"],
                                               "asset_classes": verdict["asset_classes"],
                                               "url": e["url"], "feed": pf["feed"]})
        out["admitted"].append(domain)
    # Watchlist first, then the queue: a crash between the two leaves a
    # 'proposed' queue entry plus a watchlist entry, which the next run
    # closes out cleanly via the "already on watchlist" branch above. The
    # reverse order would leave a queue entry claiming admission with no
    # matching watchlist row -- an admission that never actually happened.
    if out["admitted"]:
        write_watchlist_doc(watchlist_path, doc)
    if queue_dirty:
        write_discovery(discovery_path, entries)
    return out


# ---------------- reviews (probation -> promoted | revoked | timed_out) ------

def process_reviews(*, watchlist_path, discovery_path, seen, actions,
                    today: str | None = None) -> dict:
    """Evaluate every probation entry. D27 case 2 (2+ distinct citers) wins
    over yield; a timeout returns the domain to 'proposed' (re-proposable,
    bucketed separately as timed_out); a yield revoke blocks it. Idempotent:
    resolved entries leave probation. Both the discovery queue and the
    watchlist are loaded once and written once at the end (if anything
    changed)."""
    from .watchlist import (load_discovery, write_discovery, write_watchlist_doc,
                            flip_entries, entry_domain, proposal_citers, tier_of)
    today = today or _today()
    doc = json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
    disc = load_discovery(discovery_path)
    out = {"promoted": [], "revoked": [], "timed_out": [], "waiting": {}}
    keep: list[dict] = []
    watchlist_dirty = False
    queue_dirty = False
    for s in doc["sources"]:
        if tier_of(s) != "probation":
            keep.append(s)
            continue
        domain = s["id"]
        stats = source_stats(seen, domain)
        # Lookup is by domain over the single loaded queue snapshot; a
        # matching entry that is still 'proposed' here (rather than
        # 'probation') is harmless -- review is gated on tier=="probation"
        # on the watchlist side, not on the discovery-queue entry's status.
        match = next((de for de in disc if entry_domain(de) == domain), None)
        citers = proposal_citers(match) if match is not None else set()
        if len(citers) >= 2:
            decision = {"action": "promote", "reason": "cited by 2 distinct verified sources"}
        else:
            since = s.get("probation_since") or s.get("verified_date", "")
            decision = decide_probation(stats, since, today)
        if decision["action"] == "promote":
            s = {**s, "added_by": PROVENANCE_PROMOTED, "tier": "verified",
                 "verified_date": today,
                 "notes": s.get("notes", "") + f" | promoted {today}: {decision['reason']}"}
            s.pop("probation_since", None)
            keep.append(s)
            queue_dirty |= flip_entries(disc, domain, "auto_admitted", reason=decision["reason"])
            actions.event("source_promoted", {"domain": domain, "rule": decision["reason"],
                                              "screened": stats["screened"], "keeps": stats["keeps"]})
            out["promoted"].append(domain); watchlist_dirty = True
        elif decision["action"] in ("revoke", "timeout"):
            new_status = "blocked" if decision["action"] == "revoke" else "proposed"
            queue_dirty |= flip_entries(disc, domain, new_status, reason=decision["reason"])
            actions.event("source_auto_revoked", {"domain": domain, "rule": decision["reason"],
                                                  "action": decision["action"],
                                                  "screened": stats["screened"], "keeps": stats["keeps"],
                                                  "requeued": decision["action"] == "timeout"})
            bucket = "revoked" if decision["action"] == "revoke" else "timed_out"
            out[bucket].append(domain); watchlist_dirty = True
        else:
            keep.append(s)
            out["waiting"][domain] = decision["reason"]
    # Queue first, then the watchlist: a crash between the two leaves a
    # resolved queue status (promoted/blocked/reproposed) alongside a stale
    # probation entry still on the watchlist, which the next run re-resolves
    # from the queue's status. The reverse order would leave the watchlist
    # entry already gone with the queue still claiming 'probation' -- a
    # resolution the queue can no longer explain.
    if queue_dirty:
        write_discovery(discovery_path, disc)
    if watchlist_dirty:
        doc["sources"] = keep
        write_watchlist_doc(watchlist_path, doc)
    return out
