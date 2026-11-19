---
paths:
  - "pipeline/deadline.py"
  - "pipeline/test_deadline.py"
  - "pipeline/loop.py"
---
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
  (evaluated, unwritten because chain.lock stayed held; a lock leftover sets
  no stop flag by itself, but `stopped_at_deadline` can still be true in the
  same run when unstarted specs trip the chunk loop's deadline break),
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
  _RESERVE_FRAC / _RESERVE_MIN_S`, `gauntlet_core.PRIOR_S_BY_TF` (with
  `prior_s_for`), `screen.SCREEN_PRIOR_S_PER_SPEC / SCREEN_PRIOR_S_BY_TF`
  (with `screen_prior_s_for`) `/ _CHUNK_PER_WORKER`, `loop.SAFETY_MARGIN_S`. Tests: `pipeline/test_deadline.py`
  (fake-clock unit tests + both stages end to end) and the four
  `*deadline*` / `stale_stage` tests in test_loop.py.

