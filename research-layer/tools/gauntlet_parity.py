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
import os
import random
import struct
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


def legacy_pooled_crypto(spec: dict) -> bool:
    """A pre-Switch-1 pooled crypto spec (the 2-asset BTCUSD+ETHUSD book).
    Its chained verdict was judged while crypto's benchmark was None, so it
    carries no benchmark control, and since Switch 1 (crypto benchmark
    "self") computing one would raise "needs exactly one asset per cell".
    Parity re-judges it without the control."""
    uni = spec["universe"]
    return uni.get("asset_class", "crypto") == "crypto" and len(uni["assets"]) != 1


PROTOCOL = "gauntlet-protocol-v6"
GATE_KEYS = ("is_edge_per_trade", "oos_edge_per_trade", "edge_decay_pct",
             "mc_p05_equity", "p_ruin", "cost_stress_net_pnl", "train_sharpe",
             "is_edge_raw", "oos_edge_raw", "is_vol", "oos_vol")

__all__ = ["GATE_KEYS", "truncate_bars", "chained_base_pass", "classify_drift",
           "load_repins", "ulp_distance", "main"]

# Excused drift (Coen 2026-11-06). A metric mismatch is EXCUSED only for a
# cause named here, and the report prints the cause beside the row; every
# other mismatch is unexcused and keeps the exit at 1. A verdict change is
# never excused. The two causes:
#  - restated: a cell of the strategy had a bar at or before its recorded
#    data_end revised by tasks.repin_free_series AFTER the verdict was chained,
#    so the re-run reads different history (the manifest's `repins` records);
#  - rounding: the verdict was chained before the 2026-08-26T23:19Z engine
#    change (commit 74b9703b) and every differing metric is within ULP_MAX
#    representable steps (17 was the largest seen, CLAUDE.md "Parity tool").
ENGINE_CHANGE_UTC = "2026-08-26T23:19:00Z"
ULP_MAX = 32
DEFAULT_TS_ROOT = Path(r"E:\Users\Coen\Claude\trading-systems")
MANIFEST_REL = Path("results") / "tradfi" / "free_universe_manifest.json"


def ulp_distance(a: float, b: float) -> int:
    """Representable doubles between a and b (0 when equal)."""
    def key(x: float) -> int:
        i = struct.unpack("<q", struct.pack("<d", x))[0]
        return i if i >= 0 else -(i & 0x7FFFFFFFFFFFFFFF)
    return abs(key(a) - key(b))


