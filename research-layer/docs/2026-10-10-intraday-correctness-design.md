# Intraday correctness: measure intraday strategies on daily marks (design)

Date: 2026-10-10. Status: DRAFT for Coen's review. Morpheus build 2b
("engine v2"), sub-project **2b-1**, the first of three:

- 2b-1 intraday correctness (this document);
- 2b-2 the intraday paper stage (quarantine records intraday cells);
- 2b-3 the C2 grid and activation (the SP5 Phase 3 commit).

Decisions, all Coen's, 2026-10-10:
- Engine v2 stays in this repo (J2 deferred, D40 stands).
- The split and order above.
- **Q1 = A: intraday statistics are computed on daily marks.**

Vault: `project_morpheus_one_engine.md`, the "Build 2b kickoff" section.

## 1. Problem

The cell model is already (asset, timeframe). Screen and gauntlet load any
timeframe, and the Binance fetcher supports 15m, 30m, 1h, 4h, 12h and 1d.
But every statistic assumes that one bar is one day. Verified at source on
2026-10-10:

1. **Annualisation follows the market session, not the bar size.**
   - `engine.run_spec` takes `periods_per_year` from `cells.SESSION_PERIODS`
     (24x7 = 365), and that value drives `realized_ann_vol` (vol-target
     sizing) and short financing.
   - `engine.py:221` ranks volatility over a hard 365-bar window.
   - `plateau.TRADING_DAYS = 365` and `livegate.TRADING_DAYS = 365` are used
     by `plateau.annualized_sharpe` and
     `gauntlet_core._annualized_sharpe_from_returns`, which feeds the Sharpe
     floor.
   - `gauntlet_core.window_vol` multiplies bar returns by `sqrt(365)`; it is
     the volatility that normalises edge per trade.
   - `t_years = n / 365.0` counts equity points at `gauntlet.py:537` and
     `gauntlet_stats.py:460` (the DSR and the haircut).

   A 1h strategy would be scaled by sqrt(365) instead of sqrt(8760), so the
   0.4 Sharpe floor and the edge normalisation would misjudge every intraday
   candidate.
2. **Clustering would use the wrong data.** `gauntlet.intersect_returns`
   keeps the LAST row per date (`gauntlet.py:286-290`). An intraday
   strategy's effective-trials series would be its final hourly return each
   day, not its daily return.
3. **Quarantine is daily-only.** `quarantine.py` hardcodes `_1d.csv` (8
   sites). That is 2b-2's job; 2b-1 leaves it refusing intraday.

The chain is clean. All 17,333 registered strategies are 1d (checked
2026-10-10), so no intraday verdict exists to be wrong. 2b-1 changes only
future behaviour.

## 2. The rule

**Bar mechanics use the bar size. Every statistic uses the equity curve
marked once per UTC day.**

A daily strategy's bars ARE its daily marks. So every daily strategy's
arithmetic is unchanged, and that is the parity property in section 5.

## 3. Components

### 3.1 `pipeline/bars.py` (new)

```python
BARS_PER_DAY_24X7 = {"15m": 96, "30m": 48, "1h": 24, "4h": 6, "12h": 2, "1d": 1}

def bars_per_day(session: str, tf: str) -> int
def bars_per_year(session: str, tf: str) -> int   # SESSION_PERIODS[session] * bars_per_day
def daily_marks(series: list[tuple[str, float]]) -> list[tuple[str, float]]
```

- `bars_per_day` returns 1 for "1d" in every session.
- Intraday is supported only for session "24x7".
  - Any other session with tf != "1d" raises `IntradayUnsupported`, naming
    the session and the timeframe.
  - fx and US-equity intraday need broker data and session-hour decisions
    (D1), which are out of scope here.
- `daily_marks` keeps the LAST (date, value) of each UTC calendar date, in
  input order.
  - The date key is `date[:10]`. Both forms in this repo ("YYYY-MM-DD" and
    "YYYY-MM-DD HH:MM:SS") collapse to the same key.
  - The input must be sorted by time. Out-of-order input raises.

### 3.2 Engine: bar mechanics (`engine.py`)

- `run_spec` resolves `bars_per_year(session, tf)` in place of
  `SESSION_PERIODS[session]`. It is passed where `periods_per_year` goes
  today: `realized_ann_vol` (vol-target sizing) and the per-bar short
  financing (`short_financing_per_year / bars_per_year`).
- The volatility-percentile regime filter (`engine.py:221`) ranks over
  `bars_per_year(session, tf)` bars, one year. That replaces the literal
  365.
- Indicator lookbacks stay in bars: a 20-bar average on a 1h strategy is 20
  hours. Which lookback grids make sense intraday is 2b-3's question.
- For tf "1d", `bars_per_year == SESSION_PERIODS[session]`. The engine's
  daily arithmetic is identical.

### 3.3 Statistics: daily marks

At one boundary, every consumer of the engine's per-bar equity, and of an
asset's per-bar closes, takes them through `bars.daily_marks` first. The
365-based formulas stay as written.

- `gauntlet_core._annualized_sharpe_from_returns` / `plateau.annualized_sharpe`
  (the Sharpe floor, `train_sharpe`): returns from the daily-marked equity.
- `gauntlet_core.window_vol`: asset volatility from daily-marked closes.
- `gauntlet_core.daily_returns_with_dates` / `gauntlet.daily_returns_from_curve`
  (the clustering series and PBO inputs): daily-marked equity. This fixes
  the "last bar per date" defect.
- The `t_years` in `gauntlet.py` and `gauntlet_stats.py`: daily-marked
  points / 365.
