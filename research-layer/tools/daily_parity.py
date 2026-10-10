"""Daily parity for Morpheus 2b-1 (design s5.1, Ruling R5): fingerprint a
sample of registered 1d strategies so pre-change and post-change code can be
compared EXACTLY. Runs under the old code too (no pipeline.bars there)."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

LAYER = Path.cwd()
if str(LAYER) not in sys.path:
    sys.path.insert(0, str(LAYER))

from pipeline import cells, gauntlet, simcache                        # noqa: E402
from pipeline.engine import ENGINE_REV, run_spec                     # noqa: E402
from pipeline.gauntlet_core import (_spec_bars, daily_returns_with_dates,  # noqa: E402
                                    evaluate_standalone)
from pipeline.plateau import annualized_sharpe                       # noqa: E402
from pipeline.registry import Registry                               # noqa: E402
from pipeline.screen import DEFAULT_CUTOFF, load_cell_data           # noqa: E402
import pipeline

try:
    from pipeline.bars import bars_per_year as _bpy

    def resolved(spec: dict) -> int:
        u = spec["universe"]
        return _bpy(u.get("session"), u.get("timeframe", "1d"))
except ModuleNotFoundError as exc:          # the pre-2b-1 code
    if exc.name != "pipeline.bars":
        raise
    def resolved(spec: dict) -> int:
        return cells.SESSION_PERIODS.get(spec["universe"].get("session"), 365)


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=repr)
                          .encode("utf-8")).hexdigest()


def _special(spec: dict) -> bool:
    return any(b.get("type") == "vol_target"
               or str(b.get("type", "")).startswith("vol_percentile")
               for b in spec.get("blocks", []))


def select(specs: dict, n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    daily = sorted(s for s, p in specs.items()
                   if p["universe"].get("timeframe", "1d") == "1d")
    special = [s for s in daily if _special(specs[s])]
    take = special if len(special) <= n // 2 else sorted(rng.sample(special, n // 2))
    chosen = set(take)
    by_class: dict[str, list[str]] = {}
    for s in daily:
        if s not in chosen:
            by_class.setdefault(specs[s]["universe"].get("asset_class", "crypto"), []).append(s)
    classes = sorted(by_class)
    out = list(take)
    i = 0
    while len(out) < n and any(by_class.values()):
        c = classes[i % len(classes)]
        i += 1
        if by_class[c]:
            out.append(by_class[c].pop(rng.randrange(len(by_class[c]))))
    return sorted(out)


def fingerprint(spec: dict, data_dir: Path, cutoff: str) -> dict:
    tf = spec["universe"].get("timeframe", "1d")
    cell_list = sorted({(a, tf) for a in spec["universe"]["assets"]})
    bars_full, hashes, _ = load_cell_data(data_dir, cell_list, "9999-12-31")
    full = _spec_bars(bars_full, spec)
    res = run_spec(spec, full)
    bars_train, _, _ = load_cell_data(data_dir, cell_list, cutoff)
    tres = run_spec(spec, _spec_bars(bars_train, spec))
    ev = evaluate_standalone(spec, full, cutoff, perturb=False)
    shas = {a: hashes[cells.cell_id(a, tf)] for a in spec["universe"]["assets"]}
    rp = gauntlet._resolved_periods(spec) if hasattr(gauntlet, "_resolved_periods") else resolved(spec)
    return {
        "run_full": _h([res["equity"], res["trades"], res["metrics"]]),
        "run_train": _h([tres["equity"], tres["trades"], tres["metrics"]]),
        "standalone": _h([ev["passed"], ev["reason"], ev["metrics"], ev["mc_summary"]]),
        "dated_returns": _h(daily_returns_with_dates(res["equity"])),
        "curve_returns": _h(gauntlet.daily_returns_from_curve(res["equity"])),
        "sharpe": repr(annualized_sharpe(res["equity"])),
        "resolved_periods": rp,
        "cache_key": simcache.cache_key(spec["strategy_id"], shas, ENGINE_REV, resolved(spec)),
        "passed": ev["passed"], "reason": ev["reason"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.daily_parity")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--registry", type=Path, required=True)
    d.add_argument("--data-dir", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--sample", type=int, default=200)
    d.add_argument("--seed", type=int, default=20261010)
    d.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        x = json.loads(a.a.read_text(encoding="utf-8"))
        y = json.loads(a.b.read_text(encoding="utf-8"))
        bad = []
        # Check meta existence and identity
        if "meta" not in x or "meta" not in y:
            bad.append("meta missing")
        else:
            # Print pipeline info
            x_pipe = x["meta"].get("pipeline_file", "unknown")
            y_pipe = y["meta"].get("pipeline_file", "unknown")
            x_bars = x["meta"].get("bars_module", None)
            y_bars = y["meta"].get("bars_module", None)
            print(f"A pipeline_file: {x_pipe}, bars_module: {x_bars}")
            print(f"B pipeline_file: {y_pipe}, bars_module: {y_bars}")
            # Check meta fields
            if x["meta"].get("seed") != y["meta"].get("seed"):
                bad.append(f"seed differs: {x['meta'].get('seed')} != {y['meta'].get('seed')}")
            if x["meta"].get("cutoff") != y["meta"].get("cutoff"):
                bad.append(f"cutoff differs: {x['meta'].get('cutoff')} != {y['meta'].get('cutoff')}")
            if x["meta"].get("sample_n") != y["meta"].get("sample_n"):
                bad.append(f"sample_n differs: {x['meta'].get('sample_n')} != {y['meta'].get('sample_n')}")
            if x["meta"].get("registry_sha256") != y["meta"].get("registry_sha256"):
                bad.append(f"registry_sha256 differs: {x['meta'].get('registry_sha256')} != {y['meta'].get('registry_sha256')}")
        # Check for empty results or any errors
        if not x.get("results") or not y.get("results"):
            bad.append("empty results on one or both sides")
        if x.get("errors") or y.get("errors"):
            bad.append(f"errors present - A: {x.get('errors')}, B: {y.get('errors')}")
            for sid, err in x.get("errors", {}).items():
                print(f"A error {sid}: {err}")
            for sid, err in y.get("errors", {}).items():
                print(f"B error {sid}: {err}")
        if x["sample"] != y["sample"]:
            bad.append("sample differs")
        for sid in sorted(set(x["results"]) | set(y["results"])):
            if x["results"].get(sid) != y["results"].get(sid):
                bad.append(f"{sid}: {x['results'].get(sid)} != {y['results'].get(sid)}")
        for line in bad:
            print(line)
        print(f"compared {len(x.get('results', {}))}; mismatches {len(bad)}")
        return 0 if not bad else 1
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in Registry(a.registry).entries()
             if e["entry_type"] == "strategy_registered"}
    sample = select(specs, a.sample, a.seed)
    results, errors = {}, {}
    for i, sid in enumerate(sample, 1):
        try:
            results[sid] = fingerprint(specs[sid], a.data_dir, a.cutoff)
        except Exception as exc:             # identical errors on both sides are parity too
            errors[sid] = f"{type(exc).__name__}: {exc}"
        if i % 20 == 0:
            print(f"{i}/{len(sample)}", flush=True)
    # Compute registry SHA256
    registry_sha256 = _h(a.registry.read_bytes())
    meta = {
        "pipeline_file": str(pipeline.__file__),
        "engine_rev": ENGINE_REV,
        "bars_module": "pipeline.bars" in sys.modules,
        "seed": a.seed,
        "cutoff": a.cutoff,
        "sample_n": a.sample,
        "registry_sha256": registry_sha256,
        "registry_entries": len(specs),
    }
    a.out.write_text(json.dumps({"meta": meta, "sample": sample, "results": results, "errors": errors},
                                indent=1, sort_keys=True), encoding="utf-8")
    print(f"wrote {a.out}: {len(results)} fingerprints, {len(errors)} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
