"""tools_benchmark_backfill_report_crypto.py -- SP5 Task 5 pooled-crypto audit.

Pre-registered in docs/2026-08-28-market-data-universe-design.md (s7):
reports how the 20 pooled-crypto quarantine strategies (the whole live
crypto quarantine cohort, every one registered on the pooled
["BTCUSD", "ETHUSD"] universe) compare to THREE buy-and-hold controls,
each net of ONE round trip of the spec's OWN committed cost_model
(per_side = commission_per_side + slippage_ticks, cost = 2 * per_side):

  1. btc_hold    -- last OOS close / first OOS open - 1, on BTCUSD bars.
  2. eth_hold    -- the same, on ETHUSD bars.
  3. basket_hold -- 50/50 daily-rebalanced, matching the engine's
     mean-combine: over the SHARED OOS calendar (dates present in both
     assets' OOS bars), per-day return of each asset =
     close_t / close_{t-1} - 1 (close-to-close); day-1 return of each
     asset = close_1 / open_1 - 1 (entry at the first shared OOS open);
     basket day return = mean of the two per-asset day returns;
     basket_net = compound of the basket day returns - 2 * per_side.

READ ONLY: this script never writes to registry_log.jsonl or artifacts/. It
writes exactly one new file, this report. It reads the chain (to find the
crypto quarantine cohort -- a sid whose LAST state on the chain is
"quarantine", never a hardcoded sid list), each strategy's COMMITTED
gauntlet artifact bundle (config.json + oos_trades.csv, written by
gauntlet.write_gauntlet_artifacts at verdict time), and the pinned
data/BTCUSD_1d.csv + data/ETHUSD_1d.csv snapshots.

`strategy_net` is compound(contributions(oos_trades)) over the COMMITTED
oos_trades.csv -- the actual chained OOS trade set, never a re-simulation.

OOS window: bars with date strictly AFTER the committed config.json cutoff,
compared via [:10] slices ONLY (pipeline.gauntlet._date_le). This fixes the
eq backfill script's raw-string compare asymmetry (a time-suffixed bar
landing exactly on the cutoff date sorted lexicographically AFTER a bare
cutoff there); the eq script itself is deliberately left unmodified -- its
report is already committed history.

The eq-gen1 precedent's lesson stands ready here: 0/96 equity passes beat
their own ETF's buy-and-hold. If all 20 pooled-crypto strategies are beta,
that is the finding.

Usage:
    python tools_benchmark_backfill_report_crypto.py
        [--registry registry_log.jsonl] [--artifacts artifacts]
        [--data-dir data]
        [--out docs/runs/2026-08-28-crypto-pooled-benchmark-report.md]

Refuses (exit 2, nothing written) unless it finds EXACTLY the declared
20-strategy pooled-crypto quarantine cohort on the chain, every member on
the ["BTCUSD", "ETHUSD"] universe, each with a complete committed artifact
bundle and both pinned data CSVs on disk -- naming whichever strategy or
file is wrong rather than silently reporting on a partial set.
"""
from __future__ import annotations

import sys
import csv
import json
import argparse
import statistics
from pathlib import Path
from datetime import datetime, timezone

# Runnable from any cwd, exactly like tools_benchmark_backfill_report.py.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:                    # idempotent under re-import
    sys.path.insert(0, _HERE)

from pipeline.gauntlet import contributions, compound, _date_le  # noqa: E402
from pipeline.registry import Registry, edge_numbers, edge_label  # noqa: E402

LAYER_ROOT = Path(__file__).resolve().parent

# The pooled-crypto universe every cohort member MUST be registered on.
POOLED_ASSETS = ["BTCUSD", "ETHUSD"]
# The known pooled-crypto quarantine cohort size (chain state at design
# time, 2026-08-28: 20 crypto strategies whose last state is quarantine,
# all on the pooled BTC+ETH universe). A run that finds a different count
# means the chain has moved since this report was designed, and must refuse
# rather than silently report on whatever it happens to find.
EXPECTED_N = 20


