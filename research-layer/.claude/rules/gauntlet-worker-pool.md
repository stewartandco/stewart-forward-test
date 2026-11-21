---
paths:
  - "pipeline/gauntlet.py"
  - "pipeline/test_gauntlet_pool.py"
  - "pipeline/simcache.py"
  - "pipeline/test_simcache.py"
---
## Gauntlet worker pool must fit the box (2026-09-03 BrokenProcessPool)

The 2026-09-03 10:30 cycle died in the gauntlet after 2h26m:
`OpenBLAS error: Memory allocation still failed after 10 retries, giving up`
in a worker, surfacing as `BrokenProcessPool` in the parent, exit 1, cycle
aborted (chain untouched: verdicts are written only at the end). Windows'
Resource-Exhaustion-Detector had logged the parent at **9.6 GB of commit
during clustering** (the per-strategy return series of ~6,000 registered
strategies as Python tuples, from the 1.5 GB JSON `simcache/`), on a box that
sits at ~52 GB of a 64 GB commit limit at rest (desktop apps; pagefile already
at its 48 GB maximum). The parent then kept all of that across the spawn of
`cpu_count - 2 = 6` workers, each committing ~280 MB at import for an
8-thread OpenBLAS pool it never uses. The 09-01 run survived only because it
ran at 02:30 with the desktop idle.

Three defences in `pipeline/gauntlet.py`, pinned by `test_gauntlet_pool.py`:

- **Workers get one BLAS thread** (`worker_env()` sets `OPENBLAS/OMP/MKL_NUM_THREADS=1`
  around the executor; spawned children read it at their numpy import,
  measured 54 MB vs 279 MB at import). The parent's own BLAS is unaffected.
- **Worker count is bounded by available commit**, not only cores:
  `worker_count(n_cpu, available_commit_mb())` = `min(cpu-2, (avail - 2048 MB) // 512 MB)`,
  floor 1 (the serial reference path). The run prints when it reduces.
- **The clustering inputs are released before the pool spawns** (`dated_returns_by_sid`,
  `returns_by_id`, `equity_len_by_sid`, `full_results`, `bars_by_cell` cleared +
  `gc.collect()`; every payload already carries what its candidate needs).
  PBO still walks EVERY family after the pool through `train_returns()`, so the
  train-window float slices are cached for every strategy first (`train_cache`,
  ~1/10th of the dated pairs) -- the first attempt cleared without that and
  12 gauntlet tests said KeyError.
  The run prints `[gauntlet] clustering inputs released before the pool (parent
  commit N MB, M MB available on the box)` -- read that line on the next fire.

Also fixed: the progress line now reports the whole run (`evaluated 480/974`),
not the chunk (`24/24` twenty times over hid the real count).

**The parent's 9.6 GB itself was fixed the same evening** (`simcache.Series`,
`docs/plans/2026-09-03-simcache-arrays.md`): the registry-wide series are int32
day ordinals + float64 returns (12 B/point) in `<key>.npz`, not `[date, ret]`
Python pairs (~150 B/point; a live entry holds up to 11,450 points, 1981->2026).
Measured on 200 live entries: 17 MB vs 205 MB. Values are the same float64s,
verdicts byte-identical (test_simcache's hit-vs-miss proof). Legacy `.json`
entries migrate on read; run `python -m pipeline.simcache migrate simcache`
once after deploying (minutes). The 15:30 re-run's "released" line printed
`parent commit 9008 MB` AFTER the release -- the pairs' floats were pinned by
PBO's train cache -- which is why the representation, not the release, is the
fix; the release block now only drops what the pool phase no longer needs.

