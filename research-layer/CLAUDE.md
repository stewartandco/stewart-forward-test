# research-layer -- pipeline notes

## Chain lock (logs/chain.lock) - manual sessions MUST honour it
Before any batch of chain writes (generation, backfill, hand-run gauntlet):
check logs/chain.lock. If present, another writer (scanner card batch,
25_PipelineLoop stage, quarantine daily, or another session) is mid-window --
wait or defer. To hold it for a manual working session:
python -c "from pipeline.chainlock import ChainLock; import time; l=ChainLock('logs','session','manual work'); l.acquire(); input('release?'); l.release()"
Never delete a fresh chain.lock; a stale one (>3h) with a LIVE (or
unreadable) holder pid is broken by the loop's two-strike rule, but a stale
lock whose holder pid is provably DEAD (a hard-killed loop, crash) is broken
on the first sighting -- same dead-pid fast path loop.lock uses.
- **Locks name their holder by (pid, start time) (2026-09-30).** Every lock file
  (chain.lock, loop.lock, gauntlet_worker.lock) records `pid_start_utc` beside
  the pid, and `holder_alive()` treats a LIVE pid whose start time differs as
  DEAD: Windows reuses pids, and on 2026-09-28 a reused pid made a dead
  holder look alive and wedged loop.lock and chain.lock. A lock written before
  this change has no start time and keeps the plain pid rule. A holder whose
  start time cannot be read counts as alive (conservative).
- **The gauntlet worker holds chain.lock only for a tail read plus ONE append
  (~16 ms), once per candidate, and never waits for it.** If another writer
  holds it the worker KEEPS that candidate's evaluated result in memory (no
  bundle written yet) and retries the write later in the SAME run: once after
  each chunk, before the next is dispatched, then in a final drain pass of
  non-blocking attempts every `DRAIN_INTERVAL_S` (5 s) inside a reserved
  `DRAIN_RESERVE_S` (75 s) that the chunk loop holds back, never past the
  deadline (2026-10-02; before that every such result was discarded). The
  measurement that motivated the change: overnight runs on 2026-10-01/02
  wrote 0 verdicts each while evaluating 48-120. **The retries rescue only
  SHORT holds** (scanner/inbox card batches and similar). The loop's screen
  stage holds chain.lock for hours overnight, and a long quarantine catch-up
  holds it for minutes per date. Those holds still leave whole runs with
  `deferred_lock` equal to their evaluated count; the results are discarded
  at the end of the run and the next run re-evaluates those candidates. So
  the overnight zero-write runs are NOT fixed by this. (Amended 2026-10-08:
  screen no longer holds the lock for hours; see the next bullet.) Each retry
  re-checks on the advanced snapshot exactly what the first write does; a
  candidate another writer moved or judged meanwhile is dropped unwritten
  (`dropped_stale`). Results still unwritten at the end stay queued
  (`deferred_lock`, exit 0) and are NEVER persisted: the next run re-evaluates
  them. It never sleep-polls inside, waits on, or breaks a lock.
  HAZARD: a hand-run writer that appends to registry_log.jsonl WITHOUT taking
  chain.lock inside that ~16 ms window makes the worker see a chain that moved
  under it. It then writes nothing for that candidate, leaves it queued and
  counts it as an ERROR, so the run exits 1 and the Ops Sentinel FAILs the
  digest. That is an alarm on purpose (an unlocked writer is the defect), not a
  flake. Take chain.lock for every hand-run chain write, as above.
- **Screen holds chain.lock only for each BATCH write, not for the stage
  (2026-10-08, Coen's decision; design `docs/2026-10-08-screen-lock-release-design.md`).**
  `pipeline.screen` evaluates every chunk with NO lock, then flushes the
  verdicts under ONE non-blocking acquire (holder `screen`) per batch of at
  most `SCREEN_BATCH_MAX` (200) specs, well under a second per hold. Under
  the lock it advances the snapshot, re-checks each spec is still `proposed`
  (one that is not is dropped unwritten and counted `dropped_stale`), writes
  the bundles, and appends each spec's three entries (screened, verdict,
  gauntlet/graveyard) in ONE write, so a crash cannot leave a `screened`
  orphan. Before this the stage held the lock for its whole write phase
  (about 5.3 s per spec on the 100k-entry chain, hours overnight) and the
  worker wrote nothing meanwhile. A batch that meets a held lock is KEPT and
  retried at the next chunk, then by a final drain of non-blocking attempts
  every `DRAIN_INTERVAL_S` (5 s) inside `DRAIN_RESERVE_S` (75 s), never past
  the deadline; the reserve is subtracted from the deadline budget only
  while a batch is pending. Specs still unwritten stay `proposed` (the next
  run re-screens them), the exit is 0, and `screen_result.json` counts them
  in `deferred` and `deferred_lock` (a deadline-stopped flush reports
  `stopped_at_deadline` instead, and counts in `deferred` only). The
  loop no longer wraps screen in chain.lock, and a held lock at screen no
  longer defers the cycle. Same hazard as the worker: an unlocked hand-run
  writer that moves the chain inside a batch's hold makes that batch write
  nothing (`ChainMoved`/`UnstableEntry`), exit 1; batches already written
  stay. A crashing spec (`CellError`) keeps the earlier batches; it and
  every later spec stay `proposed`.

## Pipeline loop (25_PipelineLoop)
- **Screen is a plain stage, not a locked one (2026-10-08).** The loop runs
  `pipeline.screen` with `--logs-dir <logs>` and WITHOUT `_lock_and_run`:
  screen takes chain.lock itself, per batch (Chain lock section). A one-time
  `ChainLock.info()` probe before it logs a holder and never defers the
  cycle (a probe, not an acquire). Triage and the composer's real run keep
  `_lock_and_run`, so a held lock there still defers the cycle.
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
  SYMMETRIC: crypto's BTCUSD/ETHUSD come from the 08:20 QuarantineDaily, so if THAT stalls, crypto
  goes stale against fresh tradfi. Since 2026-10-08 that no longer parks every fire: a fire of a tradfi
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

## Gauntlet worker + recorded statistics (Build 2a, 2026-09-30)
Design: `docs/2026-09-30-gauntlet-at-scale-design.md`. Why: the gauntlet's first
phase re-simulated and clustered EVERY registered strategy before judging any
candidate, was not deadline-aware, and stopped completing as the registry grew.
Nothing the clustering produces is an input to any gate, so verdicts moved out
of it.
- **Protocol records.** `docs/notes/gauntlet-protocol-v6.1.md` (the addendum)
  and `docs/notes/correction-2026-09-30-pbo-family-kill.md` are chained
  VERBATIM, and only after Coen approves the exact text, BEFORE the first v6.1
  verdict. Once chained the chain is the record: never edit the files to
  "fix" a chained note. The addendum's first line starts
  `gauntlet-protocol-v6.1: `, and BOTH the worker's refusal check and
  verifier invariant 12 key on that prefix. With no such note the worker exits
  1 (`refused_no_protocol_note`) and writes nothing.
