---
paths:
  - "pipeline/bars.py"
  - "pipeline/qcells.py"
  - "pipeline/engine.py"
  - "pipeline/test_bars.py"
  - "pipeline/test_engine_intraday.py"
  - "pipeline/test_intraday_statistics.py"
  - "pipeline/test_qcells.py"
  - "pipeline/quarantine.py"
  - "pipeline/crypto_data.py"
  - "pipeline/registry.py"
  - "tools/intraday_rehearsal.py"
  - "tools/intraday_prefetch.py"
  - "tools/test_intraday_*.py"
  - "tools/daily_parity.py"
  - "tools/quarantine_parity.py"
---
## Intraday: bar mechanics per bar, statistics on daily marks (2b-1, 2026-10-10)
- **The rule:** an intraday strategy (timeframe below 1d, session 24x7)
  is SIMULATED per bar but MEASURED on the equity curve marked once per
  UTC day. Mechanics use the bar size: the engine's vol-target sizing
  annualises with bars per year, short financing accrues per bar
  (`short_financing_per_year / periods_per_year`), and `gate_mask`'s
  vol_percentile window is 365 x bars per day bars. Every recorded or
  gated statistic uses daily marks, so the pipeline's 365-based formulas
  stay correct at every timeframe. A 1d strategy's bars ARE its daily
  marks: every daily number is unchanged. Design:
  `docs/2026-10-10-intraday-correctness-design.md`.
- **`pipeline/bars.py` is the one home of the arithmetic.**
  `BARS_PER_DAY_24X7` = 15m 96, 30m 48, 1h 24, 4h 6, 12h 2, 1d 1;
  `bars_per_day(session, tf)`; `bars_per_year` = `cells.SESSION_PERIODS`
  [session] (default 365) x bars per day. `daily_marks(series)` keeps the
  LAST (date, value) of each UTC date (key `date[:10]`) and RAISES on
  out-of-order input; `daily_bars(rows)` is the same for bar dicts.
  `engine.run_spec` resolves bars per year with `bars.bars_per_year`
  (vol-target sizing, per-bar financing) and passes bars per day to
  `gate_mask`.