def find_crypto_quarantine_cohort(registry: Registry) -> dict[str, dict]:
    """{sid: registered spec payload} for every crypto strategy whose LAST
    state on the chain is "quarantine" -- last state wins, so a strategy
    that passed through quarantine into the graveyard is OUT. Read from the
    chain itself, never a hardcoded sid list. Raises if any cohort member
    is not on the pooled ["BTCUSD", "ETHUSD"] universe: the three controls
    below are only meaningful against exactly that basket."""
    registered: dict[str, dict] = {}
    last_state: dict[str, str] = {}
    for e in registry.entries():
        if e["entry_type"] == "strategy_registered":
            registered[e["payload"]["strategy_id"]] = e["payload"]
        elif e["entry_type"] == "state_change":
            last_state[e["payload"]["strategy_id"]] = e["payload"].get("to")

    cohort: dict[str, dict] = {}
    for sid, state in last_state.items():
        if state != "quarantine":
            continue
        spec = registered.get(sid)
        if spec is None:
            continue
        if spec["universe"].get("asset_class") != "crypto":
            continue
        if spec["universe"]["assets"] != POOLED_ASSETS:
            raise ValueError(
                f"{sid}: crypto quarantine strategy on "
                f"{spec['universe']['assets']!r}, not the pooled "
                f"{POOLED_ASSETS!r} universe this report's controls assume")
        cohort[sid] = spec
    return cohort


def _bundle_dir(artifacts_dir: Path, sid: str) -> Path:
    return artifacts_dir / sid / "gauntlet"


def check_complete(cohort: dict[str, dict], artifacts_dir: Path,
                   data_dir: Path) -> list[str]:
    """Every reason this cohort is NOT ready to report on, named one per
    line -- missing artifact files or missing pinned data CSVs. Empty
    means every input this report needs is present on disk."""
    problems = []
    for sid in sorted(cohort):
        bundle = _bundle_dir(artifacts_dir, sid)
        if not (bundle / "config.json").exists():
            problems.append(f"{sid}: missing {bundle / 'config.json'}")
        if not (bundle / "oos_trades.csv").exists():
            problems.append(f"{sid}: missing {bundle / 'oos_trades.csv'}")
    for asset in POOLED_ASSETS:
        data_path = data_dir / f"{asset}_1d.csv"
        if not data_path.exists():
            problems.append(f"missing {data_path}")
    return problems


