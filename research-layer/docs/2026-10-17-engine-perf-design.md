# Engine performance without changing a bit (design)

Date: 2026-10-17. Status: DRAFT for Coen's review. Morpheus build 2b, 2b-3
pre-work, sub-project **B** of four (A quarantine write speed -> B engine
performance -> C intraday safety rails -> D activation data).

## Decisions (all Coen's, 2026-10-16/17)
- **Goal:** both the daily quarantine catch-up and 1h-at-grid-scale gauntlet
  affordability, through the engine itself.
- **Scope:** the exact incremental vol_percentile; a per-timeframe gauntlet
  time prior; profile a real catch-up date and fix its biggest cost.
- **Exactness: BIT-IDENTICAL ONLY.** Every number any stage records must stay
  exactly what it is today. Speed may come only from not computing the same
  value twice, never from computing it differently. No rolling-sum formulas,
  no numpy reductions that change summation order.
- **Process:** same as A -- this spec, then a plan, then subagent-driven build
  with per-task reviews, mutants, and old-vs-new proofs.
- **1h target: report, don't gate (Coen 10-17).** B is done when the three
  bit-identical components land with their proofs; the re-timed cost of a 1h
  gauntlet candidate (today ~940 s) and of the catch-up date are REPORTED, and
  any further 1h speed work is a separate decision for Coen with the numbers.

## Calls made by the designer (not Coen's; check these)
- **Cache entries serve any prefix (builder check Q1).** Each cached series is
  stored WITH the exact input values it was computed from (the closes, or
  high/low/close for ATR). A request for n bars compares the stored inputs to
  today's first min(n, stored) inputs exactly (float equality): equal and
  n <= stored -> serve `series[:n]`; equal and n > stored -> compute only
  bars stored..n-1 and extend; any difference -> recompute from bar 0. One
  entry per (cell, indicator, params, ENGINE_REV), so screen's cutoff bars,
  the gauntlet's full history and quarantine's per-date prefixes all read the
  same entry without overwriting each other. Comparing inputs instead of a
  hash makes "the stored series was computed from exactly these bars"
  checkable bar by bar.
- **Who writes (builder check Q2).** The engine stays IO-free: callers pass an
  optional series-cache object into `simulate_asset` / `run_spec`; with none,
  behaviour and cost are exactly today's. Writers: the quarantine recorder
  and the gauntlet worker (the 1h goal needs the gauntlet: its pool is new
  per chunk, so only the disk cache shares series across candidates on one
  cell). The signal service READS only (its read-only contract; its synthetic
  next bar is never written). Screen and gauntlet_stats are left as today
  (stats already has `simcache`); adding them later is a one-line opt-in.
- **Per-timeframe budget (builder check Q3).** `DeadlineBudget` keeps its
  measured rate PER TIMEFRAME (bars_per_day), so the first 1h chunk is never
  judged at the 1d rate; the 1h prior comes from the re-timed rehearsal
  candidate, and unmeasured timeframes scale the 1h prior by bars_per_day
  (15m x4, 30m x2, 4h /4, 12h /12), floored at the 1d prior. Chunk size is
  unchanged.

## 1. Why: the measured costs

Profile of ONE real catch-up date (2026-10-14, 490 rows, new code after A, on
a copy of the live chain; 2,857 s total, `cProfile`, 2026-10-16):

| where | cumulative s | share |
|---|---|---|
| `simulate_asset` (all strategies) | 2,848 | 99.7% |
| `trend_tstat` | 2,206 | 77% |
| - via `entry_signals` (`trend_scan*` entries) | 1,719 | |
| - via `signal_exit` (`tstat_decay` exits, recomputed every bar in position) | 625 | |
| `gate_mask` (vol_percentile) | 398 | 14% |
| price-file reads, owed-date scan, chain | negligible | |

Earlier measurements (2b-1/2b-2 rehearsals): ~940 s per 1h vol_percentile
gauntlet candidate under perturbation; ~145 s per 1h quarantine strategy-day;
`GAUNTLET_PRIOR_S_PER_CANDIDATE` = 20 s is ~47x too low for 1h.

Root causes (read in `pipeline/engine.py`, live head `482c1805b`):
- `trend_tstat(window)` is O(w); `trend_scan*` calls it for every bar i and
  every window length in `range(20, max_lookback + 1, 10)`; `signal_exit`'s
  `tstat_decay` calls it AGAIN for every bar a position is open, over the same
  windows when `max_lookback` matches, on the same closes.
- `percentile_rank(values, i, window)` re-scans the whole trailing window per
  bar: O(n x 365 x bars_per_day).
- Every quarantine date re-simulates each owing strategy from bar 0 over bars
  that did not change since yesterday (indicators are causal: bar i's value
  depends only on bars <= i).

