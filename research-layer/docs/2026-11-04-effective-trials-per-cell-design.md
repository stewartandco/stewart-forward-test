# Effective trials per cell: design

Status: DRAFT for Coen, 2026-11-04. Not built. Nothing merges without Coen.
Method chosen by Coen 2026-11-04: the existing average-linkage tree built
once per cell, each tree cut at a fixed correlation of 0.9, counts summed.
(Coen first picked "per cell, silhouette argmax, summed"; measured, that
gave k = 609 under the 3,046 floor, which would freeze the recorded N and
SR*. Shown the numbers in section 9, he chose the rho 0.9 cut, and then
one "never traded" cluster per cell, section 4 step 2a.) Measured on
2026-11-04: k 6,082, SR* 0.1108 (floors 3,046 / 0.10317).

## 1. Why

The nightly statistics job (`pipeline/gauntlet_stats.py`, task
`\Morpheus\28_GauntletStats`, 01:00) clusters every registered strategy in
one pass (`gauntlet.cluster_registry` -> `cluster.effective_trials`). That
pass holds an n x n float64 matrix, and the memory gate (`f6ac6d6b8`)
starts it only when free physical memory covers 9 n^2 + 48 n L bytes. n
grows by about 484 registrations a day (30-day mean to 2026-11-03, in bursts
of up to 2,000). Coen ruled out more RAM (2026-11-01). Sign-off 2 row 3
passed Switch 2 on the condition that the counting method changes before
the crossing, or Switch 2 pauses.

A bit-identical memory cut of the current method is impossible (a
row-blocked Gram matrix differs in the last bit and changes the merge
history, measured 2026-11-01). So the count itself has to change.

### The deadline is probably earlier than 12-23

The 12-23 date assumed 33,626 MB free at 01:00. Measured since:

| night | n | need MB | free MB |
|---|---|---|---|
| 10-26 | 25,475 | 8,665 | 36,025 |
| 10-27 | 25,835 | 8,912 | 33,626 |
| 10-28 | 26,835 | 9,615 | 35,524 |
| 11-02 | 26,885 | 8,567 | 27,564 |
| 11-03 | 27,180 | 8,731 | 24,898 |
| 11-04 | 29,180 | 9,875 | 22,956 |

At about 23 GB free the gate stops fitting near n = 46,850 with the gate's
own L bound (`cluster_days_bound`, the shortest cached series, reads about
1,920 days against 1,308 aligned), about 29 days from 11-04 at the 610 a
day basis. **Planning date: 2026-12-03** (unless free memory recovers; at
the 30-day mean of 484 a day and L = 1,308 it would be about 12-13). The drop does not follow n (n rose by 50 between 10-28 and
11-02 while free memory fell 8 GB). The likely cause, a lead and not proven:
the TradingView desktop app, running since 2026-10-29 10:07, held 13.2 GB
working set / 16.0 GB private at 11:35 on 11-04. Restarting it is Coen's
action. This design removes the n^2 term whatever the cause.
Update 2026-11-05: free memory at the 01:00 run recovered to 34,277 MB (n
30,140). The TradingView app stays the lead for the earlier drop, not
proven. The 12-03 planning date stands as written.

## 2. What the count feeds today (verified at source 2026-11-04)

