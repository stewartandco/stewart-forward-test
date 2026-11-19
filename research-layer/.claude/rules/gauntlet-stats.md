---
paths:
  - "pipeline/gauntlet_stats.py"
  - "pipeline/test_gauntlet_stats.py"
  - "pipeline/cluster*.py"
  - "pipeline/test_cluster*.py"
  - "pipeline/pbo.py"
  - "pipeline/test_pbo.py"
  - "pipeline/test_verify_v61.py"
  - "pipeline/gauntlet.py"
  - "pipeline/simcache.py"
  - "pipeline/gauntlet_worker.py"
  - "tasks/run_gauntlet_stats.bat"
  - "tools/gauntlet_parity.py"
  - "tools/test_gauntlet_parity.py"
  - "tools/stats_rehearsal.py"
  - "tools/test_stats_rehearsal.py"
  - "tools/verify_cluster_identity.py"
  - "verify_registry.py"
  - "docs/notes/gauntlet-protocol-v6.1-amendment-*.md"
---
## Gauntlet worker + recorded statistics (continued from `gauntlet-worker.md`): the statistics job, status files, exit codes, verifier, parity
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
  does not fit is a deadline stop. **Since the per-cell change the
  clustering estimate is `cluster_s` x (sum of n_c^2 over partitions now /
  `cluster_sum_n2`)** (`cluster_sum_n2` = the sum it was measured at,
  carried forward with `cluster_s` and `cluster_n`; a status with no
  `cluster_sum_n2`, as the one the registry-wide method wrote, gives the
  UNSCALED `cluster_s`: the first night after cutover never deadline-stops a
  pass that takes seconds), **and the pass starts only when AVAILABLE
  PHYSICAL memory AND free COMMIT each cover `CLUSTER_BYTES_PER_N2` (9) x
  n_max^2 + `CLUSTER_BYTES_PER_NL` (48) x n_max x L +
  `CLUSTER_BYTES_PER_ALIGNED_NL` (11.5) x n x L bytes, plus
  `POST_CLUSTER_NEED_MB` (512)** (`cluster_need_mb(n, n_max, days)` is the
  clustering part alone, and so is the status's `cluster_need_mb`; n_max =
  the largest cell partition's series count, read from the specs BEFORE
  clustering; n = all series, whose aligned copies live through the pass; L
  bounded above by the shortest cached series, `cluster_days_bound`;
  `gauntlet_core.available_phys_mb` and `available_commit_mb`, both read
  after the series pass; unknown = no block, on either limit; a second,
  earlier checkpoint guards the series pass, bullet "Train windows as views"
  below). 11.5 measured 2026-11-05 (rehearsal on a chain copy, n 30,140,
  L 1,003, n_max 1,068): the pass added 332 MB, 257 MB of it the aligned
  copies; (332 - 33 MB for the largest cut) / n L = 10.4 B, plus about 10%.
  The need was 9,831 MB the same night under the old method. Before the
  per-cell change (history): the estimate was `cluster_s` x (n /
  `cluster_n`)^2 and the gate 9 x n^2 + 48 x n x L, re-measured 2026-11-01
  on the REAL aligned matrix (n 26,835, L 1,303: peak 6,896 MB = 8.4 n^2 +
  34 n L, plus cluster_registry's aligned copies at 8 B per n L and about
  10%); it was 14 x n^2 from 2026-10-11, one reading at 13,730 that folded
  the n L term into n^2. The
  merge loop touches every page of its matrix on every merge, so a
  partly paged-out pass thrashes: on 2026-10-11 n = 19,092 needed ~4.9 GB
  with 1.4 GB free, sat 5.8 GB committed / 44 MB resident, and outlived
  PT6H. Short of memory, physical OR commit, at either checkpoint =
  **exit 3** `deferred_memory` (status `cluster_need_mb`, `avail_phys_mb`,
  `avail_commit_mb`; series already simulated stay cached; the next run
  retries). **Nonzero on purpose (Coen, 2026-10-11, option b): task 28 is
  `daily` in the Sentinel with no tolerated codes, so a memory deferral
  FAILs the 09:15 digest the same morning** instead of surfacing only as
  the 7-day stats-lag WARN. A repeating `deferred_memory` is a capacity
  question for Coen, not a constant to raise, and it is THE PAUSE trigger
  (section "Switch 2"). Under the registry-wide method the gate would have
  stopped fitting near n 46,850 at about 23 GB free (planning date
  2026-12-03; 34,277 MB free at 01:00 on 2026-11-05); per cell, the need at
  n 30,140 is about 390 to 570 MB and grows with n_max^2 plus a linear n L
  term. **DONE 2026-11-05 (was a FOLLOW-UP): the whole job's peak AFTER
  clustering.** It was 11,745 MB private at n 30,140, ungated and growing
  linearly with n (it would have crossed the 01:00 free memory around
  2027-02 to 04). Cause: the train windows held as Python float lists
  (about 40 B a value, 206.7M values) for every strategy. Fix: the windows
  are zero-copy numpy views, a list built only inside each consumer
  (bullet "Train windows as views" below). Measured on the same 11-05
  prefix: whole-process peak private 4,069 MiB, set in the bars load and
  series pass, never exceeded after clustering.
  **A second clock beside it: the full-bars memo ceiling (final review M3).**
  Task 12 measured 5,741,210 bars over the 228 intraday files (data to
  2026-10-31); they grow 2,432 a day (76 cells x 32 bars), so once every
  intraday cell has a quarantined strategy the touched total passes
  `FULL_BARS_MEMO_CEILING_BARS` (6,000,000) about 2027-02-14. That is time, not
  correctness (results are identical; quarantine goes back to one parse per
  strategy-date-cell). The alarm is the `WARN full-bars memo: touched N bars >
  ceiling 6,000,000` line the outermost quarantine run prints, plus one
  `memo_ceiling` item in `degraded_quarantine.json` (the Sentinel WARNs, and
  FAILs after 3 days).
  **Early warning.** The Ops Sentinel's `gauntlet_queue` check WARNs when
  the clustering need >= `cluster_headroom_warn` (0.6) x `avail_phys_mb`
  (sc-ops-sentinel `ac61861`, 2026-11-04); it reads the status's need and so
  works unchanged on the per-cell need. The n line (`cluster_n_warn`
  46,000, plan 5, `9bf6637`) is retired in the same cutover as the per-cell
  change (sc-ops-sentinel branch `feat/retire-cluster-n-warn`): n no longer
  drives the need. Its twin on commit, `commit_headroom_warn` (0.6): WARN
  when the clustering need >= 0.6 x `avail_commit_mb`, and under the same
  key when `series_charge_mb` >= 0.6 x `avail_commit_series_mb` (the series
  pass, the binding commit need: about 3.7 to 4.4 GB against a clustering
  need of 348 to 570 MB) (sc-ops-sentinel branch
  `feat/commit-headroom-warn`, merged in the train-window cutover). The
  clustering need both ratios compare is `gate_need_mb` (`cluster_need_mb`
  + 512, what the gate compares), falling back to `cluster_need_mb` on a
  status written before that field existed (Controller Ruling 8): against
  the 512-less figure a WARN led a deferral only when the need was over
  768 MB, so at today's need it fired only on nights the gate had already
  deferred. All are warnings, not stops.
  The memory is one n_max x n_max float64 matrix at a time (the largest
  partition's), plus the merge history's member sets (about 0.01 n^2
  entries measured on the registry-wide pass, so about 0.01 n_max^2 per
  partition, more if the merge tree turns chain-like), which the gate covers
  only through its margin. The
  pure-Python reference path (`cluster.effective_trials` sends a constant
  nonzero series there) is far heavier and is NOT covered by the gate; the
  per-cell code never takes it: such a row raises `ConstantNonzeroRow` and
  the run refuses (`cluster_refused`, exit 1, nothing chained), a protocol
  question for Coen. The registry-wide matrix could not be cut
  without changing the clustering's float results (a row-blocked Gram
  differs from the full one in the last bit and changes the merge history,
  measured 2026-11-01), which is why the count itself changed (bullet
  "Effective trials per cell" below). A clustering deferral comes only
  after the full series pass, a series-pass deferral before anything is
  simulated; either FAILs that morning's digest (exit 3). A digest FAIL
  whose task-28 result is 3 means "not enough memory (physical or commit)
  to run safely", not a crash: read the `MEMORY:` line in
  `logs/gauntlet-stats-run.log`, which names the checkpoint and the short
  limit. One instance at a time
  (`logs/gauntlet_stats.lock`): a second one, e.g. a hand `--chain` run beside
  the 01:00 task, defers with exit 0 and never touches the status file.
  The status reports `trials_n_raw` (this run's per-cell sum of k; the
  registry-wide argmax before the per-cell change) and
  `trials_n_effective` (the floored N the entries use).
