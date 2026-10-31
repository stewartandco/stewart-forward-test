"""tools_benchmark_backfill_report.py -- SP4 Task B1 one-off backfill.

Pre-registered in docs/2026-08-24-sp4-track2a-addendum.md ("Pre-registration:
benchmark-relative control (B1, Coen 2026-08-26)"): recomputes
metrics["benchmark_relative"] -- a same-OOS-window buy-and-hold of the
cell's own asset, net of one round trip of the class's own cost model --
for the 96 equity_etf strategies eq-gen1 already carried into quarantine,
BEFORE gauntlet.py's own B1 wiring existed to record it live.

READ ONLY: this script never writes to registry_log.jsonl or artifacts/. It
reads the live chain (to find the 96-strategy eq-gen1 quarantine cohort),
each strategy's COMMITTED gauntlet artifact bundle (oos_trades.csv +
config.json, written by gauntlet.write_gauntlet_artifacts at verdict time),
and the pinned data/<ASSET>_1d.csv snapshots -- and writes exactly one new
file, the markdown report named on the command line.

This is a REPORT, not chain data (the addendum's own words): the live
registry_log.jsonl is append-only and stays untouched. The 96 strategies
here were verdicted before B1's gauntlet.py wiring shipped, so their chain
entries never gain this key retroactively -- only a verdict chained AFTER
that point carries metrics["benchmark_relative"] for real.

Usage:
    python tools_benchmark_backfill_report.py
        [--registry registry_log.jsonl] [--data-dir data]
        [--artifacts-dir artifacts]
        [--out docs/runs/2026-08-26-eq-gen1-benchmark-report.md]

Refuses (nonzero exit, nothing written) unless it finds EXACTLY the
declared 96-strategy eq-gen1 equity_etf quarantine cohort on the chain,
each with a complete artifact bundle and its cell's pinned CSV on disk --
naming whichever strategy or file is missing rather than silently
reporting on a partial set.
"""
from __future__ import annotations

import sys
import csv
import json
import argparse
import statistics
from pathlib import Path
from datetime import datetime, timezone

# Runnable from any cwd, exactly like verify_registry.py / tools_dryrun_fx.py.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:                    # idempotent under re-import
    sys.path.insert(0, _HERE)

from pipeline import cells                          # noqa: E402
from pipeline.gauntlet import contributions, compound  # noqa: E402
from pipeline.registry import Registry              # noqa: E402

LAYER_ROOT = Path(__file__).resolve().parent

# eq-gen1's sibling_group_id carries this literal marker (e.g.
# "regime_gated_range_breakout-2026-08-25-eq-gen1:SPY_1d") -- the same
# string every eq-gen1 registration and this report both key off, so a
# later generation's equity_etf strategies (Track 2b, bond/metal) can never
# be silently swept into this one-off report.
EQ_GEN1_MARKER = "eq-gen1"
# The known eq-gen1 quarantine cohort size (chained 2026-08-26: 96/96
# quarantine passes, all LONG index-ETF -- the finding that motivated B1's
# pre-registration in the first place). A rerun that finds a different
# count means the chain has moved since this report was designed, and must
# refuse rather than silently report on whatever it happens to find.
EXPECTED_N = 96


def find_eq_gen1_quarantine_cohort(registry: Registry) -> dict[str, dict]:
    """{sid: registered spec payload} for every equity_etf strategy that
    reached quarantine as part of eq-gen1 -- read from the chain itself,
    never a hardcoded sid list, so a rerun always reflects real chain
    state and would notice if the cohort ever changed."""
    registered: dict[str, dict] = {}
    for e in registry.entries():
        if e["entry_type"] == "strategy_registered":
            registered[e["payload"]["strategy_id"]] = e["payload"]

    cohort: dict[str, dict] = {}
    for e in registry.entries():
        if e["entry_type"] != "state_change" or e["payload"].get("to") != "quarantine":
            continue
        sid = e["payload"]["strategy_id"]
        spec = registered.get(sid)
        if spec is None:
            continue
        if spec["universe"].get("asset_class") != "equity_etf":
            continue
        if EQ_GEN1_MARKER not in spec["provenance"]["sibling_group_id"]:
            continue
        cohort[sid] = spec
    return cohort


def _bundle_dir(artifacts_dir: Path, sid: str) -> Path:
    return artifacts_dir / sid / "gauntlet"


def check_complete(cohort: dict[str, dict], artifacts_dir: Path,
                   data_dir: Path) -> list[str]:
    """Every reason this cohort is NOT ready to report on, named one per
    line -- missing artifact files, missing data files, or a spec whose
    universe does not fit B1's single-asset assumption. Empty means every
    input this report needs is present on disk."""
    problems = []
    for sid, spec in sorted(cohort.items()):
        bundle = _bundle_dir(artifacts_dir, sid)
        if not (bundle / "config.json").exists():
            problems.append(f"{sid}: missing {bundle / 'config.json'}")
        if not (bundle / "oos_trades.csv").exists():
            problems.append(f"{sid}: missing {bundle / 'oos_trades.csv'}")
        assets = spec["universe"]["assets"]
        if len(assets) != 1:
            problems.append(
                f"{sid}: benchmark-relative needs exactly one asset per "
                f"cell, got {assets!r}")
            continue
        timeframe = spec["universe"].get("timeframe", "1d")
        data_path = data_dir / f"{assets[0]}_{timeframe}.csv"
        if not data_path.exists():
            problems.append(f"{sid}: missing {data_path}")
    return problems


