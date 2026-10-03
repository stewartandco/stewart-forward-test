"""Bit-identity proof for clustering changes, on REAL cached return series.

Never scheduled. Read-only on everything it reads (a registry COPY, the sim
cache, the price data); writes only --out and, if asked, --matrix-cache.

v6.1 (chain entry 68486) fixes the clustering method, so a change to
pipeline/cluster.py that is meant to be performance only must change no
recorded number. This tool is that proof. It builds the registry-wide input
exactly as pipeline/gauntlet_stats.py does:
  every strategy_registered spec on the registry
  -> every cell those specs use, cut at the vintage Sunday
  -> data_digest (gauntlet_stats.data_digest_of)
  -> each spec's series from the sim cache (no simulation: a miss is an error)
  -> gauntlet.cluster_registry's own alignment (its effective_trials call is
     intercepted only to capture the aligned input).
It then draws random row samples (numpy default_rng(seed), sorted) and runs
cluster.effective_trials under the OLD revision's cluster.py (read with
`git show <old-rev>:research-layer/pipeline/cluster.py`) and under the
working tree's, and compares, exactly:
  - the full merge history (every step's pair of frozensets, in order);
  - k and the labels;
  - trials_sr_var, by float.hex;
  - expected_max_sharpe(k, trials_sr_var), by float.hex.
Any difference exits 1.

Folds in the retired tools_verify_cluster_identity.py: --reference-n N also
holds the numpy path to the pure-Python reference on an N-row sample (k and
labels identical, var within 1e-9, as that tool required). The old tool
globbed simcache/*.json and found nothing after the 2026-09-03 move to .npz.

Usage, from research-layer/ (paths are COPIES or read-only):
  python tools/verify_cluster_identity.py --registry C:/scratch/reg.jsonl \\
      --simcache-dir simcache --data-dir data --n 1000 2000 4000 8000 \\
      --out C:/scratch/ident.json
  python tools/verify_cluster_identity.py ... --new-only --n 0   # full registry,
      new path only: time, peak commit, k, var and raw SR*
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import types
from datetime import date
from pathlib import Path

import numpy as np

LAYER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAYER))

from pipeline import cells, simcache                                 # noqa: E402
from pipeline import cluster as new_cluster                          # noqa: E402
from pipeline import gauntlet                                        # noqa: E402
from pipeline.registry import Registry                               # noqa: E402
from pipeline.screen import (assert_cells_comparable, comparable_cells,  # noqa: E402
                             load_cell_data)
from pipeline.gauntlet_core import truncate_bars                     # noqa: E402
from pipeline.gauntlet_stats import (_View, vintage_date,             # noqa: E402
                                     bars_through_sha256, data_digest_of)
from pipeline.stats import expected_max_sharpe                       # noqa: E402

# The last revision before step 7's _agglomerate_np / _distance_matrix_np
# rewrite (the live head the step-7 branch was cut from).
DEFAULT_OLD_REV = "edd8ac97"


class ReadOnlyCache(simcache.SimCache):
    """The sim cache with every write path refused: no put, no poison
    (a bad entry is reported as a miss and left on disk), no migration."""

    def put(self, *a, **k):
        raise RuntimeError("read-only sim cache: put refused")

    def _write(self, *a, **k):
        raise RuntimeError("read-only sim cache: write refused")

    def _poison(self, path):
        print(f"WARNING: bad cache entry left in place: {path}", flush=True)

    def _migrate_legacy(self, legacy, path):
        print(f"WARNING: legacy cache entry not migrated: {legacy}", flush=True)
        return None

    def migrate(self, *a, **k):
        raise RuntimeError("read-only sim cache: migrate refused")


def load_old_cluster(rev: str):
    """pipeline/cluster.py as of `rev`, as a module (it imports only math and
    numpy, so it runs standalone)."""
    src = subprocess.run(
        ["git", "-C", str(LAYER), "show", f"{rev}:research-layer/pipeline/cluster.py"],
        capture_output=True, text=True, check=True).stdout
    mod = types.ModuleType(f"cluster_at_{rev}")
    exec(compile(src, f"cluster@{rev}.py", "exec"), mod.__dict__)
    return mod


def build_matrix(registry: Path, data_dir: Path, simcache_dir: Path,
                 vintage: str) -> dict:
    """The aligned registry-wide input, as gauntlet_stats builds it."""
    t0 = time.time()
    view = _View()
    Registry(registry).snapshot(on_entry=view.on_entry, track_stats=True)
    specs = view.specs
    cells_needed, class_of = comparable_cells(specs)
    bars, _, _ = load_cell_data(data_dir, cells_needed, "9999-12-31")
    bars = truncate_bars(bars, {cells.cell_id(a, tf): vintage for a, tf in bars})
    data_end = {cells.cell_id(a, tf): (b[-1]["date"] if b else "")
                for (a, tf), b in bars.items()}
    assert_cells_comparable(data_end, class_of=class_of)
    hashes = {cells.cell_id(a, tf): bars_through_sha256(data_dir / f"{a}_{tf}.csv", vintage)
              for a, tf in bars}
    bars.clear()
    cache = ReadOnlyCache(simcache_dir)
    dated, eq_len, missing = {}, {}, []
    for s in specs:
        sid = s["strategy_id"]
        tf = s["universe"].get("timeframe", "1d")
        shas = {a: hashes[cells.cell_id(a, tf)] for a in s["universe"]["assets"]}
        ppy = cells.SESSION_PERIODS.get(s["universe"].get("session"), 365)
        hit = cache.get(simcache.cache_key(sid, shas, gauntlet.ENGINE_REV, ppy))
        if hit is None:
            missing.append(sid)
            continue
        dated[sid], eq_len[sid] = hit["series"], hit["equity_len"]
    if missing:
        raise SystemExit(f"FAIL: {len(missing)} sim cache miss(es), e.g. {missing[:3]}; "
                         "this tool never simulates")
    captured = {}
    real = gauntlet.effective_trials

    def capture(returns_by_id):
        captured["returns_by_id"] = returns_by_id
        ids = sorted(returns_by_id)
        return 2, {i: 0 for i in ids}, 0.0
    gauntlet.effective_trials = capture
    try:
        out = gauntlet.cluster_registry(dated, eq_len, specs)
    finally:
        gauntlet.effective_trials = real
    rbi = captured["returns_by_id"]
    ids = sorted(rbi)
    X = np.asarray([np.asarray(rbi[i], dtype=np.float64) for i in ids])
    print(f"matrix: {X.shape[0]} x {X.shape[1]}, vintage {vintage}, "
          f"digest {data_digest_of(hashes)}, {time.time() - t0:.0f}s", flush=True)
    return {"ids": ids, "X": X, "vintage": vintage,
            "data_digest": data_digest_of(hashes),
            "registered_n": out["registered_n"],
            "trials_alignment": out["trials_alignment"],
            "trials_common_days": out["trials_common_days"]}


class PeakCommit:
    """Samples this process's private commit (MB) on a thread; `peak` is
    the highest value seen while running, minus the value at start."""

    def __init__(self, every_s: float = 0.05):
        self.every_s, self.base, self.top = every_s, None, None
        self._stop = threading.Event()

    def __enter__(self):
        self.base = self.top = gauntlet.private_commit_mb()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self):
        while not self._stop.wait(self.every_s):
            v = gauntlet.private_commit_mb()
            if v is not None and (self.top is None or v > self.top):
                self.top = v

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()

    @property
    def peak(self):
        return None if self.base is None else self.top - self.base


def run_module(mod, rbi: dict) -> dict:
    """effective_trials through `mod`'s own dispatcher, capturing the merge
    history its numpy path computes."""
    real = mod._agglomerate_np
    box = {}

    def wrapper(*a, **k):
        box["history"] = real(*a, **k)
        return box["history"]
    ids, X = mod._returns_matrix(rbi)
    if X is None:
        raise SystemExit("FAIL: input would take the reference path (ragged or a "
                         "constant non-zero row); production would not cluster it "
                         "with the numpy path either")
    mod._agglomerate_np = wrapper
    try:
        with PeakCommit() as pc:
            t = time.perf_counter()
            k, labels, var = mod.effective_trials(rbi)
            secs = time.perf_counter() - t
    finally:
        mod._agglomerate_np = real
    return {"k": k, "labels": labels, "var": var, "history": box.get("history"),
            "seconds": round(secs, 2), "peak_commit_mb": pc.peak}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path, required=True,
                    help="a COPY of registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=LAYER / "data")
    ap.add_argument("--simcache-dir", type=Path, default=LAYER / "simcache")
    ap.add_argument("--vintage", default=None,
                    help="ISO Sunday (default: gauntlet_stats' vintage for today)")
    ap.add_argument("--old-rev", default=DEFAULT_OLD_REV)
    ap.add_argument("--n", type=int, nargs="+", default=[1000, 2000, 4000, 8000],
                    help="sample sizes; 0 = every registered strategy")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--new-only", action="store_true",
                    help="time the working tree only (no identity claim)")
    ap.add_argument("--reference-n", type=int, default=0,
                    help="also compare the numpy path to the pure-Python "
                         "reference on a sample this size (0 = skip)")
    ap.add_argument("--matrix-cache", type=Path, default=None,
                    help="npz to reuse / save the aligned matrix (scratch only)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    vintage = a.vintage or vintage_date(date.today())

    if a.matrix_cache is not None and a.matrix_cache.exists():
        z = np.load(a.matrix_cache, allow_pickle=False)
        fx = {"ids": [str(s) for s in z["ids"]], "X": z["X"],
              **json.loads(str(z["meta"]))}
        print(f"matrix from {a.matrix_cache}: {fx['X'].shape}, vintage "
              f"{fx['vintage']}, digest {fx['data_digest']}", flush=True)
    else:
        fx = build_matrix(a.registry, a.data_dir, a.simcache_dir, vintage)
        if a.matrix_cache is not None:
            meta = {k: fx[k] for k in ("vintage", "data_digest", "registered_n",
                                       "trials_alignment", "trials_common_days")}
            np.savez(a.matrix_cache, ids=np.array(fx["ids"]), X=fx["X"],
                     meta=np.str_(json.dumps(meta)))
    ids_full, X_full = fx["ids"], fx["X"]
    result = {k: fx[k] for k in ("vintage", "data_digest", "registered_n",
                                 "trials_alignment", "trials_common_days")}
    result.update({"rows": len(ids_full), "days": int(X_full.shape[1]),
                   "zero_rows": int((X_full == 0).all(axis=1).sum()),
                   "old_rev": None if a.new_only else a.old_rev,
                   "seed": a.seed, "runs": []})
    old = None if a.new_only else load_old_cluster(a.old_rev)
    ok = True
    for n in a.n:
        if n == 0 or n >= len(ids_full):
            pick = list(range(len(ids_full)))
        else:
            pick = sorted(np.random.default_rng(a.seed).choice(
                len(ids_full), size=n, replace=False).tolist())
        X = np.ascontiguousarray(X_full[pick])
        rbi = {ids_full[i]: X[j] for j, i in enumerate(pick)}
        row = {"n": len(pick), "zero_rows": int((X == 0).all(axis=1).sum())}
        new = run_module(new_cluster, rbi)
        row.update({"new_s": new["seconds"], "new_peak_commit_mb": new["peak_commit_mb"],
                    "k": new["k"], "trials_sr_var": new["var"].hex(),
                    "trials_sr_var_float": new["var"],
                    "expected_max_sharpe": expected_max_sharpe(new["k"], new["var"]).hex(),
                    "expected_max_sharpe_float": expected_max_sharpe(new["k"], new["var"])})
        print(f"n={row['n']}: new {new['seconds']}s, k={new['k']}, "
              f"var={new['var'].hex()}, peak +{new['peak_commit_mb']} MB", flush=True)
        if old is not None:
            ref = run_module(old, rbi)
            same = {
                "history": ref["history"] == new["history"],
                "k": ref["k"] == new["k"],
                "labels": ref["labels"] == new["labels"],
                "trials_sr_var_hex": ref["var"].hex() == new["var"].hex(),
                "expected_max_sharpe_hex": (
                    expected_max_sharpe(ref["k"], ref["var"]).hex()
                    == expected_max_sharpe(new["k"], new["var"]).hex()),
            }
            row.update({"old_s": ref["seconds"],
                        "old_peak_commit_mb": ref["peak_commit_mb"],
                        "old_k": ref["k"], "old_trials_sr_var": ref["var"].hex(),
                        "history_steps": len(new["history"] or []),
                        "identical": same})
            ok &= all(same.values())
            print(f"n={row['n']}: old {ref['seconds']}s, identical {same}", flush=True)
            del ref
        del new
        result["runs"].append(row)
        a.out.write_text(json.dumps(result, indent=1), encoding="utf-8")

    if a.reference_n:
        pick = sorted(np.random.default_rng(a.seed).choice(
            len(ids_full), size=min(a.reference_n, len(ids_full)),
            replace=False).tolist())
        rbi = {ids_full[i]: X_full[i] for i in pick}
        ids, X = new_cluster._returns_matrix(rbi)
        nk, nl, nv = new_cluster._effective_trials_np(rbi, ids, X)
        rk, rl, rv = new_cluster._effective_trials_ref(new_cluster._as_lists(rbi))
        ref_ok = nk == rk and nl == rl and abs(nv - rv) <= 1e-9
        result["reference"] = {"n": len(pick), "k": nk, "ref_k": rk,
                               "labels_equal": nl == rl, "var_abs_diff": abs(nv - rv),
                               "var_bit_identical": nv == rv, "ok": ref_ok}
        print(f"reference n={len(pick)}: {result['reference']}", flush=True)
        ok &= ref_ok

    result["verdict"] = ("TIMING ONLY" if a.new_only and not a.reference_n
                         else "PASS" if ok else "FAIL")
    a.out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(result["verdict"], flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
