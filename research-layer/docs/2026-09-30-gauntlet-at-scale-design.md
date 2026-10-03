# Gauntlet at scale (Morpheus one-engine Build 2a) — design

Date: 2026-09-30. Status: DRAFT for Coen's review. Decisions: Coen, 2026-09-30
kickoff (Q1-Q5 and design sections 1-4 below, each approved in turn).
Programme record: vault `project_morpheus_one_engine.md` (decision E2; Build 2
split into 2a = this document, 2b = engine v2).

## 1. Why

The gauntlet no longer completes. Its first phase re-simulates and clusters
EVERY registered strategy before any candidate is judged, and neither the
re-simulation nor the clustering is deadline-aware. Measured on the live loop:

| Run (local) | Registry | Clustering phase | Candidates judged |
|---|---|---|---|
| 2026-09-18 02:30 (the last run whose log shows complete stage timings) | 6,075 | 2,675 s (5,065 cache hits / 7 misses) | 1,003 of 1,003 |
| 2026-09-25, 26, 27 22:30 | ~10k | killed at the PT4H wall (0x41306) | 0 |
| 2026-09-28 20:00 (PT11H) | 11,476 | 17,468 s (8,571 hits / 826 misses) | 864 of 2,079, then `BrokenProcessPool`; all 864 lost (verdicts are written only at the end) |
| 2026-09-29 20:00 (PT11H) | 11,835 | 36,130 s (0 hits / 9,581 misses) | 0 of 2,254 (deadline) |

The registry grew ~500 strategies a day (6,075 on 09-18 to 11,835 on 09-29).
The simulation cache key includes each price file's sha256, and the files gain
a bar every trading day, so the cache only hits when no bars arrived. The
09-29 run reported `cycle_complete` / `stopped_at_deadline` (an OK outcome by
design) after eleven hours that produced no verdict at all.

Two further facts shape the design.

**The clustering decides nothing.** Under gauntlet-protocol-v6 (chain line
2514, 2026-08-20) every gate is standalone. `gauntlet.FAIL_ORDER` is exactly
`sharpe_floor, oos_negative, edge_decay, mc_p05, p_ruin, cost_stress`; the
deflated Sharpe is recorded and deliberately not a gate; PBO is recorded and
not a gate. The effective-trials clustering (`trials_n`) feeds only the
recorded deflated Sharpe. The hours spent there produce records, not verdicts.

**The code did not match the protocol.** v6 states that "the family kill is
retired", but `gauntlet.py` kept protocol-v5's PBO family kill (`killed_groups`,
`PBO_KILL_PCTILE = 0.95`). Verdicts labelled `gauntlet-protocol-v6` then failed
five strategies with `pbo_family_kill`: b09adeb0be6faf4e, 9acf68e5a2ef607d,
7ecf180b7e8b05ce, 6aebc4b7f051baba (2026-08-25) and 15595642e7a2caaa
(2026-09-03). The kill only fires on a candidate that passed all six gates, so
all five would have entered quarantine under v6 as written.

A third fact was found in the same runs and is investigated here, not assumed:
effective trials fell from 480 clusters over 11,476 strategies (09-28) to 44
over 11,835 (09-29). A deflated Sharpe computed on 44 is far too lenient. The
gauntlet did chain verdicts against it: 676 v6 verdicts at
chain entries 66700-67375 (2026-09-30T22:11-22:22Z, registered_n 11,860; 672
fail, 4 pass) record trials_n 44, after 1,003 verdicts at 302. They stand, and
no gate depended on N.

## 2. Success criteria (Coen, Q3)

1. Every gauntlet candidate receives its verdict within **48 hours** of
   entering the queue (the moment screen moves it to state `gauntlet`).
2. The backlog present at cutover (2,254 on 2026-09-30) is cleared within a
   **week**.
3. Both still hold at **3x the 2026-09-30 registry (~35,000 strategies)**.
4. The code applies exactly the gates the chained protocol names, and the chain
   verifier rejects any verdict that applies a retired gate.

## 3. Scope

