# Intraday paper stage: quarantine records intraday strategies (design)

Date: 2026-10-11. Status: DRAFT for Coen's review. Morpheus build 2b
("engine v2"), sub-project **2b-2**, the second of three:

- 2b-1 intraday correctness (merged live `a9ffbb252`, 2026-10-11);
- 2b-2 the intraday paper stage (this document);
- 2b-3 the C2 grid and activation (the SP5 Phase 3 commit).

Decisions, all Coen's, 2026-10-11:
- **Q1 = A: one row per strategy, cell and UTC day, with trade counts.**
  Not one row per bar, and not a list of trades inside the row.
- **Q2 = A: the quarantine daily fetches only what it owes.** No full USDT
  refresh in 2b-2 (that stays in 2b-3).
- **Q3 = A: extend the existing DRAFT note**
  `gauntlet-protocol-v6.1-intraday-daily-marks` with one paragraph for the
  quarantine and the live gate. No separate addendum.
- Design sections 1-3 approved in conversation the same day.

Vault: `project_morpheus_one_engine.md`, the "Build 2b kickoff" section.
2b-1 design: `docs/2026-10-10-intraday-correctness-design.md`.

## 1. Problem

2b-1 made the screen and the gauntlet measure an intraday strategy on daily
marks. The paper stage was left refusing it: since 2b-1,
`quarantine._split_intraday` skips every non-1d quarantined strategy and
records a degraded item (`intraday_unsupported`). Without this sub-project an
intraday strategy that passes the gauntlet in 2b-3 would sit in quarantine
with no forward record, and the live gate could never judge it.

What the daily-only recorder assumes, verified at source on 2026-10-11:

1. **Files.** Every price path is `data/<asset>_1d.csv`
   (`hash_price_files`, `hash_bars_through`, `_load_eligible_bars`,
   `_owed_by_date`, `_last_bar_date`, `_owed_dates_for_catch_up`).
2. **Simulation.** `observe_day` passes `cells.SESSION_PERIODS[session]` as
   `periods_per_year` and no `bars_per_day`, so a 1h strategy would be sized
   and financed as if each bar were a day (the defect 2b-1 fixed in
   `run_spec`).
3. **Dates.** `_daily_bars` cuts every date to `[:10]` and says intraday
   "never reach[es] this module". `_rebase_index` compares `b["date"] <=
   entered`, which is wrong for `"2026-10-10 05:00:00"` against
   `"2026-10-10"`.
4. **Provenance is keyed by asset.** A `quarantine_data_snapshot` records
   `data_sha256` and `bars_sha256` per asset, and verifier invariant 9
   requires every `quarantine_decision`'s `asset` to appear in an EARLIER
   snapshot for its date. A 1d and a 1h strategy on the same asset would
   need two different files under one key.
5. **Rows have an exact shape.** `registry.validated_quarantine_decision`
   refuses any missing or extra key (`QUARANTINE_DECISION_KEYS`, 7 fields).
6. **The live gate counts trades by rows.** `livegate.closed_forward_trades`
   counts rows with `action == "exit"`. A daily row can record at most one
   exit, so an hourly strategy that closes several trades a day would be
   undercounted, and the kill arm's `MIN_FORWARD_TRADES` floor would hold it
   silent for too long.

The chain holds no intraday quarantine row today (no intraday strategy has
ever been registered), so 2b-2 changes only future behaviour.

## 2. The rule

The 2b-1 rule, carried to the paper stage: **an intraday strategy is
simulated per bar and recorded on daily marks.** One row per strategy, cell
and UTC day, describing the book at the day's last bar, plus how many trades
it opened and closed that day. A daily strategy's bar is its daily mark, so
its rows are exactly what they are today.

## 3. Components

### 3.1 The cell key

An intraday row's `asset` field is the **cell key** `ASSET@TF`, for example
`BTCUSDT@1h`. The day's snapshot records that cell's hashes under the same
key.

- Daily strategies keep the bare asset (`BTCUSD`, `SPY`), byte for byte, in
  rows and snapshots. A 1d strategy on a USDT asset also keeps the bare
  name.
