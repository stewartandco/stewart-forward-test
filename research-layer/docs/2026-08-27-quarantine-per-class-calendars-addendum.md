# Quarantine recorder addendum — per-class calendars (2026-08-27)

**Status: approved by Coen 2026-08-27 (in session), written BEFORE implementation.**
This changes the pre-registered daily recorder's refusal semantics, so the
change itself is pre-declared here, with the rules fixed before any code runs.

## Why

2026-08-26, the first day the 08-24 FX quarantine entrants (CHF x3 + ZAR) were
owed a decision, `run_quarantine.bat --date 2026-08-25` hit
`REFUSED: no CHF bar for 2026-08-25` and exited 1. The refusal is all-or-nothing:
one missing bar in the union of eligible specs' assets refuses the WHOLE day
before anything is chained. Consequences observed:

- No quarantine decisions chained for ANY eligible strategy since 2026-08-25 —
  crypto sleeves included, whose bars were on disk and on time.
- The failure recurs every day: `research-layer/data` FX files are pinned
  snapshots (CHF newest bar 2026-08-14) and the upstream FRED H.10 lane
  publishes daily FX rates roughly weekly. The eq class (entered 08-25) has the
  same structure from 08-27 on. A crypto-style "record D-1 at 00:20 UTC"
  contract is structurally impossible for FRED-sourced FX.

Coupling every class's record to the laggiest class's publication schedule
punishes the classes that CAN be witnessed on time. The runner already resolves
eligibility first so that "a gap in an asset nobody is trading today cannot
refuse everyone's day" — this addendum extends that scoping from
entry-date-eligibility to data-readiness, at spec granularity.

## Rules as changed

1. **Per-spec readiness partition.** After entry-date eligibility, each
   eligible spec is checked against the data: if every asset in its universe
   has a bar dated exactly `--date`, it is READY and is recorded as before. If
   any asset lacks that bar (file present, bar absent), the spec is DEFERRED:
   one stdout line
   `"<sid>  deferred: no <asset> bar for <date> yet (data ends <last>)"`,
   nothing chained for it, exit code unaffected. A deferred date is recorded
   later by an explicit `--date` backfill once the bar publishes — or never
   becomes owed at all if the bar never exists (weekend/holiday), which
   `--review`'s owed-dates construction already handles because owed dates are
   derived from bars that exist.
   *Footnote (same day, before the first live run):* when the file exists but
   holds zero bars on or before the date, there is no "data ends" value to
   print; the line reads `(no bars on or before the date)` instead. Still a
   deferral, never a refusal — the file exists.
2. **A missing price FILE is still a hard refusal** (exit 1, nothing chained).
   A wrong path or broken data dir must never read as "publication lag".
3. **Stall guard.** If at least one spec is eligible and EVERY one of them is
   deferred, the run exits 1 with a stderr `REFUSED` naming the condition. A
   day on which nothing at all can be recorded is an anomaly while crypto
   (which trades every calendar day) is in the pool; going quiet on it would
   hide a dead data pipeline. Revisit if the pool ever becomes tradfi-only.
4. **Snapshot provenance becomes extensible per date.** The base
   `quarantine_data_snapshot` stays unique per date and unchanged in shape.
   A new entry type `quarantine_data_snapshot_supplement` (same payload shape:
   `{date, data_sha256, bars_sha256}`) carries provenance for assets that
   first become recordable AFTER the date's base snapshot was chained (the
   backfill of a deferred class). Writer rules, enforced under the same lock
   as the append:
   - a supplement requires an EARLIER base snapshot for its date;
   - a supplement may only name assets not already covered for that date by
     the base or a prior supplement (overlap raises; the chain is append-only
     and a conflicting double-coverage could never be repaired);
   - a supplement is chained BEFORE the decision rows it licenses, exactly
     like the base.
5. **Restatement detection is unchanged in strength.** An asset already
   covered for the date whose recomputed `bars_sha256` differs still refuses
   the day (chained vs recomputed hashes printed). "Not covered yet" stops
   being a refusal and becomes the supplement path; "covered but different"
   stays fatal.
6. **Verifier invariant 9 is extended** (verify_registry.py + SCHEMA.md):
   base snapshot dates unique as before; every supplement must follow a base
   for its date and be asset-disjoint from the date's prior coverage; a
   `quarantine_decision` is covered if an EARLIER base-or-supplement for its
   date names its asset in both digest maps. Chains predating this addendum
   contain no supplements and verify exactly as before.

## What does NOT change

- Decisions are never invented; bars <= date only; idempotency per
  (strategy_id, date, asset); append-only chain; `--review` writes nothing.
- Backfill remains explicit `--date` runs; the lag report keeps a backfilled
  row distinguishable from a faithfully-kept one. FX/eq rows will routinely
  chain days after their bar — that is a property of the data source, honestly
  visible in `--review`, not hidden by this change.
- The partially-recorded-dates report in `--review` is the standing signal
  that a backfill is owed (crypto recorded, FX/eq pending).

## Operational consequence

The daily 08:20 task exits 0 on publication-lag days (deferrals printed, ready
classes recorded), so the Sentinel FAIL storm ends while a total stall or a
restatement stays loud. Missing dates since 08-25 are backfilled per class as
their bars publish.
