"""What size of real edge does protocol-v5's PBO gate actually detect?

Usage:
    python diagnose_pbo_power.py [--registry registry_log.jsonl]
                                 [--artifacts-dir artifacts]
                                 [--cutoff 2023-12-31]
                                 [--run-id 2026-08-19-gen4]
                                 [--repeats 5] [--null-draws 50]

The chained protocol-v5 note names this as its own biggest weakness: "it is
entirely possible that no family this Composer produces will ever clear it ...
no evidence currently on this chain establishes that a real family can clear a
calibrated pbo test at all." That is a question about the gate's POWER, and it
is answerable without running a generation.

METHOD. Build a synthetic family mirroring the real ones: a common market
factor taken from a real generation-4 family, plus idiosyncratic noise at the
residual sd measured there. Then PLANT an edge -- one configuration receives a
persistent daily drift sized so its annualized Sharpe advantage over its
siblings is a stated number. That is the situation the gate exists to reward: a
family where selecting the in-sample best genuinely generalises. Sweep the
planted advantage and ask, at each level, exactly what protocol-v5 asks -- does
the observed PBO land at or below the 5th percentile of that family's own
permutation null? Repeat for a DETECTION RATE rather than one draw.

Then read the answer against the scale printed first: how far apart REAL
siblings sit, and in particular the winner's margin over its NEAREST RIVAL,
which is the quantity the gate has to resolve. The family's best-worst spread
flatters it and is printed only for context.

A LOWER BOUND, NOT AN ESTIMATE. The planted edge is uniform and persistent,
which is the most detectable shape an edge can take. A regime-dependent or
episodic edge of the same average size would be harder to detect, so whatever
advantage this finds is a floor on what the gate demands.

This audits the GATE, not the strategies. Nothing here is a verdict, nothing is
re-judged, and every strategy on the chain keeps the verdict it was given. The
script has NO registry write path by construction: it opens the registry for
reading only and never constructs Registry at all.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from pipeline.pbo import cscv_pbo, permutation_null, percentile_of
from pipeline.gauntlet import (daily_returns_from_curve, CSCV_SPLITS,
                               PBO_PASS_PCTILE)
from pipeline.plateau import annualized_sharpe

ADVANTAGES = (0.0, 0.25, 0.5, 0.75, 1.0, 2.0)   # annualized Sharpe advantage
TRADING_DAYS = 365                               # 24x7 crypto, matches plateau.py
BASE_FAMILY = "symmetric_tstat_trend_ff"         # the family the synthetics are calibrated on


def families_of_run(registry: Path, run_id: str) -> dict[str, list[str]]:
    fam = defaultdict(list)
    with registry.open(encoding="utf-8") as fh:      # read only, never a writer
        for line in fh:
            e = json.loads(line)
            if e.get("entry_type") == "strategy_registered":
                p = e["payload"]
                if p["generator"]["run_id"] == run_id:
                    fam[p["family"]].append(p["strategy_id"])
    return {k: sorted(v) for k, v in fam.items()}


def train_curve(artifacts_dir: Path, sid: str, cutoff: str):
    rows = list(csv.reader(
        (artifacts_dir / sid / "equity.csv").open(encoding="utf-8")))[1:]
    return [(d, float(x)) for d, x in rows if d <= cutoff]


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, default=here / "registry_log.jsonl")
    ap.add_argument("--artifacts-dir", type=Path, default=here / "artifacts")
    ap.add_argument("--cutoff", default="2023-12-31")
    ap.add_argument("--run-id", default="2026-08-19-gen4")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--null-draws", type=int, default=50)
    args = ap.parse_args()

    fam = families_of_run(args.registry, args.run_id)
    if not fam:
        print(f"no strategies for run-id {args.run_id}")
        return 0

    print("=" * 76)
    print("SCALE CHECK on the REAL families. The MARGIN is what the gate must")
    print("resolve: the winner's lead over its nearest rival, not the spread.")
    print("=" * 76)
    print(f"  {'family':34s} {'best':>7} {'2nd':>7} {'MARGIN':>8} {'spread':>8}")
    for name in sorted(fam):
        srs = sorted((annualized_sharpe(train_curve(args.artifacts_dir, s,
                                                    args.cutoff))
                      for s in fam[name]), reverse=True)
        print(f"  {name:34s} {srs[0]:7.3f} {srs[1]:7.3f} "
              f"{srs[0] - srs[1]:8.3f} {srs[0] - srs[-1]:8.3f}")

    # The synthetic families are calibrated to a REAL one. Named explicitly
    # rather than picked by position: a positional pick would silently
    # calibrate against a different family under a different --run-id, and the
    # whole point of the sweep is that its scale matches observed data.
    base_name = BASE_FAMILY if BASE_FAMILY in fam else sorted(fam)[0]
    if base_name != BASE_FAMILY:
        print(f"\n  NOTE: {BASE_FAMILY} absent; calibrating on {base_name}")
    print(f"\n  synthetic families calibrated on: {base_name}")
    base = sorted(fam[base_name])
    series = {s: daily_returns_from_curve(
        train_curve(args.artifacts_dir, s, args.cutoff)) for s in base}
    n_obs = min(len(v) for v in series.values())
    ids = sorted(series)
    factor = [statistics.mean(series[i][t] for i in ids) for t in range(n_obs)]
    resid_sd = statistics.pstdev([series[i][t] - factor[t]
                                  for i in ids for t in range(n_obs)])
    sigma = math.sqrt(statistics.pvariance(factor) + resid_sd ** 2)
    print(f"\n  common factor sd={statistics.pstdev(factor):.5f}  "
          f"idiosyncratic sd={resid_sd:.5f}  total sd={sigma:.5f}")
    print(f"  a 1.0 annualized-Sharpe advantage = "
          f"{sigma / math.sqrt(TRADING_DAYS):.6f} extra daily mean return")

    print()
    print("=" * 76)
    print(f"POWER: planted edge vs detection at the {PBO_PASS_PCTILE:.0%} percentile")
    print(f"  {args.repeats} families per cell, {args.null_draws}-draw null each")
    print("=" * 76)
    for n_cfg, advs in ((5, ADVANTAGES), (12, (0.5, 1.0))):
        print(f"\n--- family size {n_cfg} ---")
        for adv in advs:
            drift = adv * sigma / math.sqrt(TRADING_DAYS)
            passes, pcts, obss = 0, [], []
            for rep in range(args.repeats):
                rng = random.Random(70000 + n_cfg * 1000
                                    + int(adv * 100) * 7 + rep)
                f = {f"c{i}": [factor[t] + rng.gauss(0, resid_sd)
                               + (drift if i == 0 else 0.0)
                               for t in range(n_obs)] for i in range(n_cfg)}
                obs = cscv_pbo(f, s=CSCV_SPLITS)["pbo"]
                null = permutation_null(f, s=CSCV_SPLITS,
                                        draws=args.null_draws, seed=90210 + rep)
                pct = percentile_of(null, obs)
                obss.append(obs)
                pcts.append(pct)
                passes += pct <= PBO_PASS_PCTILE
            print(f"  advantage {adv:>4.2f} SR: observed PBO "
                  f"mean={statistics.mean(obss):.3f}  percentile "
                  f"mean={statistics.mean(pcts):.0%}  -> DETECTED "
                  f"{passes}/{args.repeats}")

    print()
    print("Read against the MARGIN column above, not the spread column.")
    print("DONE - nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
