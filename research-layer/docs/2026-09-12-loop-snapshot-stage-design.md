# Loop snapshot stage + pre-spend freshness preflight — design

Date: 2026-09-12. Decision: Coen, this date ("snapshot as the loop's stage 0,
every fire", over a separate scheduled task or a by-hand snapshot).
Supersedes one sentence of `docs/2026-08-24-market-expansion-sp4-design.md` §3
(see §5 below). Source of truth for the build; the plan derives from it.

## 1. The failure this closes

2026-09-11 22:30, the first real cycle since 09-04: crypto triggered, triage,
composer and screen ran (25 screened, 15 -> gauntlet, USD 1.90 spent), then
`pipeline.gauntlet` refused at `screen.assert_cells_comparable`:

    AUD_1d (2026-08-21, fx) vs BTCUSD_1d (2026-09-10, crypto): 20 calendar
    days apart, allowance 13 (3 base + 10 declared max_end_lag_days for fx).

Chain untouched (the gauntlet writes only at the end); the 15 candidates sit
in state `gauntlet` with no verdict, the normal resumable state. September
pipeline spend crossed the USD 32 batch-stop on that cycle, so every fire
parks until 1 October.

Root cause, verified at source: `research-layer/data/*_1d.csv` for the
tradfi classes is a snapshot copied from the producer parquets in
trading-systems by `python -m pipeline.tradfi_data snapshot`, and NOTHING
scheduled runs that copy. `\StewartCo\24_TradfiFreeRefresh` (08:50, exit 0
daily) keeps the PRODUCER current (ETFs to 09-10, fx to 09-04 — FRED H.10
honestly lags about a week). The copy last ran 2026-08-31 09:35 UTC, by hand
(`data/tradfi_snapshot_manifest.json` `snapshot_utc`). `23_QuarantineDaily`
refreshes only BTCUSD/ETHUSD via `pipeline.data_fetch`, so those two march on
daily while every other class stands still; about two weeks after any hand
snapshot the cross-class gap breaches the allowance. The 09-04 cycle passed
because the snapshot was four days old.

Second defect: the comparability check sits inside the gauntlet, after every
metered stage. A stale snapshot should park the cycle at USD 0, beside the
pre-spend chain verify and the gauntlet-orphan preflight.

## 2. Stage 0 — the loop takes the snapshot itself, every fire

**Where.** In `loop.run()`, immediately after the two lock probes (section 0
instance guard, section 1 foreign chain-lock probe) and BEFORE the first
registry read. It therefore runs on every fire that gets past the locks:
`no_trigger`, `deferred_budget`, `dry_run_would_fire` and real cycles alike.
Deliberate: the 08:20 quarantine daily reads the same `data/` and its
per-class calendars defer fx dates until the bars are present, so a fire that
does nothing else still leaves fresh cells for the next morning.

**What.** One subprocess through the existing `_stage(runner, argv, cwd)`
helper, so tests drive it with the same fake runner as every other stage:

    <sys.executable> -m pipeline.tradfi_data snapshot
        --classes fx,equity_etf,bond_etf,metal_etf --out <layer root>
        --ts-root <the producer root the loop probed>

(`--ts-root` added 2026-09-13 in review: the loop probes one root for the
skip rule and the adapter must use THAT root, not re-resolve its own.)

`--classes` is a module constant `SNAPSHOT_CLASSES = ("fx", "equity_etf",
"bond_etf", "metal_etf")`, i.e. every class in `cells.CLASSES` except
`crypto`. Crypto stays as it is: BTCUSD/ETHUSD via the quarantine daily's
`data_fetch` (the only crypto cells any registered spec uses); the 100-coin
USDT grid is unscheduled today and out of scope here (crypto's active set is
empty, no registered spec names those cells, nothing compares them).

**Skipped when** the layer has no trading-systems producer to read from
(`$TRADING_SYSTEMS_ROOT`, else `tradfi_data.DEFAULT_TS_ROOT`, does not
exist): print `snapshot_skipped: no producer at <path>` and continue. This is the tmp-registry / test / fresh-clone case;
production always has the producer. The dry run DOES take the snapshot (it
writes `data/`, never the chain, and a dry run that reports "would fire" on
stale data would be lying about what the real fire will do).

**Failure.** Non-zero from the adapter (a `SnapshotRefused` — unpinned,
missing or hash-mismatched series, nothing partial written — or any crash)
ends the fire: print the adapter's stderr tail, `_write_status(logs_dir,
"snapshot_failed", overall="FAIL", extra={"snapshot_rc": rc})`, return 1.
Exit 1 is the "real defect" band (module docstring); the Sentinel FAILs the
digest and the task's 3×15 min retry fires. Spend at that point is zero and
no chain read has happened, so the status carries no counts (the same
omission as the two lock-probe paths, documented in CLAUDE.md).

**Provenance.** `tradfi_data.snapshot` rewrites
`data/tradfi_snapshot_manifest.json` with `snapshot_utc` and the producer's
`source_snapshot_utc`. The pinning that actually binds a generation to its
data is the per-cell `data_sha256` the screen and the gauntlet chain with
every verdict (screen.py / gauntlet.py `load_cell_data` hashes) — stronger
than a manifest id, and unchanged by this design. (Corrected 2026-09-13: SP4
§3's "records the snapshot id in its universe provenance" is not implemented
in composer/registry; the hash is what exists.) A generation is therefore
still pinned to the data it was bred on — taken at the generation's own
start instead of by a person some days before.

**Commit.** None. The quarantine daily commits BTCUSD/ETHUSD CSVs because its
forward records cite them; the tradfi snapshot is reproducible from the
producer's manifest + the research-layer manifest and is not committed today
by the hand runs either. Unchanged.

## 3. Pre-spend freshness preflight (4.0c)

**Where.** In `loop.run()` right after the gauntlet-orphan preflight (4.0b)
and before triage, the same zero-spend position.

**Shared helper, one implementation.** Move the gauntlet's cell/class
derivation into `screen.py` so the loop and the gauntlet cannot drift:

    def comparable_cells(all_specs) -> tuple[list[tuple[str, str]], dict[str, str]]
        # -> (cells_needed sorted, class_of by cell_id), exactly the code
        #    currently inline in gauntlet.run() before assert_cells_comparable
        #    (class from each spec's OWN universe.asset_class, default "crypto";
        #    never cells.class_of_asset — legacy BTCUSD/ETHUSD are not grid members).

    def cell_end_dates(data_dir: Path, cells) -> dict[str, str]
        # -> {cell_id: last bar date string as written in the CSV}, reading
        #    only the LAST data line of each `<asset>_<tf>.csv`. A missing
        #    file raises FileNotFoundError naming the cell. Same value
        #    load_cell_data() returns as data_end when cutoff is 9999-12-31
        #    (pinned by a test that compares the two on a fixture).

`gauntlet.run()` calls `comparable_cells` and keeps its own
`assert_cells_comparable` — belt and braces, the loop's check is the cheap
copy of the gauntlet's, not a replacement.

**The check.** `all_specs` read exactly as the gauntlet reads them:
`[e["payload"] for e in registry.entries() if e["entry_type"] ==
"strategy_registered"]`. Then
`assert_cells_comparable(cell_end_dates(data_dir, cells), class_of=class_of)`.
On `ValueError`: print `stale_data: <the ValueError text>`, `_write_status(
logs_dir, "stale_data", overall="FAIL", extra={"stale_detail": <text>,
"data_end_min": ..., "data_end_max": ...}, spent=_spent(logs_dir),
escalations=["stale_data"], state=state, counts=trigger_counts)`, return 1.
On `FileNotFoundError` (a registered cell with no CSV): same path, outcome
`stale_data`, detail naming the cell. Escalation vocabulary: both new FAIL
outcomes escalate as `run_aborted` (a registered `pipeline_status.PUSH_TRIGGERS`
string, like `stage_failed` and `loop_crashed`); the specific name lives in
`items.outcome`. **Dry run, deliberately:** `--dry-run` runs stage 0 (so a
dry run in the live tree refreshes `data/`) but returns at the trigger report,
BEFORE the chain verify, the orphan check and this preflight — a dry run does
not tell you whether the real fire would refuse with `stale_data`, exactly as
it does not tell you whether the chain verify would fail. Exit 1 because, with stage 0 in front
of it, staleness now means the source lags beyond its declared
`max_end_lag_days` or a cell vanished — a defect to look at, never weather.
Spend is zero: this runs before triage.

`data_dir` is `layer / "data"`, the same path the loop already hands every
stage as `--data-dir` (loop.py `data_argv`).

## 4. Status, docs, tests

**pipeline_status.json.** Two new outcomes, `snapshot_failed` and
`stale_data`, both FAIL / exit 1; added to the module docstring's exit-1
list and to CLAUDE.md's loop section. Every successful fire adds
`snapshot_utc` (from the rewritten manifest) to the items, so the digest can
say which snapshot a cycle was bred on; `snapshot_skipped=1` when §2's skip
fired.

**Docs.** SP4 design §3: the refresh-policy sentence gets a dated amendment
(§5). CLAUDE.md `## Pipeline loop` gains a "Stage 0 snapshot + freshness
preflight" paragraph: what runs, the two outcomes, the skip rule, and that a
hand `tradfi_data snapshot` is now only ever a repair, never a routine.
Vault: `project_market_data_campaign.md` + the index line.

