"""Measure the null distribution of the protocol-v4 PBO gate at the family
sizes this pipeline actually produces.

Usage:
    python diagnose_pbo_validity.py [--registry registry_log.jsonl]
                                    [--artifacts-dir artifacts]
                                    [--cutoff 2023-12-31]
                                    [--draws 100]
                                    [--run-id 2026-08-19-gen4]

This audits the GATE, not the strategies. Nothing here is a verdict, nothing
is re-judged, and every strategy already on the chain keeps the verdict it was
given. Generation 4's 19 gauntlet failures and the 88 buried before them stay
buried; the three in quarantine keep their protocol-v3 verdicts.

This script has NO registry write path by construction: it opens the registry
for reading only, never imports a writer, and never constructs Registry at all.

WHAT IT MEASURES. The PBO gate's thresholds (pass below 0.20, family kill above
0.50) presuppose that a family with no persistent skill differences among its
siblings scores about 0.5. Three nulls test that presupposition, all meaning
"no sibling has any PERSISTENT edge over its siblings":

  A. PERMUTATION NULL on the real families. For each day the sibling labels are
     randomly permuted. The daily cross-section is preserved exactly, so real
     fat tails, real sibling correlation and the real common market factor all
     survive, and only persistent sibling-specific skill is destroyed. This is
     the null the gate's semantics assume.
  B. BLOCK PERMUTATION, the same idea in 21-day blocks, so that part of each
     sibling's own persistence is retained. Robustness check on A: if A and B
     agree, A's answer is not an artifact of destroying autocorrelation.
  C. SYNTHETIC CORRELATED, to vary family size, which the real data cannot: a
     common factor taken from a real family plus idiosyncratic residuals scaled
     to that family's observed residual sd. No config has an edge.

It also prints the ANALYTIC uniform-rank null, which needs no simulation at
all. pbo.py computes omega as the out-of-sample rank of the in-sample winner
over n_configs + 1 and counts a split as overfit when omega <= 0.5, which is
BBLdP's own convention. At small ODD n_configs the median rank lands exactly on
0.5 and is swept into the overfit count, so the uniform-rank null is 0.5 for
even n_configs and (n_configs + 1) / (2 * n_configs) for odd. At 5 that is
0.600, which is ABOVE the protocol's own 0.50 family-kill line.

Per the ratchet, a diagnostic may argue freely to TIGHTEN. This one supports a
change in the PERMISSIVE direction, so its evidence is published on the chain
before any successor protocol is written and before the results that protocol
would affect exist. See docs/notes/pbo-validity-evidence.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import zlib
from pathlib import Path

from pipeline.pbo import cscv_pbo
from pipeline.gauntlet import daily_returns_from_curve

S = 16
BLOCK_DAYS = 21
SIZES = (4, 5, 8, 12, 25)


def analytic_null(n_configs: int) -> float:
    """What this implementation returns when the winner's OOS rank is uniform."""
    overfit = [r for r in range(1, n_configs + 1) if r / (n_configs + 1) <= 0.5]
    return len(overfit) / n_configs


def train_returns(artifacts_dir: Path, sid: str, cutoff: str) -> list[float]:
    rows = list(csv.reader(
        (artifacts_dir / sid / "equity.csv").open(encoding="utf-8")))[1:]
    return daily_returns_from_curve([(d, float(e)) for d, e in rows if d <= cutoff])


def families_of_run(registry: Path, run_id: str) -> dict[str, list[str]]:
    fam: dict[str, list[str]] = {}
    with registry.open(encoding="utf-8") as fh:          # read only, never a writer
        for line in fh:
            e = json.loads(line)
            if e.get("entry_type") != "strategy_registered":
                continue
            p = e["payload"]
            if p["generator"]["run_id"] == run_id:
                fam.setdefault(p["family"], []).append(p["strategy_id"])
    return {k: sorted(v) for k, v in fam.items()}


