# Quarantine recorder addendum — per-strategy isolation (2026-10-08)

**Status: approved by Coen 2026-10-08 (in session), written BEFORE implementation.**
This changes the pre-registered daily recorder's refusal semantics, so the
change is pre-declared here, with the rules fixed before any code runs.
Design: `docs/2026-10-08-per-asset-isolation-design.md` §6.

## Why

2026-10-03 Tiingo corrected EFA's 2014-07-03 bar; Coen re-pinned it on
2026-10-04 (trading-systems `09e0299`). From 2026-10-07 `--catch-up` refused
2026-09-28..10-01 because the chained EFA `bars_sha256` no longer reproduces.
Every strategy holding EFA already had its rows for those dates; the rows the
catch-up was trying to write belonged to OTHER strategies, refused only
because the recorder hashes the assets of every ready strategy, including
ones with nothing left to write. One asset stopped every strategy's day.

## Rules (replace rules 2 and 5 and amend rule 3 of the 2026-08-27 addendum; rules 1, 4 and 6 stand)

1. **Only owing strategies are simulated and guarded.** A ready strategy all
   of whose `(strategy_id, date, asset)` keys are already chained is counted
   as already present and not simulated; its assets are not hashed for the
   date.
2. **Covered but different defers that asset's owing strategies, not the
   day.** When an asset covered for the date has a recomputed `bars_sha256`
   different from the chained one, every owing strategy trading it is
   deferred for the date (no row for any of its assets); the rest record
   under the existing base-or-supplement provenance, and a new supplement
   names only unconflicted assets of strategies that record. A restated bar
   is still never chained over.
3. **A missing price file defers its strategies**, instead of refusing the
   whole date.
4. Deferrals under 2 and 3 are recorded in `logs/degraded_quarantine.json`
   and read by the Ops Sentinel (WARN, FAIL after 3 days). The run exits 0.
5. **Total stall stays loud:** when every eligible owing strategy is deferred
   for a missing bar or a missing price file, the run refuses with exit 1.
6. **Catch-up slots:** a date on which nothing is recordable because its owing strategies were deferred, at least one of them for a restated asset (rule 2), does not consume one of the 10 per-run slots. A date on which every owing strategy lacks a bar or a price file is a total stall (rule 5), not a slot-free date.

## What does NOT change

Invariant 9 and the verifier; decisions are never invented; bars <= date
only; idempotency per (strategy_id, date, asset); append-only chain; a
non-identical duplicate stays fatal; backfilled rows stay visible as
backfills in `--review`.

## Known limit

A restatement that hits a strategy that still owes rows has no automatic
resolution: WARN, then FAIL after 3 days, then Coen's call.
