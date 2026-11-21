# Quarantine fast path: one read per run, one append per date (design)

Date: 2026-10-16. Status: DRAFT for Coen's review. Morpheus build 2b,
2b-3 pre-work, sub-project **A** of four (Coen, 2026-10-16: A quarantine
write speed -> B engine performance -> C intraday safety rails -> D
activation data).

Decisions, all Coen's, 2026-10-16:
- **Approach A: the gauntlet worker's pattern** -- one snapshot read, advance
  over the tail only, one batched append -- rather than a per-row tail fix (B)
  or a hybrid (C).
- Design sections 1-3 approved in conversation the same day.

Vault: `project_morpheus_one_engine.md` (2b-3 pre-work decomposition).

## 1. Problem

The 08:20 quarantine daily (`\Morpheus\23_QuarantineDaily`) took about
4 h 27 min on 2026-10-15 to chain 382 decision rows: ~42 s per row on a
~100 MB, ~129,000-entry chain. The cost grows with the chain. Verified at
source on 2026-10-16:

1. **Every row re-reads the whole chain three times.**
   `Registry.record_quarantine_decision` (registry.py ~820) calls
   `strategy_states()` (full parse) to check the strategy is in quarantine,
   `entries()` (full parse) to look for a duplicate key, and, through
   `_append_locked`, `_head_hash()` (full read) to chain the row.
   `record_quarantine_snapshot` and `record_quarantine_snapshot_supplement`
   do the same scan.
2. **Every `--date` run reads the chain about eight more times.**
   `run()` reads states, `quarantine_entry_dates`, and the specs;
   `_record_date` reads `existing_decisions` twice, `data_snapshots` and
   `snapshot_supplements` two to three times.
3. **Catch-up multiplies it.** `--catch-up` runs `run(["--date", d])` per
   owed date (`run_one_date_for_catch_up`), each a fresh set of full reads,
   up to `MAX_CATCHUP_DATES` (10) dates.
4. **Simulation runs under `chain.lock`.** The write phase holds the lock
   from the provenance check through the last row, and `observe_day` (the
   simulation) runs inside it. Other writers (gauntlet worker, stats job,
   scanner card batches) defer for the whole time. For intraday strategies
   this becomes ~145-175 s per strategy-day (2b-2 rehearsal, 2026-10-15).

The gauntlet worker had the same shape in Build 2a and fixed it with
`Registry.snapshot(on_entry=...)` (one read, a caller-built view),
`Registry.advance(snap)` (parse only the new tail; `ChainMoved` on a
rewritten or truncated log) and `Registry._append_at(snap, items)` (one
batched append; refuses with `ChainMoved` unless the log is exactly where
the snapshot left it). It holds `chain.lock` for about 16 ms per verdict.

## 2. The rule

**A quarantine run reads the chain once, simulates before it locks, and
writes each date's entries in one append.** The entries it writes are the
same entry types, in the same order, with the same payloads as today; only
`ts_utc` differs.

## 3. Components

### 3.1 `QuarantineView` (new, `pipeline/quarantine_view.py`)

Built from ONE `registry.snapshot(on_entry=view.on_entry)`. It holds what a
run asks the chain today:
- lifecycle states (from the snapshot);
- quarantine entry dates (what `quarantine_entry_dates` returns);
- registered specs (`strategy_registered` payloads);
- chained decision keys `(strategy_id, date, asset)` with their payloads
  (for the identical-duplicate check);
- base `quarantine_data_snapshot` payloads by date;
- `quarantine_data_snapshot_supplement` payloads by date, in chain order.

Its methods return exactly what the old readers return:
`entry_dates()`, `decision_rows()`, `decision_keys()`, `snapshots()`,
`supplements()`. `view.advance(registry)` moves the snapshot and the view
over the tail only.

### 3.2 `Registry.advance(snap, on_entry=None)`

`advance` gains an optional callback for each tail entry it parses, in
chain order. Default None: the gauntlet worker and stats job are unchanged.

### 3.3 Readers move onto the view

`existing_decisions`, `data_snapshots`, `snapshot_supplements`,
`decision_entries`, `quarantine_entry_dates`, `run()`'s states and specs,
`--review` and catch-up's owed-day computation take a view instead of
re-reading. The module-level reader functions stay, as thin wrappers that
build a view, for tools and tests that call them.

### 3.4 Catch-up keeps one view

`--catch-up` builds one view and hands it to each date's record pass,
advancing it over the tail between dates. The per-date `DateReport`, the
slot rule, the degraded ledger rules and the exit codes are unchanged. A
date that ends in `ChainMoved` (from `advance` or `_append_at`) counts as a
failed date, as any raise does today; the view is then rebuilt with a fresh
`snapshot()` before the next date, so one failure never strands later dates.

`--fetch-owed` (step 1b) is a separate process and is not changed.

### 3.5 The write phase (per `--date` pass)

