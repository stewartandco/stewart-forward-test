"""Reader v2 continuous scanner (D23, contract v1.1).

24/7 loop: poll verified sources (token-free) -> stage-1 relevance screen
(claude-sonnet-5, strict intake parameters) -> full fetch + card extraction
through the existing pipeline (honesty guard, pending registration). Budget:
USD 25/month hard cap, 80% alert; at cap extraction stops, polling continues.

Usage:
    python -m pipeline.scanner --once      # one poll cycle, then exit
    python -m pipeline.scanner             # resident loop (launch OS-detached
                                           # via run_scanner.ps1, never as a
                                           # session child)

The scanner only ever polls watchlist entries Coen has verification-stamped;
off-list discoveries queue as Tier 3 proposals and are never fetched.
"""
from __future__ import annotations

import re
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .watchlist import (load_watchlist, pollable, queue_discovery,
                        load_discovery, DEFAULT_POLL_MINUTES_AUTO, tier_of)
from .feeds import (parse_feed, extract_links, article_links, discover_feed,
                    item_id, html_to_text, looks_paywalled, fetch_url)
from .seen import SeenStore
from .budget import BudgetMeter
from .relevance import screen_items, screen_source, ApiCreditExhausted
from .approvals import process_approvals
from .probation import (prioritise_items, process_admissions, process_reviews,
                        probation_counts, PRIORITY_CAP)
from .scanstatus import ActionLog, write_status, write_digest
from .registry import Registry
from .common import quote_in_source
from .reader import build_card, chunk_text, extract_claims_usage
from .chainlock import ChainLock, ChainLockHeld

LAYER = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-sonnet-5"  # D23 throughput-first: Sonnet for screen AND bulk extraction
DEFAULT_READER_ENV = Path(r"E:\Users\Coen\Claude\stewartandco-agents\hubs\intelligence\agents\reader\.env")
MAX_DISCOVERIES_PER_ITEM = 10
HTML_ITEMS_PER_CYCLE = 25      # cap on a feedless source's per-poll candidates
MAX_DEFER_ATTEMPTS = 3         # then park; no infinite re-feed (08-15 runaway)
MIN_ARTICLE_CHARS = 1200       # below this a fetched page is a teaser, not an article
CREDIT_BACKOFF_SECONDS = 1800  # cool-off after a billing/credential failure

# watchlist class -> research-card source metadata. Everything enters at
# "practitioner" credibility (the AFML precedent: Coen's call, tighten at triage).
CLASS_TO_SOURCE_TYPE = {
    "arxiv": "paper", "ssrn": "paper", "blog": "blog", "aggregator": "blog",
    "central_bank": "filing", "github": "blog",
}


def poll_source(source: dict, seen: SeenStore, fetch=fetch_url) -> list[dict]:
    """Poll one source (no tokens). Returns items not previously seen, each
    recorded 'seen' in the store."""
    url = source["feed"] or source["url"]
    status, text, final_url = fetch(url)
    if status != 200:
        return []
    if source["feed"]:
        items = parse_feed(text, source["id"])
    else:  # HTML listing diff, article-filtered and capped (08-15 link soup)
        items = [{"source_id": source["id"], "item_id": item_id(source["id"], link),
                  "title": title or link, "link": link, "summary": "",
                  "published": None}
                 for link, title in article_links(text, final_url,
                                                  cap=HTML_ITEMS_PER_CYCLE)]
    fresh = [it for it in items if not seen.is_seen(it["item_id"])]
    for it in fresh:
        seen.record(it["item_id"], it["source_id"], "seen",
                    title=it["title"], link=it["link"])
    return fresh


RESUME_STATUSES = ("deferred_screen", "deferred_budget", "deferred_lock", "seen")
RESUME_STALE_MINUTES = 20


