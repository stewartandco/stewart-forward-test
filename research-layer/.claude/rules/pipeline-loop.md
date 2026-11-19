---
paths:
  - "pipeline/loop.py"
  - "pipeline/loop_state.py"
  - "pipeline/pipeline_status.py"
  - "pipeline/test_loop*.py"
  - "pipeline/test_pipeline_status.py"
  - "pipeline/tradfi_data.py"
  - "pipeline/screen.py"
  - "tasks/run_pipeline_loop.bat"
  - "logs/loop_state.json"
  - "logs/screen_commit_paths.txt"
---
## Pipeline loop (25_PipelineLoop)
- **Screen is a plain stage, not a locked one (2026-10-08).** The loop runs
  `pipeline.screen` with `--logs-dir <logs>` and WITHOUT `_lock_and_run`:
  screen takes chain.lock itself, per batch (Chain lock section). A one-time
  `ChainLock.info()` probe before it logs a holder and never defers the
  cycle (a probe, not an acquire). Triage and the composer's real run keep
  `_lock_and_run`, so a held lock there still defers the cycle.
- **Screen bundles reach git through a commit list (2026-10-09).** After
  each batch write, screen appends that batch's chained bundles
  (`research-layer/artifacts/<sid>/{config.json,equity.csv,trades.csv}`) to
  `logs/screen_commit_paths.txt`, only when `--artifacts-dir` and
  `--logs-dir` are the registry's own (a scratch run is never listed). The
  loop's `commit_cycle` takes the list (rename to `.taking`; leftover
  `.taking` / `.merging` segments are folded back in first), commits it with
  the chain delta by `--pathspec-from-file`, and deletes it ONLY after the
  commit succeeds; on any failure it is folded back onto the list for the
  next commit. Listed files missing on disk are skipped with a WARNING. Take
  and settle never raise. Why: commit_cycle used to commit only bundles of
  strategies registered in the same cycle, and 14 cycles (08-31..09-28) died
  between screen and commit, so 8,627 screen bundles (plus 2,195 v6 gauntlet
  bundles judged cross-cycle) were never committed; all hash-matched their
  chained `artifacts_hash` and were committed by hand in per-day scoped
  commits on 2026-10-09. Never hand-write the list from PowerShell 5.1
  (UTF-16): it is read as UTF-8, so garbled lines are skipped with a WARNING.
- **The loop STOPS AT SCREEN (Build 2a, 2026-09-30).** A cycle runs triage,
  composer and screen; screen moves passers into state `gauntlet` and the
  standalone gauntlet worker (section below) takes them from there. The
  loop's own gauntlet stage and its `GAUNTLET_IN_LOOP` rollback flag were
  REMOVED at cutover step 8 (2026-10-07), after a clean week of the worker:
  there is no flag any more, and the only way back to in-loop judging is a
  code revert. The loop never reads or writes `gauntlet_result.json`, so
  `deferred_gauntlet` is absent from the status (absence is not a claim); it
  still runs the 4f post-screen chain verify. The old `pipeline.gauntlet`
  module stays (the stats job imports from it, and a hand run is still
  possible): it labels its verdicts v6 and reruns the registry-wide
  clustering. Since Ruling 23
  (2026-10-01) it applies NO family kill once a `gauntlet-protocol-v6.1:` note
  is on the chain (`pbo_family_kill` false on every verdict, never a reason),
  because `verify_registry.py` invariant 12 rejects one after the note: the
  loop's verify would return `chain_invalid` and, the chain being append-only,
  every later fire would fail too. Before the note it still applies the kill,
  which is why the cutover merges the code BEFORE chaining the note. Do not
  hand-run `python -m pipeline.gauntlet` without Coen's say-so.
- python -m pipeline.loop --once from the layer root; --dry-run reports the
  trigger decision and runs no METERED stage; stage 0 (tradfi snapshot into
  data/) still runs, so a dry run in the live tree refreshes the cells;
  --seed-watermarks initialises
  every class watermark to the current corpus (ACTIVATION step -- prevents a
  whole-corpus generation on first fire).
- **DO NOT run --seed-watermarks after the 2026-08-29 trigger fix.** It seeds
  every watermark to the CURRENT triggerable count, which today includes the
  539-card pending backlog. Seeding now banks that backlog as "already seen",
  so no class fires until 25 genuinely NEW cards arrive -- the loop would look
  exactly like the deadlock it just escaped (no_trigger, exit 0, healthy) while
  the whole backlog sits stranded behind the watermark. --seed-watermarks is
  an ACTIVATION-only step, for a fresh loop_state.json.
- **D38's activation checklist step 1 (run --seed-watermarks) is now
  CONDITIONAL, not unconditional:** it applies only to a loop_state.json that
  has never been seeded. The live file was seeded 2026-08-28 and must be left
  as it is.