1. **Before any lock:** eligibility, rules 1-3 and readiness from the view;
   load bars; simulate every ready strategy (all rows); compute the date's
   `bars_sha256` and `data_sha256`; validate every payload with the existing
   validators (`validated_quarantine_decision`,
   `validated_quarantine_snapshot`).
2. **Take `chain.lock`** non-blocking, as today. Held: `deferred_lock`,
   exit 0, nothing written.
3. **Under the lock:** `view.advance()`; redo the provenance decision
   against the advanced view -- base snapshot absent / already covered /
   restated (rule 2: defer those strategies, drop their rows, degraded
   `restated` items) / new cells (a supplement) -- with today's refusals
   verbatim (a supplement without a base; a base with no usable
   `bars_sha256`; an overlapping supplement that is not identical); drop rows
   already chained (identical: skipped and counted; different: raise
   `DuplicateQuarantineDecision`, nothing written); check every remaining
   strategy is still in quarantine (raise, nothing written).
4. **One `registry._append_at(view.snap, items)`:** the base snapshot or
   supplement first, then every row in today's order (ready strategies in
   order, cells in `spec_cells` order). `ChainMoved` (an unlocked writer
   moved the log) means nothing written, exit 1 -- the worker's alarm.
5. Print today's per-row lines and summary, after the append succeeds.

`registry.record_quarantine_decision` and the two snapshot writers stay, with
their chain-level guards, for hand tools and tests; the recorder no longer
calls them.

### 3.6 The one behaviour change: a date is all or nothing

Today a raise during the row loop (e.g. `observe_day`'s non-positive equity
baseline) leaves the rows before it chained and prints `PARTIAL WRITE`. Now
simulation finishes before anything is written, so the date records nothing,
stays owed, and the run exits 1 as today. One broken strategy therefore also
holds back the earlier strategies' rows for that date until it is fixed --
today it already holds back every later one. The `PARTIAL WRITE` message
and path go away.

## 4. Not in A

- Engine speed (the incremental vol_percentile) -- sub-project B.
- Intraday alarms (hole detector, stale feed), registration refusal,
  Morpheus -- C. USDT refresh, late cells, gauntlet_stats intraday -- D.
- Other writers' read costs (loop, screen, stats) -- not touched.
- No chain writes outside the normal daily. No change to what is recorded.

## 5. Testing

### 5.1 Unit and integration (`pipeline/test_quarantine_fastpath.py`)

- **The view equals the old readers** on fixture chains: states, entry
  dates, specs, decision keys and rows, snapshots, supplements, owed days.
- **`advance(on_entry=...)`** delivers exactly the tail entries in order and
  still raises `ChainMoved` on a rewritten head.
- **Races**, each forced by appending between the view's read and the lock:
  another writer chained an identical row (skipped); a different row for the
  same key (raises, nothing written); the date's base snapshot (our write
  becomes coverage or a supplement); a restating snapshot (rule-2 deferral);
  a strategy left quarantine (raises, nothing written); an unlocked append
  inside the lock window (`ChainMoved`, nothing written).
- **All or nothing:** a simulation raise for the third of five strategies
  leaves zero rows for the date, exit 1, the date still owed.
- **One read:** a run records a date with exactly one full `snapshot()` read
  (counted by monkeypatching), and catch-up over three dates with one;
  a forced `ChainMoved` on the middle date fails that date, rebuilds the
  view (a second read) and still records the third.
- The existing suites (gen3b, isolation, intraday, catch-up, fetch-owed)
  pass unchanged except the test that pins `PARTIAL WRITE`, rewritten to
  §3.6.
- Every new rule gets a mutant a test must kill; the full suite runs on the
  final head.

### 5.2 Old-vs-new replay on real data (the proof)

- Take a copy of the live chain and cut it at a PREFIX ending just before a
  recent 08:20 run's first entry (any prefix of a hash chain is valid), so
  that day's rows are owed again. Two copies of the prefix; the same data
  directory (a copy of the live `data/`).
- Run the same sequence (`--date <that day>`, then `--catch-up`) with the old
  code on one copy and the new code on the other.
- **Rule:** the appended entries are identical in `entry_type`, `payload` and
  order (`ts_utc` excluded); both copies `verify_registry.py` VALID; the
  degraded ledgers identical; exit codes identical.
- **Measured:** wall time old vs new, and the longest `chain.lock` hold.
- The 2b-2 `tools/quarantine_parity.py` (pure functions) is unaffected and
  not re-run; the replay covers deferrals and supplements, which it does
  not.

## 6. Shipping

- The merge into the live tree happens only on Coen's go, under the
  live-tree guard (research-layer/CLAUDE.md).
- The first live 08:20 run after the merge is the real check: the same kind
  of summary (`N decision(s) chained, M already present`), in minutes.
- research-layer/CLAUDE.md: the quarantine catch-up cost line, the new
  all-or-nothing rule, and that the recorder holds `chain.lock` only for a
  tail read plus one append per date.

## 7. Open points for Coen

- None blocking. The replay's prefix point (which recent day) is chosen at
  build time from the live log; the report names it.
