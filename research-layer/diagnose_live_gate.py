"""Reproduce every number in the chained quarantine-live-protocol-v1 note.

Usage:
    python diagnose_live_gate.py [--registry registry_log.jsonl] [--q 0.05]

The note's evidence is ARITHMETIC, not simulation: it comes from the standard
PSR formula and from values already chained on the quarantined strategies' own
gauntlet verdicts. This script reads those values off the chain and recomputes
the tables, so a reader can check the note against the chain rather than
against a claim.

WRITE-FREE by construction: the registry is opened for reading only and
Registry is never constructed.

WHAT IT SHOWS.
  1. The founding asymmetry -- what forward Sharpe a 60-day record would need,
     against a zero benchmark and against protocol-v3's deflated one.
  2. What the DEFLATED gate would have demanded, using each quarantined
     strategy's own chained trials_n and trials_sr_var.
  3. The graduation horizons under the plain-PSR gate this protocol declares,
     at the Benjamini-Hochberg thresholds a cohort actually imposes.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from pipeline.stats import psr, expected_max_sharpe
from pipeline.plateau import annualized_sharpe

TRADING_DAYS = 365          # 24x7 crypto; every calendar day is a trading day
MIN_TRADING_DAYS = 60       # mirrors pipeline.quarantine.MIN_TRADING_DAYS
SHARPES = (1.0, 1.3, 1.5, 2.0)
MAX_HORIZON = 40001


def days_to_clear(ann_sharpe: float, sr_star: float, target: float) -> int | None:
    """Smallest T whose PSR reaches `target`, or None inside the horizon."""
    daily = ann_sharpe / math.sqrt(TRADING_DAYS)
    return next((T for T in range(30, MAX_HORIZON)
                 if psr(daily, sr_star, T, 0.0, 3.0) >= target), None)


def sharpe_to_clear(sr_star: float, T: int, target: float) -> float:
    """Annualized Sharpe needed to reach `target` at exactly T observations."""
    lo, hi = 0.0, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if psr(mid, sr_star, T, 0.0, 3.0) < target:
            lo = mid
        else:
            hi = mid
    return hi * math.sqrt(TRADING_DAYS)


def quarantined_with_gauntlet_inputs(registry: Path) -> dict[str, dict]:
    """{sid: gauntlet metrics} for every strategy now in quarantine."""
    state, metrics = {}, {}
    with registry.open(encoding="utf-8") as fh:      # read only, never a writer
        for line in fh:
            e = json.loads(line)
            p = e.get("payload", {})
            if not isinstance(p, dict):
                continue
            if e.get("entry_type") == "state_change" and p.get("strategy_id"):
                state[p["strategy_id"]] = p.get("to")
            if (e.get("entry_type") == "verdict"
                    and p.get("stage") == "gauntlet" and p.get("strategy_id")):
                metrics[p["strategy_id"]] = p.get("metrics", {})
    return {s: metrics.get(s, {}) for s, st in state.items() if st == "quarantine"}


def fmt(t: int | None) -> str:
    return f"{t} d ({t / TRADING_DAYS:.1f}y)" if t else ">40000 d"


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, default=here / "registry_log.jsonl")
    ap.add_argument("--q", type=float, default=0.05, help="BH false discovery rate")
    ap.add_argument("--artifacts-dir", type=Path, default=here / "artifacts")
    ap.add_argument("--cutoff", default="2023-12-31")
    args = ap.parse_args()

    q = quarantined_with_gauntlet_inputs(args.registry)
    print("=" * 74)
    print("STRATEGIES IN QUARANTINE, with the inputs chained on their gauntlet verdicts")
    print("=" * 74)
    train = {}
    for sid in q:
        # gen-3 verdicts PREDATE the train_sharpe metric, which protocol-v4
        # added, so it is recomputed from the committed equity curve rather
        # than read off the verdict, where it is absent.
        rows = list(csv.reader(
            (args.artifacts_dir / sid / "equity.csv").open(encoding="utf-8")))[1:]
        train[sid] = annualized_sharpe(
            [(d, float(x)) for d, x in rows if d <= args.cutoff])
    for sid, m in sorted(q.items()):
        star = m.get("expected_max_sharpe")
        print(f"  {sid}  train_sharpe={train[sid]:.4f} (recomputed)  "
              f"trials_n={m.get('trials_n')}  trials_sr_var={m.get('trials_sr_var')}")
        if star is not None:
            print(f"{'':20s}  expected_max_sharpe={star:.4f}/day "
                  f"= {star * math.sqrt(TRADING_DAYS):.2f} annualized")

    print()
    print("=" * 74)
    print(f"1. THE ASYMMETRY: what a {MIN_TRADING_DAYS}-day record would have to show")
    print("=" * 74)
    any_m = next((m for m in q.values() if m.get("expected_max_sharpe") is not None), {})
    deflated_star = any_m.get("expected_max_sharpe", 0.0)
    print(f"  against a ZERO benchmark          : "
          f"{sharpe_to_clear(0.0, MIN_TRADING_DAYS, 0.95):.2f} annualized Sharpe")
    print(f"  against protocol-v3's deflated one: "
          f"{sharpe_to_clear(deflated_star, MIN_TRADING_DAYS, 0.95):.2f} annualized Sharpe")
    print("  Train Sharpes of the strategies actually here: "
          + ", ".join(f"{train[s]:.4f}" for s in sorted(q)))

    print()
    print("=" * 74)
    print("2. WHAT THE DEFLATED GATE WOULD HAVE DEMANDED (protocol-v3, now removed)")
    print("=" * 74)
    print(f"  {'fwd ann. SR':>12} {'days to DSR >= 0.95':>22}")
    for s in (0.97, 1.3, 1.5, 2.0):
        print(f"  {s:>12.2f} {fmt(days_to_clear(s, deflated_star, 0.95)):>22}")

    print()
    print("=" * 74)
    print(f"3. THIS PROTOCOL: plain PSR vs zero, at Benjamini-Hochberg q={args.q}")
    print("   BH adapts: the weakest member of a uniformly strong cohort faces")
    print("   0.95, and a lone graduate among m faces 1 - q/m.")
    print("=" * 74)
    cohorts = [("whole cohort strong", 0.95),
               (f"lone graduate, m=10", 1 - args.q / 10),
               (f"lone graduate, m=20", 1 - args.q / 20)]
    print(f"  {'fwd ann. SR':>12}" + "".join(f"{lab:>24}" for lab, _ in cohorts))
    for s in SHARPES:
        row = "".join(f"{fmt(days_to_clear(s, 0.0, tgt)):>24}" for _, tgt in cohorts)
        print(f"  {s:>12.1f}{row}")

    print()
    print("DONE - nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
