# Build 2b-3: crypto grid activation, daily then intraday (design)

Date: 2026-10-17. Status: DRAFT for Coen's review (written in the shared
notes folder; committed to a worktree branch from live HEAD by day as
`research-layer/docs/2026-10-17-crypto-grid-activation-design.md`).
Morpheus One Engine programme, build 2b, third and last part. 2b-1
(intraday correctness, `a9ffbb252`) and 2b-2 (intraday paper stage,
`a624321be`) are merged; sub-project A (quarantine fast path,
`c67368126`) is merged; A2 (quarantine strategy isolation,
`fix/quarantine-strategy-isolation` `1a24ac9ec`) is built, not yet merged,
with Coen's pre-approved merge; it lands before B3 touches the recorder.

## Decisions (Coen's)

- **2026-10-10:** 2b split 2b-1 -> 2b-2 -> 2b-3; 2b-3 = "C2 grid +
  activation" (fix the per-market grid, exclude late cells, intraday
  refresh task, SP5 Phase 3 commit).
- **2026-10-16:** 2b-3 pre-work decomposed A (quarantine write speed) ->
  B (engine perf) -> C (intraday safety rails) -> D (activation data);
  activation, C2 grid, note chaining and task registration stay
  Coen-gated. B = BOTH the exact incremental vol_percentile (1d
  bit-identical; sorted window with exact integer counts) AND a
  per-timeframe gauntlet prior; B also profiles one real catch-up date and
  fixes the biggest cost, with a replay proof as in A. B's process is A's:
  spec -> plan -> subagent-driven, daily_parity 200 1d bit-identical plus
  exact 1h equality.
