# Statistics job: training-window memory and a commit-aware gate: design

Status: DRAFT for Coen, 2026-11-05. Not built. Nothing touches the live tree or the chain until Coen approves this text. On approval it moves to `research-layer/docs/` on the build branch.
Decided by Coen 2026-11-05: (1) training windows held as zero-copy numpy views, with the Python list built only inside each consumer; (2) the memory gate requires BOTH free physical memory and free commit to cover the need.

## 1. Why

After the 2026-11-05 per-cell cutover the clustering needs about 0.4 GB. The nightly statistics job (`pipeline/gauntlet_stats.py`, task 28, 01:00) still peaks at about 11.7 GB, and nothing gates that peak.

Measured read-only on chain copies, 2026-11-05. Scripts and raw data are in `stewart-forward-test/_scratch/peak-measure/`. The model reproduces both independent full-run peaks to within 8 MiB:

| stage at n 30,140 | private MiB | delta |
|---|---|---|
| imports | 508 | |
| chain snapshot | 748 | +240 |
| series pass (`dated`: 228.9M points x 12 B) | 3,589 | +2,841 |
| per-cell clustering | 3,882 | +294 |
| `train` dict (gauntlet_stats.py:938) | 11,715 | **+7,833** |
| train_sharpe, sibling_families | 11,745 | +30 |
| PBO loop, verdict loop | 11,745 to 11,750 | 0 to +4.4 (transient) |

The `train` dict holds every registered strategy's whole pre-cutoff history: 206.7M values. They are held as Python floats from `simcache.Series.train(...).tolist()`, at about 40 B per value (an 8 B pointer plus a 24 B float in a 32 B allocator block). numpy holds the same value in 8 B. The dict is 67% of the peak and lives until the end of the run.

By class: equity_etf holds 52% of the values (median 6,984 per strategy), fx 33% (median 13,282, back to 1971), bond_etf 11%. Crypto, intraday included, is under 2%.

Growth: about 306 MiB per 1,000 strategies (5-point fit). It depends on the class mix: about 150 for crypto, about 350 for the registry's mean mix. The peak after the series pass meets the 01:00 free-memory readings around 2027-02 to 2027-04 at the mean mix: about n 88k at 23 GB, about n 130k at 34 GB.

### The commit finding

By day the box had only 10,103 to 12,127 MiB of commit available. About 66 GiB was committed against a 76.5 to 77.6 GiB limit, while about 31 GiB of RAM was free. Two of the measurement's full runs stopped on its own 768 MB commit guard.

The job's gate checks only free physical memory (`gauntlet_core.available_phys_mb`, `ullAvailPhys`). That is right for keeping a working set resident, but it does not stop an allocation from failing. On a night when other processes hold commit, the run would raise MemoryError (`crashed`, exit 1) instead of deferring (`deferred_memory`, exit 3). Commit headroom at 01:00 has never been recorded.

## 2. Goals, non-goals, anti-goals

Goals:
- The post-clustering peak stops growing at about 40 B per training value. Target at today's n: about 4 GB (the series pass plus about 0.3 GB, plus per-family transients). Growth falls to about 0.09 MiB per strategy, the `dated` arrays only.
- A night short of free commit defers cleanly: exit 3 `deferred_memory`, which FAILs the digest as today. It never crashes in the middle of a run.
- Commit headroom becomes visible in the status file.

Non-goals:
- No change to any recorded number, gate, verdict, PBO result or haircut.
- No change to the clustering or `cluster.py`.
- No change to the series pass. Its 2.6 GB of `dated` arrays (12 B per point) stays as it is.

Anti-goals (a failure even if it runs):
- Any `gauntlet_stats` entry that differs in any field from the current code on the same inputs.
- A PBO cache key (`pbo_inputs_sha`) that changes for the same family. A change would recompute every cached null and could make a deadline stop look like a regression.
- A commit check that defers on a night the job would have fitted. The need it charges must be measured, not guessed high.

## 3. The method

### 3.1 Training windows as views (Coen's choice)

