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
- **D10 sibling queues.** `validate_family`'s "exceeds cap, rejected, not
  clipped" refusal is GONE (chained pre-declaration:
  `docs/notes/family-openness-v1.md`). Overflow now splits via
  `composer.split_for_cycle` and queues in loop_state.json as
  `sibling_queue` per class; depth is reported as `queue_<cls>` in
  pipeline_status.json. **The composer writes that queue as a SUBPROCESS**, so
  `loop_state.refresh_queues(state, path)` runs right after the composer stage
  or the loop's own save clobbers it. A cycle whose class has a non-empty
  queue DRAINS instead of proposing -- no metered model call at all -- and is
  exempt from the `no_new_accepted_cards` stop (queued work needs no new
  card). Invariant, test-pinned: no proposed variation is ever dropped without
  either a gauntlet verdict or a queue entry.
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

## Triage cost controls (loop stage 4a)
- `--limit` comes from `loop.TRIAGE_LIMIT` (40, not 200). It is sized to fit
  the scheduled task's ExecutionTimeLimit alongside the rest of the cycle:
  ~3.85 s/reviewer-call x 3-reviewer panel, so 40 cards is ~7.7 min against a
  75-90 min cycle. **Raising it without raising the task's ExecutionTimeLimit
  re-creates a mid-flight kill loop**: Windows kills the cycle, the watermark
  never advances, and the class re-fires forever paying full freight. Pinned
  by test_triage_limit_fits_the_scheduled_execution_window.
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
