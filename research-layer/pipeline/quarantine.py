"""Quarantine forward test: the paper-trading phase, run daily.

Quarantine stops being a state label and becomes a real out-of-sample record.
For every strategy in `quarantine` state this appends one `quarantine_decision`
per asset per trading day, computed by the same pipeline/engine.py executors
the screen and the gauntlet use. Paper-trading forward on bars that did not
exist at selection time cannot be gamed by search, which is why the
multiple-testing correction moves here rather than gating entry to this stage.

Usage:
    python -m pipeline.quarantine --date 2026-08-17
        [--registry registry_log.jsonl] [--data-dir data]
    python -m pipeline.quarantine --review [--artifacts-dir artifacts]

Conventions:
  * A decision for date D describes what the book DID on D's bar (entries fill
    at D's open on a signal from D-1's close) and its state at D's close. It is
    a record, not an instruction for D+1.
  * Bars up to and including D only. A date with no bar is REFUSED - a decision
    is never invented for a non-trading day.
  * Dates at or before a strategy's quarantine-entry date are skipped: they
    precede its forward record.
  * `equity` is rebased to 1.0 at the last bar on or before the entry date.
  * Idempotent per (strategy_id, date, asset), so a missed day can be
    backfilled without duplicating.

Graduation is NOT automatic. `--review` reports and writes nothing.
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path

from .registry import Registry
from .engine import simulate_asset
from .screen import load_bars

MIN_TRADING_DAYS = 60

CONE_CAVEAT = (
    "  NOTE: that cone is a terminal-equity distribution over the strategy's\n"
    "  FULL trade count, so it is NOT directly comparable to a short forward\n"
    "  record. The graduation comparison belongs to the quarantine -> live\n"
    "  gate, which is pre-declared in its own chained note and cannot bind\n"
    "  before {n} trading days accrue.")


def quarantine_entry_dates(registry: Registry) -> dict[str, str]:
    """{strategy_id: YYYY-MM-DD} taken from the state_change that moved it
    into quarantine. The state machine allows that transition once."""
    out: dict[str, str] = {}
    for e in registry.entries():
        if (e["entry_type"] == "state_change"
                and e["payload"].get("to") == "quarantine"):
            out[e["payload"]["strategy_id"]] = e["ts_utc"][:10]
    return out


def existing_decisions(registry: Registry) -> set[tuple[str, str, str]]:
    return {(e["payload"]["strategy_id"], e["payload"]["date"],
             e["payload"]["asset"])
            for e in registry.entries()
            if e["entry_type"] == "quarantine_decision"}


def _rebase_index(bars: list[dict], since: str) -> int:
    """Index of the last bar on or before `since` - the forward record's
    zero point. 0 if quarantine began before the data starts."""
    idx = 0
    for i, b in enumerate(bars):
        if b["date"] <= since:
            idx = i
    return idx


def decide(spec: dict, bars_by_asset: dict[str, list[dict]], date: str,
           since: str) -> list[dict]:
    """One decision row per asset in the spec's universe. `bars_by_asset` must
    already be truncated to bars <= date."""
    rows = []
    for asset in spec["universe"]["assets"]:
        bars = bars_by_asset[asset]
        if not bars or bars[-1]["date"] != date:
            raise ValueError(f"no {asset} bar for {date}")
        book = simulate_asset(spec["blocks"], bars, spec["cost_model"])
        pos = book["position"]
        exited = [t for t in book["trades"] if t["exit_date"] == date]
        if exited:
            # the engine never re-enters on the bar it exits (a signal seen
            # while in a position is ignored, not queued), so "exit" and an
            # entry can never both describe the same bar
            action = "exit"
        elif pos is not None and bars[pos["entry_i"]]["date"] == date:
            action = "enter_long" if pos["side"] == 1 else "enter_short"
        else:
            action = "hold"
        base = book["equity"][_rebase_index(bars, since)]
        rows.append({
            "strategy_id": spec["strategy_id"],
            "date": date,
            "asset": asset,
            "action": action,
            "price": bars[-1]["close"],
            "position_frac": pos["notional_frac"] if pos is not None else 0.0,
            "equity": book["equity"][-1] / base if base > 0 else 0.0,
        })
    return rows


def review(registry: Registry, quarantined: list[str],
           entered: dict[str, str], artifacts_dir: Path) -> int:
    """Report progress against the pre-declared minimum. WRITES NOTHING."""
    days: dict[str, set[str]] = {}
    for e in registry.entries():
        if e["entry_type"] == "quarantine_decision":
            days.setdefault(e["payload"]["strategy_id"], set()).add(
                e["payload"]["date"])
    if not quarantined:
        print("No strategies in 'quarantine' state.")
        return 0
    for sid in quarantined:
        n = len(days.get(sid, ()))
        verdict = ("ELIGIBLE FOR REVIEW" if n >= MIN_TRADING_DAYS
                   else "NOT YET ELIGIBLE")
        print(f"{sid}  entered {entered.get(sid, '?')}  "
              f"days {n}/{MIN_TRADING_DAYS}  {verdict}")
        mc_path = artifacts_dir / sid / "gauntlet" / "mc_summary.json"
        if mc_path.exists():
            mc = json.loads(mc_path.read_text(encoding="utf-8"))
            cone = " ".join(f"{k}={mc[k]:.4f}" for k in ("p25", "p50", "p75")
                            if k in mc)
            print(f"  gauntlet MC projection: {cone or '(no cone stored)'}")
        else:
            print("  gauntlet MC projection: (no mc_summary.json found)")
        print(CONE_CAVEAT.format(n=MIN_TRADING_DAYS))
    print("\nReview only — nothing written. Graduation is a separately "
          "human-gated decision.")
    return 0


def run(argv: list[str] | None = None) -> int:
    layer = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path, default=layer / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=layer / "data")
    ap.add_argument("--artifacts-dir", type=Path, default=layer / "artifacts")
    ap.add_argument("--date", help="YYYY-MM-DD; the trading day to record")
    ap.add_argument("--review", action="store_true",
                    help="report progress against the minimum; writes nothing")
    args = ap.parse_args(argv)

    if args.review == bool(args.date):
        print("Give exactly one of --date YYYY-MM-DD or --review.")
        return 1

    registry = Registry(args.registry)
    states = registry.strategy_states()
    entered = quarantine_entry_dates(registry)
    quarantined = sorted(sid for sid, st in states.items()
                         if st == "quarantine")

    if args.review:
        return review(registry, quarantined, entered, args.artifacts_dir)

    if not quarantined:
        print("No strategies in 'quarantine' state.")
        return 0

    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in registry.entries()
             if e["entry_type"] == "strategy_registered"}
    # every asset is loaded and checked BEFORE anything is chained: a day with
    # a hole in it is refused whole rather than recorded in part
    assets = sorted({a for sid in quarantined
                     for a in specs[sid]["universe"]["assets"]})
    bars_by_asset = {}
    for a in assets:
        bars = load_bars(args.data_dir, a, args.date)
        if not bars or bars[-1]["date"] != args.date:
            print(f"REFUSED: no {a} bar for {args.date}. A decision is never "
                  f"invented for a non-trading day; backfill the data or pick "
                  f"a real trading day.")
            return 1
        bars_by_asset[a] = bars

    seen = existing_decisions(registry)
    n_written = n_skipped = 0
    try:
        for sid in quarantined:
            since = entered.get(sid)
            if since is None:
                print(f"{sid}  skipped: no quarantine entry on the chain")
                continue
            if args.date <= since:
                print(f"{sid}  skipped: {args.date} is not after its "
                      f"quarantine entry {since}")
                continue
            for row in decide(specs[sid], bars_by_asset, args.date, since):
                key = (row["strategy_id"], row["date"], row["asset"])
                if key in seen:
                    n_skipped += 1
                    continue
                registry.record_quarantine_decision(row)
                seen.add(key)
                n_written += 1
                print(f"{sid}  {row['asset']}  {row['action']:<11} "
                      f"px={row['price']:.2f}  pos={row['position_frac']:.3f} "
                      f" eq={row['equity']:.4f}")
    except BaseException:
        print(f"\nPARTIAL WRITE: {n_written} decision(s) chained before "
              f"failure. Re-running this date is a no-op for what landed.",
              file=sys.stderr)
        raise

    print(f"\n{n_written} decision(s) chained, {n_skipped} already present.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
