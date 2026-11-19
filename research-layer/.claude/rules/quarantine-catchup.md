---
paths:
  - "pipeline/quarantine.py"
  - "pipeline/quarantine_view.py"
  - "pipeline/test_quarantine_*.py"
  - "tasks/run_quarantine.bat"
  - "tools/quarantine_replay.py"
  - "tools/test_quarantine_replay.py"
---
## Quarantine daily catch-up (23_QuarantineDaily, 2026-09-28)
- **Why:** the daily records ONE date and DEFERS a class whose bar is not
  published yet (equity ETFs are always a day behind at 08:20: their only
  refresh is loop stage 0 at 20:00, before the US close; FRED fx lags about a
  week). Deferred dates were meant to be backfilled by hand `--date` runs and
  never were: by 2026-09-28 all 253 equity_etf + 19 fx strategies had ZERO
  forward days (4,764 strategy-days owed). A prose-only rule, now a stage.
- `run_quarantine.bat` ends with `python -m pipeline.quarantine --catch-up`:
  every OWED date (a deferred class, a missed day, a `deferred_lock` skip) is
  recorded through the unchanged `--date` path, oldest first, at most
  `MAX_CATCHUP_DATES` (10) per run; a deferred-only date (every owing strategy
  deferred for a restated asset or a failed simulation, 2026-10-17) is not
  counted against it. A date counts as owed only when EVERY asset
  of the strategy has a bar on it -- the `--date` path's own readiness rule --
  so a calendar-gap date is never retried forever.
- A failed date never strands later ones (exceptions included) but makes the
  run exit 1; the wrapper STILL commits what was recorded, then exits 1.
