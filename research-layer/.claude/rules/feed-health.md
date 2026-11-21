---
paths:
  - "pipeline/feed_health.py"
  - "pipeline/test_feed_health.py"
  - "pipeline/test_retired_cells.py"
  - "pipeline/quarantine.py"
  - "pipeline/crypto_refresh.py"
  - "data/intraday_holes_ack.json"
---
## Feed health (2b-3 C1/C2, plan 3, 2026-10-21)
Two feed failures used to pass silently: a whole UTC day missing INSIDE a
price file (the fetcher never backfills, quarantine never owes it), and a fetch
that succeeds but brings nothing new. `pipeline/feed_health.py` finds both and
returns plain ledger items; it writes nothing and `scan` never raises (an
unusable file is one `feed_hole` "cannot scan" item).
- **Sources:** `feed_hole` (key = the cell; the reason lists up to the first 10
  days, "N day(s) with no bars") and `feed_stale` (key = the cell). A hole is a
  UTC day strictly between the file's first and last bar's days with no bar at
  all (a missing hour inside a day that has other bars is NOT a hole; the first
  and last day are never holes). Works for every cell shape: the USDT files'
  `"YYYY-MM-DD HH:MM:SS"` open times and the legacy BTCUSD/ETHUSD bare dates.
- **Stale rule:** the fetcher's own closed-bar rule plus a grace
  (`STALE_GRACE_S = 60`): `cutoff = now - crypto_data.SKEW_MARGIN_MS - grace`,
  capped by `end_ms` (the refresh passes 00:00 UTC of today) and floored to the
  timeframe; the cell is stale iff its last bar's CLOSE is before that cutoff.
  Staleness is only checked for a cell FETCHED OK this run (a failed or
  skipped cell already has its own failure item), so a stale item always
  means "the fetch ran and brought nothing newer".
- **Ack file `data/intraday_holes_ack.json`** (tracked; committed as `{}`):
  `{"<cell key>": {"dates": ["YYYY-MM-DD", ...], "reason": "..."}}`. Coen
  decides which holes are real exchange gaps and edits it; nothing in code
  writes it. An acknowledged hole is PRINTED (`<cell>  acknowledged hole(s):
  ...`), not itemised. A malformed file (not an object, a non-list `dates`, a
  bad date, an empty `reason`) honours NOTHING (a half-trusted ack would hide
  a hole) and is itself ONE `feed_hole` item keyed by the file name.
- **Owners (who raises and who clears an item):**
  1. the `crypto_refresh` ledger (`logs/degraded_crypto_refresh.json`): holes
     for every refreshed cell (the active cells plus registered extras), stale
     only for cells fetched OK;
  2. the quarantine fetch-owed step (`logs/degraded_quarantine.json`): the
     owed intraday cells, items owned via `quarantine.FETCH_STEP_SOURCES`
     (`intraday_fetch_failed`, `feed_hole`, `feed_stale`); `--date` and
     catch-up keep them. A detector crash becomes one `feed_hole` item keyed
     `feed_health`, never a crashed step (a crash would turn the 08:20 task
     red).
  A RETIRED cell (`cells.RETIRED_CELLS`) is not scanned in either place.
- **Alarm path:** the Ops Sentinel's `research_degraded` check turns a ledger
  item WARN, then FAIL after 3 days. **The digest is the alert** (Coen
  10-17): there is no new task, mail or pager; an unacknowledged hole shows up
  in the morning digest. A repair is a deliberate, Coen-gated act (see the
  `restated` hazard in the 2b-2 section: a refetch changes `bars_sha256` for
  every chained date at or after the hole).
- **Fetch-owed retries (Coen 10-17):** `quarantine.fetch_owed` retries the
  cells that failed with a GENERIC error in the same run: 2 extra passes
  (`FETCH_RETRY_PASSES`), 120 s apart (`FETCH_RETRY_WAIT_S`), over just the
  failed cells, so at most about 4 minutes are added (and only on failure). A
  `BinanceBanned` or any `RateLimited` stops the run at once with no retry
  pass and no sleep (more calls could extend an IP-wide ban). A cell that still
  fails after the last pass is ONE `intraday_fetch_failed` item. A retired
  intraday cell is skipped before any fetch.
- **C3 and C5 (same plan):** C3 = `register_strategy` refuses a non-24x7
  intraday spec (see "24x7 only" above). C5 = the live gate's train cutoff
  compares UTC DATES (`entry_date[:10] <= cutoff[:10]`), so an intraday trade
  entered on the cutoff day counts as train, as a daily one always did.
  `examples/registry_log.example.jsonl` still chains the original 15m `"RTH"`
  example spec, which registration now refuses; the committed example stays
  VALID (the verifier never calls `register_strategy`), and
  `examples/regenerate_example.py` now writes a 24x7 spec.
- **Retired cells, multi-cell caveat:** a MULTI-cell strategy with one retired
  cell stops being recordable after that cell's last bar (quarantine records a
  strategy only when ALL its cells are recordable). Only the legacy pooled
  BTCUSD+ETHUSD specs are multi-cell, and those tickers are not retirable
  (`RETIRED_CELLS` validation needs a declared cell of a declared class), so
  this cannot arise today.
- Tests: `test_feed_health.py`, `test_crypto_refresh.py`,
  `test_quarantine_fetch_owed.py`, `test_livegate_intraday.py`,
  `test_retired_cells.py`, `test_registry_session_refusal.py`. Plan:
  `docs/plans/2026-10-19-safety-rails.md`.

