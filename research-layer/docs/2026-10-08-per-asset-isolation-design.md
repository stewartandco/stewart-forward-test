# Per-asset isolation: one bad series must not stop the research layer — design

Date: 2026-10-08. Decisions: Coen, this date, in session (listed in §2).
Changes one rule of `docs/2026-08-27-quarantine-per-class-calendars-addendum.md`
(rule 5; see §6) and one behaviour of `docs/2026-09-12-loop-snapshot-stage-design.md`
(the preflight's abort; see §5). Source of truth for the build; the plan derives
from it. **Protocol-touching:** the quarantine-isolation addendum (§6.4) is
approved by Coen BEFORE any recorder code is written.

## 1. The failure this closes

2026-10-03, Tiingo corrected a bad print in EFA's history (2014-07-03: cached
OHLC 66.10/66.48/66.03/66.48, corrected 69.06/69.29/69.03/69.22; the cached bar
implied -3.76% then +3.31% while EWG/EWU/EWJ and every other lane ETF moved
< 1.3% either way). The producer's integrity rule reported `restated` = `fail`
by design (free-lane spec §5.3). One series then stopped three things:

| # | Where | What one bad asset did |
|---|---|---|
| 1 | Loop stage 0, `pipeline.tradfi_data snapshot` | `SnapshotRefused` on EFA alone -> no snapshot, `snapshot_failed`, 25_PipelineLoop exit 1 on 10-03 (every class, crypto included) |
| 2 | Loop freshness preflight (`loop._freshness_preflight`) | Would have been next: a skipped EFA goes stale, and one stale cell exits the whole loop `stale_data` |
| 3 | Quarantine recorder (`quarantine.run --date`) | After Coen's 10-04 re-pin, the chained EFA `bars_sha256` for 09-28..10-01 no longer reproduces; the conflict refuses the WHOLE date, so every strategy owed on those dates (none of them EFA's) is blocked. 23_QuarantineDaily exit 1 from 10-07, retried daily, never self-resolving |

Coen re-pinned EFA on 2026-10-04 (trading-systems `09e0299`); rows 1 and 2
recovered once the producer verdict was ok. Row 3 is still failing.

### 1.1 Probe result (read-only, 2026-10-08, on scratch copies of chain + CSVs)

- The reconstructed OLD bar reproduces all four chained EFA digests for
  09-28..10-01 exactly; the corrected bar reproduces none. The refusal is
  exactly the restatement.
- 9 quarantined strategies hold EFA. **Every one already has its rows chained
  for 09-28..10-01** — the rows the catch-up is trying to write belong to
  non-EFA strategies. They are refused only because `run()` hashes the assets
  of every READY strategy, including strategies with nothing left to write.
- Recomputing all 99 chained EFA-strategy rows under the corrected bar:
  action, price and position_frac identical on all 99; equity differs on 7 rows
  in the last 1-2 units in the last place (e.g. 0.9950196341346615 vs
  ...613), floating-point order through a 2014 trade. No recorded decision
  changed.

## 2. Decisions (Coen, 2026-10-08)

- D1. One bad tradfi series must not stop the loop.
- D2. The freshness preflight is narrowed to the stale cells (it was Coen's
  open decision of 2026-10-07: keep blocking / WARN / narrow).
- D3. Loudness contract = WARN that escalates: a degraded item is a WARN in the
  09:15 digest and becomes a FAIL once it has persisted **3 days**. FAIL stays
  reserved for "something broke and the work did not happen".
- D4. The EFA restatement is recorded on the chain by a ONE-OFF `note`
  (no new entry type, no general restatement-resolution machinery).

## 3. The degraded contract (shared by the loop and the recorder)

Constraint: `25_PipelineLoop` carries RestartCount=3 / PT15M, so any non-zero
exit buys up to three extra METERED cycles. A degraded-but-ran run therefore
**exits 0**; the WARN travels through a file the Sentinel reads (the
`gauntlet_queue` pattern).

### 3.1 Ledgers

- `research-layer/logs/degraded_loop.json` — written by the loop (stage 0 and
  the preflight).
- `research-layer/logs/degraded_quarantine.json` — written by the recorder.

One file per writer, so the 08:20 recorder and the 20:00 loop never race on one
file. Shape:

```json
{
  "writer": "loop",
  "ts_utc": "2026-10-08T12:00:00Z",
  "items": [
    {"source": "stage0", "key": "EFA", "reason": "EFA: last integrity verdict is fail",
     "since_utc": "2026-10-03T12:00:11Z", "last_seen_utc": "2026-10-08T12:00:00Z"}
  ]
}
```

- `source` is one of `stage0`, `freshness` (loop) or `restated`,
  `price_file_missing` (recorder). `key` is an asset id (stage0, recorder) or a
  cell id (freshness).