## 2. The rule

**A result computed once is never computed again, and nothing is ever
computed differently.** Every speedup is a cache or a reuse of the exact
value the current code produces, through the exact same arithmetic, in the
same order. A cache may only skip work; it can never change a result. When in
doubt (a key mismatch, a restated bar, an engine change, a read error) the
code recomputes from scratch.

## 3. Components

### 3.1 trend_tstat computed once per (closes, i, w) within a process
- One memo per simulation input (the closes list of a cell) keyed by
  (i, w), filled by the existing `trend_tstat(closes[i-w+1:i+1])` call and read
  by both `entry_signals` (`trend_scan`, `trend_scan_ds`, `trend_scan_dense`)
  and `signal_exit` (`tstat_decay`). Same function, same slice, same result.
- The gauntlet's perturbation variants of one spec run on the same bars; the
  memo must be reachable across those variants in one process (keyed by the
  identity or content hash of the closes), so a variant that changes only
  `t_min` costs nothing new.
- Expected: removes the ~625 s exit recomputation outright, plus all
  cross-variant duplication.

### 3.2 vol_percentile, exact and incremental
- `gate_mask`'s vol_percentile keeps a sorted window of the trailing
  `365 x bars_per_day` values and counts `v <= values[i]` with bisect: the
  same integer count, the same division `count / len(win)` -> the same float.
- Same None rule: a window containing any None returns None (the current
  `any(v is None ...)`), with the window length check unchanged.
- `percentile_rank` stays as the reference implementation for tests and hand
  tools.

### 3.3 Indicator series carried across runs (the catch-up's main cost)
- A disk cache of per-cell indicator series (the trend t-stat series per
  window length; the realized-vol and percentile series per lookback/window;
  any other series the profile shows matter), so the next day's simulation
  computes only the new bars.
- **Key:** indicator name + its parameters + `ENGINE_REV` + the cell key;
  the entry stores the exact input values the series was computed from.
- **Use:** see "Calls made by the designer" above: compare stored inputs to
  today's bars exactly; serve a prefix, extend a longer request, recompute
  from bar 0 on any difference (restated bar, different engine rev,
  unreadable entry).
- **Writes:** atomic (temp + `os.replace`), safe under concurrent readers and
  the gauntlet's worker processes; a failed write is logged and the run
  continues uncached (the PBO cache precedent).
- **Location:** a gitignored directory beside `simcache/` (never committed,
  never chained). Size bounded and prunable; deleting it is always safe.
- Readers: the quarantine recorder (each date, each catch-up date), the
  signal service, and any stage that simulates the same cell repeatedly.

### 3.4 Per-timeframe gauntlet time prior
- Replace the single `GAUNTLET_PRIOR_S_PER_CANDIDATE` (20 s) with a prior per
  timeframe (via `bars.bars_per_day`), measured on real candidates after
  3.1-3.3 land, so the cycle budget is not misjudged for 1h. The measured
  rate still replaces the prior after the first chunk (unchanged
  `DeadlineBudget` behaviour).

## 4. Not in B
- No change to any recorded number, gate, threshold or verdict.
- No rolling/vectorised float formula (Coen: bit-identical only).
- No intraday activation, alarms, registration refusal (C), USDT refresh or
  late-cell work (D).
- The simulation state machine (positions, fills, costs) is not resumed
  across runs; only indicator series are cached.

## 5. Proofs (the merge gate)
- **`tools/daily_parity.py`** (`dump` before, `compare` after): 200 registered
  1d strategies, fingerprints byte-identical, 0 errors.
- **1h exactness:** the same comparison on 1h twins of real crypto strategies
  (the `intraday_rehearsal` copy pattern) -- byte-identical.
- **Quarantine replay** (`tools/quarantine_replay.py`): old vs new on a prefix
  of the live chain, `compare` MATCH, both VALID, plus the multi-date check
  (rows equal to the live chain's).
- **Cache safety tests:** a restated bar (anywhere in the prefix), an
  engine-rev bump and a corrupt entry each force a full recompute with an
  identical result; a shorter request served from a longer entry, a longer
  request extended from a shorter one, and a cache hit all equal a cold run
  byte for byte; the signal service never writes an entry.
- Mutants on every new rule; full suite on the final head.
- **Measured:** the same catch-up date re-profiled (target: minutes, not ~47);
  a 1h vol_percentile gauntlet candidate re-timed.

## 6. Shipping
- Merge into live on Coen's go under the live-tree guard; the first live
  08:20 run is the real check.
- research-layer/CLAUDE.md: the cache's location, key, invalidation rule and
  "deleting it is always safe"; the new cost figures; the per-tf prior.