- `walkforward.walkforward_report` (its `dates` folds) and
  `regime.regime_by_date` (the 200-day MA labels): daily-marked dates and
  closes, so a "200-day" regime means 200 days at every timeframe.
- Trade-based gate inputs need no change: edge per trade, edge decay, MC
  p05, ruin, cost stress, and `era_summary` by entry date. They are
  bar-size independent.
- `livegate` and `quarantine` are untouched (2b-2). Quarantine keeps
  refusing intraday specs; its existing `_1d` paths are the refusal.

### 3.4 Simulation cache (`simcache.py`)

`cache_key` takes the resolved `bars_per_year` where it takes
`periods_per_year` today. For 1d specs the value is the same number, so
every existing cache entry stays valid. Intraday entries can never collide
with daily ones.

## 4. Not in 2b-1

- Quarantine and livegate intraday (2b-2).
- Composer grids for intraday, the C2 fixed-grid declaration, excluding
  the 10 post-cutoff crypto cells, the intraday data refresh task, and
  activation (2b-3).
- Morpheus: its paper book already shows a TF column, and nothing intraday
  reaches it before 2b-2.
- **No chain writes.** The dated design docs and `docs/notes/` stay public
  (D40). The plan is private (stewartandco-agents internals).

## 5. Testing

### 5.1 Daily parity, the safety net for the existing chain

A parity run re-judges a sample of real registered 1d strategies on the old
code and the new. It follows the method of `tools/gauntlet_parity.py`: the
same truncated data, ENGINE_REV unchanged. It compares:
- the screen result;
- the six gate metrics;
- the gauntlet_stats figures (trials series, DSR inputs);
- the walk-forward, era and regime summaries;
- the simcache key.

**Rule: bit-identical metrics and identical verdicts.**

Sample: 200 strategies across all five classes, including EVERY registered
vol-targeted strategy and every strategy using the volatility-percentile
regime filter (or all of them, if fewer than 200).

### 5.2 Intraday correctness against known answers (synthetic data)

- **Equivalence.** A 1h series that moves only at UTC day boundaries, run
  as a 1h strategy, must give exactly the same Sharpe, `window_vol`,
  clustering series and gate metrics as the matching 1d strategy.
- **Vol targeting.** 1h returns with known hourly standard deviation σ
  give `realized_ann_vol == σ·sqrt(8760)`, and the matching notional.
- **Percentile window.** The regime rank on 1h uses 8,760 bars.
- **Clustering defect.** A 1h strategy whose last hourly return per day is
  noise, but whose daily return is the signal, clusters on the daily
  return.
- **`bars.py`.** The `bars_per_day` table; `daily_marks` on both date forms;
  out-of-order input raises; non-24x7 intraday raises
  `IntradayUnsupported`.
- **Quarantine still refuses an intraday spec**, unchanged.

### 5.3 Discipline

- Every changed formula gets a mutant that a test must kill.
- The full research-layer suite runs on the final head.
- The build is subagent-driven, with a task review per task and a final
  whole-branch review.

## 6. End-to-end proof on five assets (no live effect)

- **Data.** Fresh 1h bars for BTC, ETH, BNB, SOL and XRP USDT, fetched with
  `pipeline.crypto_data` into a SCRATCH data directory. The live `data/` is
  untouched.
- **Strategies.** A COPY of the live chain is the scratch registry. A spread
  of real registered 1d strategies is chosen: families, sizing rules (incl.
  vol-target), exits and regime filters. Their 1h twins on the five assets
  are registered by appending to the COPY only.
- **Run.** Screen, then the gauntlet worker, on the copy with scratch data
  and artifacts directories. Then `verify_registry.py` must report VALID on
  the copy.
- **Proof criteria.**
  1. Every twin runs to a verdict, or to a reported deferral, with no error.
  2. BTC's daily-marked volatility from 1h bars equals its 1d volatility
     over the same window. Binance's 1d bar closes at 00:00 UTC, which is
     the daily mark, so equality is expected up to bar-construction
     differences, and the report states any residual.
  3. Each 1h twin's Sharpe and gate metrics are tabulated beside its 1d
     original. Implausible gaps are investigated before merge.
  4. **Compute is measured:** seconds per strategy for screen and gauntlet,
     1h against 1d. 2b-3 sizes the 600-cell crypto grid against the
     worker's 25-minute runs and the loop window with this number.
- Results go to a private diagnostic (stewartandco-agents internals) and the
  vault.

## 7. Protocol note (drafted in 2b-1, chained in 2b-3)

Under this design, the gauntlet's recorded figures for an intraday strategy
are daily-mark statistics. A short note states this convention. Coen
approves its exact text, and it is chained verbatim **before the first
intraday verdict** (at 2b-3's activation). That is the v6.1-addendum
pattern. 2b-1 commits the draft under `docs/notes/` with a DRAFT header and
chains nothing.

Draft first line (the marker form other notes use):
`gauntlet-protocol-v6.1-intraday-daily-marks: intraday strategies are
simulated per bar and measured on daily marks.`

## 8. Shipping

- The merge into the live tree happens only on Coen's go, under the
  live-tree guard (research-layer/CLAUDE.md).
- **Daily behaviour is proven identical (5.1), and the intraday path stays
  dormant: `ACTIVE_CELLS["crypto"]` is empty until 2b-3.** No scheduled
  task can register an intraday strategy before then.
- research-layer/CLAUDE.md gains a short "Intraday: daily marks" section,
  covering the rule, `bars.py`, and the 24x7-only refusal.

## 9. Open points for Coen

- The 200-strategy parity sample (5.1): a larger sample costs only runtime.
- The draft note's wording (section 7), reviewed when 2b-3 is ready to
  chain it.