**Tests (pipeline/test_loop.py + test_screen.py), fake runner throughout.**
1. Stage 0 argv: first `runner` call is the snapshot with `SNAPSHOT_CLASSES`
   and `--out <layer>`; it precedes the first registry read (assert order via
   a registry that records access).
2. Stage 0 rc != 0 -> `snapshot_failed`, exit 1, no later stage argv, status
   has `snapshot_rc`, no counts.
3. Stage 0 runs on a `no_trigger` fire and on a `deferred_budget` fire.
4. Skip rule: producer root absent -> `snapshot_skipped` printed, fire
   continues, status item present.
5. `--dry-run` still runs stage 0.
6. Preflight: a fixture where one fx cell ends 20 days before a crypto cell
   -> `stale_data`, exit 1, zero stage argv after the preflight, `stale_detail`
   names both cells.
7. Preflight: fresh fixture -> triage argv follows as before (byte-identical
   to the pre-change argv — pin it).
8. Missing CSV for a registered cell -> `stale_data` naming the cell.
9. `cell_end_dates` == `load_cell_data(...)[2]` on the test fixture; and it
   reads only the tail (a 10 MB fixture with a garbage first line still
   returns the last date).
10. `comparable_cells` matches the gauntlet's previous inline result on the
    existing gauntlet fixtures (moved code, same output).