- **Trigger basis (amended Coen 2026-08-29 -- do not revert):** a class fires
  on its TRIGGERABLE count minus its watermark, where triggerable = routable
  cards that are accepted OR pending, never rejected. NOT accepted-only:
  cards are only accepted by the triage panel the loop runs INSIDE a cycle,
  after the trigger decision, and nothing else triages -- so an accepted-only
  trigger can never move between fires. That was a live deadlock (every fire
  no_trigger, 539 pending cards stranded). The watermark is banked on that
  SAME basis; changing one side without the other re-breaks the loop in one
  direction or the other. loop.py `_triggerable_counts` decides;
  `_routable_counts` (accepted-only) is reported, never compared.
- **Stage 0 snapshot + freshness preflight (2026-09-12, `docs/2026-09-12-loop-snapshot-stage-design.md`).**
  Every fire that gets past the lock probes first runs
  `python -m pipeline.tradfi_data snapshot --classes fx,equity_etf,bond_etf,metal_etf --out <layer> --ts-root <producer>`
  (loop.SNAPSHOT_CLASSES) BEFORE the first chain read -- on `no_trigger` and budget parks too,
  so the 08:20 quarantine daily always finds fresh cells. Skipped (printed + `snapshot_skipped=1`)
  only when the trading-systems producer root does not exist (tests, a fresh clone). A non-zero
  exit is `snapshot_failed` (FAIL, exit 1, escalation `run_aborted`, task retry, zero spend). Then,
  after the orphan check and before triage, `_freshness_preflight` reads each registered cell's
  LAST bar (and first row) and runs the gauntlet's own `assert_cells_comparable`. **Rule:** it BLOCKS
  (`stale_data`: FAIL, exit 1, `run_aborted`, zero spend; `stale_detail` names the cells,
  `stale_fired_cells` the fired class's lagging cells, `data_end_by_class` the classes) when (1) the
  FIRED class's cells are not mutually comparable, or (2) the fired class is stale AGAINST the rest
  (the lagging side of a cross-class breach) -- both Coen, 2026-10-08 -- or (3) a price file is
  MISSING, EMPTY (header only) or ENDS before screen's train cutoff (`screen.DEFAULT_CUTOFF`) for a
  fired-class cell or any cell a `proposed` spec of any class names (cell-exact, the set screen
  loads; controller Rulings 38-41), or STARTS after the cutoff for a cell a `proposed` spec names
  (screen keeps a bar iff its date[:10] <= the cutoff, so a first bar ON the cutoff day passes; a
  fired-class cell with a late start and no proposed spec does NOT block, because a re-fetch can
  never fix it and the park would never end -- Ruling 41; it is silent, not a WARN): screen would
  fail after spend on a missing file; an empty or late-starting file loads no bars, `run_spec`
  returns 0 trades and screen buries the spec with an irreversible `trade_count` fail; a file ending
  before the cutoff shortens the train window. The late-start message says a re-fetch cannot fix it:
  exclude the cell upstream or deal with the spec by hand.
  ALL OTHER staleness is a WARN, never a block: a stale cell past the cutoff in another class, or a
  missing/empty/short file no proposed spec names. The cycle runs, the
  status carries `stale_other_cells` / `stale_other_classes` / `stale_other_missing`, the run log
  prints one `WARN stale_data_report` line, and an otherwise-OK cycle's overall is WARN (exit stays
  0, so the Sentinel, which reads only the task's exit code, stays green). A fired class with no
  registered spec has no cells here and cannot block. The preflight derives its cells from `screen.comparable_cells`, the same implementation
  the gauntlet worker uses (stage 0's classes are `SNAPSHOT_CLASSES`). Successful fires carry `snapshot_utc` in the status. `--dry-run` runs stage 0
  but returns at the trigger report, before the chain verify, the orphan check and this preflight.
  **A hand `tradfi_data snapshot` is now a REPAIR, never a routine** -- the 2026-09-11 22:30 cycle
  failed in the gauntlet after USD 1.90 because the last hand snapshot was 11 days old and fx
  (FRED, ~1 week lag) sat 20 days behind crypto against a 13-day allowance. Staleness is now
  SYMMETRIC: crypto's legacy BTCUSD/ETHUSD come from the 08:20 QuarantineDaily and its 76 USDT cells from
  the 08:02 33_CryptoGridRefresh (Switch 1; both end on the last complete UTC day), so if EITHER stalls,
  crypto goes stale against fresh tradfi. Since 2026-10-08 that no longer parks every fire: a fire of a tradfi
  class runs with `stale_other_cells` / WARN, while a fire of the stale class itself (crypto, with
  BTCUSD and ETHUSD stopped together, is stale against the rest) parks at `stale_data` (zero spend,
  Sentinel FAIL) -- read `data_end_by_class` and the `stale_*` items in the status to see which class. The preflight covers only cells that already carry a registered spec: a class with no
  registrations yet (metal_etf today) gets its FIRST generation checked by the gauntlet worker, not the preflight.
  **Since step 8 (2026-10-07) the preflight's original purpose is gone.** It was written to stop the in-loop
  gauntlet stage refusing after every metered stage had run; that stage no longer exists. Screen never
  calls `assert_cells_comparable` and fences bars at its train cutoff, so a stale feed is invisible
  to it (only a missing, empty or late-starting file trips it; a pre-cutoff-ending file just trains on a shorter window); the gauntlet worker, which loads full
  history, defers non-comparable candidates (`deferred_not_comparable`) rather than refusing. It is
  now a data-health alarm. Coen decided 2026-10-08: narrowed to the fired class, which also blocks
  if it is stale against the rest; all other staleness is a WARN (status items, one WARN log line,
  overall WARN). The cell-exact block on proposed specs' missing/empty/late-starting/short files is
  the controller's Rulings 38-41, not Coen's.
  **Since 2026-10-08 (per-asset isolation, `docs/2026-10-08-per-asset-isolation-design.md`):**
  stage 0 SKIPS a bad series (verdict fail, sha mismatch, shrunk history, not
  pinned, no parquet) and writes the rest; the manifest's `skipped` map names
  them, the status carries `snapshot_skipped_series`, and a skipped series'
  CSV and record are left exactly as they were. Zero series written is still
  `snapshot_failed`. Every non-dry fire then writes `logs/degraded_loop.json`
  (stage 0 skips + a report-only freshness pass over every registered cell,
  run BEFORE the trigger decision so no_trigger nights count). The Ops
  Sentinel's `research_degraded` reads it: WARN, FAIL after 3 days. The
  preflight's blocking rules above are unchanged.
- State: logs/loop_state.json (per-class watermarks + thresholds, Coen-editable).
- **Watermark re-bank (Coen, 2026-09-04): a TARGETED hand edit, never --seed-watermarks.** After the 09-02 and 09-04 rejections every class's triggerable count sat BELOW its watermark (a deficit the loop had to repay with genuinely new cards before firing: bond 41 / crypto 26 / equity 77 / fx 43 / metal 45 needed). Coen ruled the rejection drift undone: each class whose delta was NEGATIVE had its watermark set to its live triggerable count (crypto 1197->1196, fx 462->444, equity_etf 952->900, bond_etf 581->565, metal_etf 488->468; deltas now 0, 25 new cards fire a class). Rule: NEVER lower a class's headroom -- a class at or above its watermark is left alone. Script pattern: read _triggerable_counts live, edit only between fires (no loop.lock, no chain.lock), back the file up, preserve its CRLF/indent, re-read after every chain write. Moves GATE 1 only; gate 2 (no_new_accepted_cards) still needs acceptances since the last swept generation.
- Status: logs/pipeline_status.json (NOT status.json -- that file belongs to the
  reader agent); run log logs/pipeline-loop-run.log; instance guard logs/loop.lock.
  Items carry BOTH routable_<cls> (accepted-only) and triggerable_<cls>
  (accepted+pending) per class -- a large gap between them is an undrained
  pending backlog. Present on every path that has read the chain (no_trigger,
  dry_run, cycle_complete, stage_failed, the budget/lock defers); the paths
  that run BEFORE the chain read -- the two startup lock probes,
  snapshot_failed, and loop_crashed -- legitimately omit them.
  `data/tradfi_snapshot_manifest.json` is a TRACKED file that stage 0
  rewrites on every fire, and the loop's own scoped commit never includes it
  -- so it shows as modified on a shared working tree. That is expected;
  never `git checkout --` it from another session. (The tradfi CSVs under data/ are gitignored; BTCUSD/ETHUSD are tracked and committed
  by the quarantine daily.)
- `budget_state` item: ok | hard_cap (D41 removed the 80% batch_stop value). Written on
  EVERY status path, not just the budget-blocked ones, so "ok" is a value
  that actually appears. A budget park also stamps `last_park_ts_utc` in
  loop_state.json, which rotates that class to the back of pick_class's
  queue. Parks bank NO watermark (no work was done); the stamp exists only so
  one parked class cannot monopolise every fire and starve the others.
- `routable_at_last_generation` (per class, loop_state.json): the accepted-only
  routable count as of the last cycle whose composer ACTUALLY SWEPT. Written
  only on cycle_complete, and it is the baseline the no_new_accepted_cards
  guard compares against -- scoping that guard to a single cycle stranded
  cards that a failed composer had never consumed. Absent key -> falls back to
  the pre-triage count.

