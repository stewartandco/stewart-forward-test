---
paths:
  - "pipeline/series_cache.py"
  - "pipeline/test_series_cache.py"
---
## Engine series cache (2026-10-17, measured 2026-10-19)
Design: `docs/2026-10-17-engine-perf-design.md`. The engine got faster without
changing a bit: `pipeline/series_cache.py` (`SeriesCache`) caches the trend
t-stat, realized vol and vol percentile series; the t-stat is computed once
per window per bar; the percentile is an exact incremental one.
- **Location:** `<registry dir>/seriescache/`. Gitignored and safe to delete,
  at the cost of one cold run.
- **Key:** `(kind, params, ENGINE_REV, cell)`. An `ENGINE_REV` bump orphans
  every entry (never served); prune removes them.
- **Validity rule: exact input bytes.** A request for a prefix of the stored
  input is served from the store; a longer input that extends the stored one
  extends it; ANY other difference (a restated bar, -0.0 vs 0.0, a NaN)
  recomputes from bar 0. Never relax this to a hash or a float `==`.
- **Prune:** once per quarantine recorder run, before the first date. It
  removes `.tmp` files older than 1 h, stale-rev entries and corrupt entries,
  leaves an entry a writer replaced mid-prune alone, and never raises. Since
  2b-3 D-i it reads each entry's `key` member ONLY (`_read_key`), never the
  payload or checksum, so a bit flip in the values survives prune and is
  caught by `_load` (a MISS, healed by the next write); an entry whose key
  cannot be read for any reason is treated as corrupt and unlinked if
  possible (a locked file on Windows survives the failed unlink; the cost is a
  recompute). It then CAPS the disk: if the survivors exceed `MAX_DISK_BYTES`
  (20 GiB) it deletes the oldest by mtime until at or under, with the same
  re-stat guard for a replaced file. The cap orders by WRITE mtime: a read does
  not refresh it, so it removes the least recently WRITTEN entries, not the
  least recently used. The 08:20 recorder run prints
  `seriescache: pruned N tmp, N stale-rev, N corrupt, N capped (K kept)`
  when anything was removed, so a cap hit is visible there
  (`capped_removed`); `kept` and `bytes_kept` are POST-cap. Measured 2026-10-31
  (rehearsal copy, after screen, worker and stats on 216 twins): 146 MB in 310
  entries; 1h about 11 MB per cell (102 entries over 9 cells, about 1 MB per
  entry), 4h 2.9 MB, 12h 0.7 MB per cell; prune 0.10 s over the 310.
- **In-process bound (2b-3 D-i):** the in-memory store is a byte-bounded LRU,
  `MEM_MAX_BYTES` (1 GiB; an entry weighs `len(inputs) + 8 * len(values)`)
  PER PROCESS, so the gauntlet worker's 6 processes may hold up to 6 GiB
  between them. Every get refreshes recency; the entry just inserted is never
  evicted (a lone oversize entry is kept); `stats["evictions"]` counts them.
  An evicted key is adopted back from disk (counted a hit) or recomputed:
  residency only, the values served are the same bits. Measured 2026-10-31:
  the 1h worker pool's tree peaked at 1.9 GB commit (6 workers, cold cache),
  its largest process at 1.3 GB; the eviction count was not read, and at that
  scale a process's whole disk cache (146 MB) fits the bound.
- **Who uses it:** the quarantine recorder and the gauntlet worker WRITE it;
  the signal service is read-only and imports the cache lazily (it must not
  write `seriescache/`); screen and `run_spec` callers pass none, so they use
  per-call (memory-only) caches.
- **Bit-identical by rule.** Any change to a cached kind or to the engine
  needs `tools/daily_parity dump --twins-1h N` against the pre-change commit,
  plus `tools/quarantine_replay`. The 2026-10-19 proofs: daily_parity 220/220
  (200 1d + 20 1h twins), 0 mismatches; quarantine replay 800/800 MATCH and
  2,251 rows equal to live.
- **Figures** (2026-10-19, single process):
  - catch-up date 2026-10-14: 2,857 s to 286 s cold / 201 s warm (realistic);
  - one 1h gauntlet candidate (vol_percentile + trend_scan): ~940 s to 70 s
    cold / 16 s warm;
  - quarantine `--date` plus the catch-up of the 10-18 morning: 1,810 s to
    259 s;
  - the 1h/1d cost ratio is about 22-25x for screen, gauntlet and quarantine.