- **Lifecycle:** an item seen again keeps its ORIGINAL `since_utc` (the clock
  is honest) and refreshes `last_seen_utc` and `reason`; an item not seen this
  run is removed (it recovered). The writer reads its previous ledger to do
  this; an unreadable previous ledger is treated as empty AND the run notes it
  (the clock restarts — visible, never silent).
- **Written on EVERY run that reaches the relevant stage, empty included,**
  so a missing or old ledger can never read as "all clear". Atomic replace
  (write a temp file in `logs/`, then `os.replace`).
- **Loop:** every non-dry-run fire that completes stage 0 writes the ledger,
  `no_trigger` and budget parks included (§5 moves the freshness check ahead
  of the trigger decision for exactly this reason). A `--dry-run` fire writes
  no ledger. A fire that defers before stage 0 (instance or lock) writes none;
  the Sentinel's max-age rule catches a writer that stops writing.
- **Recorder:** only `--catch-up` REMOVES items. It is the authoritative pass:
  it attempts every owed date (rule 5 of §6.1 means deferral-only dates never
  wait for a slot), so an item it does not see has recovered. A `--date` run
  (the task's daily step, or a hand backfill) only ADDS or refreshes items,
  never removes, so it can never reset a `since_utc` that catch-up is still
  seeing.

### 3.2 Exit codes

- Degraded but ran: **exit 0** (loop and recorder).
- Unchanged non-zero exits for real failures: stage 0 wrote zero series
  (§4), chain invalid, crash, the recorder's total-stall refusal (§6.3), a
  non-identical duplicate.

### 3.3 Sentinel check `research_degraded` (sc-ops-sentinel, read-only)

Reads both ledgers. Config in `manifest.json`:

```json
{"research_degraded": {
  "fail_after_days": 3,
  "loop_ledger": "E:\\Users\\Coen\\Claude\\stewart-forward-test\\research-layer\\logs\\degraded_loop.json",
  "loop_ledger_max_age_hours": 36,
  "quarantine_ledger": "E:\\Users\\Coen\\Claude\\stewart-forward-test\\research-layer\\logs\\degraded_quarantine.json",
  "quarantine_ledger_max_age_hours": 36}}
```

- OK: both ledgers present, fresh, `items` empty.
- WARN: any item younger than `fail_after_days`; detail names source, key,
  reason, age.
- FAIL: any item with `now - since_utc >= fail_after_days`; or a ledger
  missing, unreadable, or older than its max age (the loop writes nightly
  ~20:00-23:xx, the recorder at 08:20; 36 h tolerates one late fire, not a
  dead writer).
- The 3-day threshold lives in the manifest, never in task code.

**Interaction with the producer:** `24_TradfiFreeRefresh` is Sentinel `daily`
since 2026-10-08, so a producer integrity failure FAILs the digest the same
morning. The research layer's downstream degradation from the same cause is a
WARN that escalates after 3 days.

## 4. Stage 0: skip a bad series, never stop the snapshot

Today `snapshot()` collects per-series refusals and raises once
(`tradfi_data.py:271`), writing nothing.

- Each PER-SERIES check becomes a skip, keeping today's reason text: not
  pinned in the producer manifest; verdict `fail` / unreadable
  (`_check_verdict`); no cached parquet; history shrank; pinned-prefix sha256
  mismatch.
- Verified series are written as today.
- A skipped series is untouched: its CSV in `data/` is not rewritten, and its
  `series` record in `tradfi_snapshot_manifest.json` carries forward unchanged
  (the existing merge already does this for every id not written).
- `tradfi_snapshot_manifest.json` gains top-level `"skipped": {id: reason}` for
  THIS run (`{}` when none).
- **Still fatal (exit 1, `SnapshotRefused`):** zero series written from a
  non-empty request (a dead producer, not one bad series); the producer
  manifest unreadable; an explicit `--assets` id outside its class (a caller
  bug).
- CLI: one `snapshot: skipped <ID> (<reason>)` line per skip, then the existing
  summary line; exit 0.
- Loop: after stage 0 exits 0, read `skipped` from the snapshot manifest after
  confirming its `snapshot_utc` is not older than this stage's start (an old
  manifest is never read as this run's result); write one `stage0` ledger item
  per skip; add `snapshot_skipped_series` (comma-joined ids) to the status
  items. The existing `snapshot_skipped=1` item keeps its meaning (producer
  root absent).
- Consequence: a skipped series stops advancing; from the next fire its cell is
  stale and §5 records a `freshness` item for that cell. Cause and effect both
  show.

Unchanged: a hand snapshot is still a REPAIR, never a routine; the
pinned-prefix check keeps its exact strength; the producer's verdict is never
overridden.

## 5. Freshness preflight: name the stale cells, stop blocking

Since step 8 (2026-10-07) the preflight protects no in-loop stage: screen never
calls `assert_cells_comparable` and the worker defers non-comparable
candidates (`deferred_not_comparable`).

- New pure function `screen.stale_cells(data_end, class_of) -> dict[cell_id, reason]`
  beside `assert_cells_comparable`, implementing the SAME rules but naming the
  laggards:
  - a cell with no bars (`""`) -> stale (`no bars`);
  - WITHIN a class: every cell ending before that class's latest day -> stale;
  - ACROSS classes (after the within-class laggards are set aside, so each
    class is represented by its latest day): for each pair of classes whose
    latest days are more than `3 + max(lag_a, lag_b)` calendar days apart,
    every cell of the EARLIER class -> stale.
- **Equivalence (tested):** `stale_cells(...)` is non-empty exactly when
  `assert_cells_comparable(...)` raises, over generated cases and the real
  shapes (EFA lagging its class; fx lagging crypto beyond its allowance). A
  cell with no declared class in `class_of` is a caller bug: `stale_cells`
  raises the same `ValueError` the assert does, never a stale item.
  `assert_cells_comparable` itself is not modified; its callers (gauntlet
  worker, hand gauntlet, gauntlet_stats) are untouched.
- `_freshness_preflight` reads each cell's end individually; a MISSING price
  file becomes a stale item (`price file missing`) instead of aborting.
  PermissionError/OSError on a present file still escapes to `loop_crashed`,
  exactly as today.
- The preflight no longer returns `stale_data`: it writes one `freshness`
  ledger item per stale cell, keeps `data_end_by_class` and adds
  `stale_cells` (comma-joined) to the status items, and the cycle continues to
  triage, composer and screen.
- **It moves to every fire.** Today it sits at 4.0c, after the trigger
  decision, so `no_trigger` and budget parks (most nights) never reach it.
  Since it no longer aborts anything, it runs right after stage 0 on every
  non-dry-run fire, before the trigger decision; its own docstring already
  calls it cheap enough for every fire (tail reads plus one chain read of the
  registered specs). The 4.0 chain verify and 4.0b orphan check keep their
  position and their aborts.

**Deliberately unchanged: 28_GauntletStats.** It refuses (`cells_not_comparable`,
exit 1) when cells are not comparable at its Sunday vintage. A registry-wide
statistic over a subset of cells is a different statistic (screen.py: "no
quiet subsetting of the reported search space"), so it stays blocking. The
3-day FAIL (§3.3) reaches Coen days before a skipped series can fall behind a
vintage Sunday, so 28's refusal is a late backstop, not the first alarm.

## 6. Quarantine recorder: isolate by strategy

All inside the existing `--date` path; `--catch-up` keeps routing through it.

### 6.1 Rules

1. **Only OWING strategies are simulated and guarded.** A ready strategy all of
   whose `(sid, date, asset)` keys are already on the chain is counted as
   already present (one per key) and NOT simulated; its assets are not hashed
   for the date. Nothing is lost: today such rows are re-simulated and then
   skipped on the key without comparison. **This rule alone unblocks
   2026-09-28..10-01** (§1.1).
2. **A restated asset defers only its strategies.** When an asset covered for
   the date has a recomputed `bars_sha256` different from the chained one, every
   OWING strategy trading that asset is deferred for the date; the rest record
   under the existing base-or-supplement provenance, and any new supplement
   names only unconflicted assets. The log keeps today's message (chained vs
   recomputed hashes), scoped to the deferred strategies.
3. **A missing price file defers only its strategies** (today
   `_load_eligible_bars_or_refuse` refuses the whole date).
4. Each deferral under 2 or 3 adds a `restated` / `price_file_missing` item to
   `degraded_quarantine.json` (key = asset). One `--date` or `--catch-up`
   invocation writes the ledger once, aggregating every date it handled, under
   §3.1's rule (catch-up adds, refreshes and removes; `--date` only adds and
   refreshes). Exit 0.
