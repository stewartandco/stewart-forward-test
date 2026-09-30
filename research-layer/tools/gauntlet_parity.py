"""Chain parity for Build 2a: re-judge chained v6 gauntlet verdicts with the
standalone battery on the SAME data they were judged on, and compare.

Never scheduled; read-only on the chain; writes only its report. Each verdict
is re-run on bars truncated at the `data_end` its own bundle
(artifacts/<sid>/gauntlet/config.json) recorded, and every gate metric is
compared EXACTLY (no tolerance). The bundle's data_sha256 is a hash of the
WHOLE csv at judging time, and every file has grown since, so it cannot be
re-checked against truncated bars. Instead each row reports the recorded end
per cell beside the file's current last bar; a restated history would surface
as a metric mismatch, which is reported, never hidden.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date
from pathlib import Path

LAYER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAYER))

from pipeline import cells as _cells                                 # noqa: E402
from pipeline.registry import Registry                               # noqa: E402
from pipeline.screen import load_cell_data                           # noqa: E402
from pipeline.gauntlet_core import (                                 # noqa: E402
    evaluate_standalone, _spec_bars, truncate_bars)

PROTOCOL = "gauntlet-protocol-v6"
GATE_KEYS = ("is_edge_per_trade", "oos_edge_per_trade", "edge_decay_pct",
             "mc_p05_equity", "p_ruin", "cost_stress_net_pnl", "train_sharpe",
             "is_edge_raw", "oos_edge_raw", "is_vol", "oos_vol")

__all__ = ["GATE_KEYS", "truncate_bars", "main"]


def _end_summary(rec: dict, cur: dict) -> str:
    """Recorded data_end vs the file's current last bar, date part only."""
    r = sorted(v[:10] for v in rec.values())
    c = sorted(v[:10] for v in cur.values())
    return (f"{len(rec)} cells; recorded {r[0]}..{r[-1]}; "
            f"now {c[0]}..{c[-1]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--artifacts-dir", type=Path, required=True)
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260930)
    ap.add_argument("--sids", default="")
    ap.add_argument("--min-compared", type=int, default=30)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    # ONE pass over the registry (it is ~68k entries).
    specs, verdicts = {}, {}
    for e in Registry(a.registry).entries():
        p = e["payload"]
        if e["entry_type"] == "strategy_registered":
            specs[p["strategy_id"]] = p
        elif (e["entry_type"] == "verdict" and p.get("stage") == "gauntlet"
              and p["metrics"].get("protocol") == PROTOCOL):
            verdicts[p["strategy_id"]] = p           # last v6 verdict wins
    if a.sids:
        chosen = [s for s in a.sids.split(",") if s in verdicts]
        missing = [s for s in a.sids.split(",") if s not in verdicts]
        sample_desc = "explicit --sids"
    else:
        pool = sorted(s for s, v in verdicts.items()
                      if not v["metrics"].get("pbo_family_kill"))
        chosen = random.Random(a.seed).sample(pool, min(a.sample, len(pool)))
        missing = []
        sample_desc = f"{a.sample} random, seed {a.seed}, pool {len(pool)}"
    rows, details, mismatches, not_comparable = [], [], 0, 0
    for sid in chosen:
        spec, old = specs[sid], verdicts[sid]
        cfg_path = a.artifacts_dir / sid / "gauntlet" / "config.json"
        if not cfg_path.exists():
            not_comparable += 1
            rows.append((sid, old["verdict"], None,
                         "NOT COMPARABLE (no config.json)", [], "-"))
            continue
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cell_list = sorted({(x, spec["universe"].get("timeframe", "1d"))
                            for x in spec["universe"]["assets"]})
        ids = [_cells.cell_id(x, tf) for x, tf in cell_list]
        if any(i not in cfg["data_end"] for i in ids):
            not_comparable += 1
            rows.append((sid, old["verdict"], None,
                         "NOT COMPARABLE (cell missing from recorded data_end)",
                         [], "-"))
            continue
        bars, _hashes, now_end = load_cell_data(a.data_dir, cell_list, "9999-12-31")
        bars = truncate_bars(bars, cfg["data_end"])
        new = evaluate_standalone(spec, _spec_bars(bars, spec), cfg["cutoff"],
                                  perturb=False)
        old_base_pass = (old["verdict"] == "pass"
                         or old["metrics"].get("pbo_family_kill") is True)
        diffs = [(k, old["metrics"].get(k), new["metrics"].get(k))
                 for k in GATE_KEYS
                 if old["metrics"].get(k) != new["metrics"].get(k)]
        ok = (new["passed"] == old_base_pass) and not diffs
        mismatches += not ok
        rec = {i: cfg["data_end"][i] for i in ids}
        rows.append((sid, old["verdict"], old["metrics"].get("pbo_family_kill"),
                     "PASS" if new["passed"] else f"fail:{new['reason']}",
                     diffs, _end_summary(rec, {i: now_end[i] for i in ids})))
        details.append((sid, [(i, rec[i], now_end[i]) for i in ids]))
    summary = (f"compared {len(rows) - not_comparable}, mismatches {mismatches}, "
               f"not comparable {not_comparable}")
    lines = [f"# Gauntlet chain parity, {date.today().isoformat()}", "",
             summary, "",
             f"registry {a.registry.name}; protocol {PROTOCOL}; sample "
             f"{sample_desc}; metrics compared exactly, no tolerance; each "
             "strategy re-run on bars truncated at the data_end its own bundle "
             "recorded.", "",
             "| strategy | chained | family kill | standalone | differing metrics "
             "(chained vs standalone) | data_end recorded vs now |",
             "|---|---|---|---|---|---|"]
    for sid, ch, fk, st, diffs, ends in rows:
        d = "; ".join(f"{k}: {o!r} vs {n!r}" for k, o, n in diffs) or "-"
        lines.append(f"| {sid} | {ch} | {fk} | {st} | {d} | {ends} |")
    if missing:
        lines += ["", "No chained v6 gauntlet verdict found for: " + ", ".join(missing)]
    if a.sids:
        lines += ["", "## Per-cell data_end (recorded at judging vs current last bar)"]
        for sid, cl in details:
            lines += ["", f"### {sid}", "", "| cell | recorded | now |", "|---|---|---|"]
            lines += [f"| {i} | {r} | {n} |" for i, r, n in cl]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary)
    if not_comparable or missing:
        print(f"WARNING: {not_comparable} not comparable, "
              f"{len(missing)} without a verdict")
    if len(rows) - not_comparable < a.min_compared and not a.sids:
        return 3
    return 0 if mismatches == 0 and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
