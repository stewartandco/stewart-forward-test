# Phase 3, steps 1–3 — per-cycle deadline: gauntlet, screen, loop

**Spec:** `2026-09-03-phase3-spend-and-time-throttle.md` §3.2. This plan covers the *time* budget only (steps 1–3). Steps 4–5 (spend) wait on Coen's monthly number.

**Goal:** a cycle can never be killed at the task's `ExecutionTimeLimit`. Each chain-writing stage receives a deadline and **stops before starting work it cannot finish**, leaving the remainder in a state the next cycle resumes from. The scheduler limit stays PT4H as a backstop; hitting it becomes evidence of a bug.

## Facts the design rests on (verified at source 2026-09-03)

- `gauntlet.run()` is not a per-strategy loop: it simulates the **whole registry** for clustering (simcache-mitigated), clusters, evaluates candidates through a `ProcessPoolExecutor` (`max_workers = cpu-2`), computes PBO per family, then writes verdicts in `candidates` (registry) order.
- **Deferral is protocol-legal at candidate granularity.** protocol-v6 (`select_survivors`): "every edge is tested and judged standalone, and a sibling's score is not evidence about this strategy". PBO family series come from the registry-wide simulation, not from this pass's candidates, and the null is seeded off the group id — a deferred sibling judged next pass sees the same family, same null, same verdict.
- **Resumable states.** A strategy awaiting the gauntlet is in state `gauntlet` *with no verdict* — that is the normal pre-run state, and `_gauntlet_orphans` only fires on `gauntlet` **plus** a verdict. A strategy awaiting the screen is in `proposed`; screen's orphan rule only fires on `screened`. So "not started" strategies need no chain repair. *(The spec §3.2 said deferred gauntlet work "stays in `screened`" — wrong; corrected in the same commit.)*
- The loop already reads the live task window (`_live_task_window_s`, total: `None` when no task) and stamps a cycle start. Tests stub the window reader module-wide.

## Design

**`pipeline/deadline.py` — one helper, shared by all three.**

```
DeadlineBudget(deadline_utc: str | None, *, reserve_s: float = 0.0, clock=time.monotonic)
  .active            -> bool           # False when no deadline: every stage behaves exactly as today
  .remaining_s()     -> float | None
  .fits(n, rate_s)   -> bool           # now + n*rate + reserve <= deadline
  .record(n, elapsed_s)                # updates the measured per-item rate (mean over what ran)
  .rate_s(prior_s)   -> float          # measured rate, or the prior before anything ran
```
Clock injectable so tests are deterministic; the ISO deadline is converted to a monotonic offset once at construction.

**Stages evaluate in chunks and ask `fits` before each chunk.** Chunk = `max_workers × 4` candidates (gauntlet) / `workers × 8` specs (screen), registry order preserved so deferral is always a suffix. The first chunk uses a conservative prior (gauntlet 20 s, screen 2 s per item, wall); later chunks use the measured rate. Not started ⇒ not evaluated ⇒ no verdict, no state change, no artifact.

**Every stage reports machine-readably** — `logs/<stage>_result.json` beside the registry, the same pattern as `triage_result.json`: `{"evaluated": n, "deferred": m, "deadline_utc": ..., "stopped_at_deadline": bool}`. Written on every run including the no-deadline case (`stopped_at_deadline: false`), so an absent file means "did not report". The loop clears each file before its stage.

**Known approximation, stated:** in the gauntlet the registry-wide simulation and clustering run *before* any candidate and are not chunked (cache-bounded after the first pass); PBO runs *after* candidates and scales with live groups, so capping candidates bounds it only indirectly. `reserve_s` holds back a fixed share for PBO + writes; `t_pbo` is already printed per run so the reserve can be calibrated from real cycles. If PBO ever overruns the deadline the task limit still backstops — and the log will say so.

**Loop:** `deadline = cycle_start + window − SAFETY_MARGIN_S (15 min)` only when the window is known; passes `--deadline-utc` to screen and gauntlet; reads both result files; on any `stopped_at_deadline` adds status items `stopped_at_deadline=<stage>` and `deferred_<stage>=<n>`. Outcome stays `cycle_complete`, `overall: OK` — a cycle that *chose* to stop is routine. The watermark rule is unchanged (it is card-based).

## Tasks (each: RED → GREEN → commit, in the worktree)

1. **`deadline.py` + gauntlet.** Tests: `DeadlineBudget` unit (no deadline = inert; fits/record with a fake clock; ISO parse incl. `Z`); gauntlet e2e — deadline in the past ⇒ rc 0, chain unchanged, result file `evaluated 0 / deferred N / stopped true`, and a following run without a deadline evaluates all N (resumable, orphan preflight green); far-future deadline ⇒ registry byte-identical to a run without the flag.
2. **Screen.** Same three shapes against `screening_registry` / `write_data_dir`.
3. **Loop.** `FakeRunner` gains a stage-result hook (derives the logs dir from `--registry`, like its triage hook). Tests: with a stubbed window the loop passes `--deadline-utc` to both stages and it is `start + window − margin`; with no window it passes nothing (today's argv, byte-for-byte); a runner reporting `stopped_at_deadline` yields `cycle_complete` + the two status items; the stale-file guard (a leftover result is cleared before the stage).
4. **Spec §3.2 correction** and the Sentinel note in §3.2(4): `stopped_at_deadline` is an OK outcome. Vault pointer.

Suite gate before merge; the 5 known environmental failures are expected in the worktree (gitignored data/artifacts; data-pinned tests).