- **Train windows as views + the memory gate on physical AND commit (design
  `docs/2026-11-05-stats-train-window-memory-design.md`, Coen 2026-11-05).**
  No recorded number, PBO cache key or method changes: no chain note, and a
  rollback is a revert of the merge. `simcache.Series.train_view(cutoff)`
  returns `(window, fell_back)`: the zero-copy prefix `rets[:k]` (k from a
  right-side searchsorted, so the cutoff date is included) when the dates
  are non-decreasing, else the old mask copy with `fell_back` True. The job
  keeps every window as a view and builds a Python list only inside each
  consumer, dropped after it: one at a time for `train_sharpe`, one
  family's in `family_train` (at `group_pbo`'s call site), per member in
  `pbo_inputs_sha`, and the verdict's own. Nothing writes into a view.
  Identity measured 2026-11-05 (stats rehearsal on the 11-05 prefix, n
  30,140, report stewartandco-agents
  `hubs/intelligence/research-layer-internals/docs/runs/2026-11-05-stats-train-window-rehearsal.md`):
  all 300 payloads and 30,140 `moments` rows byte-identical to the current
  method's, same `data_digest`; peak private 4,069 MiB against 11,745;
  `series_mb` 3,324 MiB.
  **Two checkpoints, each exit 3 `deferred_memory` when short.** (1) Before
  `load_cell_data`, COMMIT only (residency matters to the merge loop, not
  to an allocation): free commit must cover the series charge,
  ceil(max(`series_rate_hist`) x tonight's registered n x
  `SERIES_CHARGE_MARGIN` 1.10), or with no history the fallback (12 B x n x
  7,000 points + the summed bytes of the cell CSVs to load) x
  `SERIES_FALLBACK_MARGIN` 1.75, 4,356 MiB at n 30,140 (the plan's 1.25
  gave 3,112, under the measured 3,324 x 1.25; Ruling 6). Nothing has been
  simulated when it defers. (2) Before clustering, physical AND commit,
  each against `cluster_need_mb` + `POST_CLUSTER_NEED_MB` (512, kept by
  the rehearsal: after clustering the private commit rose at most 2.6 MiB
  sampled every 10 ms and never set a new peak, so the growth is bounded
  by 219 MiB). Exit 3 means physical OR commit was short at either point.
  An unknown reading (None: off Windows or a failed call) never blocks, on
  either limit, at either checkpoint. The `MEMORY:` line names the
  checkpoint and the short limit: `MEMORY: series pass over N registered
  strategies needs ~X MB of commit (<basis>) and Y MB of commit is
  available; commit is short ...` (basis: the max rate, the pass count, n
  and the margin, or "no measurement yet: the fallback estimate"), or
  `MEMORY: clustering N series (largest partition M) needs ~T MB (C
  clustering + 512 after it); physical memory is short (...)` and/or
  `commit is short (...)`.
  **Status fields.** `avail_commit_mb`: the LAST checkpoint's reading (the
  series pass's, then the clustering's), null when unknown or not reached.
  `series_mb` / `series_n`: the last completed pass's peak commit growth
  (`PeakPagefileUsage` after the pass minus `PrivateUsage` just before
  `load_cell_data`, `gauntlet_core.process_memory_mb`, in-process MiB) and
  the registered n it was measured at; raw last-night figures, carried
  forward like `cluster_s` / `cluster_n` until a pass completes, display
  only (the charge does not read them). `series_rate_hist`: MiB per
  strategy of the last `SERIES_RATE_HIST_MAX` (8) completed passes, newest
  last; the charge reads its MAX (Ruling 5), so a warm night (cache hits)
  never lowers the charge below the last vintage roll's cold pass, which
  stays in the window for at least a week; a deadline-stopped pass appends
  nothing. `train_view_fallbacks`: how many series took the mask copy
  because their dates were not non-decreasing; 0 on a clean run, anything
  else is a corrupt or legacy cache entry worth a look (the values are
  still right). `cluster_need_mb` is the clustering figure alone (it
  excludes the 512). The three checkpoint fields (Controller Ruling 8), each
  null when its checkpoint was not reached: `gate_need_mb`, the need the
  clustering checkpoint actually compares (`cluster_need_mb` +
  `POST_CLUSTER_NEED_MB`); `series_charge_mb`, the charge the series
  checkpoint compared; `avail_commit_series_mb`, that checkpoint's commit
  reading (`avail_commit_mb` stays the LAST reading). A series-pass deferral
  records the series pair and leaves `gate_need_mb` and `cluster_need_mb`
  null. The Sentinel's headroom WARNs read `gate_need_mb` and the series
  pair (bullet "Early warning" above).
  **Morning check: a higher `series_mb` is not a regression by itself.** It
  grows with the intraday cell set (Switch 2: 228 intraday files, about 5.7M
  bars held as dicts) and with each new registered cell, not with n alone.
  The charge reads the rates of passes already completed, so after a
  cell-set jump it lags one night: the first night on the larger set is
  charged at the old rate, and the next night's charge includes it.
  **HAZARDS.** (a) `train_sharpe`'s `.tolist()` is REQUIRED for
  bit-identity: Python's `sum()` compensates exact Python floats only and
  adds np.float64 items plainly, so handing it the view moves the train
  Sharpe in the last bits (`test_recorded_stats_match_the_v6_gauntlets_own_numbers`
  and the BASE payload digest pin fail). `pbo_inputs_sha`'s `.tolist()` is
  kept to match the spec, though `array("d", view)` gives the same bytes.
  (b) Never edit `group_pbo`'s source (nor `pipeline/pbo.py` or
  `stats.percentile`) without re-pinning `BASE_PBO_CODE_SHA`:
  `pbo_code_sha()` hashes them and keys the PBO cache, so an edit
  recomputes every cached null; that is why the list conversion lives in
  `family_train`, not in `group_pbo`. (c) `BASE_PAYLOADS_SHA_V4` /
  `BASE_PAYLOADS_SHA_MIXED` and `BASE_PBO_INPUTS_SHA` in
  `test_gauntlet_stats.py` pin the BASE code's (`49e8e0fb2`) payloads and
  cache key: re-pin them only for a DELIBERATE payload or key change, never
  to make a refactor pass. (d) The first night after cutover charges the
  fallback, which the rehearsal calibrated on a WARM pass (30,140 cache
  hits, no simulation transients); a cold vintage-roll night peaks higher.
  So the cutover's first night must never be a vintage-roll night (Ruling
  7: merge by day so a warm night records a measured rate first). An
  under-charge there only reproduces the old behaviour (no commit check on
  the series pass), on that one night.
