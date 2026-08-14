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

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .watchlist import (load_watchlist, pollable, queue_discovery,
                        load_discovery)
from .feeds import (parse_feed, extract_links, item_id, html_to_text,
                    looks_paywalled, fetch_url)
from .seen import SeenStore
from .budget import BudgetMeter
from .relevance import screen_items
from .scanstatus import ActionLog, write_status, write_digest
from .registry import Registry
from .common import quote_in_source
from .reader import build_card, chunk_text, extract_claims_usage

LAYER = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-sonnet-5"  # D23 throughput-first: Sonnet for screen AND bulk extraction
DEFAULT_READER_ENV = Path(r"E:\Users\Coen\Claude\stewartandco-agents\hubs\intelligence\agents\reader\.env")
MAX_DISCOVERIES_PER_ITEM = 10

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
    else:  # HTML listing diff: every on-page link is a candidate item
        items = [{"source_id": source["id"], "item_id": item_id(source["id"], link),
                  "title": title or link, "link": link, "summary": "",
                  "published": None}
                 for link, title in extract_links(text, final_url)]
    fresh = [it for it in items if not seen.is_seen(it["item_id"])]
    for it in fresh:
        seen.record(it["item_id"], it["source_id"], "seen",
                    title=it["title"], link=it["link"])
    return fresh


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
    discovery queueing. Returns (cards_registered, honesty_dropped)."""
    source_meta = {
        "type": CLASS_TO_SOURCE_TYPE[source["class"]],
        "title": item["title"], "authors": [], "year": None,
        "url": item["link"], "doi": None, "isbn": None,
        "credibility_tier": "practitioner",
    }
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-scanner"
    registered = dropped = 0
    for label, chunk in chunk_text(page_text):
        claims, usage = extract_claims_usage(client, model, label, chunk)
        meter.record_call(model, usage, purpose="extract")
        for raw in claims:
            if not quote_in_source(raw["quote"], page_text):
                dropped += 1
                continue
            if raw["claim"] in known_claims:
                continue
            known_claims.add(raw["claim"])
            card = build_card(raw, source_meta, model, run_id)
            registry.register_card(card)
            registered += 1
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
    return registered, dropped


def process_new_items(new_items: list[dict], *, client, model: str, meter,
                      seen: SeenStore, registry: Registry, fetch,
                      watchlist_sources: list[dict], discovery_path,
                      screen_log, actions: ActionLog) -> dict:
    stats = {"items": len(new_items), "screen_keep": 0, "screen_kill": 0,
             "deferred": 0, "paywalled": 0, "fetch_failed": 0,
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
        try:
            registered, dropped = _extract_item(
                client, model, item, src_by_id[item["source_id"]], page_text,
                html, meter=meter, registry=registry, known_claims=known_claims,
                discovery_path=discovery_path,
                watchlist_sources=watchlist_sources)
        except Exception as exc:
            seen.record(item["item_id"], item["source_id"], "extract_failed",
                        reason=f"{type(exc).__name__}: {exc}"[:200])
            print(f"  extract failed for {item['link']}: {exc}", file=sys.stderr)
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
    """Cumulative cards the scanner has ever registered (run_id *-scanner) -
    unlike the pending count, this does not drop when triage clears cards."""
    return sum(1 for e in registry.entries()
               if e["entry_type"] == "card_registered"
               and str(e["payload"].get("extraction", {}).get("run_id", ""))
                   .endswith("-scanner"))


def pending_tier3_count(registry: Registry, discovery_path) -> int:
    """The two things waiting on Coen: discovery proposals + cards in triage."""
    proposals = [d for d in load_discovery(discovery_path)
                 if d["status"] == "proposed"]
    return len(proposals) + len(registry.cards(status="pending"))


# ---------------- resident loop ----------------

def _load_api_key(env_path: Path) -> None:
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    path = Path(os.environ.get("READER_ENV_PATH", env_path))
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY=") and not line.startswith("#"):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                return
    raise SystemExit(f"no ANTHROPIC_API_KEY in env and none found at {path} "
                     "(sc-reader key; see reader CONTRACT.md sec. 5)")


def _cycle_status(seen: SeenStore, meter: BudgetMeter, registry: Registry,
                  discovery_path: Path, logs_dir: Path, sources_polled: int,
                  next_due_utc: str | None) -> None:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
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
        budget_state=meter.state())
    budget_state = meter.state()
    overall = "OK" if budget_state == "OK" else "WARN"
    summary = (f"scanning; spend USD {meter.month_spend():.2f}"
               f"/{meter.monthly_cap_usd:.0f} ({budget_state})")
    write_status(
        logs_dir / "status.json", overall=overall, summary=summary,
        items={"sources_polled": sources_polled,
               "items_seen_24h": seen.count_since(hours=24),
               "screened_pass": len(seen.items_with_status("screen_keep"))
                                + len(seen.items_with_status("extracted")),
               "extracted": len(seen.items_with_status("extracted")),
               "cards_registered": scanner_cards_total(registry),
               "budget": "WARN" if budget_state != "OK" else "OK"},
        pending_tier3=pending_tier3_count(registry, discovery_path),
        digest_file=digest, next_run=next_due_utc)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--cap", type=float, default=25.0, help="monthly USD cap")
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
    meter = BudgetMeter(logs_dir / "budget_ledger.jsonl", monthly_cap_usd=args.cap)
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
            # re-feed items deferred earlier (budget/screen misses)
            for status in ("deferred_budget", "deferred_screen"):
                for iid, e in list(seen.items_with_status(status).items()):
                    if meter.can_spend() and e.get("link"):
                        new_items.append({"source_id": e["source_id"],
                                          "item_id": iid, "title": e.get("title") or "",
                                          "link": e["link"], "summary": "",
                                          "published": None})
            if new_items:
                stats = process_new_items(
                    new_items, client=client, model=args.model, meter=meter,
                    seen=seen, registry=registry, fetch=fetch_url,
                    watchlist_sources=sources, discovery_path=discovery_path,
                    screen_log=logs_dir / "screen_log.jsonl", actions=actions)
                print(f"cycle: {stats}")
            if meter.state() != "OK" and not warned_80:
                warned_80 = True
                actions.event("budget_alert", {"spend_usd": meter.month_spend(),
                                               "cap_usd": args.cap,
                                               "state": meter.state()})
            elif meter.state() == "OK":
                warned_80 = False
            upcoming = min(next_due.values())
            next_due_utc = datetime.fromtimestamp(upcoming, tz=timezone.utc)\
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            _cycle_status(seen, meter, registry, discovery_path, logs_dir,
                          sources_polled=len(due), next_due_utc=next_due_utc)
            if args.once:
                return 0
            time.sleep(max(1, min(args.interval, upcoming - time.time())))
    except KeyboardInterrupt:
        actions.event("scanner_stopped", {"reason": "keyboard interrupt"})
        return 0


if __name__ == "__main__":
    sys.exit(run())