- Cost: measured in the 2026-10-16 replay on a copy of the live chain (cut at
  129,001 entries, just before that day's 08:20 daily): a `--date` pass of
  196 rows now takes about 2.5 min (148 s, chain.lock held 0.08 s; was 1,244 s
  with the lock held 1,230 s). A `--catch-up` date can still take tens of
  minutes: 490 rows took 2,308 s with the lock held 0.33 s (was 3,954 s / 3,818
  s), because the remaining ~4.7 s per row is strategy simulation and
  price-file reads, not chain I/O. Whole replay 86.6 min old, 40.9 min new;
  old and new appended identical entries (688: 686 rows, 1 base snapshot, 1
  supplement) and both chains are VALID. Only strategies that still owe a row
  are simulated (rule 1). Steady state is one or two dates a day. Since the
  2026-10-16 fast path (`docs/2026-10-16-quarantine-fastpath-design.md`) a run
  reads the chain ONCE (`pipeline/quarantine_view.py`, `QuarantineView`) and
  catch-up shares that read across its dates; each date simulates BEFORE
  taking chain.lock and holds it for two tail reads (the re-plan before the
  append, the read over its own entries after it) plus one append (measured
  max hold 0.33 s on the 2026-10-16 replay, against 4 h 27 min for the
  2026-10-15 daily, 382 rows). `Registry.advance(snap, on_entry=None)` gained
  the callback that keeps the view current. A chain.lock already held by a
  live holder is probed BEFORE simulation (`ChainLock.info()` plus
  `holder_alive()`): the date defers with exit 0 and the unchanged
  `deferred_lock` line, without simulating; the acquire-time check stays as
  the backstop.
- **A date is all or nothing for the strategies that record (2026-10-16;
  per-strategy follow-up approved by Coen the same day; narrowed 2026-10-17).**
  A `ChainMoved` (an unlocked writer, or a rewritten log: `CHAIN MOVED:` on
  stderr) or any exception other than `SimulationDataError` in simulation or
  row validation (a code bug, a plain `ValueError` included: a malformed row,
  an unpacking mismatch, `max([])`) writes NOTHING for the date; it stays owed
  and the run exits 1. Only `quarantine.SimulationDataError` (a `ValueError`
  subclass, raised at the data-shaped sites: `observe_day`'s and
  `_observe_intraday`'s missing bar, `_rebase_index` / `_rebase_index_day`'s no
  zero point, and the two non-positive equity baselines) DEFERS ONE strategy:
  it gets no rows and stays owed (catch-up retries it every run),
  stdout says `<sid>  deferred: simulation failed: <error>`, and a
  `simulation_failed` item keyed by the strategy reaches
  `logs/degraded_quarantine.json` (the Sentinel's `research_degraded`: WARN,
  then FAIL after 3 days); the others record and the run exits 0. If NOTHING
  records, nothing is already chained for the date and every ready strategy's
  simulation failed, it is a total stall: `REFUSED: nothing recorded ...` on
  stderr, exit 1, chain.lock never taken (that stalled date is still
  `deferred_only`, below). A strategy that left quarantine
  while the run simulated (a live-gate move) is SKIPPED, not a failure:
  `<sid>  skipped: left quarantine (<state>) while this run simulated`, no
  degraded item, the others record, exit 0. The old `PARTIAL WRITE` path is
  gone. The date's provenance plan under the lock covers only the strategies
  that will record (ready minus the simulation failures and the strategies
  that left quarantine, digests filtered to their cells): such a strategy's
  assets are NOT in the date's snapshot, so its retry records with a
  `quarantine_data_snapshot_supplement` instead of being pinned `restated` by
  a later bar correction. A date where no decision row is queued (every owing
  strategy left quarantine or failed simulation) appends NOTHING, not even provenance: provenance vouches for
  rows. In catch-up a `ChainMoved` date
  rebuilds the view with a fresh read and the later dates still run; that
  includes a ChainMoved from the advance at the START of a catch-up date
  (outside the lock), which fails that date with a traceback and the date is
  not attempted. A supplement that lands while the run simulates is ordinary
  coverage (rule 2 if it disagrees), not a refusal. As built: a `ChainMoved`
  BEFORE the date's single append prints `CHAIN MOVED: ...; nothing written
  for <date>` and exits 1. A `ChainMoved` from the tail read right AFTER a
  successful append prints the row lines and summary, then `CHAIN MOVED after
  the write for <date>: the N decision row(s) above [plus the date's
  provenance snapshot/supplement] WERE chained ...`, and exits 1 (the date IS
  recorded). A row already on the chain at the date's pre-lock read (in
  catch-up, the shared view after its advance at the start of that date) is
  skipped without comparison, as before; a row that lands while the run
  simulates is compared (identical: skipped and counted; different:
  `DuplicateQuarantineDecision`, nothing written). A supplement that LANDS
  WHILE THE RUN SIMULATES and disagrees with this run's bars defers the
  owing strategies, exit 0 (only that mid-run race was refused, exit 1,
  before; one already on the chain was a rule-2 deferral then too; behaviour
  change accepted by Coen 2026-10-16).
- Tools and tests: `tools/quarantine_replay.py` (subcommands `prefix`, `run`,
  `compare`) replays the daily and catch-up on a COPY of the chain cut at a
  prefix; read-only against the live chain, never scheduled (`run` refuses,
  exit 2, an argv with no `--registry`, one naming the code root's or this
  layer's `registry_log.jsonl`, and `--fetch-owed` without a scratch
  `--data-dir`). Tests:
  `test_quarantine_fastpath.py`, `tools/test_quarantine_replay.py`.
- Backfilled rows stay visible as backfills in `--review` (write time vs bar
  date); catch-up records every owed strategy alike, so it is a schedule,
  never a selection.
- The Ops Sentinel health-asserts `23_QuarantineDaily` (`daily`) since
  2026-09-28 (Coen), so an exit 1 reaches the 09:15 digest; 267009 ("still
  running" -- a long catch-up) is tolerated for it alone.
- **Per-strategy isolation (2026-10-08 addendum, `docs/2026-10-08-quarantine-isolation-addendum.md`).**
  Only strategies that still OWE a row are simulated and guarded (rule 1), so a
  restated asset whose strategies are fully recorded blocks nobody. A restated
  asset or a missing price file defers only the owing strategies that trade it
  (rules 2-3); every owing strategy missing a bar or a file, with no row
  already chained for the date, is still a total stall (exit 1). A date where
  every ready strategy was deferred (restated asset OR failed simulation, any
  mix) uses no catch-up slot (Coen 2026-10-17): `DateReport.deferred_only` is
  true when nothing was appended, at least one ready strategy was deferred and
  every ready strategy still owing the date was; a strategy that left
  quarantine mid-run no longer owes it and is not counted. It holds for the
  all-simulation-failed total stall too (rc stays 1, REFUSED, but no slot), so
  a strategy whose simulation ALWAYS fails (a non-positive baseline at its
  entry rebase is date-independent) can no longer starve every other
  strategy's lagged backfill. **Accepted cost (Coen):** such a strategy's dates
  are re-simulated on every run, like restated dates; the `simulation_failed`
  item (WARN, FAIL after 3 days) forces a human fix.
- **Degraded ledger `logs/degraded_quarantine.json`** (read by the Ops
  Sentinel's `research_degraded`: WARN, FAIL after 3 days). `--date` only adds
  or refreshes items; `--catch-up` also removes recovered ones, and only when
  it reached every owed date, no attempted date failed, and none was skipped
  on chain.lock (a date that returned before its provenance check reports
  nothing, which is not recovery). Deferrals exit 0.