1. `simcache.Series.train(cutoff)` keeps its current return type (a list), because other callers use it. Add `Series.train_view(cutoff) -> np.ndarray`.
   - It returns `self.rets[:k]`, a zero-copy view, where k = `np.searchsorted(self.dates, date_ordinal(cutoff), side="right")`.
   - That is valid only when `self.dates` is non-decreasing. The docstring says the dates are chronological, and the method asserts it per series (cheap, one pass).
   - A series whose dates are not non-decreasing falls back to today's boolean-mask copy, `self.rets[self.dates <= c]`. It is counted in the status as `train_view_fallbacks`, so that path is visible rather than silent.
   - The view's values and order equal today's `train(...)` list element for element (test).
2. In `gauntlet_stats._run_locked`, `train` becomes `{sid: dated[sid].train_view(a.cutoff)}`, a dict of views. A Python list is built only at the point of use, and dropped after it:
   - `train_sharpe`: one list at a time inside the comprehension, `_annualized_sharpe_from_returns(train[sid].tolist())`.
   - `group_pbo`: `series = {sid: train[sid].tolist() for the family's members}`. Only one family is alive at a time; the largest family today has 332k values, about 13 MB.
   - `pbo_inputs_sha`: `array("d", train[sid].tolist())`, exactly as today, so the cache key bytes are identical. The builder may use the view's raw bytes instead only if a test proves the key equal on fixtures and on the rehearsal copy.
   - `verdict_stats`: `train[sid].tolist()` for the one verdict.
3. The consumers receive the same Python floats, in the same order, as today. Every recorded value is therefore bit-identical by construction. The proof is still run (section 5).

### 3.2 The gate on physical memory AND commit (Coen's choice)

1. `gauntlet_core.available_phys_mb()` is unchanged. The commit reading reuses the EXISTING `gauntlet_core.available_commit_mb()` (line ~518, `ullAvailPageFile`, already used by `worker_count`); no second reader.
2. The need at the existing admission point (after the series pass, before clustering) is `cluster_need_mb(n, n_max, L)` + `POST_CLUSTER_NEED_MB`.
   - `POST_CLUSTER_NEED_MB` is a constant re-measured at build time: views about 0, plus the largest family's transient lists, plus the verdict loop, plus about 10%. Expected to be a few hundred MB.
   - The pass starts only when BOTH `avail_phys_mb >= need` and `avail_commit_mb >= need`. Otherwise it returns exit 3 `deferred_memory`, as today, and the MEMORY line says which of the two was short.
   - An unknown reading (None) does not block, as today.
3. A second, earlier checkpoint protects the series pass itself (it allocates about 2.8 GB before the existing gate), with commit as the binding limit. My call: without this check, a tight-commit night could still crash in the series pass, before the existing gate.
   - **Where:** before `load_cell_data`, so the check covers the bars load, the series pass and their transients.
   - **What `series_mb` is:** the PEAK commit growth of that span, measured in-process with ctypes `GetProcessMemoryInfo` (PROCESS_MEMORY_COUNTERS_EX). It is `PeakPagefileUsage` read after the series pass, minus `PrivateUsage` read just before `load_cell_data`, in MiB. It is the peak, not the retained delta, because a cold night's simulation transients sit above the retained arrays.
   - **Carry-forward:** recorded with the registered n it was measured at (`series_n`). It is carried forward like `cluster_s`, and charged scaled linearly to tonight's n: `series_mb x n / series_n`, plus 10%.
   - **Partial passes:** a pass stopped at the deadline records nothing and keeps the prior value.
   - **No measurement yet:** 12 B per cached point (the `dated` arrays), plus `bars_by_cell` sized from the cell files, plus 25%.
   - The check defers with exit 3 when `avail_commit_mb` is below that charge. An unknown reading (None) does not block.
4. The status gains `avail_commit_mb` (read at each checkpoint, last value kept), `series_mb` and `train_view_fallbacks`. The Ops Sentinel's existing headroom WARN (`cluster_headroom_warn` 0.6, `need >= 60%` of free physical memory) gains a twin on commit: WARN when `cluster_need_mb >= 60%` of `avail_commit_mb`. This is a small change in `sc-ops-sentinel`, merged in the same cutover.

## 4. What does not change