- **2026-10-17, this design:**
  - **One design, sequenced pieces.** This one spec covers B, C, D and
    activation, built as separate plans, each with its own Coen-gated
    merge.
  - **Order (Coen, option B): the daily part first.** D's daily part (D-d)
    and Switch 1 (1d) go FIRST, then **B -> C -> D's intraday part (D-i) ->
    Switch 2**. **Switch 1's cost sign-off uses today's 1d costs**; the
    intraday sign-off is taken after B lands, so its numbers are real, not
    the pre-B slow path.
  - **Admission line (Coen, option b): at least 365 days of history before
    the cutoff**: 76 assets (D-d1).
  - **Activation is TWO commits, measured apart (Coen's option a):**
    **Switch 1 = SP5 Phase 3 as planned**, crypto per cell at **1d only** on
    the assets with pre-2024 history; **Switch 2 = intraday timeframes**, a
    separate later commit, "so the two trial-denominator changes and their
    costs are measured apart". Each switch has its own cost sign-off.
  - **No cost ceiling beyond the fixed windows** (Coen: "nothing" would make
    him say no beyond them). The fixed windows are in s3; each sign-off is
    still an explicit Coen step before anything is scheduled.
  - **Intraday bundles: option 1.** A committed intraday bundle carries a
    daily-marked `equity.csv`; the per-bar curve stays on local disk only,
    pinned by hash (D-i3). Applies from Switch 2.

Vault: `project_morpheus_one_engine.md` (2b entries, "2b-3 ACTIVATION
PRECONDITIONS", "2b-3 PREREQUISITES found by the rehearsal").

## 1. Intent

Search the declared crypto grid per cell, first at 1d, then intraday. The
machinery to simulate, judge, and paper-record per-cell and intraday
strategies exists and is proven on chain copies, but
`cells.ACTIVE_CELLS["crypto"]` is empty, so no generation proposes a single
crypto cell, and the legacy pooled BTCUSD+ETHUSD path still serves every
crypto trigger. 2b-3 retires the pooled path, activates the crypto grid in
two measured steps at a cost the box can carry, puts alarms on the new
failure modes, and fixes and records the grid before any result (decision
C2: "every timeframe x asset combination is a counted trial, PLUS a
per-market TF x asset grid is fixed BEFORE any results (no post-hoc
additions)").

Why now: the One Engine goal (Coen 10-15) is that only paper results stand
between Morpheus and live trading. Cells that never enter paper can never
graduate, and graduation is multi-year by design, so every month the grid
stays dark is a month of forward record not accumulating.

## 2. Goals, non-goals, anti-goals

**Goals**
1. B: an intraday candidate costs what the box can carry (engine
   vol_percentile no longer O(n x 365 x bars_per_day); deadline priors per
   timeframe; the quarantine catch-up's biggest cost fixed, which also
   carries Switch 1's larger daily quarantine population).
2. C: every intraday failure mode found in 2b-1/2b-2 either alarms or is
   refused up front.
3. D: the active cells have fresh, checked data every day; intraday bundles
   fit the public repo; the whole stage chain (register -> screen -> worker
   -> stats -> quarantine -> live gate) has run end to end per cell on a
   chain copy, at 1d before Switch 1 and intraday before Switch 2, at zero
   metered spend.
4. Switch 1: one reviewed commit activates crypto per cell at 1d and
   retires the pooled path; the grid record is chained before the first
   per-cell crypto verdict; the refresh task is registered in one pass.
5. Switch 2: one reviewed commit adds the intraday timeframes; the
   intraday protocol note is chained verbatim before the first intraday
   verdict.

**Non-goals**
- 15m and 30m activation (declared later phase only; its own Coen-gated
  event).
- Intraday for tradfi (fx/equity/bond/metal). Non-24x7 intraday stays
  unsupported and is refused at registration.
- Changing what the build 4a signal service serves (s9).
- Any change to gauntlet gates, thresholds, the quarantine-live protocol, or
  how N is computed beyond the per-cell registration that already makes each
  cell its own trial.
- Fixing the D9 183-day window gap or the F5 split-sweep harm (both
  declared, both Coen's open calls, untouched).

**Anti-goals (a failure even if it "works")**
- Any existing 1d result changes: every 1d statistic, verdict, bundle and
  quarantine row stays byte-identical (parity proofs are acceptance gates).
- The 08:20 quarantine daily stops finishing before the 08:45 signal
  service, or the 20:00 loop runs into its 07:00 wall, or the worker's
  25-minute deadline is misjudged because a prior is wrong.
- A grid that changes after results are seen; any cell swept that is not in
  the chained grid record; a cell activated below the admission line (s7,
  D-d1).
- The public repo grows by gigabytes per month.
- A silent failure: a missing day, a frozen feed, or an uncounted intraday
  exit that nothing reports.
- Metered spend in any rehearsal.

## 3. Constraints

- **Live tree** `E:\Users\Coen\Claude\stewart-forward-test`, branch
  `claude/ai-agent-business-automation-0lzfd9`, public repo, never pushed
  without Coen's say-so. Each plan builds in its own NEW worktree branched
  from live HEAD, created by day. No live-tree writes 20:00-07:00. Merges
  into live are Coen-gated and follow research-layer/CLAUDE.md's pre-merge
  checklist: no merge/checkout/stash while a worker git step runs; never
  `git clean -x`; never merge a branch touching `registry_log.jsonl`;
  post-merge `chain_mirror check` MATCH + `verify_registry.py` VALID.
- **Fixed windows** (the sign-offs' pass/fail lines): loop one fire
  20:00, PT11H, ends 07:00; worker every 30 min, 25-minute deadline;
  stats 01:00, PT6H, memory-gated clustering (16 GB box; exit 3
  `deferred_memory` FAILs the digest); quarantine daily 08:20 and must be
  done well before the 08:45 signal service; nothing may run into 08:20.
- **Budget:** research budget, not Morpheus (D41: pipeline cap USD 200, no
  batch-stop). Any metered fan-out is costed against the live ledger before
  it is scheduled. Every rehearsal in this design is zero-spend.
- **Tasks:** any new scheduled task goes into morpheus-hub
  `tasks/xml` + the setup bat + `apply_retry_settings.ps1` ($RETRY_TASKS and
  the time-limit table) + the fleet pin in ONE pass (morpheus-hub
  CLAUDE.md); the Sentinel manifest changes only in an `sc-ops-sentinel`
  WORKTREE (never branch-switch its main tree). A wrapper uses `%PY%`
  (absolute interpreter) and the task guard; ExecutionTimeLimit and
  `--wall-s` change together.
- **Chain notes** are chained verbatim, only after Coen approves the exact
  text, LF bytes checked.

## 4. Shape: the plans, in order

| Plan | Repo(s) | Gate to start | Gate to merge |
|---|---|---|---|
| # | Plan | Repo(s) | Gate to start | Gate to merge |
|---|---|---|---|---|
| 1 | D-d + Switch 1: daily data, refresh task, crypto per cell at 1d | stewart-forward-test, morpheus-hub, sc-ops-sentinel worktree | this spec approved | Coen: sign-off 1 signed, grid record + brief texts approved, 8.6 preconditions |
| 2 | B engine speed | stewart-forward-test | spec approved (may start in parallel with plan 1; merges after it) | Coen; parity + replay proofs |
| 3 | C safety rails | stewart-forward-test, morpheus-hub | B merged | Coen; suites |
| 4 | D-i intraday data, bundle shape, intraday rehearsal | stewart-forward-test | C merged | Coen; rehearsal report |
| 5 | Switch 2: intraday timeframes | stewart-forward-test | D-i merged + sign-off 2 | Coen; protocol note + brief texts approved |

Sign-off 1 (1d) is a report inside plan 1, on TODAY's code (Coen's
order): at 1d the vol_percentile window is 365 bars, so B changes little
for it. Sign-off 2 (intraday) is a report before plan 5, on B's code,
measured on D-i's rehearsal and corrected by Switch 1's real operating
numbers. Each plan gets its own writing-plans pass when it starts (plan 1
first, from this spec). Claude's calls: B may be BUILT in its own worktree
while plan 1 is in review or rolling out, but merges after Switch 1 is
live and has had its supervised first generation, so the two changes never
land in the same window; plan 1 and B touch different code (cells/composer
/refresh vs engine/gauntlet_core/quarantine) except quarantine load, which
is why B3 matters soon after Switch 1 (s11).

## 5. Plan B: engine speed

Coen's 10-16 decisions stand as written. In detail:

**B1. Exact incremental vol_percentile.** `engine.gate_mask`'s
`vol_percentile` / `vol_percentile_dense` branch (engine.py ~205-229)
recomputes a percentile over a window of 365 x bars_per_day bars at every
bar. Replace with a sorted window maintained incrementally (insert the new
value, remove the expired one) and a rank computed from exact integer
counts, so the result is the same float by construction, with the same
ties rule and the same None/warm-up rule as today. Proof:
`tools/daily_parity.py` dump/compare on 200 registered 1d strategies
(including every vol-target/vol-percentile spec the sample reaches) is
bit-identical, 0 errors; and an exact old-vs-new equality on 1h specs (the
2b-1 rehearsal's 1h twins on a chain copy, every fingerprinted field).
`ENGINE_REV` stays "e2" only if both proofs are exact.

**B2. Per-timeframe deadline priors.** `gauntlet_core.
GAUNTLET_PRIOR_S_PER_CANDIDATE` (20 s, gauntlet_core.py:426) becomes a
per-timeframe table, values MEASURED on B's code (not the pre-B 940 s);
a chunk's prior is the max over its candidates' timeframes until a rate is
measured, and the measured rate is kept per timeframe so a cheap 1d chunk
does not set the rate for an expensive 1h one. Claude's call: the same
treatment for `screen.SCREEN_PRIOR_S_PER_SPEC` (2 s, screen.py:40),
because screen simulates the same intraday bars; the builder may drop it if
B's measurement shows intraday screen inside 2x of 1d.

**B3. Profile and fix one real catch-up date.** Profile one real
`--catch-up` date replayed on a live-chain prefix
(`tools/quarantine_replay.py`); A's replay left ~4.7 s per row of
simulation and price-file reads. Fix the largest cost found. Candidates the
2b-2 build named: each date re-simulates a cell from bar 0, and the whole
intraday file is read per strategy per catch-up/review and once per date
per cell (`_load_cell_bars`). Proof: old-vs-new replay on a live-chain
prefix appends identical entries, both chains VALID
(`tools/quarantine_replay.py compare`), plus `tools/quarantine_parity.py`.
A2 merges first.

**B4. Measurements for the sign-offs** (s7.5): B's final report carries
seconds per candidate by timeframe for screen, worker and quarantine
strategy-day, on B's code, measured on the 2b-1/2b-2 rehearsal twins.

## 6. Plan C: intraday safety rails

**C1. Hole detector.** Today a whole UTC day with no bars inside an
intraday file is silently never owed, never backfilled, never alarmed
(`qcells.complete_days`, `crypto_data.fetch_symbol`). Add a detector: any
UTC date strictly between a cell file's first and last bar with zero bars
is a hole, reported as a degraded item (source `intraday_hole`, key = cell,
detail = the dates) in the owning ledger: `logs/degraded_quarantine.json`
when found by `--fetch-owed`, the refresh task's ledger when found there
(D-d3). The Sentinel's existing `research_degraded` check turns it WARN,
then FAIL after 3 days. It never blocks a record and never repairs:
repair stays the Coen-gated delete-and-refetch (CLAUDE.md, 2b-2 section).
Claude's call: a tracked, hand-edited acknowledgement file
(`data/intraday_holes_ack.json`, `{cell: [dates], reason}`) suppresses a
named hole that is a real exchange gap, so a genuine Binance outage does
not FAIL the digest forever; an acknowledged hole is still printed. The
detector applies to 1d crypto files too (a whole missing daily bar is the
same failure), once plan 3 merges (after Switch 1).

**C2. Stale-feed alarm.** A fetch that succeeds but brings nothing new is
invisible today. After each fetch, a cell whose last CLOSED bar is older
than (now - one bar - tolerance) is a degraded item (source
`intraday_stale`, key = cell; the builder may pick a name that also fits
1d cells). Same ledgers and Sentinel path as C1. The tolerance is the
builder's (one bar interval plus a few minutes of publication lag is a
sensible start).

**C3. Refuse non-24x7 intraday at registration.**
`Registry.register_strategy` (registry.py:484) raises `ValueError` before
any write for a spec whose session is not 24x7 and whose timeframe is not
1d, or whose timeframe is unknown (the rule `bars.bars_per_day` already
encodes; call it, do not copy it). The composer's per-spec drain already
catches `ValueError` and parks the spec in `sibling_queue_dead` with the
registry's reason. Today such a spec would abort a whole gauntlet/stats
pass inside `registry_series`.

**C4. Morpheus counts intraday exits.** morpheus-hub
`backend/app/connectors/forward_test/service.py:135` counts
`action == "exit"` rows, which undercounts intraday rows (one row per cell
per day, `exits` carries the count). Match `livegate.closed_forward_trades`:
sum `exits` where the row carries it, else count `action == "exit"`. Built
in a morpheus-hub worktree with its own tests; merges with C.

**C4b. Morpheus edge book reads intraday rows (builder check, 10-17).**
morpheus-hub `edgebook/pipeline.py` ~40-48 counts a date only when the
row's asset equals the registered universe; an intraday row carries the
cell key (`SOLUSDT@1h`) against a universe of `["SOLUSDT"]`, so an intraday
edge silently gets zero marks. `edgebook/build.py` ~88-91 counts
`action == "exit"` (C4's undercount). The edge book feeds build 4a's
target-book composer, so this is in scope (Claude's call; the anti-goals
forbid silent failures): match rows by `qcells` cell key and sum `exits`
as in C4. Same morpheus-hub worktree, merges with C.

**C5. Live gate's cutoff compare.** `pipeline/livegate.py` ~263 filters
`t["entry_date"] <= cutoff` as strings; an intraday `entry_date` such as
`2023-12-31 05:00:00` sorts after `2023-12-31`, so trades entered on the
cutoff day are dropped. Compare `entry_date[:10]`. 1d behaviour unchanged
(test-pinned).

**Proof:** full research-layer suite; mutants for each rail; quarantine
parity re-run (C1/C2 touch the fetch path).

## 7. Plan D: activation data

### D-d: the daily part (before Switch 1)

**D-d1. Admission, as a check, not a guess.** Admission line: every active
cell has **at least 365 days of history before `screen.DEFAULT_CUTOFF`**
(first bar on or before 2022-12-31). Coen's decision (option b, 10-17;
Claude's reasoning when offering it): 365 days is the longest warm-up any block needs
(`gate_mask`'s vol_percentile window), and a cell with weeks of training
data buries every spec on `trade_count` while still adding to N. That
excludes the ten 2024 starters (ENA, ETHFI, JUP, PYTH, RENDER, STRK, TAO,
WIF, ZK, ZRO) and fourteen short-history assets (GAS, GLM, PROM, ARB, SUI,
FLOKI, PEPE, PENDLE, WLD, SEI, TIA, ORDI, JTO, BONK; first 1d bars
2023-03-17 to 2023-12-15), leaving **76 assets**. The alternatives Coen
declined: first bar on or before the cutoff (90 assets) or at least 730
days (69 assets); Switch 1 writes the active asset tuple as a literal (D-d
prepares and tests it). D-d adds a per-CELL check, run by the switch's
test suite against the real data and by the refresh task every run.
Per cell, because at Switch 2 an intraday file can start later than its
asset's 1d file; any such cell is excluded and named in the grid record
(s8.3). Claude's call: `ACTIVE_CELLS` stays an assets x timeframes product
when the check finds no per-cell exception; otherwise the builder extends
`active_cells` with an explicit excluded-cells set rather than dropping a
whole asset.

**D-d2. Initial daily data.** Refresh the `*USDT_1d.csv` files of every
active asset (they end 2026-08-27). Keyless Binance via `crypto_data`,
closed bars only, gitignored CSVs (the chain pins them by hash), into the
live `data/` by day. `data/crypto_snapshot_manifest.json` is a TRACKED file
that every refresh rewrites; Claude's call: it is treated like
`tradfi_snapshot_manifest.json` (expected modified on the shared tree,
never committed by the task, never checked out by another session;
CLAUDE.md says so), so no new git committer is added.

**D-d3. The refresh script and wrapper** (registered at Switch 1, s8.5;
not in D). One run fetches closed bars incrementally for every active cell
(76 at Switch 1; the intraday cells join at Switch 2 with no code change,
because it reads `cells.active_cells("crypto")`), cell-isolated: a failed
cell is a degraded item and the others continue; a Binance 418 stops the
run at once and marks the rest (2b-2's rule). It runs D-d1's check on every
cell, and C1/C2 hook into it when plan 3 (C) merges (until then a stale or
holed 1d cell is still caught downstream: the loop's freshness preflight
reports or blocks it, quarantine defers on a missing bar, and the signal
service refuses a lagging file). Its ledger is a new `logs/degraded_crypto_refresh.json`
(writer/source naming as the existing ledgers), read by the Sentinel's
`research_degraded` check (a new manifest key, s8.5). Exit 0 on a run that
fetched what it could (degraded items are the alarm), nonzero only on a
crash or a refusal. Timing: Binance's daily bar closes 00:00 UTC = 08:00
Perth, so the run starts at about 08:02 and must be done before the 08:20
quarantine daily, which reads the same files; the wall is the builder's
from the measured runtime. **Fetches are capped at the end of the last
complete UTC day** (Claude's call, builder check 10-17):
`crypto_data.fetch_symbol` fetches up to now, so from Switch 2 a run after
09:00 Perth would leave 1h files ending on day D and 1d files on D-1, the
class would fail screen's same-day rule and that evening's crypto fire
would park. The cap is a new parameter whose default keeps
`--fetch-owed`'s behaviour unchanged. The task may retry, but no attempt
may START after 08:15 (a retry would collide with the 08:20 quarantine
reading the same files); a missed day is caught by the next run
(incremental) and by C2's stale alarm. The builder sets the retry entry in
`apply_retry_settings.ps1` to fit that rule. Hazard to keep in view: the
loop's freshness preflight BLOCKS a crypto fire when the fired class's
cells are not mutually comparable, so one cell that fails to refresh parks
every crypto fire at `stale_data` (loud, zero spend). That is acceptable as
an alarm; the refresh's degraded item names the cell first.

**D-d4. The 1d rehearsal (zero metered spend).** On a scratch COPY of the
live chain and a scratch data dir (the `tools/intraday_rehearsal.py`
pattern; refuses the live chain and live data):
1. Per-cell expansion: `composer.expand_family_for_class` with a CANNED
   family (no model call) over the active 1d cells, proving one
   single-asset spec per cell, distinct ids, and that `--assets` windowing
   (rotation, 12 assets) yields 12 cells; the benchmark control is written
   on every crypto verdict.
2. Register a sample of per-cell 1d specs; real screen, real gauntlet
   worker, real `gauntlet_stats --chain`, quarantine, live gate dry run on
   the copy.
3. `verify_registry.py` VALID on the copy; no byte of the live chain or live
   data touched (size + sha256 before and after).
4. The report carries every number sign-off 1 needs (s7.5).

### D-i: the intraday part (before Switch 2)

**D-i1. Initial intraday data.** Fetch the history of each intraday
timeframe Switch 2 activates for every active asset (today 1h/4h/12h exist
only for BTC, ETH, SOL, XRP, BNB, and those are stale: BTCUSDT_1h ends
2026-08-15, SOLUSDT_12h 2026-08-01, none has a manifest entry), with D-d1's
per-cell check.

**D-i2. The intraday rehearsal (zero metered spend).** As D-d4 but over the
intraday timeframes: multi-timeframe expansion (one spec per cell at each
timeframe; a 12-asset window yields 12 x the number of active timeframes
cells); twins via the rehearsal tool, extended from its current
`("1d", "1h")` to every activated timeframe, including `pick_originals`'
vol_target / vol_percentile picks (its selection rules are tested here,
CLAUDE.md gate 2); real `gauntlet_stats --chain` end to end on intraday
(CLAUDE.md gate 1); quarantine with a real fetch; live gate dry run.

**D-i3. Intraday bundle shape (Coen: option 1).** `screen.write_artifacts`,
for a spec whose timeframe is not 1d only:
- `equity.csv` = the DAILY-MARKED curve (`bars.daily_marks` of the result's
  equity; same header), one row per UTC day;
- `equity_bars.csv` = the per-bar curve, written beside it, gitignored
  (`research-layer/artifacts/*/equity_bars.csv`), never listed for commit;
- `config.json` gains `equity_bars_sha256` (sha256 of the LF bytes).
`bundle_hash` keeps its three names, so the chain's `artifacts_hash` covers
exactly the committed files (a public clone can verify every bundle) and,
through `config.json`, pins the per-bar file. `commit_list.BUNDLE_FILES` is
unchanged. 1d bundles are byte-identical (no new key, no new file;
test-pinned). Gauntlet worker bundles carry no equity curve and are
unchanged. Readers of `equity.csv` found by the builder check (10-17):
morpheus-hub `backend/app/connectors/research_chain/service.py` (~49, 54,
744; serves the rows, capped at 20,000) and the layer-root `diagnose_*.py`
scripts; a daily-marked file is compatible with all of them (fewer rows,
same header); the builder re-audits at build time. D-i2 measures the
committed bytes per intraday bundle; if `trades.csv` makes an intraday
bundle more than about 10x a typical 1d bundle, that comes back to Coen
before Switch 2.

## 7.5 The cost sign-offs (Coen signs each)

**Sign-off 1 (before Switch 1)**, every figure measured on TODAY's code
(Coen's order: D-d and Switch 1 before B) and refreshed on D-d4, each row
pass/fail against s3's windows:
- screen and worker seconds per 1d per-cell spec; projected screen minutes
  and worker drain hours for one crypto generation (12 cells per window x
  the recent combos-per-family distribution; the builder states the basis);
- `gauntlet_stats`: projected nightly time and clustering memory need
  (`CLUSTER_BYTES_PER_N2` x n^2) at the registry size reached after 30 and
  90 days, against the 16 GB box (the line most likely to fail first);
- quarantine daily minutes at a projected per-cell quarantine population,
  against the 08:20 -> 08:45 window, and the refresh task's runtime;
- committed repo growth per month;
- metered spend: per-cycle composer/triage cost expected unchanged
  (rotation keeps a generation's composer call the same size), stated
  against the D41 cap with the live ledger's month-to-date.

**Sign-off 2 (before Switch 2)**: the same rows for the intraday
timeframes, measured on D-i2 and corrected by Switch 1's real operating
numbers (actual registrations per generation, stats time and memory,
quarantine minutes), so the intraday increment is measured apart from the
daily one.

Nothing is scheduled and neither switch merges until Coen signs its
sign-off.

## 8. The switches

**8.1 Switch 1: crypto per cell at 1d (the SP5 Phase 3 commit).** In ONE
commit, never split (cells.py and the SP5 design both say the halves fail
differently):
- `ACTIVE_CELLS["crypto"] = {"assets": <admitted-asset literal>,
  "timeframes": ("1d",)}`;
- `composer.expander_for("crypto")` routes to `expand_family_for_class`;
  the legacy pooled branch (`expand_family` for crypto, `ALLOWED_ASSETS`,
  `UNIVERSE_BASE`) is deleted; the engine's 2-asset mean-combine path stays
  (every registered pooled strategy, counted at switch time, re-simulates
  forever);
- `CLASSES["crypto"]["benchmark"] = "self"`;
- rotation turns on by itself (`loop._sweep_window` reads the dispatch):
  12 assets per generation (Claude's call for Switch 2: a window is the
  window's assets x every active timeframe, because the cell is the unit of
  survival and rotation is a schedule, never a selection);
- every other branch that keys on the class NAME "crypto" (builder check
  10-17; the builder re-greps at build time): `composer.validate_family`
  (~810); the crypto brief (`SYSTEM_PROMPT`, ~1209, returned at ~1314),
  which says "crypto daily bars (BTCUSD, ETHUSD)" and must be rewritten for
  per-cell daily crypto on the admitted assets (Claude drafts; Coen
  approves the exact text with the switch); `PROPOSAL_SCHEMA`'s asset enum
  built from `ALLOWED_ASSETS` (~1154, ~1670); the per-cycle cap
  `specs_per_combo = 1 if asset_class == "crypto"` (~2206), which becomes
  `len(sweep_cells(...))` like every other class, or every crypto family is
  split and queued; routing (~1953): Claude's call, crypto keeps taking all
  accepted cards as today (no ROUTING tags);
- `gauntlet_core._benchmark_relative` (~270) raises on a multi-asset spec
  once crypto's benchmark is "self": see 8.6's drain precondition;
- `test_phase2_freeze.py`'s activation simulations flip from "simulated" to
  "real"; `test_gauntlet_classes.py`'s crypto absence test becomes presence.

**8.2 Switch 2: intraday timeframes.** One commit: `ACTIVE_CELLS["crypto"]
["timeframes"]` grows to `("1h", "4h", "12h", "1d")` (Claude's proposal,
from cells.py's own `PHASE_1_TIMEFRAMES`; Coen confirms the set at
sign-off 2), plus D-i3's bundle shape if it has not merged earlier, and
the crypto brief's intraday paragraph [PENDING COEN at Switch 2: what a
family's parameters mean across timeframes, bars as written (a lookback of
200 = 200 days at 1d, ~8 days at 1h; each timeframe a different counted
strategy) or time-scaled; Claude recommends bars as written]. The intraday
protocol note (8.4) is chained before the first intraday verdict.

**8.3 The pinned grid record (C2), chained at Switch 1.** A new chain note,
text approved by Coen, chained verbatim before the first per-cell crypto
verdict. To honour C2's "fixed BEFORE any results (no post-hoc additions)"
across two switches, it declares the WHOLE schedule up front (Claude's
call): the admitted assets in manifest order; the excluded assets and any
per-cell exclusions with the reason (below the admission line); phase 1 =
1d, active now; phase 2 = the intraday timeframes, activated only by its
own Coen-gated commit; phase 3 = 15m and 30m, likewise. The set of cells
is fixed by this note; only the timing of each phase is left open, and
nothing outside the declared set can ever be added. **Removals** (builder
check 10-17): Claude's call, the record says up front that a cell whose
pair is delisted or halted on Binance is RETIRED by a new note (a
retirement never adds a cell; its registered strategies stay on the chain
and keep whatever forward record they have). Until that note, the refresh
task's degraded item names the dead cell and the loop's preflight parks
crypto fires at `stale_data` (loud, zero spend), which is the intended
alarm. A note (the family-openness-v1 / v6.1 pattern) rather than code
alone, because C2 is a pre-registration promise and the chain is where
promises are dated.

**8.4 The intraday protocol note, chained at Switch 2.** `docs/notes/
gauntlet-protocol-v6.1-intraday-daily-marks.md` (DRAFT since 2b-1, second
paragraph from 2b-2), exact text approved by Coen, chained verbatim before
the first intraday verdict. Not edited by this design.

**8.5 Task registration, one pass, at Switch 1.** The refresh task (SP5
named it `26_CryptoGridRefresh`; 26 is now the live gate, so the next free
number, which the builder reads at the time across morpheus-hub master AND
unmerged branches: `30_BinanceExecutor` and `31_SignalService` exist only
on `feat/build4a-exec-panel` today, so master alone would wrongly give 30):
`tasks/xml/<NN>_CryptoGridRefresh.xml`, the setup bat, `$RETRY_TASKS` and
the time-limit table in `apply_retry_settings.ps1`, and the fleet pin, in
one pass; wrapper `tasks/run_crypto_refresh.bat` with `%PY%` and the task
guard. Sentinel: in an `sc-ops-sentinel` worktree, the task's health
assertion and the `research_degraded` key for its ledger. SP5's standing
rule is that a new task is health-asserted only after its exit code has
proven trustworthy; the manifest change is prepared in the same pass and
merged when Coen says (the ledger key can go in at once). Switch 2 adds no
task.

**8.6 Order on the day, Switch 1 (by day, inside the live-tree rules).**
Preconditions (Claude's calls, builder check 10-17): (a) **no legacy pooled
crypto spec is in flight**: none `proposed`, none in `gauntlet`, and
`queue_crypto` 0, because after the benchmark flip the worker would raise on
each one every 30 minutes (exit 1). The loop's crypto fires keep producing
pooled specs until the merge, so the merge goes in by day after the last
20:00 fire's screen and the worker have drained them; if the sibling queue
is not empty, Coen decides (drain or park) before the merge. (b) The build
4a controller has confirmed how the executor treats the new USDT universe
(s9). Then: merge (after a worker exit line, no locks) -> post-merge MATCH
+ VALID -> chain the grid record under chain.lock (LF bytes checked) ->
register the refresh task (one pass) and run it once by hand -> the 20:00
loop's first crypto fire is the first per-cell generation, supervised the
next morning: pipeline_status crypto items, screen_result, worker status,
committed bundle sizes, the 08:20 quarantine and 08:45 signal service
times. The builder confirms the order against the verifier's invariants
(the 2a cutover merged code BEFORE chaining its note for exactly that
reason) and amends it in the switch plan if the verifier needs otherwise.
Switch 2 follows the same pattern with the protocol note in place of the
grid record and no task registration, and B's per-timeframe priors in
force.

**8.7 Rollback.** Revert the switch's MERGE (never half of it). Registered
strategies and chained notes stay (append-only); a superseding note
records a pause if Coen wants one. After a Switch 1 revert, disable the
refresh task. Quarantine keeps recording any strategy already in
quarantine (2b-2 machinery is independent of ACTIVE_CELLS).

## 9. Noted, not changed

- **Signal service (build 4a, `pipeline/signal_service.py`, task 31).** It
  serves crypto 1d strategies in quarantine or live. From Switch 1, new
  per-cell 1d USDT strategies that pass the gauntlet enter quarantine and
  will therefore appear in the targets file, with Binance symbols such as
  `SOLUSDT` on assets the executor has never traded; intraday strategies
  (Switch 2) are not served (it filters 1d). The 4a controller must confirm
  before Switch 1 merges (8.6 precondition b) whether the executor accepts
  that universe as is. Coupling to weigh there: the service writes nothing
  and exits 2 if ANY served asset's file lags, so once USDT strategies are
  served, one failed USDT refresh stops the targets for every strategy,
  the legacy BTC/ETH ones included. This design does not change the
  service.
- Morpheus' other views of quarantine rows beyond C4/C4b are untouched.

## 10. Left to the builder

Module, function and file names; the hole tolerance and stale tolerance
values; the refresh task's wall and exact start minute (inside 08:00-08:15
Perth); the per-timeframe prior values (measured); the incremental
percentile's data structure, as long as it is exact; how the rehearsals
sample; whether D-d and D-i are one plan or two; test layout; mutant lists;
whether the screen prior is per-timeframe (B2).

## 10b. Open questions for Coen

- Q1 Order and Q2 Admission line: answered by Coen 10-17 (Decisions).
- (At Switch 2, not now: what a crypto family's parameters mean across
  timeframes; Claude recommends bars as written.)

## 11. Risks

- Coen's order puts B3 (the catch-up cost fix) AFTER Switch 1, so the 08:20
  quarantine daily carries a growing per-cell 1d population on today's
  ~4.7 s/row catch-up cost until B merges. Sign-off 1 projects it against
  the 08:20 -> 08:45 window; if the projection fails inside B's expected
  delivery, that comes back to Coen before Switch 1.

- Clustering memory (s7.5) may fail first; the response is Coen's (RAM,
  cadence, or a slower rotation), not a constant change.
- 1h strategies may trade often enough that `trades.csv` dominates bundle
  size (D-i3 measures and escalates).
- A whole-day Binance outage is a real hole; C1's ack file handles it
  without hiding it.
- Survivorship in the 100-asset universe is declared, not corrected (cells
  .py); forward quarantine is the arbiter.
- More registrations at a fixed bar is arithmetic about the denominator,
  never evidence of edge; the BH bar in the one shared quarantine pool
  rises as new survivors enter it. An honest zero is an acceptable outcome.