def load_oos_trades(bundle: Path) -> list[dict]:
    with (bundle / "oos_trades.csv").open(
            "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["return_net"] = float(r["return_net"])
        r["notional_frac"] = float(r["notional_frac"])
    return rows


def load_bars(data_dir: Path, asset: str, timeframe: str) -> list[dict]:
    path = data_dir / f"{asset}_{timeframe}.csv"
    bars = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bars.append({"date": row["date"], "open": float(row["open"]),
                        "close": float(row["close"])})
    bars.sort(key=lambda b: b["date"])
    return bars


def benchmark_row(sid: str, spec: dict, artifacts_dir: Path,
                  data_dir: Path) -> dict:
    """One report row. `strategy_net` is computed the exact way the live
    oos_negative gate and B1's gauntlet._benchmark_relative both compute
    it -- compound(contributions(oos_trades)) -- over the COMMITTED
    oos_trades.csv (the actual chained OOS trade set, never a
    re-simulation). `buy_hold_net` is B1's own formula: entry at the first
    post-cutoff bar's open, exit at the last bar's close, net of one round
    trip of the spec's OWN committed cost_model (read from config.json as
    recorded at generation time, never from today's cells.py, so a later
    cost-model change can never quietly move a backfilled number)."""
    bundle = _bundle_dir(artifacts_dir, sid)
    config = json.loads((bundle / "config.json").read_text(encoding="utf-8"))
    cutoff = config["cutoff"]

    oos_trades = load_oos_trades(bundle)
    strategy_net = compound(contributions(oos_trades))

    asset = spec["universe"]["assets"][0]
    timeframe = spec["universe"].get("timeframe", "1d")
    bars = load_bars(data_dir, asset, timeframe)
    oos_bars = [b for b in bars if b["date"] > cutoff]
    if not oos_bars:
        raise ValueError(
            f"{sid}: no bars for {asset!r} after cutoff {cutoff} -- cannot "
            f"compute the benchmark-relative control")
    entry_px, exit_px = oos_bars[0]["open"], oos_bars[-1]["close"]
    cost_model = config["spec"]["cost_model"]
    per_side = cost_model["commission_per_side"] + cost_model["slippage_ticks"]
    buy_hold_net = (exit_px / entry_px - 1) - 2 * per_side

    return {"sid": sid, "cell": cells.cell_id(asset, timeframe),
            "family": spec["family"], "strategy_net": strategy_net,
            "buy_hold_net": buy_hold_net, "excess": strategy_net - buy_hold_net}


def render_report(rows: list[dict], generated_utc: datetime) -> str:
    rows_sorted = sorted(rows, key=lambda r: (r["family"], r["cell"], r["sid"]))
    n_positive = sum(1 for r in rows if r["excess"] > 0)
    median_excess = statistics.median(r["excess"] for r in rows)

    lines = [
        "# eq-gen1 benchmark-relative backfill report",
        "",
        "One-off analysis, NOT chain data. Recomputes SP4 Task B1's "
        "`metrics[\"benchmark_relative\"]` (a same-OOS-window buy-and-hold "
        "of the cell's own asset, net of one round trip of the class's "
        "cost model) for the 96 equity_etf strategies eq-gen1 already "
        "carried into quarantine, from their committed gauntlet artifacts "
        "and the pinned `data/` CSVs -- these strategies were verdicted "
        "before B1's `gauntlet.py` wiring existed, so their chain entries "
        "never carry this key retroactively; only a verdict chained after "
        "B1 shipped does. Pre-registered in "
        "`docs/2026-08-24-sp4-track2a-addendum.md` (\"Pre-registration: "
        "benchmark-relative control (B1, Coen 2026-08-26)\"). RECORDED, "
        "NOT GATED -- nothing here changes any strategy's quarantine "
        "state.",
        "",
        f"Generated {generated_utc:%Y-%m-%d} UTC, {len(rows)} strategies.",
        "",
        "| sid | cell | family | oos strategy_net | buy_hold_net | excess |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows_sorted:
        lines.append(
            f"| {r['sid']} | {r['cell']} | {r['family']} | "
            f"{r['strategy_net']:+.4%} | {r['buy_hold_net']:+.4%} | "
            f"{r['excess']:+.4%} |")
    lines += [
        "",
        "## Summary",
        "",
        f"- n = {len(rows)}",
        f"- n with excess > 0: {n_positive} / {len(rows)}",
        f"- median excess: {median_excess:+.4%}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=LAYER_ROOT / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=LAYER_ROOT / "data")
    ap.add_argument("--artifacts-dir", type=Path,
                    default=LAYER_ROOT / "artifacts")
    ap.add_argument("--out", type=Path, default=LAYER_ROOT / "docs" / "runs"
                    / "2026-08-26-eq-gen1-benchmark-report.md")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    cohort = find_eq_gen1_quarantine_cohort(registry)
    if len(cohort) != EXPECTED_N:
        print(f"REFUSED: expected exactly {EXPECTED_N} eq-gen1 equity_etf "
             f"quarantine strategies on the chain at {args.registry}, "
             f"found {len(cohort)}. Nothing written.", file=sys.stderr)
        for sid in sorted(cohort):
            print(f"  found: {sid}", file=sys.stderr)
        return 1

    problems = check_complete(cohort, args.artifacts_dir, args.data_dir)
    if problems:
        print(f"REFUSED: {len(problems)} problem(s) across the "
             f"{EXPECTED_N}-strategy cohort. Nothing written.",
             file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    rows = [benchmark_row(sid, spec, args.artifacts_dir, args.data_dir)
           for sid, spec in cohort.items()]
    report = render_report(rows, datetime.now(timezone.utc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} strategies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
