"""Does the BUILT protocol-v5 reproduce the claim its chained note makes?

The note at registry entry 2512 asserts, as its own test against having been
reasoned backwards: "Applied to generation 4 this protocol also returns ZERO
survivors." That was an argument written before the code existed. This checks
it against the code that now exists.

WRITE-FREE. Reads committed artifacts and the chain; changes nothing.
"""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from pipeline.pbo import cscv_pbo, distinct_configs, permutation_null, percentile_of
from pipeline.gauntlet import (daily_returns_from_curve, CSCV_SPLITS,
                               PBO_MIN_DISTINCT, PBO_PASS_PCTILE,
                               PBO_KILL_PCTILE, PBO_NULL_DRAWS)
from pipeline.stats import percentile

CUTOFF = "2023-12-31"
RUN_ID = "2026-08-19-gen4"

fam = defaultdict(list)
group_of = {}
for line in (ROOT / "registry_log.jsonl").open(encoding="utf-8"):
    e = json.loads(line)
    if e.get("entry_type") != "strategy_registered":
        continue
    p = e["payload"]
    if p["generator"]["run_id"] == RUN_ID:
        fam[p["family"]].append(p["strategy_id"])
        group_of[p["family"]] = p["provenance"]["sibling_group_id"]

# what each gen-4 spec actually died of, under protocol-v4
died = {}
for line in (ROOT / "registry_log.jsonl").open(encoding="utf-8"):
    e = json.loads(line)
    if e.get("entry_type") == "state_change" and e["payload"].get("to") == "graveyard":
        died[e["payload"]["strategy_id"]] = e["payload"].get("reason")


def train_returns(sid):
    rows = list(csv.reader((ROOT / "artifacts" / sid / "equity.csv").open(encoding="utf-8")))[1:]
    return daily_returns_from_curve([(d, float(x)) for d, x in rows if d <= CUTOFF])


print(f"protocol-v5 as BUILT: min_distinct={PBO_MIN_DISTINCT} "
      f"pass<={PBO_PASS_PCTILE} kill>={PBO_KILL_PCTILE} draws={PBO_NULL_DRAWS}")
print()
any_pass = False
for family in sorted(fam):
    sids = sorted(fam[family])
    series = {s: train_returns(s) for s in sids}
    n = min(len(v) for v in series.values())
    series = {k: v[:n] for k, v in series.items()}
    nd = distinct_configs(series)
    obs = cscv_pbo(series, s=CSCV_SPLITS)["pbo"]
    if nd < PBO_MIN_DISTINCT:
        verdict, pct = "underpowered", None
    else:
        g = group_of[family]
        null = permutation_null(
            series, s=CSCV_SPLITS, draws=PBO_NULL_DRAWS,
            seed=int(hashlib.sha256(g.encode()).hexdigest()[:8], 16))
        pct = percentile_of(null, obs)
        member_pass = pct <= PBO_PASS_PCTILE
        verdict = ("kill" if pct >= PBO_KILL_PCTILE
                   else "pass" if member_pass else "fail")
        print(f"    null p05={percentile(sorted(null), 0.05):.3f} "
              f"p95={percentile(sorted(null), 0.95):.3f} "
              f"mean={sum(null)/len(null):.3f}")
    if verdict == "pass":
        any_pass = True
    pctxt = "n/a" if pct is None else f"{pct:.0%}"
    print(f"{family:34s} n={len(sids)} distinct={nd} "
          f"pbo_v5={obs:.3f} pctile={pctxt:>4s} -> {verdict.upper()}")
    print(f"{'':34s} v4 recorded reasons: "
          f"{sorted({died.get(s) for s in sids})}")
    print()

print("=" * 70)
print(f"ANY FAMILY PASSING THE v5 PBO GATE: {any_pass}")
print("The chained note claims generation 4 returns zero survivors under v5.")
print("A single PASS here would not by itself overturn that -- sharpe_floor")
print("and p_ruin killed 10 of the 19 before pbo is ever reached -- but a")
print("pass in symmetric_tstat_trend_ff or _voltarget would, since those two")
print("carried the nine strategies that died at the pbo gate and nothing")
print("else was wrong with them.")