def group_sizes(registry: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    with registry.open(encoding="utf-8") as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("entry_type") == "strategy_registered":
                g = e["payload"]["provenance"]["sibling_group_id"]
                sizes[g] = sizes.get(g, 0) + 1
    return sizes


def pairwise_corr(series: dict[str, list[float]]) -> list[float]:
    ids = sorted(series)
    out = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = series[ids[i]], series[ids[j]]
            n = min(len(a), len(b))
            a, b = a[:n], b[:n]
            ma, mb = statistics.mean(a), statistics.mean(b)
            va = sum((x - ma) ** 2 for x in a) ** 0.5
            vb = sum((x - mb) ** 2 for x in b) ** 0.5
            if va > 0 and vb > 0:
                out.append(sum((a[k] - ma) * (b[k] - mb)
                               for k in range(n)) / (va * vb))
    return out


def permute_labels(series: dict[str, list[float]], rng: random.Random,
                   block: int = 1) -> dict[str, list[float]]:
    """Permute sibling labels within each block of days, destroying persistent
    per-sibling skill while preserving each day's cross-section exactly."""
    ids = sorted(series)
    n_obs = min(len(series[i]) for i in ids)
    cols: dict[str, list[float]] = {i: [] for i in ids}
    t = 0
    while t < n_obs:
        end = min(t + block, n_obs)
        order = ids[:]
        rng.shuffle(order)
        for src, dst in zip(ids, order):
            cols[dst].extend(series[src][t:end])
        t = end
    return cols


def summarize(label: str, vals: list[float], observed: float | None = None) -> None:
    if not vals:
        print(f"  {label}: no computable draws")
        return
    print(f"  {label}: draws={len(vals)} mean={statistics.mean(vals):.3f} "
          f"sd={statistics.pstdev(vals):.3f} "
          f"min={min(vals):.3f} max={max(vals):.3f}")
    print(f"      P(family kill, >0.50 | NO skill)={sum(1 for v in vals if v > 0.50)/len(vals):.0%}"
          f"   P(pass, <0.20 | NO skill)={sum(1 for v in vals if v < 0.20)/len(vals):.0%}")
    if observed is not None:
        pct = sum(1 for v in vals if v < observed) / len(vals)
        print(f"      OBSERVED {observed:.3f} sits at the {pct:.0%} percentile "
              f"of this family's own no-skill null")


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, default=here / "registry_log.jsonl")
    ap.add_argument("--artifacts-dir", type=Path, default=here / "artifacts")
    ap.add_argument("--cutoff", default="2023-12-31")
    ap.add_argument("--draws", type=int, default=100)
    ap.add_argument("--run-id", default="2026-08-19-gen4")
    args = ap.parse_args()

    print("=" * 78)
    print("ANALYTIC uniform-rank null of this CSCV implementation, by family size")
    print("  pbo.py counts a split overfit when omega <= 0.5, omega = rank/(n+1).")
    print("  At ODD n the median rank lands exactly on 0.5 and counts as overfit.")
    print("=" * 78)
    for n in range(2, 26):
        v = analytic_null(n)
        print(f"  n_configs={n:3d}  uniform-rank null={v:.3f}"
              + ("   <-- ODD" if n % 2 else "")
              + ("   ** ABOVE the 0.50 family-kill line **" if v > 0.50 else ""))

    print()
    print("=" * 78)
    print("FAMILY SIZE PARITY of every sibling group on the chain")
    print("=" * 78)
    for g, n in sorted(group_sizes(args.registry).items()):
        print(f"  {g[:56]:56s} n={n:>3}  "
              f"{'ODD ' if n % 2 else 'even'}  null={analytic_null(n):.3f}")

    fams = families_of_run(args.registry, args.run_id)
    if not fams:
        print(f"\nno strategies for run-id {args.run_id}; nothing further to measure")
        return 0

    real: dict[str, dict[str, list[float]]] = {}
    print()
    print("=" * 78)
    print(f"REAL FAMILIES for run {args.run_id}: correlation and observed PBO")
    print("=" * 78)
    for name, sids in sorted(fams.items()):
        series = {sid: train_returns(args.artifacts_dir, sid, args.cutoff)
                  for sid in sids}
        n = min(len(v) for v in series.values())
        series = {k: v[:n] for k, v in series.items()}
        real[name] = series
        cors = pairwise_corr(series)
        print(f"  {name}: n_configs={len(sids)} obs={n} "
              f"pairwise_corr mean={statistics.mean(cors):.3f} "
              f"min={min(cors):.3f} max={max(cors):.3f}  "
              f"OBSERVED_PBO={cscv_pbo(series, s=S)['pbo']:.3f}")

    for block, title in ((1, "NULL A - daily label permutation on the REAL families"),
                         (BLOCK_DAYS, f"NULL B - BLOCK label permutation ({BLOCK_DAYS}d)")):
        draws = args.draws if block == 1 else max(4, args.draws // 2)
        print()
        print("=" * 78)
        print(f"{title} (no persistent skill), {draws} draws")
        print("=" * 78)
        for name, series in sorted(real.items()):
            observed = cscv_pbo(series, s=S)["pbo"]
            vals = []
            for seed in range(draws):
                rng = random.Random(zlib.crc32(name.encode()) % 10000
                                    + (0 if block == 1 else 5000) + seed)
                v = cscv_pbo(permute_labels(series, rng, block=block), s=S)["pbo"]
                if v is not None:
                    vals.append(v)
            summarize(name, vals, observed=observed)

    print()
    print("=" * 78)
    print("NULL C - SYNTHETIC CORRELATED, varying family size. No config has an edge.")
    print("=" * 78)
    base = real[sorted(real)[0]]
    ids = sorted(base)
    n_obs = min(len(base[i]) for i in ids)
    factor = [statistics.mean(base[i][t] for i in ids) for t in range(n_obs)]
    resid_sd = statistics.pstdev([base[i][t] - factor[t]
                                  for i in ids for t in range(n_obs)])
    print(f"  common factor from {sorted(real)[0]}: sd={statistics.pstdev(factor):.5f}, "
          f"idiosyncratic sd={resid_sd:.5f}")
    draws = max(4, args.draws // 2)
    for n_cfg in SIZES:
        vals = []
        for seed in range(draws):
            rng = random.Random(90000 + n_cfg * 100 + seed)
            series = {f"c{i}": [factor[t] + rng.gauss(0, resid_sd)
                                for t in range(n_obs)] for i in range(n_cfg)}
            v = cscv_pbo(series, s=S)["pbo"]
            if v is not None:
                vals.append(v)
        summarize(f"n_configs={n_cfg:3d} (analytic {analytic_null(n_cfg):.3f})", vals)

    print()
    print("DONE - nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