- Verifier invariant 9 needs **no change**: the row's `asset` still names a
  snapshot key. A cell key can never equal a bare asset key, so a 1d and a 1h
  strategy on the same asset never collide, and the existing rule that a
  supplement must not overlap its base snapshot still holds.
- One helper owns the mapping: `cell_key(asset, tf)` returns `asset` for
  `"1d"` and `f"{asset}@{tf}"` otherwise; `split_cell_key(key)` returns
  `(asset, tf)`; the file is `data/<asset>_<tf>.csv`. Every path in
  `quarantine.py` goes through it.

### 3.2 The row

An intraday row has the seven existing fields plus two counts:

| field | meaning for an intraday row |
|---|---|
| `strategy_id`, `date` | as today; `date` is the UTC day `YYYY-MM-DD` |
| `asset` | the cell key, e.g. `BTCUSDT@1h` |
| `action` | the day's LAST event: `enter_long`, `enter_short` or `exit`; `hold` if none |
| `price` | close of the day's last bar |
| `position_frac` | the position's notional fraction at the day's last bar (0.0 flat) |
| `equity` | rebased equity at the day's last bar |
| `entries` | trades opened that day (int >= 0) |
| `exits` | trades closed that day (int >= 0) |

- `registry.validated_quarantine_decision` accepts exactly two shapes: the
  7-field shape when `asset` has no `@`, and the 9-field shape when it does.
  Anything else is refused (a 9-field row on a bare asset, a 7-field row on
  a cell key, a negative or non-int count). `QUARANTINE_ACTIONS` is
  unchanged.