- **Deadline priors are per timeframe, and so are the measured rates.**
  - `gauntlet_core.PRIOR_S_BY_TF` = {"1d": 20.0, "1h": 70.0, "4h": 25.0,
    "12h": 20.0} plus
    `prior_s_for(tf)` (an unlisted timeframe = the 1h prior scaled by bars per
    day, floored at the 1d prior). The 1h value is the HEAVIEST class
    measured, rounded up to 10 s. 4h and 12h (plan 5, `fd0794887`) are the 1h
    prior x the ratios measured in the 2026-10-31 D-i rehearsal, rounded up to
    5 s; 15m and 30m stay unmeasured (the scaled fallback) until phase 3.
  - `screen.SCREEN_PRIOR_S_BY_TF` = {"1d": 2.0, "1h": 30.0, "4h": 15.0,
    "12h": 10.0} plus
    `screen_prior_s_for(tf)`. The 1h value is the MEAN measurement (20.38 s)
    rounded up to 10 s, deliberately, unlike the gauntlet's. 4h and 12h are
    the 1h prior x the measured ratios (2026-10-31), rounded up to 5 s.
  - Both are conservative on purpose: the budget charges chunk wall / n
    across a pool, and a prior is judged only until a chunk of that
    timeframe has run. `DeadlineBudget` keeps its rates per timeframe
    (`key=tf`); gauntlet and screen chunks never mix timeframes.
  - **The FIRST gauntlet chunk of a timeframe with no measured rate is ONE
    pool round (`max_workers` candidates; `gauntlet_worker._walk_chunks`,
    `DeadlineBudget.has_rate`), then chunks return to the normal size
    (2026-10-19 final review).** A prior is a single-process seconds per
    candidate and `fits` charges it un-amortised, so the full production
    chunk (6 workers x `GAUNTLET_CHUNK_PER_WORKER` 4 = 24 at the 70 s 1h
    prior, plus the 75 s reserve = 1,755 s) exceeded the whole 25-minute
    budget: a 1h chunk could never start and no 1h rate was ever measured.
    One round (6 x 70 + 75 = 495 s) fits and records the pool's real rate.
    No candidate is reordered. The same cap applies to an unmeasured first
    1d chunk (24 becomes 6 once per run). A registry
    that alternates timeframes still shrinks the GAUNTLET worker's chunks
    toward 1 spec: slower, chain unchanged. The screen no longer does: it
    groups its jobs by timeframe first (section "Switch 2"). The screen's early "not even one spec fits" exit uses the
    CHEAPEST prior in the registry (min, never max).
- **Switch 2 gates (2026-10-19 final review): all four DONE in 2b-3 D-i
  (2026-10-29).** None was a defect at 1d; they were costs or edge cases that
  arrive with 1h scale:
  - **Prune read and sha-checked every entry on each recorder run** (the
    morning wrapper may run it twice; GBs of reads on E: at 1h scale). Now it
    reads the key only (Prune bullet above); measured 0.10 s over 310 entries
    (2026-10-31).
  - **Each key stores its own copy of the closes** (about half the bytes of an
    entry). Now bounded by `MAX_DISK_BYTES` (20 GiB cap in prune); disk
    measured at 1h: about 11 MB per cell, 1 MB per entry (2026-10-31).
  - **In-process entries were never evicted** (quarantine process, and the
    serial worker path `worker_count` == 1). Now the per-process byte LRU
    (In-process bound bullet above), largest process 1.3 GB (1h worker, 2026-10-31).
  - **Exit-only trend windows are computed over all n bars on per-call
    caches** (screen), so a pathological series (overflow, or a
    standard-error underflow) could raise on a bar the old path never
    visited. **The deferred-fault rule (2b-3 D-i):**
    `trend_tstat_series.compute` catches `ArithmeticError` per bar
    (ZeroDivisionError, OverflowError) and stores `math.nan` for that bar;
    `_best_tstat` reads the series element-wise per window in `windows`
    order and, on a NaN, makes the DIRECT `trend_tstat` call on that slice
    (`_series_or_direct`): a faulting window raises its own exception at its
    own place in the order, and a bar whose true value is NaN returns the
    direct call's own NaN (bit-identical). A bar the direct path never reads
    never raises. Non-NaN values are served untouched; `ValueError` (short
    or empty window) paths are unchanged. Cost: a faulting bar re-runs the
    direct call on every read (no cache for the deferred value), so only
    pathological data pays.
  - **ROLLBACK RULE (read before reverting this merge):** NaN markers are
    PERSISTED in `seriescache/` by the new code, and pre-D-i code would serve
    them as t-stat VALUES where the direct path raises. A revert of the D-i
    merge MUST also delete `seriescache/` (one cold run). The same is true of
    any hand-copy of `seriescache/` taken after the merge.
- **Still uncached (follow-up candidate, Coen's call):** vol_target sizing's
  realized vol (about 66 s of the warm 201 s), channel_breakout
  highest/lowest (about 25 s), and the sma/stdev series and `atr_wilder`.