- **Worker: `python -m pipeline.gauntlet_worker --max-workers 6`**, wrapper
  `tasks/run_gauntlet_worker.bat`, task `\Morpheus\27_GauntletWorker` every 30
  minutes, 25-minute deadline per run. It judges queued candidates cheapest
  first with the six standalone gates in `pipeline/gauntlet_core.py`
  (sharpe_floor 0.4, oos_negative, edge_decay -25%, mc_p05 1.0, p_ruin 0.05,
  cost_stress; order and thresholds unchanged) and chains each verdict plus its
  state change as soon as it exists. Its import graph has no clustering or PBO
  module (a test pins it). Verdicts carry `metrics.protocol
  gauntlet-protocol-v6.1` and NO registry-wide statistics. The family kill is
  NOT applied. After each verdict write the worker appends the judged bundle's
  repo-relative path to `logs/gauntlet_worker_commit_paths.txt`; the wrapper
  commits `registry_log.jsonl` plus those bundles best-effort in ONE commit,
  scoped by `--pathspec-from-file` (never argv, never anything another session
  staged), and clears the list only after that commit succeeds. It skips the
  commit while chain.lock exists (the list is kept) and never pushes.
- **⚠ NEVER run `git merge`, `checkout`, `switch`, `reset` or `stash` in the
  LIVE tree while a worker run is in its git step (incident 2026-10-02 12:21).**
  `logs/gauntlet_worker.lock` is released when the python process exits, but
  `run_gauntlet_worker.bat` runs `git add` / `git commit` AFTER that. A merge
  started in that gap collided with the wrapper's commit ("unable to unlink old
  'research-layer/registry_log.jsonl': Invalid argument"), and the chain file
  was left at HEAD: the run's 48 uncommitted verdicts vanished from disk. The
  chain stayed VALID (a clean truncation) and the next run re-judged the same
  48 to identical verdicts on unchanged data, but on a day the data refreshed
  in between the re-judgement could differ. Before any such git command in the
  live tree, ALL of: the last line of `logs/gauntlet-worker-run.log` is the
  current run's `==== ... exit N ====` line; no `.git/index.lock`,
  `logs/gauntlet_worker.lock` or `logs/chain.lock`; `git diff --cached` is
  empty; and the next :00/:30 fire is more than a few minutes away.
- **Run order and failure rules.** (1) Orphan repair: a v6.1 verdict with no
  following state change gets the change it implies (fail -> graveyard with the
  gate as the reason, pass -> quarantine); a verdict is NEVER re-evaluated.
  (2) The queue. A candidate that raises gets NO verdict, stays queued, and the
  run exits 1; nothing is skipped or buried because of a crash. A v6.1 orphan
  is caught ONLY by the worker's own repair at the start of each run: the
  loop's `gauntlet_orphan` pre-spend check (`loop._gauntlet_orphans`)
  deliberately EXCLUDES v6.1 verdicts, so the 20:00 loop never sees one, and
  an orphan is judged, so it is not counted in the status `queued` or
  `oldest_queued_age_hours`. If the repair itself raises (a chain or disk
  failure, or a chain that moved under chain.lock) the run exits 1 with
  `exit_reason` `crashed`, and the Sentinel FAILs both the task result and the
  `gauntlet_queue` check. If chain.lock is held the repair is deferred to the
  next run with only a log line (`orphan repair deferred` in
  `logs/gauntlet-worker-run.log`, `repaired` 0); nothing alarms on a deferral
  alone. (A hand run of `pipeline.gauntlet` refuses on any orphan, v6.1
  included, and exits 1; the loop no longer runs it, so no loop path creates a
  v6 orphan, and the `gauntlet_orphan` pre-spend check stays only as the alarm
  for one that already exists or that a hand run leaves.)
- **Locks.** The worker's own instance lock is `logs/gauntlet_worker.lock`
  (stale after one hour, broken only when stale AND its holder is dead). A
  second instance defers (exit 0) and NEVER writes the status file, so a wedged
  holder's status goes stale and the Sentinel catches it. chain.lock rules are
  in the Chain lock section: held for a tail read plus one append, never
  waited for, never broken; a write that finds it held is kept and retried
  within the same run (between chunks, then a <= 75 s final drain inside the
  deadline), never carried to the next run; an unlocked hand-run writer in
  that window turns into an exit-1 alarm.
- **Statistics job: `python -m pipeline.gauntlet_stats`**, wrapper
  `tasks/run_gauntlet_stats.bat`, task `\Morpheus\28_GauntletStats` daily
  01:00. It computes effective trials, the deflated Sharpe, PBO (recorded only),
  plateau_ok and the haircut for every v6.1 verdict that has none, on a WEEKLY
  data vintage (bars truncated at the most recent Sunday, so the simulation
  cache keys hold all week and each night resumes where the last stopped), and
  with `--chain` appends ONE `gauntlet_stats` entry per verdict, linked by
  `verdict_entry_hash`. It records and never changes a strategy's state. It
  carries the multi-gigabyte work, which is why it runs at 01:00: the worker is
  memory-light and safe by day. Entries carry `data_vintage`, `data_digest` and
  `data_end_by_cell`; the vintage names a Sunday but does NOT pin the bars, so
  compare digests, never vintages. A clustering pass starts only when the
  time left covers its estimate (the last measured duration, `cluster_s` in
  its status file, else a 2 h prior), and each PBO null only when the time
  left covers the measured mean (`pbo_null_mean_s`, else 60 s); a pass that
  does not fit is a deadline stop. One instance at a time
  (`logs/gauntlet_stats.lock`): a second one, e.g. a hand `--chain` run beside
  the 01:00 task, defers with exit 0 and never touches the status file.
  The status reports `trials_n_raw` (this run's argmax) and
  `trials_n_effective` (the floored N the entries use).
- **Step 7 (2026-10-03): the clustering takes ~2 min, not 4 h.** 95% of the
  old 4.1 h was a Python tie loop in `cluster._agglomerate_np`, driven by the
  all-zero return rows (79 at 13,730; distance exactly sqrt(0.5) to
  everything). The bookkeeping is now array-based and the distance
  arithmetic in place (one n x n matrix at a time; +2.4 GB commit at
  13,730), with the METHOD UNCHANGED: `tools/verify_cluster_identity.py`
  (read-only, never scheduled; run it on a registry COPY) proved merge
  history, k, labels, `trials_sr_var` and SR* bit-identical to the old code
  at n = 1k/2k/4k/8k on real data. Measured on a copy: 117.7 s, k = 560 at
  13,730. Any future change to `cluster.py` re-runs that proof first.
  `test_cluster_np.py` asserts the per-row cache against a fresh re-scan
  after EVERY merge (the O(1) tie-only update is invisible to history
  comparisons).
- **SR* is floored (step 7, Coen option a, Ruling 35).** `expected_max_sharpe`
  on an entry = max(`expected_max_sharpe_raw`, `expected_max_sharpe_floor`);
  raw is v6.1's own `expected_max_sharpe(trials_n, trials_sr_var)`, the floor
  the highest SR* on the chain (v3-v6 verdicts' `metrics.expected_max_sharpe`
  via the SAME `FLOOR_PROTOCOLS`, plus `expected_max_sharpe_raw` on earlier
  stats entries), named by `expected_max_sharpe_floor_entry_hash`. The DSR
  uses the floored SR*; the haircut reads `trials_n` only. Why: SR* scales
  with sqrt(`trials_sr_var`), which moves ~400x between the silhouette
  curve's two peaks. On a copy (10-03) the chain floor was 0.06883 (entry
  9829) and the night's raw SR* 0.0665, so the floor binds.