In: a continuous gauntlet worker; a separate, lagging recorded-statistics job;
the loop ending at the screen stage; a chained protocol addendum (v6.1) and a
chained correction note; verifier invariants; lock hardening; Sentinel checks;
D9 re-trials of the five.

Out (Build 2b): multi-timeframe x multi-asset strategies, intraday data, the
fixed TF x asset grid (C2), and J2 (moving the engine to a private repo, which
would reverse D40's "pipeline public as the trust asset" and is decided there).
2a is built in place in `stewart-forward-test` (Coen, Q2).

## 4. Architecture

### 4.1 Gauntlet worker — `pipeline/gauntlet_worker.py`
- Scheduled task `\Morpheus\27_GauntletWorker`, **every 30 minutes**, each run
  draining the queue until its own **25-minute** deadline, then exiting. A
  periodic task, not a resident process: every run leaves an exit code the Ops
  Sentinel can read, and a dead worker cannot hide.
- Queue: every strategy in state `gauntlet` with no gauntlet verdict, ordered
  **cheapest first** (fewest bars x assets; ties by strategy id) per E2.
- Per candidate: simulate on the current data; run the six gates in
  `FAIL_ORDER` with unchanged thresholds; write the artifact bundle; chain the
  verdict and the state change; move on.
- Imports and computes **no PBO and no clustering**. The retired family kill
  cannot recur because the code that applied it is not on this path.
- Candidate-evaluation parallelism reuses `gauntlet.worker_count` (bounded by
  available commit) and `worker_env` (one BLAS thread per worker).

### 4.2 Recorded-statistics job — `pipeline/gauntlet_stats.py`
- Scheduled task `\Morpheus\28_GauntletStats`, daily **01:00**, **PT6H**.
- Computes, over the whole registry, effective trials (`cluster.effective_trials`,
  method unchanged), the deflated Sharpe, PBO, `plateau_ok` and the
  Harvey-Liu haircut (the last two are also group-derived and were recorded
  in v6 verdicts; amended at planning, 2026-09-30), and chains one
  `gauntlet_stats` entry per v6.1 verdict that does not yet have one.
- Deadline-aware and **resumable**: it checkpoints progress and continues the
  next night. It may lag; the lag is reported (section 7), never hidden.
- The 480 -> 44 investigation and the optional weekly data-vintage freeze
  (so the cache hits six nights in seven) live here.

### 4.3 The pipeline loop
The loop stops after the screen stage (triage, composer, screen unchanged).
Screen already moves passers into state `gauntlet`; the worker takes them from
there. The loop's gauntlet stage stays behind a flag, off by default, for one
week after cutover as the rollback path (section 10), then is removed.

## 5. Chain entries

### 5.1 Verdict (worker)
Entry type `verdict`, same shape as today, with `stage: "gauntlet"` and
`metrics.protocol: "gauntlet-protocol-v6.1"`. Every per-strategy metric stays
(`oos_edge_per_trade`, `edge_decay_pct`, `mc_p05_equity`, `p_ruin`,
`cost_stress_net_pnl` and the rest). Removed from v6.1 verdicts:
`deflated_sharpe`, `trials_n`, `registered_n`, `pbo`, `pbo_percentile`,
`pbo_family_kill` and the group context. The artifact bundle's `config.json`
keeps `cutoff` and `data_end` exactly as today (D9 re-trials and the live gate
read them). Then the `state_change`: fail -> `graveyard` with the failing
gate's name as the reason; pass -> `quarantine`, reason `gauntlet pass`.

### 5.2 `gauntlet_stats` (statistics job) — new entry type
`{strategy_id, verdict_entry_hash, trials_n, registered_n, deflated_sharpe,
pbo, pbo_percentile, plateau_ok, haircut, cluster_method, data_vintage}`
(`plateau_ok` and `haircut` added at planning: both depend on the sibling
family or on trials_n, both were recorded in v6 verdicts, and dropping them
would lose that evidence; the v6.1 addendum and its amendment-1 list the
fields as built, including `expected_max_sharpe_raw` / `_floor` /
`_floor_entry_hash` added in step 7). Exactly one per v6.1
gauntlet verdict, linked by the verdict entry's hash. It records; it never
changes a strategy's state.

### 5.3 Verifier (`verify_registry.py`) — new invariants
- A `gauntlet_stats` entry must reference an existing v6.1 gauntlet verdict for
  the same strategy; at most one per verdict.
- From the v6.1 addendum's chain line onward, no gauntlet verdict may carry
  `pbo_family_kill` true and no `state_change` may give it as a reason.

### 5.4 Unchanged
Historical entries, the six gates and thresholds, quarantine, the live gate,
D9 re-trials, and Morpheus's display (its historical `dsr` gate label).

## 6. Failures, locks, memory

- **Orphan repair.** At the start of every run, a strategy with a v6.1 verdict
  and no following state change receives the change its verdict implies
  (fail -> graveyard, pass -> quarantine). Idempotent and logged. A verdict is
  never re-evaluated.
- **A candidate that raises** gets no verdict; the run exits 1 (Sentinel FAIL)
  and the candidate stays queued, so the queue-age check names it if it keeps
  failing. Nothing is skipped or buried because of a crash.
- **`chain.lock`** is held only for one candidate's two writes. It is never
  waited on and never broken. If another writer holds it, the worker keeps
  that candidate's evaluated result in memory, with no bundle written, and
  retries the write later in the same run: once after each chunk, before the
  next is dispatched, then in a final drain pass of non-blocking attempts
  every 5 s inside a 75 s reserve the chunk loop holds back, never past the
  deadline. Each retry re-checks, on the advanced snapshot, that the
  candidate is still in `gauntlet` with no verdict; a candidate moved or
  judged meanwhile is dropped unwritten. Results still unwritten at the end
  of the run stay queued (exit 0) and are never persisted: the next run
  re-evaluates them. This rescues only short holds (scanner/inbox card
  batches). The loop's hours-long overnight screen hold and long quarantine
  catch-ups still leave whole runs unwritten. (Amended 2026-10-02: until then
  a held lock deferred the candidate to the next run and its evaluation was
  discarded.)
- **PID reuse.** Locks additionally record the holder process's start time;
  `holder_alive()` treats a live pid with a different start time as dead. (On
  2026-09-28 a reused pid wedged `loop.lock` and `chain.lock`.)
- **Memory.** The worker holds one candidate's data at a time plus a
  commit-bounded pool; nothing registry-wide is loaded, so it is safe by day.
  The statistics job carries the multi-gigabyte work at 01:00.
- **Known risk.** At ~35k strategies the full clustering may not fit even
  across nights. Criteria 1-3 bind verdicts only, so this cannot block
  quarantine, but the statistics could fall steadily behind. Levers: the weekly
  vintage and incremental clustering. The lag threshold is in section 7.

## 7. Status and monitoring

- `logs/gauntlet_worker_status.json`: `evaluated`, `deferred_lock`, `queued`,
  `oldest_queued_age_hours`, `exit_reason`, `retried_written`,
  `dropped_stale`. `evaluated` = verdicts chained this run, on a first
  attempt or a retry; `retried_written` = those written on a retry after
  chain.lock was held at the first attempt; `dropped_stale` = kept results
  dropped unwritten because another writer moved or judged the candidate
  first; `deferred_lock` = evaluated results still unwritten at the end of
  the run. (Amended 2026-10-02: `retried_written` and `dropped_stale` added;
  `deferred_lock` previously counted first attempts that met the lock. No
  new `exit_reason` value.)
- `logs/gauntlet_stats_status.json`: `stats_written`, `verdicts_without_stats`,
  `oldest_unstatted_verdict_age_hours`, `stopped_at_deadline`. (Amended
  2026-10-03, step 7: `retried_written` = entries chained on a retry after
  chain.lock was held at their first flush; `deferred_lock` = computed
  entries still unwritten at the end (exit_reason `deferred_lock`);
  `pbo_nulls_computed` / `pbo_nulls_cached` = PBO nulls built this run /
  served from `logs/gauntlet_stats_pbo_cache.json`;
  `expected_max_sharpe_raw` / `expected_max_sharpe_effective` = the run's
  SR* before and after the SR* floor. New stats `exit_reason`
  `refused_no_amendment_note` (exit 1): `--chain` before the
  gauntlet-protocol-v6.1-amendment-1 note is chained. The Sentinel's
  `gauntlet_queue` check reads only `oldest_unstatted_verdict_age_hours`
  from this file.)
- Ops Sentinel (`sc-ops-sentinel`): `27_GauntletWorker` in `hourly`;
  `28_GauntletStats` in `daily` (01:00 precedes the 09:15 run); a new
  queue-age check reading the worker status file: **FAIL when
  `oldest_queued_age_hours` > 48**; **WARN when the statistics lag exceeds 7
  days**. The queue-age check is what closes the blind spot that let an
  eleven-hour, zero-verdict night read OK.

## 8. Protocol records (chained BEFORE the first v6.1 verdict)

Both texts are approved by Coen verbatim before they are chained.

1. **gauntlet-protocol-v6.1 (addendum to v6).** Verdicts are chained without
   the registry-wide recorded statistics, which follow in a linked
   `gauntlet_stats` entry; the six gates, their order and thresholds are
   unchanged; the family kill that v6 retired is not applied (and the verifier
   rejects it from this line on). It supersedes v6 only in when the recorded
   statistics are written.
2. **Correction note.** The five strategies named in section 1 were buried by
   `pbo_family_kill` under verdicts labelled v6 although v6 had retired that
   gate; the implementation did not match the chained protocol. Evidence: their
   verdict entries, the v6 text, and the worker's pass on the same data (section
   9). Their graveyard entries stand (the chain is append-only). Each is
   re-tried as a NEW strategy id through the D9 re-trial path once the worker
   is live, charged to N in full and judged by all six gates from scratch.