- **Statistics on daily marks:** `plateau.annualized_sharpe`,
  `gauntlet_core.daily_returns_with_dates`,
  `gauntlet.daily_returns_from_curve`, `window_vol` (over `daily_bars`),
  `train_dates` for the walk-forward, `regime_by_date` on `daily_bars`
  of the regime asset (`BTCUSD` if the spec's bars hold it, else the first
  asset sorted -- a per-cell spec, tradfi or crypto since Switch 1, is bucketed
  by its OWN asset, no BTC lookup; recorded, never gated; `[:10]` keys), and
  `engine.run_spec`'s `max_dd` metric (the screen's recorded max drawdown).
  The simcache key uses
  `gauntlet._resolved_periods(spec)`. `ENGINE_REV` stays "e2" (a 1d
  strategy's results are bit-identical, so no cached sim is invalidated).
  Trade-based gates (edge per trade, edge decay, MC p05, ruin, cost stress)
  are unchanged.
- **24x7 only:** any non-24x7 session with tf != "1d" raises
  `bars.IntradayUnsupported` (a `ValueError`), as does an unknown tf.
  Hazard for 2b-3: a NON-24x7 intraday spec reaching `registry_series`
  aborts the whole gauntlet/stats pass. In practice it fails BEFORE
  `IntradayUnsupported` is reached: `load_cell_data` raises
  `FileNotFoundError` (no tradfi `*_1h.csv` exists) or the
  `data_hashes[cell_id]` lookup in `registry_series` raises `KeyError`
  (no cell hash). Registration now refuses such a spec up front (2b-3
  C3, done: `registry.register_strategy` calls `bars.bars_per_day` on the
  spec's universe BEFORE any write and raises a `ValueError` that starts with
  the strategy id and ends "(2b-3 C3: intraday needs a 24x7 session)"; in
  between is the `bars.IntradayUnsupported` text, which names the timeframe
  and the session for a non-24x7 session, but only the timeframe for an
  unknown one on a 24x7 session (the session check runs first, so an unknown
  timeframe on a non-24x7 session reports the session text); every 1d spec,
  whatever its session, passes; pinned by `test_registry_session_refusal.py`).
  The fixture `test_pipeline.make_strategy` and `examples/regenerate_example.py` register
  their 15m spec on session `24x7` for that reason (Coen 2026-10-20).
- **Quarantine records intraday strategies (2b-2, 2026-10-11):** the
  recorder writes one row per CELL per UTC day. The cell key
  (`pipeline/qcells.py`) is `ASSET@TF` (`BTCUSDT@1h`) for an intraday tf
  and the bare asset for 1d; `"X@1d"` is never valid. The key names the
  row's `asset`, the snapshot's digest key and the price file
  (`data/<ASSET>_<TF>.csv`). An intraday row has 9 fields: the 7 daily
  ones plus `entries` and `exits` (non-negative ints, never bool;
  `registry.QUARANTINE_INTRADAY_KEYS`); a daily row is the 7-field
  shape, byte for byte. Price, position and equity are the book at the
  day's LAST bar, rebased at the entry day's last bar, and `action` =
  the day's LAST event (enter/exit/hold). `observe_day` passes
  `bars_per_year` and `bars_per_day` to the engine. Completeness: a day
  is recordable for a cell when its last slot (D 00:00 + (bars_per_day -
  1) x tf) is on disk OR any later bar is (`qcells.complete_days`), so a
  missing hour cannot make a day owed forever; a missing hour inside a
  day that has other bars is not a hole (that day is complete and is
  recorded from the bars present), but a whole UTC day with no bars is
  never owed (as for daily) and the fetcher never backfills it
  (`crypto_data.fetch_symbol`). Since 2b-3 C1 it is no longer silent: it
  raises a `feed_hole` item (section "Feed health (2b-3 C1/C2)"). A FAILED
  fetch raises `intraday_fetch_failed`. Repair = delete the cell's CSV so the
  next `--fetch-owed` refetches it from scratch. The chain is append-only: a
  refetch leaves every chained row as it is, but it changes `bars_sha256`
  for every already-chained date at or after the hole (`hash_bars_through`
  covers all bars up to the date), so a strategy that still owes such a
  date on that cell is deferred as `restated` (rule 2), every run, until a
  human resolves it -- so a repair is a deliberate, Coen-gated act, not
  routine. A restated-only deferral exits 0, not 1 (`deferred_only`), so a
  repair-stalled date does not turn the task red; it records a `restated`
  item keyed by the cell in `logs/degraded_quarantine.json`, which the
  Sentinel's `research_degraded` check turns WARN, then FAIL after 3 days.
  While the CSV is absent its strategies are deferred (missing price file,
  addendum rule 3); step 1b fetches before recording, so a normal run is
  unaffected, but if the refetch fails (e.g. a 418 ban) they stay deferred
  until a fetch succeeds, and a day where nothing records and no eligible
  strategy is already fully chained for that date (`n_present == 0`) exits
  1 (the total-stall rule). Owed days,
  catch-up and `--review` stay day-based on complete UTC days. Fetch:
  step 1b of `tasks/run_quarantine.bat` runs `python -m
  pipeline.quarantine --fetch-owed`: only the intraday cells of
  quarantined strategies (nothing while none is quarantined;
  `crypto_data` is imported only when a cell is owed), incremental,
  closed bars only. The intraday CSVs are gitignored (`data/*.csv`),
  never committed; the chain pins them by hash. A failed cell is a
  degraded item with source `intraday_fetch_failed` (key = the cell) in
  `logs/degraded_quarantine.json`; the fetch step OWNS those items and the
  `feed_hole` / `feed_stale` ones (`FETCH_STEP_SOURCES`,
  `_write_fetch_ledger`), and catch-up and `--date` keep them
  (`keep_sources`). A generic failure is retried in the same run (2 passes,
  see "Feed health" below). A Binance 418 ban stops the step at once and
  marks every remaining cell.
  A fetch failure never blocks the daily record: only a crash of the
  step is carried to the task's exit code (`FETCH_RC`), after the record
  is committed. A non-24x7 intraday spec is deferred with a degraded
  item `intraday_unsupported` (`_split_unsupported`), never a crash;
  registration now refuses such a spec up front (2b-3 C3), so the deferral
  only guards a spec chained before C3. The live gate's
  `closed_forward_trades` (`pipeline/livegate.py`) SUMS `exits` on rows
  that carry them and counts `action == "exit"` on daily rows. Cost
  hazard: each date re-simulates the cell from bar 0 (measured in the
  2026-10-15 rehearsal, before 2026-10-17 engine perf: ~145 s pure
  simulation per 1h vol-percentile strategy-day, ~175 s per strategy-day
  including chain I/O, at the then O(n x 365 x bars_per_day) percentile;
  chain I/O removed from that figure by the 2026-10-16 fast path;
  simulation still runs per date, now outside chain.lock). Since the
  2026-10-17 engine perf (section "Engine series cache") the percentile is
  an exact incremental one and the series are cached across runs: the
  catch-up of date 2026-10-14 went 2,857 s to 286 s cold / 201 s warm, and
  `--date` plus the catch-up of the 10-18 morning 1,810 s to 259 s (single
  process, measured 2026-10-19). Since 2b-3 D-i each intraday file is parsed ONCE
  per process (`quarantine._full_bars` memo, bullet below), no longer per
  strategy per date (`_load_cell_bars`); the per-date re-simulation from bar 0
  and the O(n) `complete_days` pass per call remain. Measured 2026-10-31 (the
  D-i rehearsal on a chain copy, 12 assets, 4 twins per timeframe, simulation
  timed per twin): 1h 0.605 s, 4h 0.353 s, 12h 0.087 s per strategy-day; 9
  dates of 12 rows took 130 s of catch-up (first date 13.0 s cold, then 3.3 to
  4.0 s). Proof:
  `tools/quarantine_parity.py` (`dump` before and after a change, then
  `compare`) recomputes a seeded sample of chained daily quarantine days
  and compares snapshot digests and rows exactly, old code vs new;
  re-run it after any change to the recorder. The `quarantine`
  subcommand of `tools/intraday_rehearsal.py` runs the whole stage end
  to end on a scratch chain COPY and refuses the live chain. Tests:
  `test_quarantine_intraday.py`, `test_quarantine_fetch_owed.py`.
  Design: `docs/2026-10-11-intraday-paper-stage-design.md`.
- **Parity guarantee:** `tools/daily_parity.py` (`dump` before and after a
  change, then `compare`) fingerprints registered 1d strategies. On
  2026-10-10, 200 registered 1d strategies (167 of them vol-target or
  vol-percentile) were bit-identical across the change, 0 errors.
  `compare` fails on a meta mismatch (seed, cutoff, sample size, registry
  sha), a sample-list mismatch, any per-strategy fingerprint mismatch,
  empty results, or ANY error, so it cannot pass vacuously. Re-run
  it after any change to `bars.py`, the engine's sizing/financing/gate
  code, or the statistics above.
- **`tools/intraday_rehearsal.py`:** scratch-only. It registers 1d/1h
  twins of real crypto strategies on a COPY of the chain, runs the real
  screen and gauntlet on the copy and reads the outcomes side by side.
  `quarantine` moves twins to quarantine on the copy by a synthetic
  gauntlet transition, then runs the real recorder with a REAL Binance
  fetch into its scratch data dir. Since 2b-3 D-i it moves `--n` twins PER
  timeframe (`pick_twins`, coarsest first) for every timeframe in
  `--timeframes`; the default is every non-1d timeframe the twins file holds
  (a 1d+1h file still moves 1h only, a 1d-only file still moves 1d), and a
  requested timeframe the file lacks, or fewer than `--n` twins at one, is a
  SystemExit before anything is written. `--transition-only` verifies the
  chain BEFORE, hands the twins to quarantine and stops: no
  `quarantine.run`, no fetch, no catch-up or review, no `logs/`, no data
  file touched (exit 0 iff `REGISTRY VALID`; no verify AFTER by design), so a
  rehearsal can leave every date owed. Both `register` and `quarantine`
  REFUSE the live chain, and `quarantine` also refuses the live data dir
  (exit 2, nothing written). `pick_originals` (the vol_target/vol_percentile
  picks first, one per family before any repeat, seeded) is pinned by seven
  tests in `test_intraday_rehearsal.py`.
- **Phase 2 prefetch (2b-3 D-i1): `tools/intraday_prefetch.py`.** HAND-RUN,
  never scheduled, and it never touches the refresh ledger
  (`logs/degraded_crypto_refresh.json`), the chain, git or a lock. It fetches
  the full listing history of every asset in `ACTIVE_CELLS["crypto"]` at
  1h, 4h and 12h (228 cells, coarsest first per asset) through
  `crypto_refresh.refresh` (keyless klines via `crypto_data.fetch_symbol`),
  then CLIPS each completed file so it never starts before the asset's 1d
  file (SUN's starts 2021-06-18) and re-stamps that cell's snapshot-manifest
  entry; it reports admission rows, feed health, holes, failures and timing
  per cell (`--out`). The stop is the EARLIER of the next 07:58 Perth and
  20:00 Perth of the run day, INCLUDING the clip tail (a cell that needs a clip
  writes nothing at or after the stop: `clip_deferred`, a re-run after 08:40
  clips it), and it refuses to START 07:58-08:40 (exit 2; `--now` is for
  tests). A cold full run takes about 45 min (2,542 s measured): on the live
  tree START IT BY 18:30 (a daytime start stops at 20:00, and a start at or after 20:00 does
  nothing and reports every cell `aborted`; a start 00:00-07:58 runs to 07:58,
  so the standing no-live-write 20:00-07:00 rule is the operator's to keep). A 418
  or a RateLimited stops it at once (the rest are `aborted` rows). A
  `clip_failed` cell (file still starts before the 1d file) is a report row,
  not a stop: re-run until there are zero before the data is used. A clip
  whose manifest re-stamp fails is `clipped_manifest_stale` (file clipped,
  entry stale): a plain re-run HEALS it, because `refresh` re-stamps every cell
  it completes from the file on disk (`fetch_symbol` returns the on-disk
  sha256/rows/first_date even when it adds no bar) and the clip then finds
  nothing to drop; check the re-run's report shows the cell `fetched`, and
  never hand-edit the manifest or refetch to repair it. **A deleted intraday
  file is recreated from its asset's 1d start by ANY path (2b-3 plan 5,
  `dfaf72b44`):** `crypto_data.fetch_symbol` on a tf != "1d" file with no rows
  starts at `daily_floor_ms(symbol, data_dir)` (00:00 UTC of the first
  `<symbol>_1d.csv` row's date) and drops any kline opening before it, so
  `crypto_refresh` (the 08:02 `33_CryptoGridRefresh`), `crypto_data fetch`,
  `quarantine --fetch-owed` and this tool all rebuild a file that never starts
  before its 1d file (SUN's starts 2021-06-18). With the 1d file absent or
  empty it raises `crypto_data.MissingDailyFile` (a `ValueError`) BEFORE any
  request: an ordinary cell failure (`crypto_refresh_failed` /
  `intraday_fetch_failed`), never a ban stop, nothing written. Fix a missing 1d
  file first. The floor applies only when CREATING: an existing intraday file
  resumes as always. The clip above stays as the second belt for a file written
  before the guard; neither admission nor feed health detects a violation.
  The report's `attempts` are FETCH CALLS, not HTTP requests (a cold cell costs
  about ceil(rows/1000) requests). It DELIBERATELY reads `cells.ACTIVE_CELLS`
  assets, not
  `active_cells()`, so an `EXCLUDED_CELLS` cell is still fetched: the
  admission rows it produces are what decides the exclusions. A hand run
  leaves the tracked manifest modified on the shared tree, as
  `33_CryptoGridRefresh` does. Tests: `test_intraday_prefetch.py`.
- **Intraday price files are parsed once per process (2b-3 D-i, Task 4).**
  `quarantine._full_bars(data_dir, asset, tf)` is an LRU memo over the whole
  intraday parse, keyed `(resolved path, st_mtime_ns, st_size)`, stat'ed before
  AND after the read (a mismatch serves the parse and memoises nothing; a
  missing file raises exactly as before). `_load_cell_bars` takes the date's
  prefix by `bisect_right` on `date[:10]` and returns a NEW list over shared
  bar dicts (no intraday path mutates a bar; pinned by a deep-copy test), so
  results equal the old per-date re-parse. Bound: `FULL_BARS_MEMO_MAX_BARS`
  = 2,000,000 bars across held files (about 1 GB as dicts); one file over the
  bound is still served and held. Access is cyclic, so a bound below the
  working set gets ZERO hits: read `full_bars_memo_stats()` (hits, misses,
  evictions, bars and files held) after a run. Measured 2026-10-31 on the
  rehearsal (8 intraday files, 211,159 bars held): 160 hits, 8 misses, 0
  evictions, peak working set of the whole run 712 MB. **Sized per run (2b-3
  plan 5, `358313d23`):** `FULL_BARS_MEMO_MAX_BARS` is only the DEFAULT bound
  and is never mutated. The OUTERMOST `quarantine.run` (a catch-up's per-date
  passes share its view and do not) calls `size_full_bars_memo(data_dir,
  cell_keys)` over the intraday cells it records, which sets the RUNTIME bound
  `_full_bars_bound` = min(`FULL_BARS_MEMO_CEILING_BARS` 6,000,000, max(default,
  total data rows of those cells)); rows are counted by newline bytes, no
  parse. At Switch 2 the 76 1h files alone hold about 4 million bars, so an
  unsized memo would get zero hits. The ceiling (about 3 GB as dicts at about
  0.5 KB a bar) stops a runaway grid taking the box; above it the LRU behaves
  as before (correct, slower) and the run prints a `WARN full-bars memo:
  touched N bars > ceiling 6,000,000` line and adds one `memo_ceiling` ledger
  item (projected crossing 2027-02-14; the clustering memory wall it was once
  paired with is retired by the per-cell method, so this ceiling stands on its
  own). **Read the `full-bars memo: hits N, misses N,
  evictions N, bound N bars` line the run prints (once, in a `finally`):
  evictions above 0 with hits near 0 means the grid outgrew the ceiling.**
  `_reset_full_bars_memo` restores the default bound. Daily cells never use it (`_daily_bars` rewrites
  dates in place). Residual: a same-size rewrite inside one filesystem
  timestamp tick in the same process would be served stale. The NTFS mtime
  comes from the system clock tick (up to about 15 ms), not 100 ns, so the
  window is wider than it looks; it is safe because the only writer of an
  intraday file, `fetch_symbol`, only APPENDS and never re-serves a bar, so a
  same-size rewrite is a byte-identical rewrite (and production files are
  written by another process earlier). Anyone adding a second writer of
  intraday files must re-read this. Tests: `test_quarantine_intraday.py`.
- **Crypto intraday is ACTIVE from the Switch 2 commit (2b-3 plan 5; Switch 1,
  2026-10-17, was 1d only):** `cells.ACTIVE_CELLS["crypto"]` = 76 USDT assets
  x `("1h", "4h", "12h", "1d")` (declaration order, import-checked) = 304
  cells; a 12-asset rotation window is 48 cells (the rotation still steps over
  ASSETS). 15m and 30m (phase 3) stay declared and unswept. `EXCLUDED_CELLS`
  stays empty: the latest phase 2 first bar is APTUSDT 2022-10-19, so the
  admission rule excludes nothing. Pinned by `test_switch2.py`,
  `test_switch2_guard.py`, `test_switch2_preview.py`. The composer still holds
  the intraday cells back until BOTH notes are chained, and the pause and
  rollback rules are in the "Switch 2" section below.
- **Switch 2 hazards (costed for plan 5; the figures stay as the basis for any
  new timeframe):** `vol_percentile` in
  `gate_mask` was O(n x 365 x bars_per_day), about 940 s per 1h
  vol_percentile candidate under perturbation, and
  `GAUNTLET_PRIOR_S_PER_CANDIDATE` (20 s) was ~47x too low for 1h, so the
  cycle budget would have been misjudged (both before 2026-10-17 engine
  perf). Now one 1h gauntlet candidate (vol_percentile + trend_scan) takes
  ~70 s cold / ~16 s warm (measured 2026-10-19), and the priors are per
  timeframe (`PRIOR_S_BY_TF`, `SCREEN_PRIOR_S_BY_TF`; section "Engine series
  cache"); 1h still costs about 22-25x a 1d candidate, so cost the grid
  before activating. Artifact storage (2b-3 D-i3, 2026-10-29): an intraday
  bundle's committed `equity.csv` holds the UTC DAILY MARKS (`bars.daily_marks`;
  each row keeps the day's LAST bar's stamp: `YYYY-MM-DD 23:00:00` at 1h,
  `20:00:00` at 4h, `12:00:00` at 12h, an earlier stamp on a partial last day;
  the `[:10]` date compares are right for all three); the per-bar curve (size per bundle,
  measured 2026-10-31: 1h median 1.27 MB, max 2.16 MB; 4h median 316 KB; 12h
  median 104 KB; a 1d bundle has none) goes to `equity_bars.csv`, gitignored
  (`artifacts/*/equity_bars.csv`), never in `commit_list.BUNDLE_FILES`, never in
  `bundle_hash`'s three names, and pinned by `config.json["equity_bars_sha256"]`
  (LF-normalised sha, so `artifacts_hash` still binds it). A 1d bundle is byte
  identical (no new key, no new file; `universe.timeframe` read with `.get`).
  Readers of `equity.csv` that compare the `date` column must compare `d[:10]`
  (the diagnose_* scripts do, `[:10]` on both sides, so a cutoff day is
  kept). The Switch 2 gate that `gauntlet_stats` had never run end to end on an
  intraday spec is MET on copies (2026-10-31 D-i; 2026-11-01 Task 12: gate 1
  MET, `cluster_n` 27,102, `cluster_need_mb` 8,125, 106 stats chained, wall
  390 s). Closed in 2b-3 D-i:
  `tools/intraday_rehearsal.pick_originals`' selection rules are now tested,
  and the rehearsal's `quarantine` covers every phase-2 timeframe (see its
  bullet above). `pipeline/test_switch2_preview.py` pins that a 12-asset
  window at 1h/4h/12h/1d is 48 cells (one single-asset 24x7 spec per cell per
  combo), and that one spec per timeframe registers on a scratch chain and passes C3.
- **The two Switch 2 notes are chained by Coen at activation, VERBATIM, protocol
  note first:** `docs/notes/gauntlet-protocol-v6.1-intraday-daily-marks.md`
  (prefix `gauntlet-protocol-v6.1-intraday-daily-marks:`) and
  `docs/notes/crypto-grid-phase2-v1.md` (prefix `crypto-grid-phase2-v1:`), each
  with `python -m tools.grid_switch chain-note ... --prefix ...` (LF bytes
  enforced by `.gitattributes`; the tool refuses a CR). Coen approves the exact
  text; the commit that added them still calls them drafts. Plan 5 removed the
  protocol note's "STATUS: DRAFT" line. Since 2b-2 the protocol note has a second paragraph, "Quarantine and
  the live gate" (the cell key, entries/exits, the completeness rule, the live
  gate's sum of exits). Do not edit either wording after approval, and do not
  treat intraday daily-mark verdicts as protocol-covered before both are on the
  chain: the composer hold below enforces it for new registrations.

