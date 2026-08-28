# Market data + universe expansion design (SP5)

**Status: APPROVED IN SESSION by Coen 2026-08-28 (design dialogue; spec review pending). Written BEFORE any implementation. Parent conventions: `2026-08-24-market-expansion-sp4-design.md` (declared space, ship bar), `2026-08-24-sp4-track2a-addendum.md` (B1 benchmark pre-registration precedent), `2026-08-27-sp4-track2b-addendum.md` (class declaration precedent).**

## 0. Decisions (Coen, 2026-08-28)

| # | Decision | Call |
|---|---|---|
| D1 | Universe rule | Declared present-day rule: top-100 by market cap today, exclusions declared, Binance USDT spot pair with >= 2y history required, survivorship bias openly documented |
| D2 | TF scope | Full 6-TF grid DECLARED (15m 30m 1h 4h 12h 1d); activation staged, 1d tranche first |
| D3 | Benchmark program | `benchmark: "self"` for crypto AND fx, plus a read-only backfill audit of the existing 20-strategy pooled-crypto quarantine cohort |
| D4 | Class shape | Migrate the `crypto` class IN PLACE (5 -> 100 assets, per-cell path); a cell-level activation gate is built first because crypto is already live |
| D5 | Activation staging | Declare 6-TF, activate 95x1d first; intraday tranches follow data + quarantine support, each tranche its own Coen-gated commit |
| D6 | Sweep control | Cell rotation per cycle (~12 assets/cycle, cursor in loop state), pre-registered here |
| D7 | End goal | Multiple timeframes x multiple assets x multiple markets. All new machinery is class-generic; crypto is the first market through it |
| D8 | Continuity | Chain stays continuous; every found strategy is kept. Once the full data universe is landed, a UNIFIED RE-RUN of the whole registry through the gauntlet is executed as its own pre-declared governance event (s10) |
| D9 | Re-trials | The resurrection guard's PERMANENT exclusion is removed. A buried composition becomes re-testable once its cell's data end is >= 6 months past the burying verdict's cutoff, and at declared protocol events (the unified re-run). Every re-trial is a NEW numbered trial |
| D10 | Caps | Caps become QUEUES. The 60-sibling refusal is replaced by split-and-carry across cycles; nothing proposed is ever silently dropped. The gauntlet is the only place an edge can die |
| D11 | Edge numbering | Every registered strategy carries a sequential chain-order number (#0001...) on every human-facing surface (reports, audits, Morpheus pipeline UI); the 16-hex id is demoted to provenance detail |
| D12 | Variation coverage | Bounded by the curated declared per-block param grids (the "reasonable" fence); a coverage map reports declared-but-untested points per family structure; proposer steering toward gaps is a later, separate decision |

Priority order (Coen): data + benchmark machinery first, grid expansion second, activation last.

## 1. End goal and scope

The end state is a declared grid of markets x assets x timeframes, every class on the per-cell path, every class carrying a benchmark control, one continuous chain. This spec covers:

- the crypto universe expansion (5 -> 100 assets, 6 declared TFs), because crypto has a free intraday data lane today;
- the class-generic machinery (cell activation gate, sweep rotation, data snapshot conventions) that every later market expansion reuses;
- the benchmark program (fx flip + crypto self-benchmark + pooled-cohort audit).

**Multi-TF is data-gated per market.** Binance covers all 100 crypto assets intraday, keyless. Tiingo free is daily-only for the 26 ETFs; FRED fx is a daily fix (no intraday exists at that source). The tradfi classes therefore keep `("1d",)` until a paid intraday source is bought; when that happens, widening a tradfi class's `timeframes` tuple rides on the machinery built here with no new mechanism. Futures stay out of scope entirely (Norgate track, separate campaign, STOP until Coen's readout).

## 2. Universe rule (D1)

A one-time selection script (`tools_select_crypto_universe.py`, layer root) executes the declared rule:

1. Rank by market cap from CoinGecko's keyless `/coins/markets` endpoint, snapshot taken at selection time.
2. Walk the ranking, EXCLUDING: stablecoins (declared symbol list + pegged-price heuristic, both recorded), wrapped/staked/bridged derivatives (declared pattern list: `W*`-wrapped, `ST*`-staked, etc., every exclusion recorded with its reason), assets with no Binance USDT spot pair, assets with < 2 years of Binance USDT daily history.
3. Admit the first 100 qualifying assets. The 5 incumbents (BTC ETH SOL XRP BNB) are expected to qualify inside the top 100; the script asserts they do.

Output: `universe_manifest.json` (committed), carrying the selection timestamp, the full ranked walk with every exclusion and its reason (the `pin_universe` excluded-with-reasons convention from trading-systems `strat/xs_data.py`), and the admitted 100 with their Binance symbols. **The manifest is pinned: the script refuses to overwrite an existing manifest.** Re-selection is a new declared event, never a silent refresh.

`cells.py`'s asset tuple is then written as a LITERAL from the manifest, with a test asserting manifest <-> tuple agreement. The grid stays declared in code (Search-Space-First); the manifest is the provenance of the declaration, not a runtime input.

**Honesty limit (survivorship):** today's top-100 backtested to listing date is winners picked after the race. This bias is declared here, carried as a numbered limit on every report surface that touches expanded-universe results (the dividends-excluded convention), and forward quarantine remains the honest arbiter.

## 3. Cell-level activation gate (D4 prerequisite, class-generic)

`LIVE_CLASSES` gates classes; it cannot stage a live class's expansion. New declaration in `cells.py`:

```python
ACTIVE_CELLS = {
    # class -> {"assets": tuple | "all", "timeframes": tuple | "all"}
    "crypto":     {"assets": (), "timeframes": ()},   # empty until activation
    "fx":         {"assets": "all", "timeframes": "all"},
    "equity_etf": {"assets": "all", "timeframes": "all"},
    "bond_etf":   {"assets": "all", "timeframes": "all"},
    "metal_etf":  {"assets": "all", "timeframes": "all"},
}
```

- `active_cells(cls)` = the cross product restricted to the active subsets; import-time assertions: every ACTIVE_CELLS entry subsets its class's declared grid, every LIVE_CLASSES member has an ACTIVE_CELLS entry.
- `expand_family_for_class` sweeps `active_cells(asset_class)` instead of `class_cells(asset_class)`. For the four tradfi classes `"all"`/`"all"` makes this byte-identical to today (pinned by test).
- `validate_cell` continues to accept the whole DECLARED grid (declaration admits data/import work; activation admits sweeping).
- Growing an ACTIVE_CELLS entry is the denominator event: Coen's own reviewed commit, one line, exactly the LIVE_CLASSES activation discipline extended to cell granularity. The `cells.py` docstring is amended to say so.

## 4. Crypto migration (D4)

At Phase 2 (declaration), `cells.py`'s crypto entry becomes: `assets` = the 100 manifest symbols (USDT spot names), `timeframes` unchanged (6 declared), `cost_model` = `{"commission_per_side": 0.001, "slippage_ticks": 0.0005}` (the literal values of `composer.COST_MODEL`, now declared where every other class declares them), `benchmark: "self"`, other fields unchanged. ACTIVE_CELLS crypto stays EMPTY, so nothing sweeps.

The composer's crypto special case (`expand_family`, `ALLOWED_ASSETS`, `UNIVERSE_BASE`) keeps serving live crypto triggers while ACTIVE_CELLS crypto is empty. **The activation commit (Phase 3) both populates ACTIVE_CELLS crypto (100 assets x ("1d",)) and switches the composer branch to route crypto through `expand_family_for_class`** — after which crypto specs are single-asset per-cell, benchmarked, like every other class. The legacy branch is deleted in that same commit; the engine's 2-asset mean-combine path stays (it serves the 155 registered pooled specs in registry re-simulation forever).

