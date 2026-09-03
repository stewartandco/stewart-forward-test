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

## Triage cost controls (loop stage 4a)
- `--limit` comes from `loop.TRIAGE_LIMIT` (200 since 2026-08-31, Coen; was
  40). `MIN_TASK_WINDOW_S` is DERIVED from it. ~3.85 s/reviewer-call x
  3-reviewer panel, so 200 cards is ~38 min. **It bounds triage only** -- one
  stage of five, ~60% of the money and ~30% of the clock; the composer sweep
  and the gauntlet scale with the class, not with this number (the
  2026-09-01 fx cycle: triage 38 min of 237). The window-fit test now asserts
  the cycle fits even with the panel at HALF its measured speed.
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