- **Effective trials per cell (design
  `docs/2026-11-04-effective-trials-per-cell-design.md`, Coen 2026-11-04;
  `pipeline/cluster_cells.py`, `gauntlet.cluster_registry_by_cell`).** The
  stats job no longer clusters the whole registry. Method: the SAME aligned
  series as `cluster_registry` (`gauntlet._align_for_trials`); rows split by
  cell key (`cluster_cells.partition_key` = the spec's cells sorted, joined
  by `|`; `BTCUSDT@1h` keeps the timeframe; the legacy pair is its own
  partition); in each partition the never-traded rows (all exactly 0.0) leave
  the tree and form ONE cluster (representative Sharpe 0.0, entered once in
  the pooled variance); a constant NONZERO row refuses the run
  (`cluster_refused`); the rest get `cluster._agglomerate_np`'s unchanged
  merge history, each merge's average-linkage height is replayed
  (`merge_heights`), and the tree is cut at `CUT_DISTANCE` = sqrt(0.5 x (1 -
  `CUT_RHO`)), `CUT_RHO` = 0.9: a merge counts when round(height, 12) <=
  round(CUT_DISTANCE, 12), the counted merges are the PREFIX up to the first
  one over the cut (a later one at or under it is float noise, not counted,
  reported as `cut_late_merges`); k = rows - counted (+1 for never-traded
  rows); `trials_n_raw` = the sum of k; labels offset in sorted key order;
  `trials_sr_var` = every partition's representatives pooled. Floors, SR*,
  DSR (still the registry-wide aligned series, Ruling 11) and the haircut are
  unchanged code; `cluster.py` and `gauntlet.run` are untouched (a change to
  `cluster.py` re-runs `tools/verify_cluster_identity.py` first). **`CUT_RHO`
  changes only by a new chained note, never as a tuning knob.** Entries carry
  `cluster_method` `"effective_trials/v4 per-cell average-linkage cut
  rho>=0.9"` and exactly three new fields: `trials_partitions`,
  `trials_largest_partition_n`, `trials_cut_rho`. The per-partition table
  (every partition, sorted key: n, k, never_traded) goes to the `--report`
  file; the run log prints the top 20 by n and a line naming the report;
  neither goes on an entry. Status fields: `cluster_max_cell_n` and
  `cut_late_merges` (THIS run's, null on a night that did not cluster) and
  `cluster_sum_n2` (carried forward like `cluster_s`). **THE RATCHET:** the
  per-cell k (6,183 on 2026-11-05, against the old method's 4,320 the same
  night) and SR* (0.13453 against 0.12533) sit above the chain floors, so
  once the first per-cell entry is chained the floors hold them: a code
  rollback to the registry-wide method restores the code, not the recorded N
  or SR*. **A code rollback AFTER the amendment-2 note is chained also needs
  a superseding chained note:** the old code would chain v3-method entries
  under a chained text that says the count is per cell from that note
  onward (the entries label themselves by `cluster_method`, but the chained
  protocol text would be wrong for them). A fixed cut can dip by a few as
  members arrive; the floor absorbs it. Measured 2026-11-05 on a chain copy (300 verdicts re-statted): every
  recorded DSR falls (median 1.42e-5 -> 3.83e-6), none >= 0.95 before or
  after. The 7,687 `gauntlet_stats` entries chained before the amendment-2
  note (count on the 2026-11-05 copy) stand, unrestated.
- **`--chain` REFUSES until `docs/notes/gauntlet-protocol-v6.1-amendment-2.md`
  is chained** (exit 1, `exit_reason` `refused_no_amendment2_note`; first-line
  prefix `gauntlet-protocol-v6.1-amendment-2:`, first occurrence; the hyphen
  after v6.1 arms none of the `gauntlet-protocol-v6.1:` checks). Report-only
  runs are unaffected. The note is chained VERBATIM, after Coen approves its
  exact text, BEFORE the first 01:00 run on the per-cell code, with
  `python -m tools.grid_switch chain-note` and `--prefix
  gauntlet-protocol-v6.1-amendment-2:` (LF bytes, `.gitattributes`
  `eol=lf`; the tool refuses a CR). Until then the committed file is a DRAFT
  (it carries no STATUS line, the plan 5 convention, so it can be chained as
  is: the draft state is in the commit message and the run ledger). Never
  edit it after approval.
- **`tools/stats_rehearsal.py`: report-only rehearsal on a chain COPY.**
  HAND-RUN, NEVER scheduled. It runs the real `gauntlet_stats.run` WITHOUT
  `--chain` on a registry copy, reading the live `data/` and `simcache/`,
  and measures the clustering's memory (private bytes sampled every 10 ms
  per phase) and time: `python -m tools.stats_rehearsal --registry
  <scratch copy> --data-dir <live data> --simcache-dir <live simcache>
  --scratch <scratch dir> [--dump-aligned <scratch>/aligned.npz]`. Every
  `SimCache` write path (`put`, `_write`, `_poison`, `_migrate_legacy`)
  raises for the whole run, so a cache miss aborts (exit 2) instead of
  writing; data and simcache fingerprints must match before and after (exit
  3 if not). The passthrough (after `--`) is an ALLOW-LIST: only
  `--cutoff`, `--deadline-hours` and `--pbo-null-draws`, each with its value
  (`--opt value` or `--opt=value`; handed on as `--opt=value`). Any other
  token REFUSES (exit 2, nothing run), because gauntlet_stats' argparse
  takes a prefix (`--chai` is `--chain`) and lets a repeated option win (a
  second `--registry`, `--logs-dir` or `--report` would point the run at the
  live chain or status file): so `--chain`, `--registry`, `--data-dir`,
  `--simcache-dir`, `--logs-dir`, `--report` and every prefix of them are
  refused there. It also REFUSES (exit 2) a `--registry` or output path in a
  directory holding `registry_log.d` (the live layout), and a `--registry`
  that is this layer's own chain. Writes
  only under `--scratch` (logs/, report.md, rehearsal.json). Copy the chain
  between worker fires with no `chain.lock` held, and check line count and
  size before and after the copy. A copy with no unstatted v6.1 verdict
  stops at `nothing_to_do` before clustering: cut a prefix just before the
  last night's first `gauntlet_stats` entry (as the 2026-11-05 run did).
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
  is chained (line 81246). A note like it is chained verbatim (the git
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
  chain (it is, line 81246), and `gauntlet-protocol-v6.1-amendment-2` (the
  per-cell count, bullet above). Coen's
  effective-trials decision (2026-10-01, option 1) is in the v6.1 addendum and
  IS implemented and committed in `gauntlet_stats.py` (b7c17658): `trials_n` is
  the larger of the clustering's count (`trials_n_raw`: the registry-wide
  argmax until the per-cell change, the per-cell sum of k after it) and the
  highest cluster count already on the chain (`trials_n_floor`, source named
  by `trials_n_floor_entry_hash`; it still reads `trials_n_raw` of earlier
  stats entries and `FLOOR_PROTOCOLS` is unchanged). Reason: the argmax moved
  302 -> 480 -> 44 on a registry that only grew, and a falling N flatters the
  deflated Sharpe; the floor is also what makes the per-cell jump a ratchet. Chain
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
  floor); `cluster_n` / `cluster_s` / `cluster_sum_n2` (carried forward),
  `cluster_need_mb` / `avail_phys_mb` (this run's memory gate) and
  `cluster_max_cell_n` / `cut_late_merges` (this run's per-cell shape, null
  when it did not cluster); since the train-window change `avail_commit_mb`
  (the last memory checkpoint's reading), `series_mb` / `series_n` (carried
  forward, display only), `series_rate_hist` (the last 8 passes' rates, what
  the series checkpoint charges by its max), `gate_need_mb` /
  `series_charge_mb` / `avail_commit_series_mb` (this run's checkpoint
  figures, null when not reached) and `train_view_fallbacks`
  (bullet "Train windows as views"). Sentinel `gauntlet_queue` check (sc-ops-sentinel `master`): FAIL when the worker
  status is missing or stale, when `exit_reason` is `crashed`,
  `refused_no_protocol_note` or unknown, or when `oldest_queued_age_hours` >
  48; WARN when the statistics lag exceeds 7 days. `27_GauntletWorker` is in
  the Sentinel's `hourly` list (267009, still running at the 09:15 digest, is
  tolerated for it alone), `28_GauntletStats` in `daily`. Do not rename
  these fields without changing `sc-ops-sentinel` in the same change.