Legacy continuity:
- The 155 pooled BTCUSD/ETHUSD registrations and their verdicts stand untouched (gauntlet reads each spec's own universe; they can never be candidates again — pinned by a new test: benchmark "self" + a multi-asset LEGACY spec never meet in `_benchmark_relative`).
- `data_fetch.py` keeps feeding quarantine's tracked `BTCUSD_1d.csv`/`ETHUSD_1d.csv` daily, unchanged.
- Naming split (`BTCUSD` legacy vs `BTCUSDT` cells) persists on the chain and stays documented; new cells use manifest symbols only.

## 5. Sweep rotation (D6)

Per-generation cost control, declared here: a crypto generation sweeps a ROTATING WINDOW of 12 assets (x active TFs), cursor persisted in `logs/loop_state.json` (`rotation_cursor` per class), advancing per completed generation, wrapping at the universe end. The loop passes the window to the composer as an explicit `--assets` subset. Full coverage in 9 generations (100/12, last window short); each generation lands ~1,400 registrations at observed family density (2A scale, proven manageable). Manual `--assets` override on the composer bypasses rotation for hand runs (recorded in provenance as today).

N accounting is untouched: the trial denominator counts REGISTRATIONS, and the BH bar counts quarantine survivors; neither reads the sweep schedule. Rotation is a cost schedule, not a selection mechanism — every active cell is swept with equal frequency, pinned by test.

## 6. Data machinery (build priority 1)

New module `pipeline/crypto_data.py`, mirroring `tradfi_data.py`'s conventions (explicit runs, verify-all-then-write-all, snapshot manifest), replacing nothing yet:

- Keyless Binance `/api/v3/klines`, per-interval pagination (limit 1000), incremental resume from the existing CSV tail, atomic write (`.tmp` + replace), still-open final bar dropped (the `close_time` future check).
- Retry discipline copied from the scanner's `_get_with_retry`: 3 tries, linear backoff, HTTP 429/418 honour `Retry-After` capped at 120s, a terminal 418 raises an explicit IP-ban error and STOPS the whole run. Fixed inter-page sleep >= 0.15s. **A 418 bans this box's IP for every Binance consumer (paper bot included); pacing is deliberately conservative and a bulk backfill never runs concurrently with itself.**
- Output: `data/{SYMBOL}_{tf}.csv` in the standard 6-column format, plus `data/crypto_snapshot_manifest.json` (key-wise merged provenance per file: fetched_utc, rows, sha256, first/last date — the tradfi manifest convention).
- CLI: `python -m pipeline.crypto_data fetch --timeframes 1d [--assets ...]`; assets default to the universe manifest. Refuses timeframes outside the declared grid.

Backfill plan: the 1d cold backfill for ~95 new assets is ~400 requests (minutes, supervised). The intraday backfill (~58k requests at full grid) is DEFERRED to the intraday tranche and runs as an OS-detached long job with the same ban-safe pacing.

Daily refresh: at Phase 3 activation, a scheduled task (`26_CryptoGridRefresh`, pattern of `24_TradfiFreeRefresh`) refreshes the ACTIVE grid's 1d files. Registered via `setup_scheduler.bat` + `$RETRY_TASKS` in the same pass (scheduler standing rule); pinned in the sentinel manifest DELIBERATELY, only after its exit code is proven trustworthy (standing rule). Until activation no schedule exists — snapshots are explicit, like tradfi.

Same-day discipline: crypto's `max_end_lag_days: 0` means all active crypto cells must end the same calendar day or screen refuses the run. The refresh task fetches the whole active grid in one run for exactly this reason.

## 7. Benchmark program (D3, build priority 1)

**fx flip.** `cells.py` fx `benchmark: None -> "self"`. The declared-rationale comment is REWRITTEN, not just the value: the old text ("fx has no long-only drift to separate from skill") is replaced with the new rationale — a recorded control is strictly more information than none, and recorded-not-gated means no verdict moves. The `basis` string becomes per-class-honest: fx reads "price returns, carry excluded on both sides" (a USD-per-foreign hold's true driver is carry, which this control cannot see — declared limitation). Tests updated: `test_cells.py:175`, `test_gauntlet_classes.py` absence tests for fx become presence tests; crypto's absence test survives until Phase 3.

**Crypto self-benchmark** lands with the migration: per-cell single-asset specs satisfy `_benchmark_relative`'s single-asset requirement; `cost_model` is populated at declaration. No gauntlet code change. Recorded, not gated, exactly like eq/bond/metal (B1).

**Pooled-cohort backfill audit** (`tools_benchmark_backfill_report_crypto.py`, eq-precedent clone): chain-derived cohort (asset_class crypto + state quarantine, EXPECTED_N refusal recounted at run time, currently 20), READ-ONLY (zero chain entries, one markdown report under `docs/runs/`), computing three controls per strategy over the committed OOS artifacts: BTC-hold excess, ETH-hold excess, and 50/50 daily-rebalanced basket excess (matching the engine's mean-combine structure). Costs from each spec's own committed `config.json`. Also fixes the eq script's raw date-compare asymmetry (uses `_date_le` semantics). The eq report's lesson stands ready: if all 20 are beta, that is the finding.

## 7b. Family openness (D9-D12)

**Context (verified at source):** protocol-v6 already retired sibling selection ("there is no selection... a sibling's score is not evidence about this strategy", `gauntlet.py:345-364`); composition dedup is exact-identity only (`composer.py:195`); the two remaining mechanisms that could exclude an edge without a gauntlet verdict are the resurrection guard's permanence and the sibling cap's reject-not-clip behavior (`composer.py:404`). Both are changed here.

**Re-trial protocol (D9).** `screen_siblings`' permanent exclusion becomes burial-with-expiry: a composition whose fingerprint matches a registered strategy is dropped ONLY IF the existing registration's burying verdict has a cutoff less than 6 months behind the target cell's current data end. Past that window (or at a declared protocol event such as the unified re-run, D8), the composition re-registers as a NEW strategy with a new id and number - a new trial, counted in N, raising the BH bar honestly. Rationale: the gauntlet is deterministic, so a same-data re-test is a known answer bought at the price of a higher bar for every live survivor; a re-test after the world has changed is a genuinely new question. In-run and in-cycle duplicates remain malformed/dropped as today (those ARE same-data). "No resurrection" is not a chained rule (verified: the chain contains no such note), but this loosening is still PRE-DECLARED in a chained note before the first re-trial registration exists, per the ratchet's spirit.

**Caps become queues (D10).** `validate_family`'s "exceeds cap - rejected, not clipped" refusal is replaced by split-and-carry: a family whose sweep exceeds the per-cycle sibling bound registers the first window now and QUEUES the remainder (persisted in loop state, visible in `pipeline_status.json` items), draining across subsequent cycles until fully tested. The same applies to any per-cycle bound in this design (the 12-asset rotation is already a schedule, not a filter). Invariant, pinned by test: no proposed variation is ever dropped without either a gauntlet verdict or a queue entry. The gauntlet is the only place an edge can die.

**Edge numbering (D11).** The chain's append-only registration order defines a stable sequential number per strategy (#0001 upward, never renumbered). Surfaced everywhere human-facing: gauntlet console lines, docs/runs reports, the pooled-cohort audit, and Morpheus's pipeline/starfield/inspector surfaces (morpheus-hub, separate repo, own commit wave) - hex ids demoted to provenance tooltips/detail. Derivation is a pure chain read; no identity mechanics change.

**Variation coverage (D12).** The declared per-block param grids are the "reasonable" fence - params snap to grids at fingerprint time, so an absurd combination cannot exist unless the grid declares it; widening a grid is a reviewed, declared edit. A coverage-map report (read-only, docs/runs convention) shows tested vs declared-untested points per family structure and cell, so unexplored variations are visible instead of dependent on proposer luck. Steering the proposer toward gaps is explicitly OUT of this spec - its own decision later, once the map exists to steer by.

## 8. What this does to N and the bar

- Declaration (Phase 2) moves NOTHING: `registered_n` and the BH bar are chain-population quantities.
- Activation (Phase 3) grows registrations ~1,400/generation; the quarantine pool's BH bar rises as new survivors enter the one shared pool. An honest zero on any tranche is an acceptable outcome (bond/metal precedent). Never claim both pipelines clear one bar (standing).
- Compute: the $20/month budget cap meters LLM spend ONLY; gauntlet wall-clock is invisible to it. Rotation (s5) is the declared compute control. The clustering pool grows toward ~14k registrations over the first full rotation (~8 generations); the perf-proof extrapolation (~8-9 min clustering at n~5k) says this is tolerable but must be WATCHED — a chain index / clustering budget review is pre-committed at n >= 10,000 registrations, before the wall, not after.

## 9. Honesty limits (carried on every surface)

1. Survivorship: universe selected by today's outcomes (s2). Documented, not corrected; forward quarantine arbitrates.
2. Benchmark basis is price returns only: dividends excluded (ETFs), carry excluded (fx), staking/funding yield excluded (crypto).
3. Ragged histories: younger listings enter with < full history; per-cell OOS windows differ. Recorded per cell, never padded.
4. The 155 pooled legacy crypto strategies predate cell benchmarks; their audit (s7) is a read-only report, their chain verdicts stand.
5. More cells means more trials: pass counts on the expanded grid are NEVER evidence by count ("productive, not good" — the gen-5 lesson).

## 10. Phasing and ship bars

**Phase 1 — data + benchmarks (no denominator movement).** Universe script + committed manifest; `crypto_data.py` + 1d backfill for the 100; fx flip + test updates; pooled-cohort audit report; edge numbering in reports/audit (D11, chain-derived); variation coverage-map report (D12). Ship bar: suite green, manifest committed and pinned, all 100 1d CSVs present with snapshot provenance, audit + coverage reports delivered to Coen.
**Phase 2 — declaration + machinery (behavior-frozen for sweeping; family-openness live on merge).** Addendum-grade edits: ACTIVE_CELLS gate (tradfi `"all"` pinned byte-identical), crypto class migration in `cells.py` (ACTIVE_CELLS empty), rotation machinery, re-trial protocol + sweep queues (D9/D10, their chained pre-declaration note committed FIRST), same-pass test-pin updates (`test_cells.py` counts/order/tuples, `test_loop.py:865`, composer tests). Morpheus UI numbering (D11) ships as its own morpheus-hub commit wave alongside. Ship bar: full suite green, a dry-run crypto generation on the DECLARED grid via explicit `--assets`/`--timeframes` override producing valid single-asset specs (registered nowhere), live loop behavior verified unchanged (crypto trigger still legacy path).
**Phase 3 — activation (Coen-gated).** One reviewed commit: ACTIVE_CELLS crypto = 100 x ("1d",) + composer branch switch + legacy branch deletion. Plus: `26_CryptoGridRefresh` registered, watermark/threshold review for crypto (routing is card-side; watermark needs no reseed — verified), first supervised generation.
**Later tranches (each Coen-gated):** intraday data backfill + quarantine's 1d-hardcode extension -> intraday ACTIVE_CELLS tranches; tradfi TF widening when an intraday source is bought; a `commodity_etf` class (Tiingo free lane, bond/metal-precedent addendum — Coen 2026-08-28); commodity FUTURES stay with the Norgate decision (separate track); prediction markets are flagged as a FUTURE CAMPAIGN needing its own design (event contracts, different edge type — not a class entry here); **the unified re-run (D8): once the declared data universe is fully landed, the whole registry is re-run through the gauntlet under one regime — pre-declared here, executed as its own governance event (Lane A protocol note at execution time), keeping every found strategy and the continuous chain.**

## 11. What does NOT change

Chain continuity and every existing verdict; quarantine clocks (first assessment 2026-10-17); the reader/triage/D31 pipeline; loop trigger mechanics (cards-based, class watermarks); tradfi lanes and their tasks; `cfg.timeframes` vs `TF_MS` separation in trading-systems (untouched — all fetching here is research-layer-native); futures (Norgate) — STOPPED pending Coen's readout. Graveyard VERDICTS stand unedited forever; what changes (D9) is that a buried COMPOSITION may re-enter as a new trial once its re-trial window opens.

## 12. Risks and open items

- Quarantine's chain-write window grows with pool size (known, ~2.4 min now, quadratic) — the n >= 10k review (s8) covers the same wall.
- CoinGecko keyless rate limits are tight (~5-15 req/min): the selection script batches (250 assets/page, 1 page usually suffices) and is a one-time run.
- Binance listings < 2y are excluded by rule; borderline histories (2.0-2.5y) produce thin OOS windows — recorded per cell (honesty limit 3), not special-cased.
- The consecutive-deferral escalation policy for the loop (pre-existing open judgment item) becomes more likely to matter as generations grow — unchanged here, flagged.
