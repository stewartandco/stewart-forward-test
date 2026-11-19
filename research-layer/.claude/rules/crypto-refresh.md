---
paths:
  - "pipeline/crypto_refresh.py"
  - "pipeline/test_crypto_refresh*.py"
  - "pipeline/crypto_data.py"
  - "pipeline/test_crypto_data.py"
  - "tasks/run_crypto_refresh.bat"
  - "data/crypto_snapshot_manifest.json"
---
## Crypto grid refresh (33_CryptoGridRefresh, Switch 1, 2026-10-17)
- `python -m pipeline.crypto_refresh` (wrapper `tasks/run_crypto_refresh.bat`,
  task `\Morpheus\33_CryptoGridRefresh`; number 33 because 30/31 are build 4a's
  and 32 is build 5's), daily **08:02 Perth**, `ExecutionTimeLimit` PT15M, NO
  StartWhenAvailable, no retry by design. Registered on MERGE DAY (Task 12 of
  the plan, Coen-gated), not before. The Sentinel will watch its ledger (label
  `crypto_refresh`, 30 h proposed, pending Coen) and list 33 as "observing";
  that lands at merge (sc-ops-sentinel `feat/crypto-refresh-ledger`).
- **What it fetches:** every cell of `refresh_cells(registry)` (the active
  crypto cells, 304 since Switch 2, the 76 1d cells FIRST then the other 228 in
  `active_cells` order, so the 08:12 hard stop reaches the 1d files the 08:20
  quarantine and 08:45 signal service read before any intraday file; then every other cell a registered crypto
  strategy runs on; section "Switch 2"), incrementally, through `crypto_data.fetch_symbol(..., end_ms=)`;
  it only APPENDS, never repairs or trims rows. **End cap:** every fetch is
  capped at 00:00 UTC of today's UTC date (`crypto_data.
  last_complete_utc_day_end_ms`), so no bar dated "today UTC" is ever written,
  however late or hand-run. Why: the loop preflight's same-day rule needs all
  of crypto's cells (these plus the legacy BTCUSD/ETHUSD that `data_fetch`
  refreshes at 08:20) to end on one day, and the signal service serves every
  asset on a single `bar_end`.
- **Timing:** Binance's 1d bar closes 00:00 UTC = 08:00 Perth. Pass 1 is not
  deadline-gated (only the hard stop below gates it); retry passes
  (max 2) never START at or after `--deadline-local` (default 08:15 Perth,
  UTC+8), so the run is over before the **08:20** quarantine daily (which reads
  the same 1d files) and the 08:45 signal service. The wrapper's task guard is
  `--wall-s 780 --grace-s 30` inside the PT15M limit: change them together.
- **Hard stop (final review I1):** the guard holds no lock, so no grace
  applies: it KILLS the run at 08:15:00, and a killed run writes neither the
  ledger nor the manifest (yesterday's ledger, still under the Sentinel's
  30 h, would read all clear on exactly the morning every cell timed out:
  ~96 s per cell, 3 tries x 30 s + backoff). So the wrapper passes
  `--deadline-local 08:11 --stop-local 08:12`: no retry starts at or after
  08:11, and NO attempt, pass 1 included, at or after 08:12. Every cell not
  yet fetched (and not already failed) gets a `crypto_refresh_failed` item
  `skipped: past the stop time 08:12; not fetched this run`, the ledger and
  the manifest (completed cells) are still written, exit 0; the worst single
  attempt plus the ledger write lands well before the 08:15 wall.
  `--stop-local` defaults to none, so `python -m pipeline.crypto_refresh` by
  hand stays ungated; a hand run of the WRAPPER after 08:12 fetches nothing
  and marks every cell skipped.
- **Ban / rate limit:** `BinanceBanned` (418) or ANY `RateLimited` that reaches
  the module (fetch_symbol has already retried a 429) stops the run AT ONCE with
  no sleep: that cell gets a `crypto_refresh_failed` item and every cell not yet
  completed gets one with reason `skipped: ...`.
- **Ledger `logs/degraded_crypto_refresh.json`, writer `crypto_refresh`:** items
  are `crypto_refresh_failed` (key = the cell), `crypto_admission` (first
  bar after 2022-12-31, or CSV missing/empty), and `feed_hole` / `feed_stale`
  (2b-3 C1, C2: a UTC day with no bar inside a file; a cell fetched OK whose
  last bar is behind the newest closed bar; see "Feed health (2b-3 C1/C2)").
  Written with `remove_unseen=True`
  (a recovered item leaves; an empty run still refreshes `ts_utc` so the
  Sentinel sees a live writer). The items ARE the alarm.
- **Exit codes:** 0 = the run completed, stopped on a ban or stopped at the
  hard stop (failed/skipped cells are ledger items, not a non-zero exit: a
  retry would hit Binance again); 2 = `--data-dir` missing, nothing written;
  1 = crash; 4 = the wrapper could not open its run log
  (`logs\crypto-refresh-run.log`); 124/125 = the task guard's wall / the outer
  cmd died.
- **Touches:** the cell CSVs (gitignored `data/*.csv`), the ledger, and
  `data/crypto_snapshot_manifest.json` (key-wise merge, COMPLETED cells only).
  Never the chain, git or `chain.lock`. `crypto_snapshot_manifest.json` is a
  TRACKED file the task rewrites and never commits, so it shows as modified on
  the shared working tree exactly like `tradfi_snapshot_manifest.json`: that is
  expected; never `git checkout --` it from another session.