- **Wrapper exit codes (both tasks, 2026-10-11):** 124 = the task guard's
  wall ended the run, 125 = the guard saw the task's cmd.exe die and ended
  the tree (normally masked by Task Scheduler's own 267014), 4 = the run log
  was locked so nothing ran (Task guard section). Otherwise the python step's code, as below.
- **Worker idle path (2026-10-11):** with nothing queued at the start the
  end-of-run full chain read is skipped (`queued` 0); on 2026-10-10 under
  memory pressure that one read took ~90 min (2.3 s normally).
- **Exit codes.** Worker: 0 = drained, deadline stop, or deferred on a lock
  (routine); 1 = a candidate raised, the run crashed, or setup was refused.
  Stats: 0 = done, nothing to do, deadline stop, chain.lock held, or another
  instance running; 1 = the data or the chain refused the run, or `--chain`
  without the amendment-1 note (`refused_no_amendment_note`), or `--chain`
  without the amendment-2 note (`refused_no_amendment2_note`), or the
  clustering refused (`cluster_refused`, e.g. a constant nonzero series in
  a partition), or the run crashed (`crashed`);
  2 = argparse usage error; 3 = not
  enough free physical memory OR commit to run safely, at the series-pass
  checkpoint (commit only) or the clustering one (both) (`deferred_memory`,
  FAILs the digest on purpose; the `MEMORY:` line names which).
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
  `correction-2026-10-02-retrial-path` note (`docs/notes/`; chained at line
  79228) each is re-tried inside the D8/D9 unified
  re-run, in exit-rules-v7's D15(b) form: a version-2 composition with a
  permitted exit set, a NEW strategy id charged to N in full, screened and
  then judged by all six gates from scratch. That replaces the old "once the
  worker is live" trigger. Never move one out of the graveyard by hand.
- **Parity tool:** `tools/gauntlet_parity.py` re-judges chained v6, v6.1 and
  v6.2 gauntlet verdicts (since 2026-11-06, Coen; v6 only before), each with
  the battery that judged it (`BATTERY`: v6 and v6.1 -> "v6.1", the six
  gates; v6.2 -> "v6.2"), on bars truncated at each verdict's recorded
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
  **Excused drift (Coen 2026-11-06):** a metric mismatch is excused only for
  a named cause, printed in the report's "drift cause" column: `restated`
  (a bar at or before the recorded data_end re-pinned by
  `tasks.repin_free_series` AFTER the verdict, read from the trading-systems
  `results/tradfi/free_universe_manifest.json` `repins`, override `--repins`,
  and only when the `--data-dir` CSV already holds the re-pinned close)
  or `rounding` (verdict before 2026-08-26T23:19Z and every difference within
  `ULP_MAX` = 32 ulps). A verdict change, a verdict with no timestamp, or any
  other drift is UNEXCUSED, and the exit is 0 only when none is. 2026-11-06
  run (seed 20260930, 60 compared): 13 mismatches, all excused (9 rounding,
  max 17 ulps; 4 restated: 3 EFA, 1 QQQ), exit 0; without the manifest the
  4 restated rows are unexcused and the exit is 1.
  **Per protocol (2026-11-06):** `--protocol` (v6 / v6.1 / v6.2 / `all`,
  default all) and `--sample` N PER protocol, each drawn by its own
  `random.Random(seed)` over its sorted pool, so the v6 sample is the same
  one as before. A v6.2 verdict also compares `beats_hold.strategy_sharpe`,
  `.hold_sharpe` and `.n_days` whenever either side records `beats_hold`
  (`metric_diffs`). A bundle whose `config.json` protocol differs from the
  verdict's (a later judging rewrote it) is NOT COMPARABLE, never compared.
  The summary line is the total; one line per protocol follows it. Live run
  2026-11-06 (seed 20260930, 60 per protocol): v6 60 compared, 13 excused;
  v6.1 60 compared, 8 mismatches all `restated` (EFA, QQQ, TIP), 52
  bit-identical; v6.2 none chained yet (fixture-tested only); exit 0. Six
  intraday v6.1 verdicts (2 each at 1h, 4h, 12h) by `--sids`: all
  bit-identical.
  **Exit (`exit_code`):** 0 clean; 1 any unexcused drift, or (with
  `--sids`) any named strategy missing or NOT COMPARABLE; 3 a sample with
  fewer than min(`--min-compared`, pool) compared rows in ANY protocol whose
  pool is not empty (so v6 cannot cover for a protocol whose bundles all
  failed), or fewer than `--min-compared` in total when every pool is empty.
  A v6.2-only run before any v6.2 verdict exists needs `--min-compared 0`.