- The series pass, the clustering, `cluster_cells`, `cluster.py`, the floors, SR*, the DSR series and every gauntlet gate.
- No chain note is needed: no recorded number or method changes. The method string `cluster_method` stays the same.
- Exit codes keep their meanings. Exit 3 still means "not enough memory to run safely", now physical OR commit.

## 5. Verification (what the builder must show)

- `train_view` equals `train` element for element on fixtures. These include a series whose last date equals the cutoff, a series with no date at or before the cutoff (empty), and a series with non-monotonic dates (fallback taken, counted).
- `pbo_inputs_sha` is unchanged for the same family. The test compares old and new code on a fixture family.
- **Identity proof on the real registry.**
  - **Baseline:** the SAVED 11-05 rehearsal of the current method, `stewart-forward-test/_scratch/etc-rehearsal/run/rehearsal.json`. It holds 300 payloads plus `moments`, on the 11-05 prefix at n 30,140, vintage 2026-11-01. The old code does not need re-running by day (its 11.7 GB peak would meet the day's 10-12 GB of commit headroom).
  - **The run:** the branch, with `tools/stats_rehearsal.py`, on the same prefix (`_scratch/etc-rehearsal/`), its scratch `logs/` seeded with the same PBO cache copy that run used, the same `--pbo-null-draws` and a deadline large enough that nothing stops.
  - **What must match:** only `payloads` and `moments`, compared field by field as canonical JSON (sorted keys). They must be identical. Fields that differ between any two runs are not compared: `wall_s`, `memory_end`, `argv`, `pipeline_file`, the status timestamps.
  - **Precondition:** each payload's `data_digest` must equal the baseline's, or the comparison is void. So the proof runs before the Sunday 2026-11-08 vintage roll. If it slips past that, a fresh baseline is needed: the old code re-run on a night-time or high-commit window that Coen approves.
  - **Also record:** the branch's peak (expected about 4 GB, against the baseline's 11,745 MiB) and `series_mb`.
- **Gate:**
  - a stubbed `available_commit_mb` below the need makes the run defer with exit 3, with a MEMORY line naming commit;
  - the same stub on physical memory names physical;
  - None on both does not block;
  - the earlier series-pass checkpoint defers on short commit.
- Mutation checks (Python string-replace, ast.parse, the target test path confirmed):
  - `train_view` returns `rets[:k+1]`;
  - drop the `.tolist()` in `pbo_inputs_sha`, if that changes the key;
  - the gate checks only physical;
  - `series_mb` is not carried forward.
- `pipeline/cluster.py` diff is empty.

## 6. Cutover

The same pattern as 2026-11-05:
1. Build and review in a worktree.
2. Run the rehearsal identity proof, by day, outside 07:58-08:40 and 20:00-07:00.
3. Coen approves the merge.
4. Merge to live under the worker git-step rules.
5. Merge the Sentinel change.
6. The next morning, check: task 28 exit 0, peak (from the status `series_mb` and the log) well under the old 11.7 GB, `avail_commit_mb` recorded, entries unchanged in shape.

No chain note, so rollback = revert the merge. The ratchet does not apply, because no recorded number changes.

## 7. Left to the builder

- Function names beyond `train_view` and `available_commit_mb`.
- How `POST_CLUSTER_NEED_MB` is measured (the rehearsal's stage markers, like the 11-05 measurement).
- Whether the Sentinel commit WARN shares the 0.6 ratio or takes its own key. Default: its own key `commit_headroom_warn: 0.6`.

## 8. Open questions for Coen

None. Both decisions are recorded above, and the gate ships ON at the first cutover, as Coen chose: "gate on both", not "record only".

The builder check (2026-11-05) raised three points, settled as my calls:
- the identity proof compares `payloads` and `moments` only, against the saved 11-05 baseline, before the 11-08 vintage roll (section 5);
- `series_mb` is the in-process peak commit growth, charged scaled to n (3.2 item 3);
- the existing `available_commit_mb` is reused.

The earlier commit check before the series pass (3.2 item 3) is also my call. Strike it if you only want the single existing checkpoint.

Risk, stated: commit headroom at 01:00 has never been recorded. If it is routinely below about 4.5 GB, the gate would defer nightly (exit 3, digest FAIL). By day it was 10-12 GB. After the fix the whole need is about 4.4 GB.
