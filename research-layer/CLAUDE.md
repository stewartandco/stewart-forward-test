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

## Pipeline loop (25_PipelineLoop)
- python -m pipeline.loop --once from the layer root; --dry-run reports the
  trigger decision without running anything; --seed-watermarks initialises
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
- State: logs/loop_state.json (per-class watermarks + thresholds, Coen-editable).
- Status: logs/pipeline_status.json (NOT status.json -- that file belongs to the
  reader agent); run log logs/pipeline-loop-run.log; instance guard logs/loop.lock.
  Items carry BOTH routable_<cls> (accepted-only) and triggerable_<cls>
  (accepted+pending) per class -- a large gap between them is an undrained
  pending backlog. Present on every path that has read the chain (no_trigger,
  dry_run, cycle_complete, stage_failed, the budget/lock defers); the three
  paths that run BEFORE the chain read -- the two startup lock probes and
  loop_crashed -- legitimately omit them.
- `budget_state` item: ok | batch_stop (80% line) | hard_cap. Written on
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
  means "did not report", never "deferred nothing". The loop UNLINKS both
  before each stage and reads them on cycle_complete into status items
  `deferred_screen`, `deferred_gauntlet`, and `stopped_at_deadline=<stage>`.
  **`stopped_at_deadline` is an OK outcome** (overall OK, cycle_complete): a
  cycle that chose to stop is routine; one killed at the wall is the defect.
  When the Sentinel is pointed at pipeline_status.json, treat it so.
- **Known approximation, stated.** In the gauntlet the registry-wide
  simulation and clustering run BEFORE any candidate and are not chunked
  (simcache-bounded after the first pass); PBO runs AFTER and scales with the
  live groups the candidates leave. A reserve (25% of what is left when
  candidates begin, floor 60 s) is held back for it; `t_pbo` is printed every
  run so the reserve can be calibrated from real cycles. The PT4H task limit
  remains the backstop -- hitting it is now evidence of a bug, not weather.
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

## Triage cost controls (loop stage 4a)
- **`--limit` is DERIVED each cycle from the spend allowance (Phase 3 step 5,
  2026-09-03), clamped to `loop.TRIAGE_CEILING` = 200 (`TRIAGE_LIMIT` is its
  alias; the Gate-2 window test governs the ceiling).** `pipeline/allowance.py`:
  `expected_cycles = max(10, cycles completed in the trailing 30 days)`
  (state["cycles"]); `allowance = PIPELINE_CAP_USD x (1 - 0.15) / expected`;
  `triage_count = clamp(floor((allowance - composer_pair_usd) / usd_per_card), 1, ceiling)`.
  The two unit costs are the loop's OWN measured spend deltas (trailing means
  in state["calibration"]; priors 0.018/card and 0.64/pair from 2026-09-01/02).
  At USD 40 and 20 cycles/month that is ~USD 1.70 a cycle, ~58 cards -- the
  intended effect of the cap, stated in the plan. Never hand-type a card count
  into the loop again; change the cap (a decision) or the reserve.
- **Two parks after triage, both BANK the reviewed cards.** `deferred_budget`
  = the MONTHLY batch-stop / hard-cap line (WARN, budget_cap semantics),
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
- `logs/triage_escalated.json` is the advisory escalation skip-set. Escalated
  cards are never chained, so without it they re-occupy the head of the
  --limit window every cycle and the backlog behind them is unreachable.
  Advisory: missing or corrupt -> WARN + skip nothing, never a crash.
  `--no-skip-escalated` forces a re-review. It is a TRIAGE-cost control only
  and never a trigger input -- escalated cards still count as pending work.
  **The file is only ever overwritten when it was ABSENT or read cleanly.** A
  file that exists but could not be read (corrupt JSON, or a transient
  sharing lock from AV/the indexer) is left alone -- overwriting it from an
  empty dict would destroy the history and make the next cycle re-pay for the
  whole escalated backlog. `--no-skip-escalated` likewise preserves the set;
  it suppresses the filter, not the history. `times_seen` counts cycles a
  card has been sighted still waiting on Coen (a high number = blocking the
  queue for weeks); `first_escalated_utc` never moves.
- `no_new_accepted_cards` outcome: triage ran but accepted nothing new for a
  class that fired on pending cards, so the composer would see an unchanged
  corpus. Exits 0 before any metered composer call and still advances the
  watermark (those cards were seen). Spec Decision 2, "no new information, no
  new trials".
- `gauntlet_orphan` outcome (exit 1, FAIL): a strategy sits in state
  'gauntlet' with a gauntlet verdict already chained -- gauntlet.py refuses on
  this unconditionally, so the loop detects it next to the pre-spend chain
  verify rather than paying ~$4.20/fire to reach a guaranteed failure. Repair
  the chain manually.
- Fires 10:30 / 15:30 / 21:30 local once \StewartCo\25_PipelineLoop is
  registered (activation Coen-gated per D29); exit 0 covers no_trigger and
  polite deferrals (distinguished in status items.outcome); nonzero = real
  defect (Sentinel FAILs the digest).

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
- `tasks/run_livegate.bat` = `\StewartCo\26_LiveGateWeekly`, **Sunday 09:10**,
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

## Triage escalations carry their reasons; `--queue` groups them (2026-09-03)
- Each dissenting reviewer's one-sentence reason is now stored on the
  skip-set entry (`dissent_reasons`, advisory, additive) and carried across
  sightings; a `--no-skip-escalated` re-review replaces them with the newer
  panel's objections. Before this the reasons were read once and discarded.
- `python -m pipeline.triage_batch --queue [--escalated-state PATH]` prints
  Coen's T3 backlog grouped by reason, most common first, with times_seen and
  first-escalation date. Read-only: returns before Registry is constructed.
  Cards escalated before 2026-09-03 show `(no reason recorded)` until
  re-reviewed (a one-off `--no-skip-escalated` pass over 331 cards costs
  ~USD 6 -- Coen's call).

## Budget lines (D39, 2026-09-03): pipeline 40, Reader 20, one constant
- `pipeline/budget.py` `PIPELINE_CAP_USD` is THE pipeline cap;
  `pipeline_budget.MONTHLY_USD` imports it. Batch-stop = 80% = 32. The
  Reader's default meter cap and `scanner --cap` default are 20. Tests derive
  every threshold from the constants -- never pin a literal dollar figure.
- `BudgetMeter.state()` judges the CURRENT calendar month. A test that
  stamps rows in a fixed month goes silent when the month turns (that is what
  broke test_pipeline_budget on 2026-09-01); use a this-month timestamp.
