"""Weekly source scout: web-search for quant research sources NOT yet known.

Candidates go ONLY to the Tier 3 discovery queue (sources/discovery_queue.jsonl)
for Coen's verification - the scout never ingests content and never touches the
watchlist. This widens discovery beyond the citation graph of verified sources
(the boxed-in risk Coen flagged 2026-08-15) while keeping D23's admission gate
fully human.

    python -m pipeline.scout            # one run (scheduled weekly:
                                        # StewartCo\\22_SourceScout)
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

from .watchlist import load_watchlist, load_discovery, queue_discovery
from .scanstatus import ActionLog

LAYER = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-sonnet-5"
SEARCH_USD = 0.01  # web search server tool: $10 per 1000 searches
MAX_SEARCHES = 8
MAX_CONTINUATIONS = 3

SCOUT_SYSTEM = """\
You are the source scout for Stewart & Co.'s quantitative research pipeline.
Your job: find sources of quantitative trading research that are NOT already
known to us - active blogs, practitioner sites, preprint venues, or research
groups publishing testable material about trading signals, portfolio
construction, execution, risk, market microstructure, or regime detection.

Use web search to find candidates. Rules:
- Only propose sources with substantive, recurring research output (not
  one-off posts, not social media accounts, not courses/marketing sites).
- Never propose domains from the known list the user gives you.
- Prefer sources with RSS/Atom feeds; note the feed URL if you find one.
- 5-10 strong candidates beat a long weak list."""

SCOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "name": {"type": "string"},
                    "why": {"type": "string"},
                    "source_class": {"enum": ["arxiv", "aggregator", "blog",
                                              "ssrn", "central_bank", "github"]},
                },
                "required": ["url", "name", "why", "source_class"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def _known_domains(watchlist_sources: list[dict], discovery: list[dict]) -> set[str]:
    from .scanner import _watchlist_domains
    from .watchlist import discovery_domain
    known = _watchlist_domains(watchlist_sources)
    known |= {e.get("domain") or discovery_domain(e["url"]) for e in discovery}
    return {d for d in known if d}


def run_scout(*, client, model: str, meter, watchlist_sources: list[dict],
              discovery_path: str | Path, actions: ActionLog,
              max_searches: int = MAX_SEARCHES) -> dict:
    if not meter.can_spend():
        return {"queued": 0, "searches": 0, "skipped": "budget"}

    known = _known_domains(watchlist_sources, load_discovery(discovery_path))
    user_prompt = ("Find new quant research sources. Domains already known "
                   "to us (never propose these):\n"
                   + "\n".join(sorted(known))
                   + "\n\nReturn your candidates per the schema.")
    messages = [{"role": "user", "content": user_prompt}]
    searches = 0
    msg = None
    for _ in range(MAX_CONTINUATIONS + 1):
        msg = client.messages.create(
            model=model,
            max_tokens=8000,
            system=SCOUT_SYSTEM,
            tools=[{"type": "web_search_20260209", "name": "web_search",
                    "max_uses": max_searches}],
            output_config={"format": {"type": "json_schema",
                                      "schema": SCOUT_SCHEMA}},
            messages=messages,
        )
        searches += sum(1 for b in msg.content
                        if getattr(b, "type", "") == "server_tool_use")
        if msg.stop_reason != "pause_turn":
            break
        messages = [{"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": msg.content}]
    meter.record_call(model, msg.usage, purpose="scout", agent="reader",
                      extra_usd=searches * SEARCH_USD)

    queued, candidates = 0, []
    if msg.stop_reason not in ("refusal",):
        text = next((b.text for b in msg.content
                     if getattr(b, "type", "") == "text"), None)
        if text:
            candidates = json.loads(text).get("candidates", [])
    from .watchlist import discovery_domain
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for cand in candidates:
        if discovery_domain(cand["url"]) in known:
            continue
        if queue_discovery(discovery_path, cand["url"],
                           found_in=f"scout/{date}",
                           reason=f"scout ({cand['source_class']}): "
                                  f"{cand['name']} - {cand['why']}"[:300]):
            queued += 1
    actions.event("scout_run", {"searches": searches, "candidates": len(candidates),
                                "queued": queued})
    return {"queued": queued, "searches": searches,
            "candidates": len(candidates)}


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-searches", type=int, default=MAX_SEARCHES)
    args = ap.parse_args(argv)

    from .scanner import _load_api_key, DEFAULT_READER_ENV
    from .budget import BudgetMeter
    _load_api_key(DEFAULT_READER_ENV)
    import anthropic
    client = anthropic.Anthropic()

    logs = LAYER / "logs"
    result = run_scout(
        client=client, model=args.model,
        meter=BudgetMeter(logs / "budget_ledger.jsonl", agent="reader"),
        watchlist_sources=load_watchlist(LAYER / "sources" / "verified_sources.json"),
        discovery_path=LAYER / "sources" / "discovery_queue.jsonl",
        actions=ActionLog(logs / "reader_actions.jsonl"),
        max_searches=args.max_searches)
    print(f"scout: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
