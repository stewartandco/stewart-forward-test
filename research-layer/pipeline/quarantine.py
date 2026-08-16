"""Quarantine forward test: the paper-trading phase, run daily.

Quarantine stops being a state label and becomes a real out-of-sample record.
For every strategy in `quarantine` state this appends one `quarantine_decision`
per asset per trading day, computed by the same pipeline/engine.py executors
the screen and the gauntlet use. Paper-trading forward on bars that did not
exist at selection time cannot be gamed by search, which is why the
multiple-testing correction moves here rather than gating entry to this stage.

Search-gaming is closed by construction, but SELECTIVE RECORDING is not: the
runner is deliberately idempotent and backfillable, so a missed day can be
filled in later. Nothing about that is hidden. `--review` reconstructs the days
a strategy OWED from the price files themselves and reports every bar date with
no decision, plus how long after its bar each row was actually chained
(`ts_utc` vs `payload.date`). A record kept faithfully every day and a record
backfilled to flatter a strategy do not look the same in that report.

Usage:
    python -m pipeline.quarantine --date 2026-08-17
        [--registry registry_log.jsonl] [--data-dir data]
    python -m pipeline.quarantine --review
        [--data-dir data] [--artifacts-dir artifacts]

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

from .registry import Registry, parse_iso_date
from .engine import simulate_asset
from .screen import load_bars

MIN_TRADING_DAYS = 60
# a row chained more than this many days after its own bar is a backfill, not
# a forward observation, and is reported as such
BACKFILL_LAG_DAYS = 2
MAX_LISTED_GAPS = 12

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


def decision_entries(registry: Registry) -> list[dict]:
    """Every chained decision as {strategy_id, date, asset, ts_utc}. `ts_utc`
    is the WRITE time and `date` the BAR date; the gap between them is what
    makes a late backfill visible."""
    return [{"strategy_id": e["payload"]["strategy_id"],
             "date": e["payload"]["date"],
             "asset": e["payload"]["asset"],
             "ts_utc": e["ts_utc"]}
            for e in registry.entries()
            if e["entry_type"] == "quarantine_decision"]


def existing_decisions(registry: Registry) -> set[tuple[str, str, str]]:
    """Cheap in-process pre-filter only. The authoritative de-duplication is
    Registry.record_quarantine_decision, which re-checks under the lock."""
    return {(d["strategy_id"], d["date"], d["asset"])
            for d in decision_entries(registry)}


def _rebase_index(bars: list[dict], entered: str, asset: str) -> int:
    """Index of the last bar on or before `entered` - the forward record's
    zero point."""
    idx = None
    for i, b in enumerate(bars):
        if b["date"] <= entered:
            idx = i
    if idx is None:
        raise ValueError(
            f"{asset}: no bar on or before the quarantine entry date "
            f"{entered}, so the forward record has no zero point (data starts "
            f"at {bars[0]['date']}). Rebasing on the first available bar would "
            f"silently fold pre-quarantine performance into the record.")
    return idx


def observe_day(spec: dict, bars_by_asset: dict[str, list[dict]], date: str,
                entered: str) -> list[dict]:
    """One decision row per asset in the spec's universe: a RECORD of what the
    book did on `date`, not an instruction. `bars_by_asset` must already be
    truncated to bars <= date."""
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
        base = book["equity"][_rebase_index(bars, entered, asset)]
        if base <= 0:
            # reachable: a short gapped through its stop can drive
            # mark-to-market equity non-positive. The ratio is undefined, and
            # recording 0.0 would read as "the book was wiped out".
            raise ValueError(
                f"{asset}: equity baseline at {entered} is {base!r}, so the "
                f"rebased forward equity for {date} is undefined")
        rows.append({
            "strategy_id": spec["strategy_id"],
            "date": date,
            "asset": asset,
            "action": action,
            "price": bars[-1]["close"],
            "position_frac": pos["notional_frac"] if pos is not None else 0.0,
            "equity": book["equity"][-1] / base,
        })
    return rows


def _load_day_bars_or_refuse(data_dir: Path, assets: list[str],
                             date: str) -> dict[str, list[dict]] | None:
    """Bars <= date for every asset, or None after printing the refusal.

    All-or-nothing, and resolved BEFORE anything is chained: a day with a hole
    in it is refused whole rather than recorded in part.
    """
    bars_by_asset: dict[str, list[dict]] = {}
    for asset in assets:
        path = data_dir / f"{asset}_1d.csv"
        if not path.exists():
            print(f"REFUSED: no price file for {asset} at {path}.",
                  file=sys.stderr)
            return None
        bars = load_bars(data_dir, asset, date)
        if not bars or bars[-1]["date"] != date:
            print(f"REFUSED: no {asset} bar for {date}. A decision is never "
                  f"invented for a non-trading day; backfill the data or pick "
                  f"a real trading day.", file=sys.stderr)
            return None
        bars_by_asset[asset] = bars
    return bars_by_asset


def _owed_dates(data_dir: Path, spec: dict, after: str,
                through: str) -> set[str] | None:
    """Every bar date in (after, through] across the strategy's universe, i.e.
    the days it owed a decision. None if a price file is missing."""
    owed: set[str] = set()
    for asset in spec["universe"]["assets"]:
        if not (data_dir / f"{asset}_1d.csv").exists():
            return None
        for b in load_bars(data_dir, asset, through):
            if b["date"] > after:
                owed.add(b["date"])
    return owed


def _report_completeness(registry_rows: list[dict], spec: dict | None,
                         entered: str | None, data_dir: Path) -> None:
    """The audit half of --review: what is missing, and how late what is there
    arrived. Both are computed from evidence already on the chain and on disk,
    not from anything the runner asserts about itself."""
    if not registry_rows:
        print("  completeness: no decisions chained yet")
        return

    recorded = {r["date"] for r in registry_rows}
    through = max(recorded)
    if spec is None or entered is None:
        print("  completeness: cannot audit (no spec or entry date on chain)")
    else:
        owed = _owed_dates(data_dir, spec, entered, through)
        if owed is None:
            print("  completeness: cannot audit (price file missing for part "
                  "of the universe)")
        else:
            missing = sorted(owed - recorded)
            shown = ", ".join(missing[:MAX_LISTED_GAPS])
            if len(missing) > MAX_LISTED_GAPS:
                shown += f", ... (+{len(missing) - MAX_LISTED_GAPS} more)"
            print(f"  unrecorded bar dates in window: {len(missing)}"
                  + (f"  ({shown})" if missing else ""))

    lags = [(parse_iso_date(r["ts_utc"][:10]) - parse_iso_date(r["date"])).days
            for r in registry_rows]
    late = [n for n in lags if n > BACKFILL_LAG_DAYS]
    if late:
        print(f"  backfill lag: max {max(lags)}d, {len(late)} row(s) chained "
              f"more than {BACKFILL_LAG_DAYS}d after their bar")
    else:
        print(f"  backfill lag: max {max(lags)}d, every row chained within "
              f"{BACKFILL_LAG_DAYS}d of its bar")


def review(registry: Registry, quarantined: list[str],
           entered: dict[str, str], specs: dict[str, dict],
           artifacts_dir: Path, data_dir: Path) -> int:
    """Report progress against the pre-declared minimum. WRITES NOTHING."""
    if not quarantined:
        print("No strategies in 'quarantine' state.")
        return 0

    rows_by_sid: dict[str, list[dict]] = {}
    for r in decision_entries(registry):
        rows_by_sid.setdefault(r["strategy_id"], []).append(r)

    for sid in quarantined:
        rows = rows_by_sid.get(sid, [])
        n = len({r["date"] for r in rows})       # a date counts once, not once per asset
        verdict = ("ELIGIBLE FOR REVIEW" if n >= MIN_TRADING_DAYS
                   else "NOT YET ELIGIBLE")
        print(f"{sid}  entered {entered.get(sid, '?')}  "
              f"days {n}/{MIN_TRADING_DAYS}  {verdict}")
        _report_completeness(rows, specs.get(sid), entered.get(sid), data_dir)

        mc_path = artifacts_dir / sid / "gauntlet" / "mc_summary.json"
        if not mc_path.exists():
            print("  gauntlet MC projection: (no mc_summary.json found)")
            continue
        mc = json.loads(mc_path.read_text(encoding="utf-8"))
        cone = " ".join(f"{k}={mc[k]:.4f}" for k in ("p25", "p50", "p75")
                        if k in mc)
        if not cone:
            print("  gauntlet MC projection: (no cone stored)")
            continue
        print(f"  gauntlet MC projection: {cone}")
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
        print("Give exactly one of --date YYYY-MM-DD or --review.",
              file=sys.stderr)
        return 1
    if args.date is not None:
        try:
            parse_iso_date(args.date, "--date")
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1

    # A missing path must never read as "nothing to do": Registry.entries()
    # returns silently on a missing file, so a wrong path, a moved cwd or a
    # scheduler quirk would otherwise report success and leave an invisible
    # hole in the forward record.
    if not args.registry.exists():
        print(f"REFUSED: no registry at {args.registry}. The forward record "
              f"cannot be appended to a chain that is not there.",
              file=sys.stderr)
        return 1
    if not args.data_dir.is_dir():
        print(f"REFUSED: no data directory at {args.data_dir}. Decisions are "
              f"computed from bars, and their completeness is audited against "
              f"the price files.", file=sys.stderr)
        return 1

    registry = Registry(args.registry)
    states = registry.strategy_states()
    entered = quarantine_entry_dates(registry)
    quarantined = sorted(sid for sid, st in states.items()
                         if st == "quarantine")
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in registry.entries()
             if e["entry_type"] == "strategy_registered"}

    if args.review:
        return review(registry, quarantined, entered, specs,
                      args.artifacts_dir, args.data_dir)

    if not quarantined:
        print("No strategies in 'quarantine' state.")
        return 0

    # Resolve who is actually owed a decision BEFORE touching the data, so a
    # gap in an asset nobody is trading today cannot refuse everyone's day.
    eligible = []
    for sid in quarantined:
        since = entered.get(sid)
        if sid not in specs:
            print(f"{sid}  skipped: no strategy_registered entry on the chain")
        elif since is None:
            print(f"{sid}  skipped: no quarantine entry on the chain")
        elif args.date <= since:
            print(f"{sid}  skipped: {args.date} is not after its "
                  f"quarantine entry {since}")
        else:
            eligible.append(sid)

    bars_by_asset: dict[str, list[dict]] = {}
    if eligible:
        assets = sorted({a for sid in eligible
                         for a in specs[sid]["universe"]["assets"]})
        loaded = _load_day_bars_or_refuse(args.data_dir, assets, args.date)
        if loaded is None:
            return 1
        bars_by_asset = loaded

    seen = existing_decisions(registry)
    n_written = n_skipped = 0
    try:
        for sid in eligible:
            for row in observe_day(specs[sid], bars_by_asset, args.date,
                                   entered[sid]):
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