5. **Catch-up slots:** a date on which every owing strategy was deferred under
   2 or 3 (nothing recordable) does not consume one of the `MAX_CATCHUP_DATES`
   (10) slots, so permanently blocked dates cannot starve newer ones. Cost stays
   small: rule 1 means only the deferred strategies are simulated.

### 6.2 Unchanged

- Invariant 9 and `verify_registry.py`: no new entry type; every decision is
  covered by an EARLIER base-or-supplement naming its asset; a restated bar is
  never chained over; supplements stay asset-disjoint.
- A non-identical duplicate stays fatal.

### 6.3 Total stall stays loud

When every ELIGIBLE strategy is deferred for a missing BAR (the existing
`not ready` path), the run still refuses with exit 1 — a dead data pipeline
while a 24x7 class is in the pool. Deferrals under rules 2 and 3 do not count
toward this; they are tracked in the ledger.

### 6.4 Protocol addendum (written and approved FIRST)

`docs/2026-10-08-quarantine-isolation-addendum.md`, in the 08-27 addendum's
form ("approved by Coen ..., written BEFORE implementation"). It narrows 08-27
rule 5 from "covered but different still refuses the day" to "refuses that
asset's owing strategies for the day, recorded in the degraded ledger", and
declares rules 1, 3 and 5 above. SCHEMA.md's quarantine text ("a restatement of
covered bars stays a hard refusal") is updated to match in the same change.