- **`--chain` REFUSES until `docs/notes/gauntlet-protocol-v6.1-amendment-1.md`
  is chained** (exit 1, `exit_reason` `refused_no_amendment_note`; first-line
  prefix `gauntlet-protocol-v6.1-amendment-1:`, first occurrence). The note
  is a DRAFT until Coen approves its exact text; chain it verbatim (the git
  blob is LF: check the bytes are LF before chaining, `core.autocrlf` is on).
  Report-only runs are unaffected. The Sentinel does not read the stats
  `exit_reason`; an exit 1 on task 28 would reach the digest as a task result.
- **PBO scope (step 7, amendment-1).** The null is built for EVERY family with
  a v6.1 verdict needing stats whenever it is measurable (observed PBO, >= 4
  distinct configs), passing verdict or not; before, only for a family with a
  pass in the batch, so siblings statted on different nights could differ.
  Each result is cached in `logs/gauntlet_stats_pbo_cache.json` per (family,
  vintage, data_digest, inputs sha, `pbo_code_sha()` = PBO code + constants
  + `PBO_CACHE_REV`); only the current vintage is kept, a failed write is
  logged and the run continues uncached, and an
  unreadable file is an empty cache. Measured on a copy: the 10-03 backlog
  (2,193 verdicts, 286 families) needs ~32,300 s of nulls, about two nights.
- **chain.lock held at a flush (step 7, T7):** the computed entries are KEPT
  and retried in the same run (next full-batch flush, then a final drain of
  non-blocking attempts every `DRAIN_INTERVAL_S` 5 s inside
  `DRAIN_RESERVE_S` 75 s, never past the deadline; after a hold the family
  loop holds that reserve back). Still held at the end: exit 0
  `deferred_lock`, nothing written for them, the next run recomputes. Never
  waits on, polls inside, or breaks the lock.
- **`--chain` on task 28 is ON since 2026-10-04 (Coen's say-so, the plan's
  cutover step 7).** It was enabled only after a `--chain` run against a COPY
  of the chain wrote 16 entries that `verify_registry.py` read VALID. Never
  remove it, or re-add it after a code-revert rollback (the
  `GAUNTLET_IN_LOOP` flag no longer exists), without Coen's say-so; the job
  itself refuses to chain until `gauntlet-protocol-v6.1-amendment-1` is on the
  chain (it is, line 81246). Coen's
  effective-trials decision (2026-10-01, option 1) is in the v6.1 addendum and
  IS implemented and committed in `gauntlet_stats.py` (b7c17658): `trials_n` is
  the larger of the clustering's argmax (`trials_n_raw`) and the highest cluster
  count already on the chain (`trials_n_floor`, source named by
  `trials_n_floor_entry_hash`). Reason: the argmax moved 302 -> 480 -> 44 on a
  registry that only grew, and a falling N flatters the deflated Sharpe. Chain
  the entries only after the 480 -> 44 explanation is on record. A
  `gauntlet_stats` entry is append-only and cannot be corrected, only superseded
  by a note.
- **`FLOOR_PROTOCOLS` in `gauntlet_stats.py` is an explicit v3-v6 whitelist**
  of the protocols whose `trials_n` counts as a cluster count (an unknown
  protocol string may be a registration count, so the floor is not inferred).
  Any FUTURE gauntlet protocol that records cluster counts MUST add itself to
  `FLOOR_PROTOCOLS` in the same change, or its higher k silently fails to raise
  the floor and the recorded deflated Sharpe is flattered.
- **Status files (the Ops Sentinel reads both).** `logs/gauntlet_worker_status.json`:
  `evaluated`, `deferred_lock`, `queued`, `oldest_queued_age_hours`,
  `exit_reason`, `ts_utc` (the Sentinel's fields). Their meanings since
  2026-10-02: `evaluated` = verdicts CHAINED this run, first attempt or retry;
  `retried_written` = of those, the ones written on a retry after chain.lock
  was held at the first attempt; `dropped_stale` = kept results dropped
  unwritten because another writer moved or judged the candidate before the
  retry; `deferred_lock` = evaluated results STILL unwritten at the end of the
  run (they stay queued; `exit_reason` `deferred_lock` when nothing worse
  happened). No new `exit_reason` value was added.
  `logs/gauntlet_stats_status.json`: `stats_written`,
  `verdicts_without_stats`, `oldest_unstatted_verdict_age_hours`,
  `stopped_at_deadline`; since step 7 also `retried_written` /
  `deferred_lock` (T7 flush), `pbo_nulls_computed` / `pbo_nulls_cached` (PBO
  scope) and `expected_max_sharpe_raw` / `expected_max_sharpe_effective` (SR*
  floor). Sentinel `gauntlet_queue` check (on the UNMERGED
  `sc-ops-sentinel` branch `feat/gauntlet-queue-check` until cutover): FAIL when the worker
  status is missing or stale, when `exit_reason` is `crashed`,
  `refused_no_protocol_note` or unknown, or when `oldest_queued_age_hours` >
  48; WARN when the statistics lag exceeds 7 days. `27_GauntletWorker` is in
  the Sentinel's `hourly` list (267009, still running at the 09:15 digest, is
  tolerated for it alone), `28_GauntletStats` in `daily`. Do not rename
  these fields without changing `sc-ops-sentinel` in the same change.
- **Exit codes.** Worker: 0 = drained, deadline stop, or deferred on a lock
  (routine); 1 = a candidate raised, the run crashed, or setup was refused.
  Stats: 0 = done, nothing to do, deadline stop, chain.lock held, or another
  instance running; 1 = the data or the chain refused the run, or `--chain`
  without the amendment-1 note (`refused_no_amendment_note`).
- **Verifier.** Invariant 11: a `gauntlet_stats` entry must point at an EARLIER
  v6.1 gauntlet verdict of the same strategy, at most one per verdict.
  Invariant 12: from the v6.1 note's line onward no gauntlet verdict may carry
  `pbo_family_kill` true and no state change may cite it. The five v6 burials
  before the note stay valid history.