- "Last event" is decided by bar order: of the day's entry and exit events,
  the one on the latest bar. The engine never enters on the bar it exits
  (`observe_day`'s existing comment), so two events never share a bar.

### 3.3 When a day is complete

Day D is recordable for an intraday cell when either:
- the cell's **last slot of D** is on disk: the bar opening at D 00:00 UTC
  plus `(bars_per_day - 1) x tf` (D 23:00 for 1h), or
- **any bar dated after D** is on disk (so an hour the exchange never
  published cannot make D owed forever).

`crypto_data.fetch_symbol` writes closed bars only (it drops the open kline
by the exchange's own `close_time`), so at the 08:20 local run (00:20 UTC)
yesterday's 23:00 bar is on disk and yesterday is complete. A strategy's day
is recordable when every one of its cells is complete for that day: the
existing "every asset has a bar" rule (`recordable_owed_dates`), applied per
cell.

### 3.4 Simulation (`observe_day`)

- Mechanics come from 2b-1: `periods_per_year = bars.bars_per_year(session,
  tf)` and `bars_per_day = bars.bars_per_day(session, tf)`, passed to
  `simulate_asset`. For tf `"1d"` both equal today's values, so the daily
  call is unchanged.
- Bars are loaded at the cell's timeframe and truncated to the end of D
  (`date[:10] <= D`). The readiness check becomes "the last loaded bar is in
  D", not "the last bar's date equals D".
- The rebase zero point is the daily mark of the quarantine entry date: the
  last bar with `date[:10] <= entered`. For daily bars this is the same bar
  as today.
- Counts come from the book's trades: `entries` = trades whose entry bar is
  in D, plus an open position entered in D; `exits` = trades whose exit bar
  is in D.

### 3.5 Fetching only what is owed

`run_quarantine.bat` gains one step, after the existing
`python -m pipeline.data_fetch` and before `--date`:

`python -m pipeline.quarantine --fetch-owed`

- It collects the intraday cells of every strategy currently in quarantine
  and runs `crypto_data.fetch_symbol` for each (incremental, closed bars
  only, atomic write) into `data/`.
- **Empty set (true until 2b-3): nothing is fetched**, and the run prints
  one line saying so.
- A cell whose fetch raises is printed and recorded as a degraded item
  (`source: intraday_fetch_failed`, key the cell) in
  `logs/degraded_quarantine.json`; the step exits 0. Its strategies' days stay
  owed and are recorded by catch-up once a fetch succeeds. The Ops Sentinel's
  `research_degraded` check already turns that ledger WARN, then FAIL after 3
  days, so a dead feed cannot stay quiet. `--catch-up`'s existing recovery
  rule removes the item once its cell records again.
- Intraday CSVs are **gitignored and never committed**, like every USDT cell
  (`research-layer/.gitignore`: market data "is not the record"). The chain
  pins their bytes by hash, and closed Binance klines do not change, so a
  re-fetch reproduces them.

### 3.6 Provenance, owed days, catch-up, review

- Snapshots: `data_sha256` = the cell file's sha256; `bars_sha256` = the
  existing `hash_bars_through` recipe (header plus every row with
  `date[:10] <= D`, file order, LF-normalized) read from the cell's file.
  Keyed by the cell key.
- Owed days stay **day**-based: the dates a cell owes are the UTC days after
  the entry date up to its last complete day (3.3). `_owed_by_date`,
  `_last_bar_date` and `_owed_dates_for_catch_up` read cells through the
  3.1 helper.
- Unchanged, and covering intraday through the cell key: the 10-date
  catch-up slot limit, `--review`'s gap and backfill-lag report, the
  restatement guard (a `bars_sha256` mismatch defers only the strategies
  that trade that cell), the missing-file deferral, the degraded ledger and
  its recovery rule.
- `_split_intraday` and the `intraday_unsupported` refusal are removed: the
  recorder now handles every 24x7 timeframe in `bars.BARS_PER_DAY_24X7`. A
  non-24x7 intraday strategy cannot be simulated (`bars.IntradayUnsupported`);
  the recorder defers it with a degraded item (`intraday_unsupported`, the
  existing source) rather than crash the date. Registration-time refusal of
  such a spec stays in 2b-3.

### 3.7 The live gate (`livegate.py`)

- `closed_forward_trades` sums `exits` on rows that carry it and counts
  `action == "exit"` on rows that do not. Daily rows give the same count as
  today.
- Unchanged and already correct on daily rows: `forward_series` (grouped by
  date and `asset`, now the cell key), the `MIN_TRADING_DAYS` count (UTC
  days with rows), PSR on forward daily returns, the rebuilt cone.
- `train_contributions` already re-runs the spec with `timeframe=tf`
  through `run_spec`, which has the 2b-1 mechanics.

### 3.8 Morpheus

`morpheus-hub` copies `quarantine_decision` payloads into its forward book
as plain dicts (`research_chain/state.py`) and never counts exits, so the
cell key shows as the cell's label and the two counts pass through. No
Morpheus change. The plan includes a read-only check that its tests accept a
9-field row.

## 4. The protocol note (DRAFT, chained at 2b-3 activation)

One paragraph is appended to `docs/notes/gauntlet-protocol-v6.1-intraday-daily-marks.md`,
one line, ASCII, the note's existing format. Draft text (Coen approves the
exact wording when 2b-3 is ready to chain the note):

`Quarantine and the live gate. An intraday strategy's forward record is one quarantine_decision per cell per UTC day, written at the day's last bar: the cell is named ASSET@TF (for example BTCUSDT@1h) in the row's asset field and in that day's data snapshot; price, position and equity are the book at the day's last bar; the row adds entries and exits, the trades the strategy opened and closed that day. A day is complete when the cell's last bar of the day is on disk, or when any later bar is. The live gate counts closed forward trades as the sum of exits over these rows, and a trading day for a 24x7 strategy is a UTC calendar day. Daily strategies' rows are unchanged.`

The note stays a DRAFT and NOT chained. The chaining order holds by
construction: an intraday strategy reaches quarantine only after an
intraday gauntlet verdict, and the first such verdict already requires this
note on the chain (2b-1 s7).

## 5. Not in 2b-2

- Everything 2b-3 owns: the crypto C2 grid, activation, excluding the 10
  late-starting cells, the full daily USDT refresh, the incremental exact
  percentile, a per-timeframe gauntlet prior, registration-time refusal of
  non-24x7 intraday specs, the end-to-end `gauntlet_stats` run on intraday.
- **Cost, stated not solved.** The recorder re-simulates each owing strategy
  from bar 0 every date. A 1h cell is ~80,000 bars; a vol-percentile
  strategy costs ~100 s per cell per date at today's O(n x 365 x
  bars_per_day) percentile (2b-1 measurement). Dormant until 2b-3, and the
  same incremental-percentile prerequisite bounds it.