- `trials_n` = max(this run's k, the highest k on the chain). Floor today:
  3,046 (11-04 raw = 3,046). Used by the recorded deflated Sharpe (via SR*)
  and the Harvey-Liu haircut.
- `trials_sr_var` = sample variance of the cluster representatives' Sharpe
  (each representative = the mean daily Sharpe of its members).
- SR* = max(expected_max_sharpe(trials_n, trials_sr_var), the highest SR*
  on the chain). Floor today: 0.10317.
- `deflated_sharpe` = psr(Sharpe, SR*, T, skew, kurt) on the registry-wide
  aligned series (Ruling 11).
- Nothing gates on any of these. v6.1 verdicts carry no registry-wide
  statistic. Sibling ranking by DSR was retired in protocol v6
  (`gauntlet.select_survivors` returns every pass; `sibling_not_selected` is
  retired). Morpheus only displays `deflated_sharpe`.

So the change moves recorded numbers only. No verdict, state change,
quarantine, paper or live decision reads them.

## 3. Goals, non-goals, anti-goals

Goals
- The nightly clustering memory no longer grows with n^2 of the whole
  registry: it grows with the largest single cell's n^2.
- The recorded N never falls (Ruling 20 floor kept) and the recorded SR*
  never falls (Ruling 35 floor kept).
- Same determinism and order independence as today.
- Lands, chained note included, before the crossing (target: merged by
  2026-11-24, leaving a week of margin before the 12-03 planning date).

Non-goals
- No change to any gate, verdict, the worker, quarantine, PBO, plateau, or
  the haircut formula.
- No change to the series the deflated Sharpe is computed on (still the
  registry-wide aligned series, Ruling 11).
- No change to `cluster.py`'s existing functions.
- No restatement of the 7,387 `gauntlet_stats` entries already chained.

Anti-goals (a failure even if it runs)
- A recorded N or SR* lower than the chain's floor.
- Any per-entry field that grows with the number of cells on EVERY entry
  (about 1,000 entries a night; the chain size budget matters,
  `project_registry_log_size_2026-10-09`).
- A per-cell result that depends on cell iteration order, or, GIVEN the
  aligned series, on which other cells exist. (The aligned window itself
  is registry-wide, as today: when a late-starting cell is registered the
  intersection shortens and every partition's correlations move. That is
  existing behaviour, kept on purpose so the measured figures, the memory
  model and the DSR window stay as they are. My call.)

## 4. The method (Coen's choice: per cell, cut at rho 0.9)

1. Build the aligned series exactly as today (`cluster_registry`'s
   alignment: intersection when classes are mixed or calendars ragged,
   native otherwise). Same rows, same days, same L.
2. Partition the rows by cell key: for each strategy,
   `"|".join(sorted(qcells.spec_cells(spec)))`. A multi-asset spec (the
   legacy BTCUSD+ETHUSD pair) is its own partition. An intraday spec's key
   carries its timeframe (`BTCUSDT@1h`), so intraday twins sit apart from
   their 1d originals. 63 partitions on 2026-11-04, largest 1,068.
2a. Never-traded rows (Coen, 2026-11-04). In each partition, rows whose
   aligned series has zero variance (all exact 0.0: the strategy never
   trades in the window) are taken out before the tree and form ONE
   cluster of their own, when the partition has any. Its representative
   Sharpe is 0.0, entered once in the pooled variance. Why: identical
   behaviour is one trial (the same principle as the duplicate pin), and
   273 identical Sharpe-0 representatives were shrinking the variance.
   Measured 2026-11-04: 273 such rows in 15 partitions (EEM 40, EWJ 35,
   EWZ 30, ...). Under the unchanged distance each of them sits at sqrt(0.5)
   from every row, so taking them out changes no other merge below t.
2b. A constant NONZERO row (no such row on 2026-11-04) makes the run
   refuse: exit 1, `exit_reason` `cluster_refused`, nothing chained. Today
   `effective_trials` sends such input to the pure-Python reference path
   because the two paths can classify its variance differently, and that
   path is far too slow for a 1,000-member partition. A refusal FAILs the
   digest, so it is loud; what to do with such a row is then a protocol
   question for Coen. (My call.)
3. In each partition, build the merge history of the remaining rows with the unchanged
   `cluster._distance_matrix_np` and `cluster._agglomerate_np` (same
   distance, same average linkage, same deterministic tie-break). Record
   each merge's height: the average-linkage distance between the two
   clusters at the moment they merge, computed on that same distance
   matrix.
4. Cut each partition's tree at the fixed distance
   t = sqrt(0.5 x (1 - 0.9)) = 0.2236..., the distance that corresponds
   to a correlation of 0.9. A merge counts when round(height, 12) <=
   round(t, 12) (the same 12-decimal rounding as the tie-break key).
   The merges that count are the PREFIX of the history up to the first
   merge whose rounded height is over t; partition k = (remaining rows)
   minus that prefix's length, plus 1 if the partition has never-traded
   rows (2a), and `cluster.labels_for_k` gives the labels. Average linkage
   has no inversions under global-minimum merging, so in exact arithmetic
   nothing at or under t comes after the first merge over t. In floats a
   later merge can land an ulp under (the 2e-12 tie window, `S[keep] +=
   S[drop]` sums): such merges do NOT count, and the run reports how many
   there were (`cut_late_merges`, status and run log only, never on
   entries) so a nonzero value is visible. (My call.) A 1-member partition
   gives k = 1. A 2-member partition gives k = 1 when its one merge counts,
   else 2. (No forced minimum of 2, unlike the silhouette path.)
5. `trials_n_raw` = the sum of the per-partition k.
6. Global labels: partitions in sorted key order, each partition's labels
   offset by the k of the partitions before it.
7. `trials_sr_var` = `cluster._reps_variance` over ALL rows with the global
   labels (every cluster representative of every partition pooled in one
   sample variance).
8. Floors, SR*, DSR, haircut: unchanged code, fed by 5 and 7.
9. The rho 0.9 threshold is a named module constant (`CUT_RHO = 0.9`) and
   is recorded on every entry. Changing it is a protocol change (a new
   chained note), never a tuning knob.

The silhouette search (`_effective_trials_np`'s replay) is no longer run by
the statistics job. That also ends the unstable argmax (302 -> 480 -> 44,
Task 8, 10-01): a fixed cut moves smoothly as strategies arrive.

Effects:
- Correlated strategies in different cells (SPY vs QQQ twins, an intraday
  twin and its 1d original) count as separate trials. Measured, this
  inflates k by about 10% against a whole-registry count at the same
  threshold (section 9).
- k rises above the 3,046 floor (measured 6,082), SR* rises above its floor
  (0.10317 -> about 0.1108), and every recorded DSR falls a little: a
  stricter recorded number.
- The k jump is a ratchet. Once a k of about 6,082 is chained, the floor
  holds it: a later rollback to the old method cannot lower the recorded N
  or SR*. Rollback restores the code, not the number.
- A fixed cut is not strictly monotone (a new member can pull two clusters'
  average under t and merge them), so a night's raw k can dip by a few;
  the floor absorbs it, as today.

## 5. Memory and time

Per pass, only one partition's n_c x n_c matrix is alive. The gate becomes:

need = 9 x n_max^2 + 48 x n_max x L + c_NL x n x L bytes

where n_max is the largest partition and the last term is the aligned
series held across all partitions (today's "8 B per n L" for
`cluster_registry`'s copies, plus whatever the partition slicing copies).
The builder re-measures c_NL on the real matrix, as on 2026-11-01, and
rounds up about 10%. At n = 100,000 and L = 1,400 the n L term is about
1.1 GB at 8 B; the n_max term at n_max = 5,000 is about 0.5 GB.

The time estimate scales by the sum of n_c^2 instead of n^2: the status
gains `cluster_sum_n2` (carried forward with `cluster_s`), and
`cluster_estimate_s` scales `cluster_s` by the ratio of sums.

The gate stays (exit 3 `deferred_memory` keeps its meaning).

## 6. What goes on the chain

- `cluster_method` = `"effective_trials/v4 per-cell average-linkage cut
  rho>=0.9"`.
- Three new fields on each entry: `trials_partitions` (the number of
  partitions), `trials_largest_partition_n` and `trials_cut_rho` (0.9). No per-cell breakdown on
  entries (anti-goal); the breakdown goes to the status file's report
  (`--report`) and the run log.
- A protocol note, `gauntlet-protocol-v6.1-amendment-2:` as its first line,
  chained VERBATIM after Coen approves the exact text, BEFORE the first
  per-cell entry. `--chain` refuses (exit 1, a new exit_reason
  `refused_no_amendment2_note`) until it is on the chain, the same pattern
  as amendment-1. The note states the method, the ratchet, the
  measured k / SR* / DSR shift, and that earlier entries stand.
- `FLOOR_PROTOCOLS` is unchanged: the floor reads `trials_n_raw` from
  earlier `gauntlet_stats` entries, which still holds a cluster count.

## 7. Verification (what the builder must show)

- The merge history inside each partition is bit-identical to
  `cluster._agglomerate_np` on that subset (it IS that function; the test
  pins that no copy was made).
- Heights: on a small fixture, each recorded height equals a brute-force
  average of the pairwise distances between the two merged clusters; the
  heights are non-decreasing.
- The cut: hand-checked fixtures with a pair at rho just above, at, and
  just below 0.9; a 2-member partition merging and not merging; a
  1-member partition.
- Cross-check: on a real-data copy, k per partition matches
  `scipy.cluster.hierarchy.fcluster(linkage(..., "average"), t,
  "distance")` except where a tie sits exactly at t (report any).
- Order independence: on a FIXED aligned matrix, the result does not
  depend on row order, partition order, or the presence of other
  partitions' rows.
- Never-traded rows: a partition with several zero rows gives exactly one
  extra cluster with representative Sharpe 0.0; a partition of only zero
  rows gives k = 1.
- A constant nonzero row refuses the run (`cluster_refused`, exit 1).
- A forced late merge (a fixture or monkeypatched height sequence with a
  merge under t after one over it) is not counted and shows in
  `cut_late_merges`.
- Pooled variance equals a hand computation on a small fixture.
- Floors: a fixture where the per-cell sum is below the chain floor records
  the floor; above it records the sum.
- `--chain` refuses without the amendment-2 note; report-only runs work.
- Gate: `cluster_need_mb` uses n_max; a mutation that puts n back fails a
  test.
- Mutation checks per `Feedback/feedback_mutation_testing_discipline.md`
  (Python string-replace, ast.parse, confirm the test path).
- `tools/verify_cluster_identity.py` is re-run if `cluster.py` is touched at
  all (this design does not touch it).
- A report-only run against a COPY of the chain before the merge, giving
  k, var, SR*, the DSR shift, peak working set and duration, compared with
  the night before.

## 8. Cutover

Order: build and review in a worktree -> report-only run on a chain copy ->
Coen approves the amendment-2 text -> merge to live 07:00-20:00, outside
07:58-08:40, under the live-tree git rules (no worker git step in flight,
no locks) -> chain the note -> the next 01:00 run writes the first per-cell
entries -> check that morning's status and digest.

Docs in the same change: `research-layer/CLAUDE.md` (Gauntlet worker
section: method, gate formula, ratchet; the Switch 2 PAUSE clause tied to
the crossing is retired), the module docstrings, the vault note.

Ops Sentinel (separate repo `sc-ops-sentinel`): the `cluster_n_warn`
threshold at 46,000 stops meaning anything once n no longer drives the
need. The headroom WARN merged 2026-11-04 (`ac61861`, `cluster_headroom_warn`
0.6: WARN when `cluster_need_mb` >= 60% of `avail_phys_mb`) already reads
the status's need and so keeps working unchanged on the per-cell need. The
change here: remove `cluster_n_warn` from the manifest and its pin test, in
the same cutover as this one.

Rollback: revert the merge. The code goes back to one registry-wide pass
(and its memory wall); the recorded N stays at the per-cell floor (section
4, ratchet).

## 9. Measured on the 2026-11-04 registry (read-only, chain copy)

Inputs: 29,180 registered strategies, vintage 2026-11-01, every series a
simcache hit (no simulation, no cache write), intersection-aligned L =
1,308 days, 63 partitions (largest IWM, n 1,068). Tonight's registry-wide
method: k 3,046, SR* 0.10317 (= the floor). Per-cell clustering took 12 s
for the silhouette path. Cut figures are from scipy's average linkage on
the same distance matrix (estimates; the build uses cluster.py's merge
order, section 7 cross-check).

| method | k | trials_sr_var | SR* raw | recorded |
|---|---|---|---|---|
| per cell, silhouette argmax, summed | 609 | 0.000376 | 0.0603 | frozen at floor |
| per cell, cut rho 0.95 | 9,593 | 0.000829 | 0.1109 | stricter |
| **per cell, cut rho 0.9, never-traded = one per cell (chosen)** | **6,082** | **0.000878** | **0.1108** | **stricter** |
| per cell, cut rho 0.9, never-traded each its own | 6,340 | 0.000844 | 0.1088 | stricter |
| per cell, cut rho 0.9, never-traded excluded | 6,067 | 0.000880 | 0.1109 | stricter |
| per cell, cut rho 0.8 | 3,375 | 0.000825 | 0.1030 | N up, SR* at floor |
| per cell, cut rho 0.7 | 2,044 | 0.000800 | 0.0976 | frozen |
| per cell, cut rho 0.5 | 966 | 0.000627 | 0.0813 | frozen |
| whole registry, streaming rho 0.9 | 5,777 | 0.000850 | 0.1086 | stricter, order-dependent |
| whole registry, streaming rho 0.7 | 1,696 | 0.000811 | 0.0969 | frozen |

Read: SR* barely moves with the threshold (0.103 to 0.111 from rho 0.8 to
0.95), so the choice is about what counts as one idea, not about the
number. Treating cells separately adds about 10% to k against a
whole-registry count at the same rho (6,340 vs 5,777, both with never-traded
rows as their own trials). Zero-variance rows on 2026-11-04: 273 in 15
partitions; constant nonzero rows: 0. No registered
strategy has a recorded DSR >= 0.95 under either SR*; the median DSR is
about 3e-5.

Scripts and raw output (scratchpad, not committed):
`measure_per_cell.py`, `measure_variants.py`, `per_cell_result.json`,
`variants_result.json`.

### Addendum 2026-11-05: rehearsal on the live registry

The figures above are kept as history. The built code was run report-only
on a copy of the chain on 2026-11-05 (`tools/stats_rehearsal.py`, live data
and simcache read only, nothing chained), re-statting the 300 verdicts of
that night's 01:00 run on the same registry, vintage and data. Run report:
`stewartandco-agents/hubs/intelligence/research-layer-internals/docs/runs/2026-11-05-effective-trials-rehearsal.md`.

| figure | 11-04 (above, scipy estimate) | 11-05 (built code) |
|---|---|---|
| n (series clustered) | 29,180 | 30,140 |
| partitions (intraday) | 63 (0) | 111 (36) |
| largest partition | IWM 1,068 | IWM 1,068 |
| L (aligned days) | 1,308 | 1,003 |
| registry-wide method k / SR* (= the chain floors) | 3,046 / 0.10317 | 4,320 / 0.12533 |
| per-cell raw k | 6,082 | 6,183 |
| trials_sr_var | 0.000878 | 0.001293 |
| per-cell SR* raw | 0.1108 | 0.13453 |
| never-traded rows (partitions) | 273 (15) | 375 (15) |
| late merges | not measured | 0 |
| DSR median, same verdicts, old -> new | about 3e-5 (no shift measured) | 1.42e-5 -> 3.83e-6, all 300 fall |
| DSR >= 0.95 | 0 | 0 before, 0 after |
| clustering memory | 9,875 MB need (old method) | +332 MB measured (old method's need 9,831 MB) |

Why the numbers moved: the first intraday registrations (Switch 2, from
11-02) start later than the daily cells, so the intersection-aligned window
shortened from 1,308 to 1,003 days. Every partition's correlations moved
with it, and the shorter window raised the pooled variance of the cluster
representatives (0.000878 to 0.001293), which raises SR*. The registry-wide
method saw the same shorter window the same night: its own k and SR* rose to
4,320 and 0.12533, and those are the chain floors the per-cell figures are
compared with. The per-cell k and SR* sit above both floors, so the first
per-cell entries would record the per-cell figures. scipy's average linkage
cut at the same distance agreed in all 111 partitions. The memory constant
for the aligned copies was re-measured at the same time (`CLUSTER_BYTES_PER_ALIGNED_NL`
10.0 -> 11.5 B per n L).

## 10. Left to the builder

- Module placement (new `pipeline/cluster_cells.py` preferred, so
  `cluster.py` stays untouched), function names, and how
  `gauntlet_stats` calls it (the tests that pin "the job calls the
  gauntlet's own functions" must keep holding, or be updated with a reason).
- How the merge heights are obtained without editing `cluster.py`
  (default: replay the returned history on a recomputed distance matrix,
  as `_effective_trials_np` already replays it, reading
  S[keep, drop] / (size_keep x size_drop) before each merge).
- The re-measured gate constants.
- Whether `gauntlet.run` (the hand-run v6 path) keeps the old registry-wide
  call. Default: yes, untouched; it is not scheduled.
