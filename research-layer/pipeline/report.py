"""Scanner checkpoint report: the funnel by source over a window.

Built for the contract's first live milestone (48h report: items seen /
screened / extracted / cards registered / spend) so the checkpoint is one
command instead of ad-hoc queries:

    python -m pipeline.report                 # last 48 hours
    python -m pipeline.report --hours 24
    python -m pipeline.report --since 2026-08-14T13:00:00Z
"""
from __future__ import annotations

import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

LAYER = Path(__file__).resolve().parent.parent

STATUS_COLUMNS = ("extracted", "screen_kill", "paywalled", "fetch_failed",
                  "deferred_screen", "deferred_budget", "screen_keep",
                  "extract_failed")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def build_report(seen_events: list[dict], screen_rows: list[dict],
                 ledger_rows: list[dict], discovery: list[dict],
                 since_utc: str) -> dict:
    # cohort = items first seen in the window; latest event per item wins
    latest: dict[str, dict] = {}
    for e in seen_events:
        latest[e["item_id"]] = e
    cohort = {iid: e for iid, e in latest.items()
              if e["first_seen_utc"] >= since_utc}

    per_source: dict[str, dict] = defaultdict(lambda: {
        "seen": 0, "cards": 0, **{c: 0 for c in STATUS_COLUMNS}})
    for e in cohort.values():
        row = per_source[e["source_id"]]
        row["seen"] += 1
        if e["status"] in row:
            row[e["status"]] += 1
        if e["status"] == "extracted" and e.get("reason"):
            m = re.match(r"(\d+) cards", e["reason"])
            if m:
                row["cards"] += int(m.group(1))
    for row in per_source.values():
        screened = row["extracted"] + row["screen_keep"] + row["screen_kill"]
        row["keep_rate"] = (round((row["extracted"] + row["screen_keep"])
                                  / screened, 2) if screened else None)

    window_screen = [r for r in screen_rows if r["ts_utc"] >= since_utc
                     and r["item_id"] in cohort]
    kill_reasons = Counter(r["reason"] for r in window_screen
                           if r["decision"] == "screen_kill")

    window_ledger = [r for r in ledger_rows if r["ts_utc"] >= since_utc]
    spend = {p: round(sum(r["usd"] for r in window_ledger if r["purpose"] == p), 4)
             for p in sorted({r["purpose"] for r in window_ledger})}
    spend["total"] = round(sum(r["usd"] for r in window_ledger), 4)

    return {
        "window": {
            "since_utc": since_utc,
            "items_seen": len(cohort),
            "extracted": sum(r["extracted"] for r in per_source.values()),
            "cards": sum(r["cards"] for r in per_source.values()),
            "paywalled": sum(r["paywalled"] for r in per_source.values()),
        },
        "per_source": dict(sorted(per_source.items())),
        "kill_reasons": dict(kill_reasons.most_common()),
        "spend": spend,
        "discoveries_queued": len([d for d in discovery
                                   if d["queued_utc"] >= since_utc]),
    }


def render_report(rep: dict) -> str:
    w = rep["window"]
    lines = [f"Scanner checkpoint report — window since {w['since_utc']}",
             "=" * 64, "",
             f"Items seen {w['items_seen']} | extracted {w['extracted']} | "
             f"cards {w['cards']} | paywalled {w['paywalled']} | "
             f"discoveries queued {rep['discoveries_queued']}", "",
             f"{'source':24} {'seen':>5} {'keep%':>6} {'extr':>5} "
             f"{'cards':>6} {'kill':>5} {'payw':>5} {'defer':>6}"]
    for src, r in rep["per_source"].items():
        keep = f"{r['keep_rate']:.0%}" if r["keep_rate"] is not None else "-"
        deferred = r["deferred_screen"] + r["deferred_budget"]
        lines.append(f"{src:24} {r['seen']:>5} {keep:>6} {r['extracted']:>5} "
                     f"{r['cards']:>6} {r['screen_kill']:>5} "
                     f"{r['paywalled']:>5} {deferred:>6}")
    lines += ["", "Window spend (USD):"]
    for purpose, usd in rep["spend"].items():
        lines.append(f"  {purpose}: {usd:.2f}")
    lines += ["", "Top kill reasons:"]
    top = list(rep["kill_reasons"].items())[:10]
    if top:
        lines += [f"  {n:>3}  {reason}" for reason, n in top]
    else:
        lines.append("  (none)")
    return "\n".join(lines) + "\n"


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--since", default=None,
                    help="ISO UTC timestamp; overrides --hours")
    args = ap.parse_args(argv)
    since = args.since or (datetime.now(timezone.utc)
                           - timedelta(hours=args.hours)
                           ).strftime("%Y-%m-%dT%H:%M:%SZ")
    logs = LAYER / "logs"
    rep = build_report(
        _load_jsonl(logs / "seen_items.jsonl"),
        _load_jsonl(logs / "screen_log.jsonl"),
        _load_jsonl(logs / "budget_ledger.jsonl"),
        _load_jsonl(LAYER / "sources" / "discovery_queue.jsonl"),
        since_utc=since)
    print(render_report(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
