"""Old-vs-new clustering identity proof on REAL cached return series.

Builds a fixture from the sim cache (read-only), intersection-aligns it the
same way the gauntlet does, then runs the pure-Python reference and the
numpy fast path side by side. Identity of (k, labels) and agreement of
trials_sr_var within 1e-9 is the ship bar; a mismatch exits nonzero and the
build STOPS (declared-protocol-note territory, never a silent change).

Usage (from research-layer/):
    python tools_verify_cluster_identity.py --n 400
    python tools_verify_cluster_identity.py --full        # new path only, all entries
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pipeline.cluster import (_effective_trials_ref, _effective_trials_np,
                              _returns_matrix)
from pipeline.gauntlet import intersect_returns, MIN_TRIALS_COMMON_DAYS


def load_series(cache_dir: Path, limit: int | None) -> dict[str, list[tuple[str, float]]]:
    out = {}
    for p in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            series = [(str(r[0]), float(r[1])) for r in payload["series"]]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
            continue
        if len(series) < 50:
            continue
        out[p.stem] = series
        if limit is not None and len(out) >= limit:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simcache-dir", type=Path,
                    default=Path(__file__).resolve().parent / "simcache")
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--full", action="store_true",
                    help="new path only, over every usable cache entry")
    args = ap.parse_args(argv)

    limit = None if args.full else args.n
    dated = load_series(args.simcache_dir, limit)
    if len(dated) < 3:
        print(f"FAIL: only {len(dated)} usable cache entries in "
              f"{args.simcache_dir}")
        return 2
    returns_by_id, common = intersect_returns(dated)
    print(f"{len(returns_by_id)} series, {len(common)} common days "
          f"(floor {MIN_TRIALS_COMMON_DAYS})")
    if len(common) < MIN_TRIALS_COMMON_DAYS:
        print("FAIL: intersection below the gauntlet floor; pick different "
              "entries")
        return 2

    ids, X = _returns_matrix(returns_by_id)
    if X is None:
        print("FAIL: input rejected by _returns_matrix (ragged or "
              "constant-nonzero row); the production dispatcher would take "
              "the reference path here")
        return 2
    t0 = time.time()
    new_k, new_labels, new_var = _effective_trials_np(returns_by_id, ids, X)
    t_new = time.time() - t0
    print(f"new path: k={new_k}, var={new_var!r}, {t_new:.1f}s")

    if args.full:
        print("PASS (timing-only mode; identity is proven by --n runs)")
        return 0

    t0 = time.time()
    ref_k, ref_labels, ref_var = _effective_trials_ref(returns_by_id)
    t_ref = time.time() - t0
    print(f"reference: k={ref_k}, var={ref_var!r}, {t_ref:.1f}s")

    if new_k != ref_k:
        print(f"FAIL: k differs (ref {ref_k} vs new {new_k}) -- STOP THE "
              f"BUILD and report (protocol-note territory)")
        return 1
    if new_labels != ref_labels:
        diff = [i for i in ref_labels if ref_labels[i] != new_labels.get(i)]
        print(f"FAIL: labels differ on {len(diff)} ids (first: {diff[:5]}) "
              f"-- STOP THE BUILD and report")
        return 1
    if abs(new_var - ref_var) > 1e-9:
        print(f"FAIL: trials_sr_var differs beyond 1e-9 "
              f"({ref_var!r} vs {new_var!r}) -- STOP THE BUILD and report")
        return 1
    exact = "bit-identical" if new_var == ref_var else "within 1e-9"
    print(f"PASS: k and labels identical, var {exact}; "
          f"speedup x{t_ref / max(t_new, 1e-9):.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