### 6.5 Known limit (deliberate, D4)

A future restatement that hits a strategy that still OWES rows has no
automatic resolution: it shows as WARN, then FAIL after 3 days, and the fix is
Coen's call at that time.

## 7. The one-off chained note (D4)

- `entry_type: note`, `payload: {"text": "restatement-2026-10-04-efa: ..."}`,
  in the form of the 2026-10-01 correction notes (68485/68486).
- Text: the Tiingo correction (dates, old/new OHLC, the peer evidence); the
  re-pin (trading-systems `09e0299`, manifest EFA sha `8a7cec8e…` ->
  `c90ad148…`); the chained vs recomputed EFA `bars_sha256` for 09-28, 09-29,
  09-30, 10-01; the probe result of §1.1 (9 strategies, 99 rows, 0 decision
  changes, 7 rows of last-place equity drift); the consequence: rows from 10-02
  are computed on the corrected bar, and EFA dates <= 10-01 no longer reproduce
  bit-for-bit (equity only, <= 2 units in the last place).
- Written under `chain.lock`, outside 20:00-07:00, never during the gauntlet
  worker's git step. Before writing, confirm the key prefix collides with no
  protocol-note prefix the verifier interprets (invariant 12) and run
  `verify_registry.py` before and after (VALID both times).

## 8. Build order

On a feature branch in a worktree; merged into the live tree only inside a
safe window (not 20:00-07:00, not during the worker's git step), as step 8 was.

1. Addendum + SCHEMA.md text (§6.4) -> Coen approves.
2. Recorder (§6) + its ledger writer. The urgent part.
3. Loop: stage 0 skip (§4), `stale_cells` + narrowed preflight (§5), loop
   ledger writer.
4. Sentinel `research_degraded` (§3.3), enabled only after 2 and 3 are live
   (a missing ledger would otherwise FAIL the first digest).
5. The chained note (§7).

## 9. Testing

TDD per unit; full research-layer and Sentinel suites run in the worktree,
never the live tree. Mutation runs on the guards (rule 1's owing filter, rule
2's per-asset deferral, the zero-write fatal, `stale_cells` equivalence, the
Sentinel's day threshold). Builder/verifier split: a cold reviewer verifies
each claim at source before the merge.

Required tests (each must be seen failing first):

- Recorder, the EFA shape: a restated asset whose strategies already have the
  date's rows, plus other strategies owed -> the date records, ledger empty,
  exit 0.
- Recorder: an owing strategy on a restated asset is deferred alone, others
  record, a `restated` item is written, exit 0; a missing file defers alone;
  every eligible strategy missing a bar -> still exit 1.
- Recorder catch-up: deferral-only dates do not consume slots; newer owed dates
  still record within the run.
- Stage 0: one bad series skipped, the rest written, `skipped` recorded, the
  skipped id's manifest record and CSV byte-identical to before; zero written
  -> exit 1; explicit out-of-class `--assets` -> exit 1.
- Loop: an old snapshot manifest (`snapshot_utc` before the stage) is not read
  as this run's skips.
- `stale_cells` equivalence with `assert_cells_comparable`.
- Ledger: `since_utc` kept across runs; recovered items removed; empty ledger
  written; unreadable previous ledger -> clock restart is noted; a recorder
  `--date` run never removes an item (only `--catch-up` does).
- Loop: a `no_trigger` fire still runs the freshness check and writes the
  ledger; a `--dry-run` fire writes none.
- Sentinel: OK empty+fresh; WARN under 3 days; FAIL at >= 3 days; FAIL on a
  missing / unreadable / stale ledger.

## 10. Acceptance (live, after merge)

- The next 08:20 23_QuarantineDaily records 2026-09-28..10-01 and exits 0;
  `degraded_quarantine.json` present with no items.
- The next 20:00 25_PipelineLoop fire writes `degraded_loop.json` with no
  items.
- The 09:15 digest shows `research_degraded` OK.
- Forced check in a SCRATCH tree only (never live): a hand-made restated
  fixture produces WARN, and FAIL with `since_utc` back-dated 3 days.

## 11. Out of scope

- 28_GauntletStats over fresh cells only (a statistics protocol decision).
- A general restatement-resolution entry type (D4 chose the one-off note).
- Keeping the composer away from stale cells (the worker already defers; cost
  is a little composer spend).