def load_oos_trades(bundle: Path) -> list[dict]:
    with (bundle / "oos_trades.csv").open(
            "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["return_net"] = float(r["return_net"])
        r["notional_frac"] = float(r["notional_frac"])
    return rows


def load_bars(data_dir: Path, asset: str) -> list[dict]:
    bars = []
    with (data_dir / f"{asset}_1d.csv").open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bars.append({"date": row["date"], "open": float(row["open"]),
                         "close": float(row["close"])})
    bars.sort(key=lambda b: b["date"])
    return bars


def oos_bars(bars: list[dict], cutoff: str) -> list[dict]:
    """Bars strictly AFTER the cutoff, date-only ([:10]) compare via
    gauntlet's own _date_le -- a bar timestamped ON the cutoff date is not
    after it."""
    return [b for b in bars if not _date_le(b["date"], cutoff)]


def _hold_net(asset_oos: list[dict], per_side: float, sid: str,
              asset: str) -> float:
    if not asset_oos:
        raise ValueError(
            f"{sid}: no {asset} bars after cutoff -- cannot compute the "
            f"buy-and-hold control")
    return (asset_oos[-1]["close"] / asset_oos[0]["open"] - 1) - 2 * per_side


def _basket_net(btc_oos: list[dict], eth_oos: list[dict], per_side: float,
                sid: str) -> float:
    """50/50 daily-rebalanced basket, per the module docstring: shared OOS
    calendar, day-1 per-asset return close_1/open_1 - 1, thereafter
    close_t/close_{t-1} - 1, basket day return = mean of the two, compound,
    minus one round trip."""
    btc_by_date = {b["date"][:10]: b for b in btc_oos}
    eth_by_date = {b["date"][:10]: b for b in eth_oos}
    shared = sorted(set(btc_by_date) & set(eth_by_date))
    if not shared:
        raise ValueError(
            f"{sid}: no shared OOS calendar between BTCUSD and ETHUSD -- "
            f"cannot compute the basket control")

    def day_returns(by_date: dict[str, dict]) -> list[float]:
        rets = []
        prev_close = None
        for i, d in enumerate(shared):
            bar = by_date[d]
            if i == 0:
                rets.append(bar["close"] / bar["open"] - 1)
            else:
                rets.append(bar["close"] / prev_close - 1)
            prev_close = bar["close"]
        return rets

    r_btc = day_returns(btc_by_date)
    r_eth = day_returns(eth_by_date)
    basket = [(a + b) / 2 for a, b in zip(r_btc, r_eth)]
    return compound(basket) - 2 * per_side


def benchmark_row(sid: str, spec: dict, artifacts_dir: Path,
                  data_dir: Path) -> dict:
    """One report row. `strategy_net` is computed the exact way the live
    oos_negative gate computes it -- compound(contributions(oos_trades)) --
    over the COMMITTED oos_trades.csv, never a re-simulation. All three
    controls use the spec's OWN committed cost_model (read from config.json
    as recorded at generation time, never from today's cells.py, so a later
    cost-model change can never quietly move a backfilled number)."""
    bundle = _bundle_dir(artifacts_dir, sid)
    config = json.loads((bundle / "config.json").read_text(encoding="utf-8"))
    cutoff = config["cutoff"]
    cost_model = config["spec"]["cost_model"]
    per_side = cost_model["commission_per_side"] + cost_model["slippage_ticks"]

    strategy_net = compound(contributions(load_oos_trades(bundle)))

    btc_oos = oos_bars(load_bars(data_dir, "BTCUSD"), cutoff)
    eth_oos = oos_bars(load_bars(data_dir, "ETHUSD"), cutoff)
    btc_hold = _hold_net(btc_oos, per_side, sid, "BTCUSD")
    eth_hold = _hold_net(eth_oos, per_side, sid, "ETHUSD")
    basket_hold = _basket_net(btc_oos, eth_oos, per_side, sid)

    return {"sid": sid,
            "sibling_group_id": spec["provenance"]["sibling_group_id"],
            "strategy_net": strategy_net,
            "btc_hold": btc_hold, "eth_hold": eth_hold,
            "basket_hold": basket_hold,
            "excess_btc": strategy_net - btc_hold,
            "excess_eth": strategy_net - eth_hold,
            "excess_basket": strategy_net - basket_hold}


def render_report(rows: list[dict], numbers: dict[str, int],
                  generated_utc: datetime) -> str:
    rows_sorted = sorted(rows, key=lambda r: numbers.get(r["sid"], 0))

    def summary(key: str) -> tuple[int, float]:
        n_pos = sum(1 for r in rows if r[key] > 0)
        return n_pos, statistics.median(r[key] for r in rows)

    lines = [
        "# Pooled-crypto benchmark backfill report",
        "",
        "READ ONLY: this script never writes to registry_log.jsonl or "
        "artifacts/. It writes exactly one new file, this report.",
        "",
        "One-off analysis, NOT chain data. Compares each pooled-crypto "
        "quarantine strategy's committed OOS performance "
        "(compound(contributions(oos_trades.csv)) -- the chained trade "
        "set, never a re-simulation) to three buy-and-hold controls, each "
        "net of ONE round trip of the spec's own committed cost_model "
        "(per_side = commission_per_side + slippage_ticks, cost = "
        "2 * per_side):",
        "",
        "1. `btc_hold`: last OOS close / first OOS open - 1 on BTCUSD "
        "bars.",
        "2. `eth_hold`: the same on ETHUSD bars.",
        "3. `basket_hold`: 50/50 daily-rebalanced, matching the engine's "
        "mean-combine: over the SHARED OOS calendar (dates present in "
        "both assets' OOS bars), per-day return of each asset = "
        "close_t / close_{t-1} - 1 (close-to-close); day-1 return of "
        "each asset = close_1 / open_1 - 1 (entry at the first shared "
        "OOS open); basket day return = mean of the two; basket_net = "
        "compound of basket day returns - 2 * per_side.",
        "",
        "OOS window: bars with date strictly after the committed "
        "config.json cutoff, compared via [:10] date slices only.",
        "",
        f"Generated {generated_utc:%Y-%m-%d} UTC, {len(rows)} strategies.",
        "",
        "| edge | sid | sibling_group_id | strategy_net | btc_hold | "
        "eth_hold | basket_hold | excess_btc | excess_eth | "
        "excess_basket |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows_sorted:
        label = edge_label(numbers[r["sid"]])
        lines.append(
            f"| {label} | {r['sid']} | {r['sibling_group_id']} | "
            f"{r['strategy_net']:+.4%} | {r['btc_hold']:+.4%} | "
            f"{r['eth_hold']:+.4%} | {r['basket_hold']:+.4%} | "
            f"{r['excess_btc']:+.4%} | {r['excess_eth']:+.4%} | "
            f"{r['excess_basket']:+.4%} |")

    lines += ["", "## Summary", "", f"- n = {len(rows)}"]
    for key, name in (("excess_btc", "btc_hold"),
                      ("excess_eth", "eth_hold"),
                      ("excess_basket", "basket_hold")):
        n_pos, med = summary(key)
        lines.append(f"- vs {name}: n with excess > 0 = {n_pos} / "
                     f"{len(rows)}, median excess = {med:+.4%}")
    lines += [
        "",
        "## Survivorship and honesty notes",
        "",
        "- This cohort is the SURVIVORS: 20 quarantine passes out of every "
        "pooled-crypto strategy the pipeline ever generated. Comparing "
        "survivors to buy-and-hold overstates the pipeline; the graveyard "
        "is not in this table.",
        "- The eq-gen1 precedent (docs/runs/2026-08-26-eq-gen1-benchmark-"
        "report.md): 0/96 equity quarantine passes beat their own ETF's "
        "buy-and-hold -- an absolute gate passing beta. If all 20 crypto "
        "strategies sit below all three controls here, that is the same "
        "finding, and it is the finding.",
        "- RECORDED, NOT GATED: nothing here changes any strategy's "
        "quarantine state. This is a report, not chain data.",
        "- Edge numbers are D11 display labels (chain registration order), "
        "never identity or N accounting.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=LAYER_ROOT / "registry_log.jsonl")
    ap.add_argument("--artifacts", type=Path,
                    default=LAYER_ROOT / "artifacts")
    ap.add_argument("--data-dir", type=Path, default=LAYER_ROOT / "data")
    ap.add_argument("--out", type=Path, default=LAYER_ROOT / "docs" / "runs"
                    / "2026-08-28-crypto-pooled-benchmark-report.md")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    cohort = find_crypto_quarantine_cohort(registry)
    if len(cohort) != EXPECTED_N:
        print(f"REFUSED: expected exactly {EXPECTED_N} pooled-crypto "
              f"quarantine strategies on the chain at {args.registry}, "
              f"found {len(cohort)}. Nothing written.", file=sys.stderr)
        for sid in sorted(cohort):
            print(f"  found: {sid}", file=sys.stderr)
        raise SystemExit(2)

    problems = check_complete(cohort, args.artifacts, args.data_dir)
    if problems:
        print(f"REFUSED: {len(problems)} problem(s) across the "
              f"{EXPECTED_N}-strategy cohort. Nothing written.",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        raise SystemExit(2)

    numbers = edge_numbers(registry.entries())
    rows = [benchmark_row(sid, spec, args.artifacts, args.data_dir)
            for sid, spec in cohort.items()]
    report = render_report(rows, numbers, datetime.now(timezone.utc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} strategies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