def load_repins(path: Path) -> list[dict] | None:
    """The manifest's `repins` records; None when the manifest is absent."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("repins", [])


def _is_float(x) -> bool:
    return isinstance(x, float) or (isinstance(x, int) and not isinstance(x, bool))


def classify_drift(diffs, verdict_ts: str, cells, repins,
                   *, verdict_match: bool) -> tuple[bool, str]:
    """(excused, cause) for one mismatching row.

    `cells` is [(asset, timeframe, recorded data_end)], `repins` the manifest
    records (None or empty when unknown). Restated history is checked first,
    because a revised bar explains drift of any size and rounding does not.
    """
    if not verdict_match:
        return False, "verdict changed"
    if not verdict_ts:
        return False, "verdict has no timestamp"
    restated = []
    for asset, _tf, end in cells:
        for r in repins or []:
            if r.get("id") != asset or not r.get("repinned_utc", "") > verdict_ts:
                continue
            for c in r.get("changed", []):
                if c.get("date", "9999") <= end[:10]:
                    restated.append(f"{asset} {c['date']} "
                                    f"(repinned {r['repinned_utc'][:10]})")
    if restated:
        return True, "restated: " + ", ".join(restated)
    if verdict_ts < ENGINE_CHANGE_UTC and all(
            _is_float(o) and _is_float(n) for _k, o, n in diffs):
        worst = max(ulp_distance(float(o), float(n)) for _k, o, n in diffs)
        if worst <= ULP_MAX:
            return True, f"rounding before the engine change ({worst} ulps)"
    return False, "unexplained"


def chained_base_pass(verdict: dict, burial_reason: str | None) -> bool:
    """Did the chained verdict clear the six base gates?

    Yes iff the verdict is a pass, or the strategy was buried for
    pbo_family_kill (its graveyard state_change reason). The verdict's
    metrics["pbo_family_kill"] flag is NOT enough: the chain sets it on every
    member of a killed group, including members that had already failed a
    gate, and the verdict payload carries no reason. Only the burial says why.
    """
    return (verdict["verdict"] == "pass"
            or burial_reason == "pbo_family_kill")


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
    ap.add_argument("--repins", type=Path, default=None,
                    help="free_universe_manifest.json (default: "
                         "$TRADING_SYSTEMS_ROOT/results/tradfi/...)")
    a = ap.parse_args(argv)
    manifest = a.repins or (Path(os.environ.get("TRADING_SYSTEMS_ROOT")
                                 or DEFAULT_TS_ROOT) / MANIFEST_REL)
    repins = load_repins(manifest)
    # ONE pass over the registry (it is ~68k entries).
    specs, verdicts, burial, verdict_ts = {}, {}, {}, {}
    for e in Registry(a.registry).entries():
        p = e["payload"]
        if e["entry_type"] == "strategy_registered":
            specs[p["strategy_id"]] = p
        elif e["entry_type"] == "state_change" and p.get("to") == "graveyard":
            burial[p["strategy_id"]] = p.get("reason")   # last burial wins
        elif (e["entry_type"] == "verdict" and p.get("stage") == "gauntlet"
              and p["metrics"].get("protocol") == PROTOCOL):
            verdicts[p["strategy_id"]] = p           # last v6 verdict wins
            verdict_ts[p["strategy_id"]] = e.get("ts_utc")
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
    excused, causes = 0, {}
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
                                  perturb=False,
                                  record_benchmark=not legacy_pooled_crypto(spec))
        reason = burial.get(sid)
        old_base_pass = chained_base_pass(old, reason)
        chained = (old["verdict"] if old["verdict"] == "pass"
                   else f"{old['verdict']} ({reason})")
        diffs = [(k, old["metrics"].get(k), new["metrics"].get(k))
                 for k in GATE_KEYS
                 if old["metrics"].get(k) != new["metrics"].get(k)]
        ok = (new["passed"] == old_base_pass) and not diffs
        mismatches += not ok
        rec = {i: cfg["data_end"][i] for i in ids}
        if not ok:
            exc, cause = classify_drift(
                diffs, verdict_ts[sid],
                [(x, tf, rec[i]) for (x, tf), i in zip(cell_list, ids)],
                repins, verdict_match=new["passed"] == old_base_pass)
            excused += exc
            causes[sid] = ("excused: " if exc else "UNEXCUSED: ") + cause
        rows.append((sid, chained, old["metrics"].get("pbo_family_kill"),
                     "PASS" if new["passed"] else f"fail:{new['reason']}",
                     diffs, _end_summary(rec, {i: now_end[i] for i in ids})))
        details.append((sid, [(i, rec[i], now_end[i]) for i in ids]))
    unexcused = mismatches - excused
    summary = (f"compared {len(rows) - not_comparable}, mismatches {mismatches} "
               f"(excused {excused}, unexcused {unexcused}), "
               f"not comparable {not_comparable}")
    lines = [f"# Gauntlet chain parity, {date.today().isoformat()}", "",
             summary, "",
             f"registry {a.registry.name}; protocol {PROTOCOL}; sample "
             f"{sample_desc}; metrics compared exactly, no tolerance; each "
             "strategy re-run on bars truncated at the data_end its own bundle "
             "recorded. A mismatch is excused only for a named cause "
             "(history re-pinned after the verdict, or rounding of at most "
             f"{ULP_MAX} ulps before the {ENGINE_CHANGE_UTC} engine change); "
             "the exit is 0 only when none is unexcused.",
             f"repins: {manifest}" + (" (ABSENT: no restated excuse applies)"
                                      if repins is None else
                                      f" ({len(repins)} records)"), "",
             "| strategy | chained | family kill | standalone | differing metrics "
             "(chained vs standalone) | drift cause | data_end recorded vs now |",
             "|---|---|---|---|---|---|---|"]
    for sid, ch, fk, st, diffs, ends in rows:
        d = "; ".join(f"{k}: {o!r} vs {n!r}" for k, o, n in diffs) or "-"
        lines.append(f"| {sid} | {ch} | {fk} | {st} | {d} | "
                     f"{causes.get(sid, '-')} | {ends} |")
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
    if repins is None:
        print(f"WARNING: no repins manifest at {manifest}; restated drift is unexcused")
    if not_comparable or missing:
        print(f"WARNING: {not_comparable} not comparable, "
              f"{len(missing)} without a verdict")
    if len(rows) - not_comparable < a.min_compared and not a.sids:
        return 3
    return 0 if unexcused == 0 and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