Full suite must stay green (1366 + the known data-pinned four).

**Live proof, in this order.** (a) `python -m pipeline.loop --once --dry-run`
in the live tree while the budget is parked: stage 0 runs for real, fx cells
move 2026-08-21 -> 2026-09-04, ETFs 08-28 -> 09-10, manifest `snapshot_utc`
is today; the dry run then reports `deferred_budget` as before. (b)
`git diff --stat data/` shows only the tradfi CSVs + manifest. (c) The next
scheduled fire's `pipeline_status.json` carries `snapshot_utc`. Read these,
never assume them.

## 5. Policy amendment (SP4 §3, dated)

Old: "a snapshot is taken at track start and then deliberately (before a
generation run), never on a schedule — generations pin to the snapshot they
were bred on."

Amended 2026-09-12: the loop IS the generation run and fires unattended, so
"deliberately before a generation run" is done by the loop itself as its
stage 0, on every fire. The pinning half is unchanged — each generation still
records the snapshot it was bred on — and there is still no separate
schedule for the snapshot. A hand `tradfi_data snapshot` is a repair, not a
routine.

## 6. Out of scope, stated

- Widening the 13-day allowance or any `max_end_lag_days`.
- The 100-coin USDT crypto grid's refresh (unscheduled; no registered spec
  compares it; revisit when crypto's active set is non-empty).
- Backfilling quarantine dates deferred while the snapshot was stale
  (`python -m pipeline.quarantine --review` lists them; none owed today).
- Moving the freshness check OUT of the gauntlet (it stays).