## 9. Testing

Test-first; builder and verifier separate (standing rules).
- **Chain parity (the key test).** A sample of real v6 gauntlet verdicts that
  were not family-killed, re-run through the worker on the same data: identical
  pass/fail and identical per-strategy metrics.
- **The five.** The worker, on the same data, passes all five family-killed
  strategies. The correction note cites this result.
- **Pins:** the worker imports no PBO or clustering module; the verifier
  rejects `pbo_family_kill` after the addendum and rejects an orphaned or
  duplicate `gauntlet_stats` entry; orphan repair; a reused pid with a
  different start time reads dead; cheapest-first order; deadline and exit
  codes. Every rule mutation-checked.
- **Statistics job:** on fixtures, effective trials equal today's
  implementation (the method is unchanged). The 480 -> 44 drop is explained
  with a measurement before any `gauntlet_stats` entry is chained.
- The full research-layer suite, and a cold review before merge.

## 10. Cutover and rollback

1. Build in the `feat/gauntlet-at-scale` worktree; suite green; cold review.
2. Coen approves the exact addendum and correction texts.
3. Merge `feat/gauntlet-at-scale` between 07:00 and 20:00 (outside the loop's
   window). The loop now ends at screen.
4. Chain the correction note, then the v6.1 addendum, between 07:00 and 20:00
   with no loop running.
5. Merge morpheus-hub; Coen registers `27_GauntletWorker` and
   `28_GauntletStats` (elevated).
6. Confirm `logs/gauntlet_worker_status.json` exists AND that 28 has run once;
   only then merge the Sentinel branch (manifest and queue-age check), so the
   09:15 digest never FAILs on a task or a status file that does not exist yet.
7. Watch: the backlog drains (expected within hours), the queue-age check
   reads green.
8. D9 re-trials of the five.

The order is merge first (Ruling 23) because, with the new code merged and no
v6.1 note yet, the worker refuses and the loop stops at screen, so nothing is
judged between merge and chaining, whereas a note chained before the merge
would let a 20:00 fire of the old code apply the family kill after it and
write an entry that invariant 12 rejects, on an append-only chain.

Rollback: re-enable the loop's gauntlet stage via its flag, which restores the
pre-2a stage, and disable `27_GauntletWorker` and `28_GauntletStats`. Once the
v6.1 note is chained, that stage applies no family kill (it reads the note), so
its verdicts stay valid under invariant 12. v6.1 entries already chained remain
valid under the addendum.
