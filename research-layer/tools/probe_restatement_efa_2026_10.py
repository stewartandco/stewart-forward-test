"""Read-only probe behind the restatement-2026-10-04-efa chain note. NEVER scheduled.

Usage: python tools/probe_restatement_efa_2026_10.py <scratch dir>
Read-only probe: does the EFA 2014-07-03 restatement change any quarantine decision?

Works on SCRATCH copies of the chain and the price files. For every quarantined
strategy whose universe holds EFA, recomputes every chained decision date plus
the four refused dates (09-28..10-01) under the OLD bar and the NEW bar, and
compares against the chained rows. Writes nothing outside the scratch dir.
"""
import shutil
import sys
from pathlib import Path

LAYER = Path(r"E:\Users\Coen\Claude\stewart-forward-test\research-layer")
SCRATCH = Path(sys.argv[1])
sys.path.insert(0, str(LAYER))
from pipeline import quarantine as q  # noqa: E402
from pipeline.registry import Registry  # noqa: E402

NEW_ROW = "2014-07-03 00:00:00,69.06,69.29,69.03,69.22,7757300.0"
OLD_ROW = "2014-07-03 00:00:00,66.1,66.48,66.03,66.48,7757300.0"
REFUSED = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]

reg_copy = SCRATCH / "registry_log.jsonl"
shutil.copyfile(LAYER / "registry_log.jsonl", reg_copy)
reg = Registry(reg_copy)
states = reg.strategy_states()
entered = q.quarantine_entry_dates(reg)
specs = {e["payload"]["strategy_id"]: e["payload"] for e in reg.entries()
         if e["entry_type"] == "strategy_registered"}
efa_sids = sorted(s for s, st in states.items()
                  if st == "quarantine" and "EFA" in specs[s]["universe"]["assets"])
assets = sorted({a for s in efa_sids for a in specs[s]["universe"]["assets"]})

new_dir, old_dir = SCRATCH / "data_new", SCRATCH / "data_old"
for d in (new_dir, old_dir):
    d.mkdir(exist_ok=True)
    for a in assets:
        shutil.copyfile(LAYER / "data" / f"{a}_1d.csv", d / f"{a}_1d.csv")
txt = (old_dir / "EFA_1d.csv").read_text(encoding="utf-8")
assert txt.count(NEW_ROW) == 1, "live EFA CSV does not carry the corrected row as expected"
(old_dir / "EFA_1d.csv").write_text(txt.replace(NEW_ROW, OLD_ROW), encoding="utf-8")

# 1. the OLD reconstruction must reproduce the chained digests (proves the probe's baseline)
snaps, sups = q.data_snapshots(reg), q.snapshot_supplements(reg)
for d in REFUSED:
    chained = q.merged_bars_coverage(snaps[d], sups.get(d, []))["EFA"]
    print(d, "old reproduces chained:", q.hash_bars_through(old_dir, "EFA", d) == chained,
          "| new reproduces:", q.hash_bars_through(new_dir, "EFA", d) == chained)

# 2. decisions: chained rows vs OLD vs NEW
chained_rows = {}
for e in reg.entries():
    if e["entry_type"] == "quarantine_decision":
        p = e["payload"]
        if p["strategy_id"] in efa_sids:
            chained_rows[(p["strategy_id"], p["date"], p["asset"])] = p

KEYS = ("action", "price", "position_frac", "equity")
print(f"\n{len(efa_sids)} quarantined EFA strategies; {len(chained_rows)} chained EFA-strategy rows")
mismatch_new_vs_chained = mismatch_old_vs_new = compared = 0
examples = []
for sid in efa_sids:
    dates = sorted({k[1] for k in chained_rows if k[0] == sid} | set(REFUSED))
    for d in dates:
        if d <= entered[sid]:
            continue
        try:
            bo = {a: q._daily_bars(old_dir, a, d) for a in specs[sid]["universe"]["assets"]}
            bn = {a: q._daily_bars(new_dir, a, d) for a in specs[sid]["universe"]["assets"]}
            ro = q.observe_day(specs[sid], bo, d, entered[sid])
            rn = q.observe_day(specs[sid], bn, d, entered[sid])
        except ValueError as exc:
            examples.append(f"{sid[:16]} {d}: not computable ({exc})")
            continue
        for o, n in zip(ro, rn):
            compared += 1
            if any(o[k] != n[k] for k in KEYS):
                mismatch_old_vs_new += 1
                if len(examples) < 12:
                    examples.append(f"{sid[:16]} {d} {o['asset']}: OLD {[o[k] for k in KEYS]} NEW {[n[k] for k in KEYS]}")
            c = chained_rows.get((sid, d, n["asset"]))
            if c is not None and any(c[k] != n[k] for k in KEYS):
                mismatch_new_vs_chained += 1
                if len(examples) < 12:
                    examples.append(f"{sid[:16]} {d} {n['asset']}: CHAINED {[c[k] for k in KEYS]} NEW {[n[k] for k in KEYS]}")
print(f"rows compared: {compared}")
print(f"OLD vs NEW rows differing: {mismatch_old_vs_new}")
print(f"CHAINED vs NEW rows differing: {mismatch_new_vs_chained}")
owed = [(s, d) for s in efa_sids for d in REFUSED
        if d > entered[s] and not any(k[0] == s and k[1] == d for k in chained_rows)]
print(f"(strategy, refused date) pairs with NO chained row yet: {len(owed)}")
for x in examples:
    print("  ", x)