def refeedable_deferred(seen: SeenStore, max_attempts: int = MAX_DEFER_ATTEMPTS,
                        statuses=RESUME_STATUSES,
                        stale_minutes: int = RESUME_STALE_MINUTES) -> list[dict]:
    """Items owed another pass: deferred retries, plus 'seen' items a crash or
    restart stranded between polling and screening (2026-08-15: 2,002 items
    orphaned - poll_source records 'seen' first, and dedup then blocks
    re-polling forever). 'seen' items are only resumed once they are older
    than stale_minutes so the current cycle's own items aren't double-fed.
    max_attempts parks persistent failures so nothing can spin the loop.
    deferred_lock occurrences are excluded from that attempt count: the park
    mechanism exists to stop pathological ITEMS, and lock contention is
    environmental, not the item's fault. An item must never be permanently
    dropped just because the loop happened to be mid-write. Unbounded
    re-feed on lock contention alone is safe -- NOT because the lock itself
    clears quickly (a manual session may legitimately hold it for hours,
    and a stale lock only breaks after 3h STALE_AFTER_S plus the loop's
    two-strike rule) but because _extract_item / process_inbox probe
    chain.lock BEFORE the gather phase's LLM spend: a lock-deferred re-feed
    costs only the batched screen call and a re-fetch (~$0.001/item/cycle),
    never a re-paid extraction, so re-feeding it indefinitely while the lock
    is held stays cheap."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    out = []
    for status in statuses:
        for iid, event in list(seen.items_with_status(status).items()):
            if not event.get("link"):
                continue
            if status == "seen":
                touched = datetime.strptime(event["ts_utc"], "%Y-%m-%dT%H:%M:%SZ") \
                    .replace(tzinfo=timezone.utc)
                if touched > cutoff:
                    continue  # in flight in this cycle
            attempts = sum(1 for e in seen.events_for(iid)
                           if e["status"] in statuses and e["status"] != "deferred_lock")
            if attempts >= max_attempts:
                seen.record(iid, event["source_id"], "deferred_parked",
                            reason=f"parked after {attempts} attempts")
                continue
            out.append({"source_id": event["source_id"], "item_id": iid,
                        "title": event.get("title") or "",
                        "link": event["link"], "summary": "", "published": None})
    return out


def _watchlist_domains(sources: list[dict]) -> set[str]:
    domains = set()
    for s in sources:
        for u in (s["url"], s["feed"]):
            if u:
                domains.add(urlsplit(u).netloc.lower())
    return domains


def _extract_item(client, model, item, source, page_text, html, *,
                  meter, registry, known_claims, discovery_path,
                  watchlist_sources):
    """Stage 2 for one kept item: extraction, honesty guard, registration,
    discovery queueing. Returns (cards_registered, honesty_dropped,
    deferred_lock). deferred_lock=True means chain.lock was held by another
    writer: nothing in this item was registered and the caller must leave
    the item re-feedable rather than marking it extracted."""
    source_meta = {
        "type": CLASS_TO_SOURCE_TYPE[source["class"]],
        "title": item["title"], "authors": [], "year": None,
        "url": item["link"], "doi": None, "isbn": None,
        "credibility_tier": "practitioner",
    }
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-scanner"

    logs_dir = Path(registry.log_path).parent / "logs"
    lock = ChainLock(logs_dir, holder="scanner",
                     purpose=f"extract {item['source_id']}/{item['item_id']}")
    if lock.info() is not None:
        # Pre-gather probe: someone else is mid-write. Defer before paying
        # for the gather phase's LLM calls, not just before the append --
        # unbounded lock re-feed (RESUME_STATUSES / park exemption below)
        # would otherwise re-pay this spend every cycle a lock is held. This
        # is a probe only (.info(), never acquire()): a false positive costs
        # a free no-op deferral, a false negative just falls through to the
        # real acquire() below, which still guards the append -- no TOCTOU
        # exposure either way.
        return 0, 0, True

    # Gather every chunk's claims first (LLM calls only, no chain writes) so
    # the chain.lock window below covers just the card-append batch, not the
    # extraction calls themselves.
    all_claims = []
    for label, chunk in chunk_text(page_text):
        claims, usage = extract_claims_usage(client, model, label, chunk)
        meter.record_call(model, usage, purpose="extract",
                          agent="reader")
        all_claims.extend(claims)

    registered = dropped = 0
    try:
        lock.acquire()
    except ChainLockHeld:
        # advisory, non-blocking: never wait on another writer's window.
        # Nothing for this item is registered this cycle; it stays
        # re-feedable via RESUME_STATUSES / refeedable_deferred.
        return 0, 0, True
    try:
        for raw in all_claims:
            if not quote_in_source(raw["quote"], page_text):
                dropped += 1
                continue
            if raw["claim"] in known_claims:
                continue
            known_claims.add(raw["claim"])
            card = build_card(raw, source_meta, model, run_id)
            registry.register_card(card)
            registered += 1
    finally:
        lock.release()

    # off-list references become Tier 3 proposals for Coen — never fetched
    domains = _watchlist_domains(watchlist_sources)
    queued = 0
    item_domain = urlsplit(item["link"]).netloc.lower()
    for link, _text in extract_links(html, item["link"]):
        if queued >= MAX_DISCOVERIES_PER_ITEM:
            break
        netloc = urlsplit(link).netloc.lower()
        if netloc in domains or netloc == item_domain:
            continue
        if queue_discovery(discovery_path, link,
                           found_in=f"{item['source_id']}/{item['item_id']}",
                           reason="referenced by ingested item"):
            queued += 1
    return registered, dropped, False


def process_new_items(new_items: list[dict], *, client, model: str, meter,
                      seen: SeenStore, registry: Registry, fetch,
                      watchlist_sources: list[dict], discovery_path,
                      screen_log, actions: ActionLog) -> dict:
    stats = {"items": len(new_items), "screen_keep": 0, "screen_kill": 0,
             "screen_keep_low": 0, "deferred": 0, "deferred_lock": 0,
             "paywalled": 0, "fetch_failed": 0, "thin_content": 0,
             "extracted": 0, "cards_registered": 0, "honesty_dropped": 0}
    if not new_items:
        return stats

    decisions = screen_items(client, model, new_items, meter, screen_log)
    by_id = {it["item_id"]: it for it in new_items}
    keeps = []
    for iid, (status, reason) in decisions.items():
        it = by_id[iid]
        seen.record(iid, it["source_id"], status, reason=reason)
        if status == "screen_keep":
            keeps.append(it)
            stats["screen_keep"] += 1
        elif status == "screen_kill":
            stats["screen_kill"] += 1
        elif status == "screen_keep_low":
            stats["screen_keep_low"] += 1   # on the record, never extracted
        else:
            stats["deferred"] += 1
    actions.event("screen_batch", {"n": len(new_items),
                                   "kept": stats["screen_keep"],
                                   "killed": stats["screen_kill"],
                                   "deferred": stats["deferred"]})

    known_claims = {c["claim"] for c in registry.cards().values()}
    src_by_id = {s["id"]: s for s in watchlist_sources}
    for item in keeps:
        if not meter.can_spend():
            seen.record(item["item_id"], item["source_id"], "deferred_budget",
                        reason="monthly cap reached")
            stats["deferred"] += 1
            continue
        status, html, _final = fetch(item["link"])
        if status == 0:
            seen.record(item["item_id"], item["source_id"], "fetch_failed",
                        reason=html[:200])
            stats["fetch_failed"] += 1
            continue
        if looks_paywalled(status, html):
            seen.record(item["item_id"], item["source_id"], "paywalled",
                        reason=f"http {status}")
            stats["paywalled"] += 1
            continue
        page_text = html_to_text(html)
        if len(page_text) < MIN_ARTICLE_CHARS:
            # teaser/landing page: extraction would spend $0.07 on nothing
            seen.record(item["item_id"], item["source_id"], "thin_content",
                        reason=f"{len(page_text)} chars < {MIN_ARTICLE_CHARS}")
            stats["thin_content"] += 1
            continue
        try:
            registered, dropped, deferred_lock = _extract_item(
                client, model, item, src_by_id[item["source_id"]], page_text,
                html, meter=meter, registry=registry, known_claims=known_claims,
                discovery_path=discovery_path,
                watchlist_sources=watchlist_sources)
        except Exception as exc:
            seen.record(item["item_id"], item["source_id"], "extract_failed",
                        reason=f"{type(exc).__name__}: {exc}"[:200])
            print(f"  extract failed for {item['link']}: {exc}", file=sys.stderr)
            continue
        if deferred_lock:
            seen.record(item["item_id"], item["source_id"], "deferred_lock",
                        reason="chain.lock held by another writer")
            stats["deferred"] += 1
            stats["deferred_lock"] += 1
            continue
        seen.record(item["item_id"], item["source_id"], "extracted",
                    reason=f"{registered} cards")
        stats["extracted"] += 1
        stats["cards_registered"] += registered
        stats["honesty_dropped"] += dropped

    if stats["extracted"] or stats["honesty_dropped"]:
        actions.event("cards_registered", {
            "items_extracted": stats["extracted"],
            "cards": stats["cards_registered"],
            "honesty_dropped": stats["honesty_dropped"]})
    return stats


def scanner_cards_total(registry: Registry) -> int:
    """Cumulative cards this agent has registered (scanner cycles + inbox
    drops) - unlike the pending count, this does not drop when triage clears."""
    return sum(1 for e in registry.entries()
               if e["entry_type"] == "card_registered"
               and str(e["payload"].get("extraction", {}).get("run_id", ""))
                   .endswith(("-scanner", "-inbox")))


# ---------------- inbox drop-folder (Coen-retrieved gated content) ----------

INBOX_SUFFIXES = {".html", ".htm", ".pdf", ".txt", ".md"}
RETRYABLE_FLAGGED = {"paywalled", "fetch_failed"}


def _norm_title(t: str) -> str:
    return " ".join((t or "").lower().split())


def match_flagged(title: str, seen: SeenStore) -> dict | None:
    """Match a dropped file's title to a flagged (paywalled/fetch_failed)
    seen-store item so the card inherits the original URL and source."""
    wanted = _norm_title(title)
    if not wanted:
        return None
    for event in seen._latest.values():
        if event["status"] in RETRYABLE_FLAGGED \
                and _norm_title(event.get("title") or "") == wanted:
            return event
    return None


def _inbox_identity(path: Path, title: str, seen: SeenStore):
    """Resolve (url, source_type, matched_event) for a dropped file: sidecar
    metadata wins, then title-match against flagged items, else None."""
    sidecar = path.with_name(path.name + ".meta.json")
    if sidecar.exists():
        import json as _json
        meta = _json.loads(sidecar.read_text(encoding="utf-8"))
        if meta.get("url"):
            return meta["url"], meta.get("source_type", "blog"), None
    event = match_flagged(title, seen)
    if event:
        source_type = "blog"
        return event["link"], source_type, event
    return None


def process_inbox(*, client, model: str, meter, seen: SeenStore,
                  registry: Registry, actions: ActionLog,
                  inbox: Path = LAYER / "inbox") -> dict:
    """Ingest files Coen retrieved legitimately (paywalled/WAF-gated items)
    through the normal extraction path. Identity comes from a .meta.json
    sidecar URL or a title match to a flagged item; unidentifiable files are
    left in place with a note so Coen can add a sidecar."""
    stats = {"files": 0, "cards_registered": 0, "honesty_dropped": 0,
             "skipped_no_identity": 0, "deferred_lock": 0}
    if not inbox.exists():
        return stats
    from .reader import read_source_text
    files = [p for p in sorted(inbox.iterdir())
             if p.is_file() and p.suffix.lower() in INBOX_SUFFIXES]
    for path in files:
        if not meter.can_spend():
            break
        if path.suffix.lower() in (".html", ".htm"):
            html = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", html,
                          re.IGNORECASE | re.DOTALL)
            title = " ".join(m.group(1).split()) if m else path.stem
            text = html_to_text(html)
        else:
            text = read_source_text(path)
            title = path.stem
        identity = _inbox_identity(path, title, seen)
        if identity is None:
            stats["skipped_no_identity"] += 1
            print(f"  inbox: no identity for {path.name} - add "
                  f"{path.name}.meta.json with a url", file=sys.stderr)
            continue
        url, source_type, event = identity
        source_meta = {"type": source_type, "title": title, "authors": [],
                       "year": None, "url": url, "doi": None, "isbn": None,
                       "credibility_tier": "practitioner"}
        run_id = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-inbox"
        known_claims = {c["claim"] for c in registry.cards().values()}

        logs_dir = Path(registry.log_path).parent / "logs"
        lock = ChainLock(logs_dir, holder="scanner",
                         purpose=f"inbox batch {path.name}")
        if lock.info() is not None:
            # Pre-gather probe: someone else is mid-write. Defer before
            # paying for the gather phase's LLM calls -- the inbox has no
            # deferred-status mechanism (unlike seen-store items), so the
            # simplest safe move is to leave the file and its sidecar
            # exactly where they are, unprocessed; the next process_inbox
            # call retries it from scratch. Probe only (.info(), never
            # acquire()): a false positive costs a free no-op deferral, a
            # false negative falls through to the real acquire() below,
            # which still guards the append -- no TOCTOU exposure.
            print(f"  inbox: chain.lock held, deferring {path.name}",
                  file=sys.stderr)
            stats["deferred_lock"] += 1
            continue

        # Gather every chunk's claims first (LLM calls only, no chain
        # writes) so the chain.lock window below covers just the
        # card-append batch — same pattern as the scanner's own extraction
        # path (_extract_item), per the spec: the lock is adopted by ALL of
        # the scanner's registration paths, inbox included.
        all_claims = []
        for label, chunk in chunk_text(text):
            claims, usage = extract_claims_usage(client, model, label, chunk)
            meter.record_call(model, usage, purpose="inbox_extract",
                              agent="reader")
            all_claims.extend(claims)

        registered = dropped = 0
        try:
            lock.acquire()
        except ChainLockHeld:
            # advisory, non-blocking: never wait on another writer's window.
            print(f"  inbox: chain.lock held, deferring {path.name}",
                  file=sys.stderr)
            stats["deferred_lock"] += 1
            continue
        try:
            for raw in all_claims:
                if not quote_in_source(raw["quote"], text):
                    dropped += 1
                    continue
                if raw["claim"] in known_claims:
                    continue
                known_claims.add(raw["claim"])
                registry.register_card(build_card(raw, source_meta, model, run_id))
                registered += 1
        finally:
            lock.release()
        if event is not None:
            seen.record(event["item_id"], event["source_id"], "extracted",
                        reason=f"{registered} cards via inbox")
        processed = inbox / "processed"
        processed.mkdir(exist_ok=True)
        path.replace(processed / path.name)
        sidecar = path.with_name(path.name + ".meta.json")
        if sidecar.exists():
            sidecar.replace(processed / sidecar.name)
        actions.event("inbox_ingested", {"file": path.name, "url": url,
                                         "cards": registered,
                                         "honesty_dropped": dropped})
        stats["files"] += 1
        stats["cards_registered"] += registered
        stats["honesty_dropped"] += dropped
    return stats


# ---------------- D27 quality-bar auto-admission ----------------------------

def process_auto_admissions(*, discovery_path, watchlist_path, actions) -> list[dict]:
    """Admit proposals meeting the D27 mechanical bar: scout-researched, or
    cited by >= 2 distinct verified sources. Honest provenance (added_by
    auto-d27), chain-logged, idempotent (admitted proposals flip status).
    Everything else stays queued for Coen."""
    import json as _json
    from .watchlist import (load_discovery, entry_domain, proposal_citers,
                            is_mechanically_admissible, watchlist_domains)
    entries = load_discovery(discovery_path)
    doc = _json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
    known = watchlist_domains(doc)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    admitted, queue_dirty = [], False
    for e in entries:
        if e.get("status") != "proposed" or not is_mechanically_admissible(e):
            continue
        domain = entry_domain(e)
        if domain in known:
            e["status"] = "auto_admitted"  # already present; just close it out
            queue_dirty = True
            continue
        found_in = e.get("found_in", "")
        raw_citers = set(e.get("cited_by") or [found_in.split("/", 1)[0]])
        is_scout = found_in.startswith("scout/") or "scout" in raw_citers
        citers = proposal_citers(e)
        rule = "scout-researched" if is_scout else \
               f"cited by {len(citers)} distinct verified sources"
        entry = {
            "id": domain, "class": "blog", "name": domain, "url": e["url"],
            "feed": None, "poll_minutes": DEFAULT_POLL_MINUTES_AUTO,
            "added_by": "auto-d27", "verified_date": today,
            "notes": (f"auto-admitted {today} per D27 ({rule}); Coen-revocable. "
                      "feed unset (HTML diff on url)."),
        }
        doc["sources"].append(entry)
        known.add(domain)
        e["status"] = "auto_admitted"
        queue_dirty = True
        admitted.append(entry)
        actions.event("source_auto_admitted",
                      {"domain": domain, "rule": rule, "url": e["url"]})
    if admitted:
        Path(watchlist_path).write_text(
            _json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    if queue_dirty:
        Path(discovery_path).write_text(
            "".join(_json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
            encoding="utf-8")
    return admitted


def pending_tier3_count(registry: Registry) -> int:
    """What still waits on Coen: cards in triage. Source proposals have been
    admitted or blocked mechanically (D27 cases 1-3) since the D27 case-3
    build (2026-08-23/24)."""
    return len(registry.cards(status="pending"))


def refresh_sources(watchlist_path, sources: list[dict],
                    next_due: dict[str, float]) -> list[dict]:
    """Reload the pollable watchlist after admissions/reviews/approvals may
    have changed it. `next_due` is updated in place: new ids default to
    due-now (0.0), ids no longer present are dropped.

    An EMPTY reload almost always means a load/write hazard (a crash
    mid-write, a transient bad-JSON read) rather than every verified source
    actually having been revoked in one cycle -- keep polling the previous
    `sources` list rather than silently going quiet, and leave `next_due`
    untouched so nothing loses its due time in the process."""
    reloaded = pollable(load_watchlist(watchlist_path))
    if not reloaded:
        print(f"WARNING: watchlist reload from {watchlist_path} came back "
              f"empty; keeping the previous {len(sources)} source(s) polling "
              "rather than treating it as 'everything revoked'", file=sys.stderr)
        return sources
    for s in reloaded:
        next_due.setdefault(s["id"], 0.0)
    keep_ids = {s["id"] for s in reloaded}
    for sid in [k for k in next_due if k not in keep_ids]:
        next_due.pop(sid)
    return reloaded


# ---------------- resident loop ----------------

def _load_api_key(env_path: Path) -> None:
    """Load the reader .env (sc-reader key, approval-signing key, ...) into
    the environment; existing env vars win."""
    import os
    path = Path(os.environ.get("READER_ENV_PATH", env_path))
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                os.environ.setdefault(name.strip(), value.strip())
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(f"no ANTHROPIC_API_KEY in env and none found at {path} "
                         "(sc-reader key; see reader CONTRACT.md sec. 5)")


def _cycle_status(seen: SeenStore, meter: BudgetMeter, registry: Registry,
                  discovery_path: Path, logs_dir: Path, sources_polled: int,
                  next_due_utc: str | None, watchlist_path: Path,
                  held: int = 0) -> None:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prob = probation_counts(watchlist_path, logs_dir / "reader_actions.jsonl")
    rejections: dict[str, int] = {}
    screen_log = logs_dir / "screen_log.jsonl"
    if screen_log.exists():
        import json as _json
        for line in screen_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = _json.loads(line)
            if row["ts_utc"][:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d") \
                    and row["decision"] == "screen_kill":
                rejections[row["reason"]] = rejections.get(row["reason"], 0) + 1
    new_by_source: dict[str, int] = {}
    for e in seen._latest.values():
        if e["first_seen_utc"][:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
            new_by_source[e["source_id"]] = new_by_source.get(e["source_id"], 0) + 1
    digest = write_digest(
        logs_dir, date=today, new_by_source=new_by_source,
        rejections=rejections,
        discoveries=[d["url"] for d in load_discovery(discovery_path)
                     if d["queued_utc"][:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d")],
        paywalled=[e["link"] for e in seen.items_with_status("paywalled").values()],
        spend_usd=meter.month_spend(),
        cards_registered=scanner_cards_total(registry),
        budget_state=meter.state(), probation=prob)
    budget_state = meter.state()
    overall = "OK" if budget_state == "OK" else "WARN"
    summary = (f"scanning; spend USD {meter.month_spend():.2f}"
               f"/{meter.monthly_cap_usd:.0f} ({budget_state})")
    write_status(
        logs_dir / "status.json", overall=overall, summary=summary,
        # Flat scalar keys, not a nested "probation" dict -- the /ops
        # dashboard types items values as strings and can't render a dict.
        items={"sources_polled": sources_polled,
               "items_seen_24h": seen.count_since(hours=24),
               "screened_pass": len(seen.items_with_status("screen_keep"))
                                + len(seen.items_with_status("extracted")),
               "extracted": len(seen.items_with_status("extracted")),
               "cards_registered": scanner_cards_total(registry),
               "budget": "WARN" if budget_state != "OK" else "OK",
               "probation_on": prob["on_probation"],
               "probation_admitted_30d": prob["admitted"],
               "probation_promoted_30d": prob["promoted"],
               "probation_revoked_30d": prob["revoked"],
               "probation_timed_out_30d": prob["timed_out"],
               "probation_blocked_30d": prob["blocked"],
               "probation_held": held},
        pending_tier3=pending_tier3_count(registry),
        digest_file=digest, next_run=next_due_utc)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--cap", type=float, default=20.0,   # D39: Reader 20
                    help="monthly USD cap (D33: 50 -> 35, fitting Reader plus "
                         "the pipeline's 20 inside D28's Intelligence band)")
    ap.add_argument("--watchlist", type=Path,
                    default=LAYER / "sources" / "verified_sources.json")
    ap.add_argument("--registry", type=Path, default=LAYER / "registry_log.jsonl")
    ap.add_argument("--interval", type=int, default=60,
                    help="loop wake interval, seconds")
    args = ap.parse_args(argv)

    _load_api_key(DEFAULT_READER_ENV)
    import anthropic
    client = anthropic.Anthropic()

    logs_dir = LAYER / "logs"
    logs_dir.mkdir(exist_ok=True)
    discovery_path = LAYER / "sources" / "discovery_queue.jsonl"
    seen = SeenStore(logs_dir / "seen_items.jsonl")
    meter = BudgetMeter(logs_dir / "budget_ledger.jsonl",
                        monthly_cap_usd=args.cap, agent="reader")
    registry = Registry(args.registry)
    actions = ActionLog(logs_dir / "reader_actions.jsonl")

    sources = pollable(load_watchlist(args.watchlist))
    if not sources:
        print("watchlist has no verified sources - nothing to poll. "
              "Coen must stamp verified_date first (Tier 3 gate).")
        return 1
    actions.event("scanner_started", {"verified_sources": len(sources),
                                      "model": args.model, "cap_usd": args.cap})
    print(f"scanner up: {len(sources)} verified sources, model {args.model}, "
          f"cap USD {args.cap}/month")

    next_due = {s["id"]: 0.0 for s in sources}
    warned_80 = meter.state() != "OK"
    try:
        while True:
            now = time.time()
            due = [s for s in sources if next_due[s["id"]] <= now]
            new_items: list[dict] = []
            for s in due:
                new_items.extend(poll_source(s, seen))
                next_due[s["id"]] = now + s["poll_minutes"] * 60
            # re-feed deferred + restart-orphaned items, retry-capped;
            # dedupe so this cycle's fresh polls are never fed twice
            if meter.can_spend():
                fresh_ids = {i["item_id"] for i in new_items}
                new_items.extend(i for i in refeedable_deferred(seen)
                                 if i["item_id"] not in fresh_ids)
            # D27 case 3: probation-source items screen first each cycle, capped
            # per source so one noisy probation feed can't starve the backlog.
            # Items over the cap stay 'seen'/'deferred' in the store and are
            # picked up by refeedable_deferred on a later cycle.
            probation_ids = {s["id"] for s in sources if tier_of(s) == "probation"}
            new_items, _held = prioritise_items(new_items, probation_ids)
            if _held:
                print(f"probation: {len(_held)} items held over the "
                      f"{PRIORITY_CAP}/source cap")
            try:
                if new_items:
                    stats = process_new_items(
                        new_items, client=client, model=args.model, meter=meter,
                        seen=seen, registry=registry, fetch=fetch_url,
                        watchlist_sources=sources, discovery_path=discovery_path,
                        screen_log=logs_dir / "screen_log.jsonl", actions=actions)
                    print(f"cycle: {stats}")
                inbox_stats = process_inbox(
                    client=client, model=args.model, meter=meter, seen=seen,
                    registry=registry, actions=actions)
                if inbox_stats["files"] or inbox_stats["deferred_lock"]:
                    print(f"inbox: {inbox_stats}")
                # D27 case 3: single-citation proposals -> prefilter -> source screen -> probation
                def _screen(domain, titles, about):
                    return screen_source(client, args.model, meter, domain, titles, about,
                                         logs_dir / "source_screen_log.jsonl")
                adm = process_admissions(discovery_path=discovery_path,
                                         watchlist_path=args.watchlist, actions=actions,
                                         screen=_screen, can_spend=meter.can_spend)
                rev = process_reviews(watchlist_path=args.watchlist,
                                      discovery_path=discovery_path, seen=seen, actions=actions)
                if adm["admitted"] or adm["blocked"] or rev["promoted"] or rev["revoked"] or rev["timed_out"]:
                    print(f"probation: +{len(adm['admitted'])} admitted, {len(adm['blocked'])} blocked, "
                          f"{len(rev['promoted'])} promoted, {len(rev['revoked'])} revoked, "
                          f"{len(rev['timed_out'])} timed out")
                sources = refresh_sources(args.watchlist, sources, next_due)
            except ApiCreditExhausted as exc:
                actions.event("api_unavailable", {"error": str(exc)[:300],
                                                  "backoff_s": CREDIT_BACKOFF_SECONDS})
                write_status(
                    logs_dir / "status.json", overall="FAIL",
                    summary=f"API unavailable (billing/credentials): {str(exc)[:120]}",
                    items={"budget": meter.state(), "api": "FAIL"},
                    pending_tier3=pending_tier3_count(registry),
                    digest_file=None, next_run=None)
                print(f"API unavailable; sleeping {CREDIT_BACKOFF_SECONDS}s: {exc}",
                      file=sys.stderr)
                if args.once:
                    return 2
                time.sleep(CREDIT_BACKOFF_SECONDS)
                continue
            # D26: consume Coen's signed source decisions from the Morpheus panel
            import os as _os
            approvals = process_approvals(
                queue_path=logs_dir / "approvals_queue.jsonl",
                watchlist_path=args.watchlist,
                discovery_path=discovery_path, actions=actions,
                state_path=logs_dir / "approvals_state.json",
                key=_os.environ.get("READER_APPROVAL_KEY", ""))
            for entry in approvals["approved"]:
                if entry["id"] not in next_due:
                    sources.append(entry)
                    next_due[entry["id"]] = 0.0  # poll the new source now
            # a block record can revoke a source that refresh_sources already
            # reloaded into `sources` earlier this cycle; drop it from
            # polling immediately rather than waiting for next cycle's reload
            for rid in approvals["revoked"]:
                sources = [s for s in sources if s["id"] != rid]
                next_due.pop(rid, None)
            if approvals["approved"] or approvals["blocked"] or approvals["invalid"]:
                print(f"approvals: +{len(approvals['approved'])} sources, "
                      f"{approvals['blocked']} blocked, "
                      f"{approvals['invalid']} invalid")
            # D27: mechanical quality-bar admissions (scout finds, 2+ citers)
            for entry in process_auto_admissions(
                    discovery_path=discovery_path,
                    watchlist_path=args.watchlist, actions=actions):
                if entry["id"] not in next_due:
                    sources.append(entry)
                    next_due[entry["id"]] = 0.0
                print(f"auto-admitted: {entry['id']} ({entry['notes'][:60]})")
            if meter.state() != "OK" and not warned_80:
                warned_80 = True
                actions.event("budget_alert", {"spend_usd": meter.month_spend(),
                                               "cap_usd": args.cap,
                                               "state": meter.state()})
            elif meter.state() == "OK":
                warned_80 = False
            # next_due can only be empty if refresh_sources's empty-reload
            # guard never fired and every source still vanished some other
            # way; fall back to a plain interval wake rather than crashing
            # min() on an empty sequence.
            upcoming = min(next_due.values()) if next_due else time.time() + args.interval
            next_due_utc = datetime.fromtimestamp(upcoming, tz=timezone.utc)\
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            _cycle_status(seen, meter, registry, discovery_path, logs_dir,
                          sources_polled=len(due), next_due_utc=next_due_utc,
                          watchlist_path=args.watchlist, held=len(_held))
            if args.once:
                return 0
            time.sleep(max(1, min(args.interval, upcoming - time.time())))
    except KeyboardInterrupt:
        actions.event("scanner_stopped", {"reason": "keyboard interrupt"})
        return 0


if __name__ == "__main__":
    sys.exit(run())
