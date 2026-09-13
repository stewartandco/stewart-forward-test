# simcache arrays: the gauntlet's registry-wide series as numpy, not Python pairs

**Date:** 2026-09-03 · **Branch:** `feat/simcache-arrays` off `920f9fc` · **Status:** building

## Why (the incident, in numbers)

- 2026-09-03 10:30 cycle: gauntlet worker `OpenBLAS error: Memory allocation still
  failed after 10 retries` → `BrokenProcessPool` → cycle aborted after 2h26m.
- Windows Resource-Exhaustion-Detector: the gauntlet **parent at 9.6 GB of commit**
  during clustering, on a box that idles at ~52 of 64 GB commit (desktop apps,
  pagefile at its 48 GB max).
- `920f9fc` (pushed) bounded the pool by available commit and gave workers one
  BLAS thread; that let the 15:30 re-run survive with 4 workers once Coen closed
  apps. Its "release the series before spawn" freed nothing: the run printed
  `parent commit 9008 MB` AFTER the release, because the train slices cached for
  PBO hold the very float objects that sat inside the pairs, so no allocator
  arena ever empties.
- The representation is the cost. A live simcache entry holds **11,450** daily
  points (1981→2026). ~6,000 registered strategies × ~11k `[date, ret]` Python
  pairs × ~150 B (list + str + float + pointer) ≈ **10 GB**. As arrays:
  int32 day ordinal + float64 return = 12 B/point ≈ **0.8 GB**, and the 1.5 GB
  of JSON that takes most of the 75-minute "clustering" stage to parse becomes
  a few seconds of `np.load`.

## What changes

### `pipeline/simcache.py`
- On disk: `<key>.npz` with `dates` (int32, `date.toordinal()` of the
  `YYYY-MM-DD` key), `rets` (float64), `equity_len` (int64 scalar) and
  `sha256` (over the raw bytes of `dates` + `rets`, so the self-check survives
  the format change with the same semantics: mismatch → poison → miss).
- `get()` returns `{"series": Series, "equity_len": int}`; `put()` accepts a
  list of `(date_str, ret)` pairs or a `Series`. Same atomic tmp+replace.
- **Legacy `.json` entries are read and converted in place** (migrate-on-read:
  parse, self-check the JSON sha exactly as before, write the `.npz`, unlink
  the `.json`). `python -m pipeline.simcache migrate DIR` does it in bulk with a
  summary; nothing requires the bulk pass to have run.
- `Series`: `.dates` (np.int32), `.rets` (np.float64), `len()`, iteration
  yields `(date_str, float)` so every legacy reader (error-path diagnostics,
  tests) sees exactly what it saw. `.train(cutoff)` returns the list of returns
  dated `<= cutoff[:10]` (the `_date_le` rule) — floats identical to slicing
  the pairs.

### `pipeline/gauntlet.py` (clustering pass only)
- `dated_returns_by_sid: dict[str, Series]` — candidates and cache misses wrap
  `daily_returns_with_dates(...)` (unchanged function) with
  `Series.from_pairs`; hits come from the cache as `Series`.
- `intersect_returns`: `np.intersect1d` over the ordinal arrays, then
  `searchsorted` gathers; returns `dict[str, np.ndarray]` + the common dates
  as ISO strings (same signature, same values, same order).
- native branch: `returns_by_id = {sid: s.rets}`.
- `train_returns(sid)` = `dated_returns_by_sid[sid].train(cutoff)`; the
  `train_cache` from `920f9fc` goes (unnecessary and the thing that pinned the
  arenas).
- `_candidate_payload` receives `rets` as a Python list (`.tolist()`) so the
  worker stays pure-Python and pickles plain data, as its docstring demands.
- The release block keeps clearing `full_results` / `bars_by_cell` (still
  hundreds of MB for ~1,000 candidates) and keeps its commit print.

### `pipeline/cluster.py`
- `effective_trials`: the reference path (n ≤ 2, ragged, constant-nonzero
  rows) coerces array rows to lists before the pure-Python oracle runs. The
  numpy fast path already builds its matrix with `np.asarray`.

## Exactness (what the tests must prove)
- Every return value is the same float64; every `sum()` runs over the same
  floats in the same order → **verdicts byte-identical**. Proved by the
  existing `test_cache_hit_vs_miss_verdicts_are_byte_identical`, the deadline
  byte-identical test, and the gauntlet/gauntlet_classes suites.
- A legacy `.json` entry is served with the same values as its `.npz`
  conversion (new test), and is gone from disk afterwards (new test).
- `ENGINE_REV` and `cache_key` are untouched: the numbers did not change, so
  no entry is invalidated by this.

## Deploy
1. Fast-forward merge after the live cycle ends (workers re-import
   `gauntlet.py` from disk at spawn — never edit the live tree mid-cycle).
2. `python -m pipeline.simcache migrate simcache` once in the live tree
   (~7,999 files; minutes). Stragglers migrate on read.
3. Read the next fire's `[gauntlet] clustering done in ...s` and
   `clustering inputs released ... (parent commit N MB ...)` lines: expect
   minutes not 75, and well under 2 GB not 9.

## Not in scope
- Sharing one calendar array across series that have the same dates (would
  cut the 0.8 GB further) — later if the numbers say so.
- Pruning stale keys (older `ENGINE_REV` / data shas) from `simcache/`.