- No chain writes. This document and `docs/notes/` stay public (D40); the
  plan is private (stewartandco-agents internals).

## 6. Testing

### 6.1 Daily parity, the safety net for the live record

A parity run takes a sample of REAL chained quarantine dates and, for each,
computes the snapshot digests and the rows the recorder WOULD write (the
pure functions `hash_price_files`, `hash_bars_used` and `observe_day` over
that date's quarantined strategies; nothing is written, since the recorder
skips rows already on the chain), with the old code and the new, on a COPY
of the chain and data. **Rule: old and new byte-identical** (canonical JSON
of each payload). Agreement with the rows actually on the chain is reported
too, as information (an older engine revision may have written them). The sample covers every class with quarantined strategies, both
base and supplement snapshots, and dates where a strategy was deferred
(restated or missing file). It extends `tools/daily_parity.py`'s method:
meta block, code-identity check (old vs new module), and failure on empty
results or any error.

### 6.2 Known answers (synthetic data)

- **Equivalence:** a 1h strategy whose bars move only at UTC day boundaries
  records, day by day, the same `equity`, `position_frac` and `price` as its
  1d twin, and `entries`/`exits` match the twin's trades.
- **Multi-trade day:** a day with two round trips records `entries` 2,
  `exits` 2, and `action` the last event.
- **Completeness:** D complete with the 23:00 bar present; D incomplete with
  it absent and nothing later; D complete with an hour missing once a bar of
  D+1 exists.
- **No collision:** a 1d and a 1h strategy on BTCUSDT on the same date write
  disjoint snapshot keys and both rows verify.
- **Writer:** refuses a 9-field row on a bare asset, a 7-field row on a cell
  key, a negative or non-int count.
- **Live gate:** `closed_forward_trades` sums `exits` across 9-field rows
  and counts exit actions on 7-field rows.
- **Fetch:** `--fetch-owed` on an empty set calls nothing; a raising fetch
  yields a degraded item and exit 0; catch-up records the cell's owed days
  after a later successful fetch.
- **Non-24x7 intraday:** deferred with `intraday_unsupported`, not a crash.
- **Rebase:** the zero point is the entry day's last bar.
- `verify_registry.py` reads VALID on a chain holding intraday rows.

### 6.3 Discipline

- Every changed rule gets a mutant a test must kill.
- The full research-layer suite on the final head (in a worktree the 6
  live-chain tests fail for want of `registry_log.jsonl`; they run in the
  live tree after merge).
- Subagent-driven build: task review per task, a final whole-branch review.

## 7. End-to-end proof on a chain copy (no live effect)

- A COPY of the live chain as the scratch registry; scratch `data/` and
  `logs/`.
- One or two 1h twins from the 2b-1 rehearsal set, registered on the copy
  and moved to `quarantine` on the copy by raw append (none passed the
  gauntlet in the rehearsal, so the state change is made by hand, on the
  copy only, and said so in the report).
- Run on the copy: `--fetch-owed`, `--catch-up`, `--review` and
  `livegate --dry-run`.
- Proof criteria: the owed days are recorded with correct counts; `--review`
  shows no gap; the live gate reads the record; `verify_registry.py` VALID
  on the copy; nothing reaches the live tree (chain, `data/`, `logs/`).
- Measured: seconds per strategy-day for a 1h strategy (feeds 2b-3 sizing).
- Results go to a private diagnostic (stewartandco-agents internals) and the
  vault.

## 8. Shipping

- The merge into the live tree happens only on Coen's go, under the
  live-tree guard (research-layer/CLAUDE.md).
- **Dormant until 2b-3:** no intraday strategy is in quarantine, so
  `--fetch-owed` fetches nothing and every daily path is proven unchanged
  (6.1).
- research-layer/CLAUDE.md's "Intraday" section gains the paper-stage rule,
  the cell key, the fetch step and the completeness rule; the "Quarantine
  refuses intraday until 2b-2" bullet is replaced.

## 9. Open points for Coen

- The note paragraph's exact wording (s4), reviewed when 2b-3 is ready to
  chain the note.
- The parity sample size (6.1): proposed 60 real dates across all classes;
  a larger sample costs only runtime (~4-7 min per date on the live chain).