- **The five** (b09adeb0be6faf4e, 9acf68e5a2ef607d, 7ecf180b7e8b05ce,
  6aebc4b7f051baba, 15595642e7a2caaa) were buried by the family kill under v6
  verdicts although v6 retired it. Their graveyard entries STAND. All five are
  version 1 with `exit/time_stop`, so after exit-rules-v7 (chain line 32897)
  the buried compositions themselves can never be registered again (invariant
  10; `register_strategy` refuses them). From the
  `correction-2026-10-02-retrial-path` note (`docs/notes/`; it applies once it
  is chained, after Coen's approval) each is re-tried inside the D8/D9 unified
  re-run, in exit-rules-v7's D15(b) form: a version-2 composition with a
  permitted exit set, a NEW strategy id charged to N in full, screened and
  then judged by all six gates from scratch. That replaces the old "once the
  worker is live" trigger. Never move one out of the graveyard by hand.
- **Parity tool:** `tools/gauntlet_parity.py` re-judges chained v6 verdicts with
  the standalone battery on bars truncated at each verdict's recorded
  `data_end` and compares ELEVEN gate metrics exactly (is_edge_per_trade,
  oos_edge_per_trade, edge_decay_pct, mc_p05_equity, p_ruin,
  cost_stress_net_pnl, train_sharpe, is_edge_raw, oos_edge_raw, is_vol,
  oos_vol). It does NOT compare walkforward, era_summary, regime or
  perturbation, so "bit-identical" means those eleven only. Read-only; never
  scheduled. Parity rule: identical verdicts, and metrics bit-identical when
  compared under the same engine revision. Observed: in the 17 pre-change
  verdicts compared (of 818 pre-change v6 verdicts on the chain at the time of
  writing, "pre-change" = dated before the 2026-08-26T23:19Z engine change,
  commit 74b9703b), the eleven metrics differed by at most 17 ulps (train_sharpe);
  six of the 17 were bit-identical on them. All 48 compared verdicts after the
  change were bit-identical on them.

## Sweep rotation, sibling queues, re-trials (SP5 P2-T4: D6 / D10 / D9)
- **D6 rotation.** `loop.ROTATION_SIZE` = 12 (spec s5); `loop.ROTATION_CLASSES`
  = `("crypto",)`. A rotating class sweeps a window of 12 of its ACTIVE assets
  per generation, cursor `rotation_cursor` per class in loop_state.json,
  advanced ONLY on cycle_complete. **The small-set rule:** an active set of
  <= ROTATION_SIZE assets is returned WHOLE and the cursor never moves, so no
  `rotation_cursor` key is written for a class that does not rotate.
  ROTATION_CLASSES is a DECLARATION, never inferred from the asset count:
  equity_etf's active set is 16 assets (above the window), so an inferred
  "bigger than the window" rule would silently window it 12-of-16 and break
  the Phase 2 sweep freeze. Rotation is a SCHEDULE, never a selection --
  every active cell is swept with equal frequency and N accounting is
  untouched. The loop passes `--assets` only when the window differs from the
  full active list, so today's composer argv is byte-identical to pre-D6.
  Nothing rotates today (crypto's active set is empty).
- **D6 second gate -- BOTH sides read the routing dispatch, never the class
  name.** `--assets` is a view onto active cells, and the legacy POOLED
  expander has none, so `composer.run` refuses it. Both `loop._sweep_window`
  and that refusal test `expander_for(cls) is expand_family`, so rotation
  switches on in the SAME commit that makes a window legal. Keyed on the
  string "crypto" instead, SP5 Phase 3 would have emitted a window into a
  composer guaranteed to exit 1 -- `stage_failed` and a Sentinel FAIL on
  every crypto fire, three times a day. `test_phase2_freeze.py` simulates
  both a half-landed and a fully-landed Phase 3, so a coupling error fails at
  test time rather than in production.
- **Before SP5 Phase 3 activates the crypto USDT grid (Ruling 41): exclude late-starting cells
  upstream.** Cells whose first bar is after `screen.DEFAULT_CUTOFF` (2023-12-31) must be excluded in
  the ACTIVE_CELLS admission or the composer. Ten live cells start in 2024: ENA, ETHFI, JUP, PYTH,
  RENDER, STRK, TAO, WIF, ZK, ZRO (the ">= 730 days of history" admission rule in cells.py admits
  them all). Otherwise new specs on them are screened in-cycle with zero train bars and buried, or,
  if left `proposed` (a screen deadline deferral does this), they park EVERY fire of every class at
  `stale_data` (the freshness preflight blocks a late-starting cell a proposed spec names; a
  re-fetch cannot fix it).
- **`docs/2026-08-28-market-data-universe-design.md` s5 is STALE in this
  worktree** on the rotation rule (as is s4 on crypto's benchmark and s7b on
  the resurrection chaining). Trust the code, `docs/notes/family-openness-v1.md`
  and this file.
- **D10 sibling queues.** `validate_family`'s "exceeds cap, rejected, not
  clipped" refusal is GONE (chained pre-declaration:
  `docs/notes/family-openness-v1.md`). Overflow now splits via
  `composer.split_for_cycle` and queues in loop_state.json as
  `sibling_queue` per class; depth is reported as `queue_<cls>` in
  pipeline_status.json. **The composer writes that queue as a SUBPROCESS**, so
  `loop_state.refresh_queues(state, path)` runs right after the composer stage
  or the loop's own save clobbers it. A cycle whose class has a non-empty
  queue DRAINS instead of proposing -- that STAGE makes no metered model call
  (the cycle's triage panel still runs and still costs). Invariant,
  test-pinned: no proposed variation is ever dropped without either a gauntlet
  verdict or a queue entry.
- **A queue is its own trigger, at BOTH gates.** `pick_class` treats
  `queue_depth > 0` as over-threshold, and the `no_new_accepted_cards` stop
  exempts a queued class. Queued work is already proposed and already counted;
  it needs capacity, not new cards. With only one of the two, a class whose
  card flow goes quiet parks its queue forever -- the silent drop D10 removes,
  moved one gate earlier. The trigger BASIS itself is untouched.
- **The drain runs BEFORE the "no accepted cards" refusal**, which is a
  proposal precondition, not a drain one. Revoke the last accepted card with
  that check first and every cycle exits 1 without ever looking at the queue.
- **`sibling_queue_dead`** holds queued specs the registry refuses outright --
  a queued spec can outlive its cited card, because `review_card` may revoke
  an acceptance at any time and `register_strategy` then refuses forever. The
  drain catches `ValueError` PER SPEC (never a bare except -- chain IO errors
  must still abort), parks the offender with the registry's own reason, and
  keeps going. Depth shows as `queue_dead_<cls>` in pipeline_status.json,
  emitted only when non-zero, and it is **a human action item, not a level to
  watch drain** -- it never re-triggers its class.
- **⚠ KNOWN HARM (F5), declared not fixed: a split sweep can manufacture
  `edge_of_grid` plateau failures at the cut.** `plateau.qualifies` reads what
  is on the chain when the gauntlet runs, and the queued combos are not there
  yet, so siblings adjacent to the cut can be failed for a capacity reason
  wearing a statistical costume -- the very thing family-openness-v1 condemns,
  one layer down. Draining later does NOT repair it: those verdicts are
  already written. No PARTITION avoids it (a cartesian product cannot be split
  without severing an axis; cutting on the outer axis makes the window size
  vary with family shape, trading a visible harm for a hidden one) -- but that
  is exhaustive only over ways to CUT the sweep. Three open options, Coen's
  call: (1) accept it as shipped; (2) queue at FAMILY granularity so no sweep
  is cut, which the chained note's wording forecloses; (3) HOLD a split
  sibling group out of the gauntlet until its queue drains, which costs
  latency rather than correctness and does not touch the note at all (the note
  governs the composer's admission, not the gauntlet's batching). Only
  reachable for families that TODAY are refused outright, so nothing
  regresses.
- **D9 re-trials.** `composer.RETRIAL_WINDOW_DAYS` = 183. A composition whose
  fingerprint matches a registered strategy is still dropped UNLESS that
  registration is currently BURIED and its burying verdict's cutoff is >= 183
  days behind the target cell's CURRENT data end. **A composition with no
  burying verdict (quarantine or live) has no expiry and stays permanently
  excluded** -- that half is a tightening. The cutoff is not on the chain; it
  is read from `artifacts/<sid>/gauntlet/config.json` (or the screen bundle's
  `config.json`), so anything unreadable closes the window -- which is also
  why no tmp-registry test opens it. In-run/in-cycle duplicates are still
  malformed/dropped: those ARE same-data. Every re-trial is a NEW strategy id
  entering N honestly. **More registrations and more survivors at a fixed bar
  is arithmetic about the denominator, NEVER evidence of edge** -- see the
  chained note's own wording before reporting any of it.
- **D9 ends chain-wide fingerprint uniqueness on purpose.** All 2,775 pre-D9
  registrations carried distinct composition fingerprints; a re-trial is by
  definition a second registration of one. The surviving invariant is PER RUN:
  no single run may register a composition twice. That is why
  `screen_siblings` checks `fp not in run_fps` before admitting a re-trial --
  without it, family A's re-trial and family B's copy of it both chain, same
  run, same data.
- **`verify_registry.py` invariant 8 enforces that SAME rule, through the same
  function.** `composer.retrial_verdict` is the one implementation; the
  verifier and `retrial_oracle` are both thin readers of it, and a second copy
  of the rule anywhere is a defect. They differ ONLY in how they read a window
  they cannot establish: the composer is deciding, so unreadable = refuse; the
  verifier is checking with strictly less evidence, so unreadable = report
  `window not verifiable` and PASS. A verifier that failed there would call
  the chain corrupt every time an artifact bundle was pruned.
- **`Registry.register_strategy` enforces invariant 10 at WRITE time
  (2026-10-02).** Once the chain holds the exit-rules-v7 note (detected by
  `blocks.is_exit_rules_v7_note`, the verifier's own rule) it raises
  `ValueError` before any write for a spec that is not version 2 or carries a
  `blocks.RETIRED_TYPES` block. Why: the verifier only reports a violation
  after it is chained, so a hand-run writer (e.g. re-registering one of the
  five's version-1 `exit/time_stop` compositions) would leave the append-only
  chain INVALID for good. Verifier tests that need such an entry write it with
  a raw `reg.append`.
- **The window leg is not a chain fact.** The verifier reads the cutoff from
  `artifacts/` and the data end from `data/`, defaulting to beside the log and
  overridable with `--artifacts-dir`/`--data-dir` (the loop passes both). Run
  it against a COPY of the chain without those dirs and it still says VALID,
  but it has only checked the buried-priors and same-run legs -- read the
  NOTE line before concluding a re-trial was verified.
- **⚠ THE 183-DAY WINDOW DOES NOT CURRENTLY BITE. Known protocol gap,
  measured 2026-09-01, behaviour deliberately UNCHANGED -- Coen's call.** The
  cutoff `burying_cutoff` reads is the fixed train/OOS split constant, not a
  per-verdict date: **all 4,065 `config.json` bundles carry
  `cutoff = 2023-12-31`, zero exceptions, zero missing.** So the window is
  OPEN for all 2,702 burials and SHUT for none; the narrowest margin is 964
  days against a 183-day requirement. The first two live re-trials
  (`50f48ae9a07d01cc`, `4f8d2fc81c27f76e`, chain lines 16183/16184) are
  therefore **9-day re-tests** -- buried 2026-08-22, re-registered 2026-08-31,
  gauntlet `data_end` 2026-08-21 against bars ending 2026-08-30 -- on a chain
  that is 26 days old. As shipped, D9 reads as "any buried composition is
  re-triable", and every re-trial charges N in full. **Do not report re-trial
  survivor counts as edge** (the chained note says so itself).
- **If that clock is ever fixed, the fix is NOT uniform across burial
  stages.** The gauntlet bundle's per-cell `data_end` IS an honest clock
  (1175/1218 carry it, all > cutoff) and would have shut the window on both
  live re-trials. But **screen** bundles' `data_end` is train-truncated
  (2767/2767 have `data_end <= cutoff`), so it carries no information -- and
  **1,629 of the 2,702 burials came from `screened`, not `gauntlet`**. A
  re-screen runs a fixed train window and is deterministic, so it returns the
  identical verdict however much new data arrives: whether a screen-buried
  composition should be re-triable at all before the CUTOFF itself moves is an
  open protocol question.
  `test_a_buried_composition_uses_the_screen_cutoff_when_it_never_reached_gauntlet`
  currently asserts that it should. Leave that test alone until the question
  is decided on the chain.

## ⚠ Composer hazards for HAND RUNS in the live tree
- `--loop-state` defaults to `logs/loop_state.json` next to `--registry`, so a
  real (non-dry) `python -m pipeline.composer` in the live tree now READS AND
  DRAINS THE LIVE SIBLING QUEUE and can write to the loop's state file. The
  composer never touched that file before P2-T4. Pass an explicit
  `--loop-state` (or `--dry-run`, which never mutates it) for a hand run you
  do not want interacting with the loop's queue.
- `--data-dir` likewise defaults next to `--registry`; it is read-only (D9
  cell dating) but it means a hand run against a tmp registry silently gets a
  tmp data dir, which is the intended test isolation.

## Cycle deadline (Phase 3 steps 1-3, 2026-09-03) -- no stage may run into the PT4H wall
- **Why:** 2026-09-01 21:30 the loop cycle was hard-killed by Task Scheduler at
  exactly the PT4H ExecutionTimeLimit -- work discarded, composer spend not,
  and a hard kill leaves no terminator in any log. TRIAGE_LIMIT bounds triage
  only; the gauntlet was ~150 of that cycle's 237 min with no bound at all.
- **Mechanism (`pipeline/deadline.py`, one helper shared by all three).** The
  loop derives `deadline = cycle start + live task window - SAFETY_MARGIN_S
  (15 min)` -- ONLY when the window is known; no registered task means no
  deadline and byte-identical stage argv -- and passes `--deadline-utc` to
  screen and gauntlet. Each stage evaluates in chunks and asks
  `DeadlineBudget.fits(n, rate)` BEFORE each chunk: a conservative prior until
  the first chunk has run (gauntlet 20 s/candidate, screen 2 s/spec), the
  measured rate after. **Nothing is abandoned mid-flight; what is not started
  is simply not started.**
- **Resumable states, and why the orphan preflights stay green.** A deferred
  screen spec stays `proposed` (screen's orphan rule fires only on
  `screened`). A deferred gauntlet candidate stays in state `gauntlet` WITH NO
  VERDICT -- that is the normal pre-run state (screen advances a passer to
  `gauntlet`; only the gauntlet's own verdict moves it on), and
  `_gauntlet_orphans` fires only on `gauntlet` PLUS a verdict. Deferral is
  protocol-legal at candidate granularity: protocol-v6 judges every edge
  standalone, PBO family series come from the registry-wide simulation, and
  the null is seeded off the group id, so a sibling judged next pass sees the
  same family, same null, same verdict.
- **Reporting.** Every completed non-dry stage run writes
  `logs/<stage>_result.json` (`evaluated`, `deferred`, `deadline_utc`,
  `stopped_at_deadline`) -- the triage_result.json convention; an absent file
  means "did not report", never "deferred nothing". **Screen's meanings
  changed 2026-10-08 (design `docs/2026-10-08-screen-lock-release-design.md`):
  `evaluated` = specs CHAINED (written), no longer specs merely evaluated;
  `deferred` = specs not started PLUS specs evaluated but unwritten. Three
  counters are new (via `deadline.write_result(extra=...)`): `deferred_lock`
  (evaluated, unwritten because chain.lock stayed held; no stop flag),
  `retried_written` (written on a retry after a held lock) and
  `dropped_stale` (no longer `proposed` when the write came).** The loop UNLINKS the
  screen's before it runs and reads it on cycle_complete into status items
  `deferred_screen` and `stopped_at_deadline=<stage>` (screen is the loop's only
  deadline-aware stage since 2026-10-07; `deferred_gauntlet` is gone).
  **`stopped_at_deadline` is an OK outcome** (overall OK, cycle_complete): a
  cycle that chose to stop is routine; one killed at the wall is the defect.
  When the Sentinel is pointed at pipeline_status.json, treat it so.
- **PBO is deadline-aware too (2026-09-03 evening).** The 15:30 cycle was
  killed by the PT4H wall at PBO family 325 of 366 with every verdict unwritten:
  the 25% reserve (~36 min) was nowhere near a ~2 h pass of permutation nulls.
  Now the PBO loop only visits families that have a candidate this run, and
  asks the budget before EACH live family's null (`PBO_NULL_PRIOR_S` = 60 s
  until the first null is timed, the measured mean after). A family whose
  null does not fit is `deferred_deadline`: its candidates are removed from
  the verdict write and stay in `gauntlet` state with no verdict, counted in
  `deferred` / `stopped_at_deadline`, and the next run re-evaluates them (a
  family is judged in one pass or not at all -- no verdict is ever written
  against an unmeasured null). Measured families' verdicts are written.
- **Known approximation, stated.** The registry-wide simulation and clustering
  still run BEFORE any candidate and are not chunked (simcache-bounded after
  the first pass; arrays since 2026-09-03). The candidate reserve (25% of what
  is left when candidates begin, floor 60 s) now only has to cover the
  verdict/artifact writes plus however many nulls the loop chooses to run.
  `t_pbo` is printed every run. The PT4H task limit remains the backstop --
  hitting it is evidence of a bug, not weather.
- Tuning constants: `gauntlet.GAUNTLET_PRIOR_S_PER_CANDIDATE / _CHUNK_PER_WORKER /
  _RESERVE_FRAC / _RESERVE_MIN_S`, `screen.SCREEN_PRIOR_S_PER_SPEC /
  _CHUNK_PER_WORKER`, `loop.SAFETY_MARGIN_S`. Tests: `pipeline/test_deadline.py`
  (fake-clock unit tests + both stages end to end) and the four
  `*deadline*` / `stale_stage` tests in test_loop.py.

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

## Re-extract shadow run (tools/, NOT a pipeline stage)

`python -m tools.reextract_shadow [--dry-run] [--layer DIR] [--seed N] [--sample N] [--model M] [--panel-model M]`
measures whether today's extractor beats August's on documents we already own.
Design: `docs/2026-09-06-reextract-shadow-design.md`; plan: `docs/plans/2026-09-06-reextract-shadow.md`.

- **It lives in `tools/`, never `pipeline/`, and must never be scheduled.** It is a
  measurement, not a stage.
- **Always run `--dry-run` first.** It selects and prints the seeded ten-document
  sample (five whose cards mostly passed, five that mostly stalled) with no model
  call, no spend, no lock and no file written. The sample is deterministic for a
  seed within one Python version, so the dry run shows exactly what the live run
  will read.
- **Run it from the LIVE tree, and only the live tree.** Every path derives from
  `--layer` (default: this directory): the chain, `logs/chain.lock`, the ledger and
  `docs/runs/`. A missing `logs/budget_ledger.jsonl` is a REFUSAL (exit 2), never
  "zero spend this month" - in a worktree `logs/` is gitignored and absent, and an
  unguarded run there would have passed the cycle guard, locked the wrong tree and
  metered nothing.
- **It never writes to the chain, and proves it:** size + sha256 before and after,
  on every exit path. It HOLDS `chain.lock` (holder `reextract-shadow`) for its
  duration so the resident scanner and the quarantine daily defer politely instead
  of appending mid-run; run it clear of 22:30 / 02:30 (the loop), 08:20 (quarantine
  daily) and 09:00-09:15 (Reader, Sentinel). If the chain still moves, the report
  records it as a field and names the legitimate writers - read that before
  blaming the harness.
- **Money.** Pilot ceiling USD 3, checked before each document (the last document
  can overshoot by its own cost - cents); the monthly pipeline cap is checked
  before every extraction call and inside the panel. Spend is real and lands in the
  shared monthly pipeline cap under agent `pipeline`; a stop is reported as which
  ceiling stopped it.
- **The panel runs on `--panel-model` (default `claude-sonnet-5`, the model that
  judged every card in the chain), never on the extractor's model.** Judging with a
  different panel would change the measuring instrument inside the measurement.
  Both models are recorded in the report. `DEFAULT_PANEL_MODEL` is a hand-synced
  copy of `triage_batch.run`'s argparse default - change both or the comparison
  silently drifts.
- **The verdict is decided BEFORE the run** (design doc): green >= 2 novel accepted
  per document with a novel accept rate no worse than the old one, red < 0.5,
  amber between, NO RESULT when zero documents completed (exit 3). Both accept
  rates are over JUDGED cards: old excludes still-pending, new excludes cards a
  budget-stopped panel never reached (`novel_unjudged`).
- **The report survives an abort** (Ctrl-C, a crash, the chain moving): it is written
  from a `finally`, to `docs/runs/<date>-reextract-shadow-seed<seed>.md` plus a JSON
  sidecar, so paid work is never lost and a same-day re-run cannot overwrite it.
- **Duplicate detection is two-stage** (step 5b, added after the first pilot on
  2026-09-06 returned 0 fingerprint duplicates in 41 claims from documents holding 22
  cards): `claim_fingerprint` (normalised, not semantic) against EVERY chain card,
  then one call on the panel model per survivor asking whether it restates a card
  held from the SAME document. Restatements are counted (`restated_existing`) and
  never reach the panel. **Read `judge failures N` in the claims line first:** a
  failed judge call keeps the claim novel (fail open toward measuring) but is counted
  and WARNED, so an inert dedupe cannot masquerade as "0 restatements". Cross-document
  paraphrase remains the stated gap. An empty or missing quote is a guard FAILURE
  (`""` is a substring of everything), deliberately stricter than the Reader's
  crash-on-missing-key.
- Exit codes: 0 measured, 2 refused (lock held, no ledger), 3 no result.

## Triage cost controls (loop stage 4a)
- **`--limit` is DERIVED each cycle from the spend allowance (Phase 3 step 5,
  2026-09-03), clamped to `loop.TRIAGE_CEILING` = 200 (`TRIAGE_LIMIT` is its
  alias; the Gate-2 window test governs the ceiling).** `pipeline/allowance.py`:
  `expected_cycles = max(10, cycles completed in the trailing 30 days)`
  (state["cycles"]); `allowance = PIPELINE_CAP_USD x (1 - 0.15) / expected`;
  `triage_count = clamp(floor((allowance - composer_pair_usd) / usd_per_card), 1, ceiling)`.
  The two unit costs are the loop's OWN measured spend deltas (trailing means
  in state["calibration"]; priors 0.018/card and 0.64/pair from 2026-09-01/02).
  At USD 40 and 20 cycles/month that was ~USD 1.70 a cycle, ~58 cards -- the
  intended effect of the cap, stated in the plan. **Since D41 raised the cap to
  200 the derivation saturates: the allowance buys more than `TRIAGE_CEILING`
  at every cycle count, so the limit passed is ALWAYS 200 and the cap no longer
  shapes cycle size.** Never hand-type a card count into the loop; change
  `TRIAGE_CEILING` (now the binding constraint), the cap, or the reserve.
- **Two parks after triage, both BANK the reviewed cards.** `deferred_budget`
  = the MONTHLY hard-cap line (WARN, budget_cap semantics; since D41 there is
  no earlier batch-stop line),
  checked first; `deferred_cycle_budget` = this cycle's own allowance would be
  exceeded by the composer pair (overall OK -- Coen: a park counts as a clean
  day). Before 2026-09-03 the monthly park recorded a park and never banked,
  so the cards triage had just paid for were re-paid on the next fire.
- Status items on every path that reaches triage: `cycle_usd_allowance`,
  `expected_cycles`, `triage_limit_used`, `usd_per_card`, `composer_pair_usd`,
  `cycle_spent`. The digest can say WHY a cycle was the size it was.
- **ExecutionTimeLimit lives in TWO places and the second one wins.**
  `quant/tasks/xml/25_PipelineLoop.xml` declares it, but
  `quant/tasks/apply_retry_settings.ps1` re-stamps every `$RETRY_TASKS` entry
  at the end of every `setup_scheduler.bat` run, and 25_PipelineLoop is on
  that list. Change it in BOTH places or the override wins -- the same
  sentence `setup_scheduler.bat` carries at the 25_PipelineLoop line. That
  mismatch is exactly how the live task sat at PT1H while its XML said PT2H.
  The current values are XML PT4H + a `$TIME_LIMIT_OVERRIDES` entry of PT4H.
- The loop WARNs at startup (never refuses -- a nonzero exit would FAIL the
  Sentinel digest) when the LIVE task's window is under PT2H, naming the
  elevated fix command. Reading the task setting is fully defensive: not
  registered, no schtasks, odd duration -> silent no-op, so manual runs and
  tests behave identically.
- **D44 (Coen, 2026-09-26): triage decides EVERY card; nothing waits for Coen.**
  A full panel of three unanimous accepts -> accepted. ANY dissent, or a panel
  still short after bounded in-call re-asks (`MAX_VOTE_ATTEMPTS`), -> rejected
  `claim_not_supported`, provenance `auto-d44`. A majority rule was offered and
  rejected (D31's reason: one reviewer spotting overreach must never be
  outvoted). The escalation skip-set (`logs/triage_escalated.json`), its
  `--escalated-state` / `--no-skip-escalated` / `--queue` flags and Coen's T3
  card queue are GONE; the 261 cards it held were resolved under the new rule
  on 2026-09-26. Each rejection's dissent reasons are appended to
  `logs/triage_rejections.jsonl` (the chain only records the reason code), so
  a rejected card can still be audited and revoked. `triage_result.json`
  still carries `skipped_escalated` (always 0) because loop.py reads it.
- **Resolving cards OUTSIDE a cycle moves the trigger basis -- re-bank.** The
  retired skip-set's cards were PENDING (so triggerable) and already banked in
  every class watermark. Rejecting them out of cycle (the D44 backfill) dropped
  every class BELOW its watermark (crypto -95 .. equity_etf -142) and would have
  stalled the loop. Fixed the same day under loop.lock: each watermark lowered
  by the retired cards routable to that class (crypto 261, fx 67, equity_etf
  205, bond_etf 78, metal_etf 80), restoring the pre-backfill gaps exactly;
  backup `logs/loop_state.json.bak-2026-09-26-pre-d44-rebank`. Any future
  out-of-cycle disposition of pending cards needs the same re-bank.
- **D44 sources: accepted by default.** D27 case 3's AI source screen
  (`relevance.screen_source`, one Sonnet call per discovered source that could
  BLOCK it on content) is REMOVED, not unwired -- `process_admissions` has no
  `screen`/`can_spend` parameter (test-guarded). A discovered source that passes
  the mechanical prefilter (reachable, has articles, not a store/login
  subdomain) goes straight onto probation and its YIELD decides. The per-item
  relevance screen still filters its articles. The 97 domains the old screen
  had blocked were reopened to `proposed` on 2026-09-26 so they re-enter
  through the prefilter. Code changes reach the RESIDENT scanner only after it
  is restarted (`run_scanner.ps1`).
- `no_new_accepted_cards` outcome: triage ran but accepted nothing new for a
  class that fired on pending cards, so the composer would see an unchanged
  corpus. Exits 0 before any metered composer call and still advances the
  watermark (those cards were seen). Spec Decision 2, "no new information, no
  new trials".
- `gauntlet_orphan` outcome (exit 1, FAIL): a strategy sits in state
  'gauntlet' with a non-v6.1 gauntlet verdict already chained -- a hand run of
  gauntlet.py refuses on this unconditionally. Since step 8 (2026-10-07) the
  loop no longer runs gauntlet.py, so this is an alarm for an orphan that
  already exists or that a hand run left, detected next to the pre-spend chain
  verify. Repair the chain manually.
- Fires ONCE at 20:00 local with a PT11H window, ending 07:00 (Coen 2026-09-28:
  PT4H killed every cycle 09-25..27 once the registry passed ~10k strategies --
  the gauntlet's registry-wide clustering is not deadline-aware -- and the 02:30
  fire never ran behind the killed 22:30 instance). Was 22:30/02:30 PT4H from
  2026-09-04 (night: a cycle needs ~9 GB of commit the desktop does not leave
  free by day -- WATCH the 20:00 start for that; the gauntlet's commit-bounded
  worker count is the defence). No window may reach 08:20 QuarantineDaily.
  morpheus-hub/tasks/xml/25_PipelineLoop.xml + apply_retry_settings.ps1's
  $TIME_LIMIT_OVERRIDES carry it -- change BOTH once \Morpheus\25_PipelineLoop (moved from \StewartCo\ on 2026-09-13; the loop's TASK_NAME follows it) is
  registered (activation Coen-gated per D29); exit 0 covers no_trigger and
  polite deferrals (distinguished in status items.outcome); nonzero = real
  defect (Sentinel FAILs the digest).

## Quarantine daily catch-up (23_QuarantineDaily, 2026-09-28)
- **Why:** the daily records ONE date and DEFERS a class whose bar is not
  published yet (equity ETFs are always a day behind at 08:20: their only
  refresh is loop stage 0 at 22:30, before the US close; FRED fx lags about a
  week). Deferred dates were meant to be backfilled by hand `--date` runs and
  never were: by 2026-09-28 all 253 equity_etf + 19 fx strategies had ZERO
  forward days (4,764 strategy-days owed). A prose-only rule, now a stage.
- `run_quarantine.bat` ends with `python -m pipeline.quarantine --catch-up`:
  every OWED date (a deferred class, a missed day, a `deferred_lock` skip) is
  recorded through the unchanged `--date` path, oldest first, at most
  `MAX_CATCHUP_DATES` (10) per run. A date counts as owed only when EVERY asset
  of the strategy has a bar on it -- the `--date` path's own readiness rule --
  so a calendar-gap date is never retried forever.
- A failed date never strands later ones (exceptions included) but makes the
  run exit 1; the wrapper STILL commits what was recorded, then exits 1.
- Cost: ~4-7 min per date on the 2026-09 chain (each date re-reads the chain
  and re-simulates every ready strategy). Steady state is one or two dates a day.
- Backfilled rows stay visible as backfills in `--review` (write time vs bar
  date); catch-up records every owed strategy alike, so it is a schedule,
  never a selection.
- The Ops Sentinel health-asserts `23_QuarantineDaily` (`daily`) since
  2026-09-28 (Coen), so an exit 1 reaches the 09:15 digest; 267009 ("still
  running" -- a long catch-up) is tolerated for it alone.

## Quarantine -> live gate runs unattended (26_LiveGateWeekly, 2026-09-03)
- `python -m pipeline.livegate` judges BOTH arms of the chained
  `quarantine-live-protocol-v1` and, when not `--dry-run`, chains a
  `live_gate` verdict plus a state change for every strategy it moves
  (quarantine -> live, or -> graveyard); a HOLD writes nothing. It takes
  `logs/chain.lock` only when there is a verdict to chain and defers politely
  (`deferred_lock`, exit 0) when it is held -- the quarantine daily's rule.
- `--report DIR` writes `<UTC date>-livegate-assessment.md` (every quarantined
  strategy, cohort size, verdicts) -- Coen's quarterly read. Written on a dry
  run too; it is not a chain write.
- `tasks/run_livegate.bat` = `\Morpheus\26_LiveGateWeekly` (in `\StewartCo\` until 2026-09-13), **Sunday 09:10**,
  exit code load-bearing. Weekly, not daily: the note charges
  Benjamini-Hochberg over "the eligible strategies at each assessment" and
  fixes no cadence; weekly keeps the kill arm prompt without re-asking a
  barely-changed record daily. Change the cadence here AND in
  `quant/tasks/setup_scheduler.bat` in the same pass.
- **LIVE is a lifecycle state, not capital** (the note's own words). Money at
  risk stays Coen's separate decision; nothing here can construct a
  `RouterConfig(mode="live")` in trading-systems, and nothing should.
- Reachability, from the note: graduation is a MULTI-YEAR proposition
  (Sharpe 1.3 ~ 587 days best case). Do not read a slow record as a weak
  strategy; it may be a large cohort.

## Triage escalations -- REMOVED by D44 (2026-09-26)
- The 2026-09-03 `--queue` view and the skip-set's `dissent_reasons` are gone
  with the queue itself. Dissent reasons now live in
  `logs/triage_rejections.jsonl`, one row per rejected card.

## Budget lines (D41, 2026-09-18): pipeline 200, Reader 20, one constant, NO batch-stop
- `pipeline/budget.py` `PIPELINE_CAP_USD` is THE pipeline cap;
  `pipeline_budget.MONTHLY_USD` imports it. The Reader's default meter cap and
  `scanner --cap` default are 20. Tests derive every threshold from the
  constants -- never pin a literal dollar figure.
- **D41 (Coen): the 80% batch-stop is GONE and the cap went 40 -> 200.** The
  batch-stop made the last fifth of every month unusable and skipped good
  candidates; work now runs right up to the hard cap. `may_start_batch` is
  kept as a named call site but is now the same line as `may_spend`.
- **⚠ 200 EXCEEDS the old D28 pool of 100 and no longer fits the Reader 20 /
  pipeline 40 band split. That band is superseded, not stretched.**
- **⚠⚠ AT 200 THE ALLOWANCE NO LONGER BINDS -- `TRIAGE_CEILING` (200 cards)
  does.** Measured: at 10/20/30 cycles a month the derived triage limit is 200
  every time, so each cycle costs a flat ~USD 4.24 and MONTHLY SPEND NOW SCALES
  WITH CYCLE COUNT (~42 / ~85 / ~127) instead of self-regulating to ~34 at any
  cadence. Two fires a day that all run full cycles would reach the 200 cap
  around day 23 and then park hard, with no glide path now the 80% stop is
  gone. **If that is not wanted, the lever is `TRIAGE_CEILING`, not the cap.**
- `BudgetMeter.state()` judges the CURRENT calendar month. A test that
  stamps rows in a fixed month goes silent when the month turns (that is what
  broke test_pipeline_budget on 2026-09-01); use a this-month timestamp.
