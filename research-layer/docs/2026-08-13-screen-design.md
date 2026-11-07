# Screening Engine v1 — design spec (2026-08-13)

The third pipeline stage: execute pre-registered block specs on daily OHLCV
training data, apply the screen gate, and chain verdicts + lifecycle
transitions. First consumer: the 22 `proposed` strategies from the Composer's
first run.

Kickoff decisions (Coen-approved):

| Decision | Choice |
|---|---|
| Screen gate | **Amended pre-results**: ≥ 40 trades AND net P&L > 0 after costs (SCHEMA v1's ≥100 floor is calibrated to intraday; 40 fits daily-bar frequency). Amendment chained as a `note` BEFORE any verdict exists |
| Data | Binance spot daily klines (BTCUSDT, ETHUSDT) fetched via public REST, CSVs + fetch script committed — every verdict reproducible from the public repo alone |
| Train fence | Screen sees ONLY bars ≤ **2023-12-31**. Everything after is gauntlet holdout; the engine must be unable to receive post-fence bars |
| Architecture | Pure-stdlib block interpreter, bar-by-bar event loop (auditability over vectorization; no numpy/pandas) |

## 1. Modules

| File | Responsibility |
|---|---|
| `pipeline/data_fetch.py` | CLI: fetch Binance spot 1d klines via `urllib` (endpoint `/api/v3/klines`, paginated 1000/req), write `data/BTCUSD_1d.csv` + `data/ETHUSD_1d.csv` (`date,open,high,low,close,volume`), print row counts + sha256 of each file. Exchange symbols BTCUSDT/ETHUSDT map to the specs' universe names BTCUSD/ETHUSD (USDT is the pricing proxy; mapping is fixed in code) |
| `pipeline/engine.py` | Pure functions only, no registry/IO: indicator helpers, per-block executors, `run_spec(spec, bars_by_asset) -> SpecResult{trades, equity, metrics}`. Deterministic: same spec + bars → identical output |
| `pipeline/screen.py` | CLI: load `proposed` specs from the registry, enforce the fence, run engine, apply gate, chain entries, write artifact bundles. `--dry-run` prints the results table and writes nothing |

CLI: `python -m pipeline.screen [--registry registry_log.jsonl] [--cutoff 2023-12-31] [--data-dir data] [--dry-run]`

## 2. Screen protocol note (pre-results anchor)

Before the first real screen run, one `note` entry is chained with text
beginning `screen-protocol-v1:` and stating: the amended gate (≥40 trades,
net P&L > 0 after costs), the train fence (2023-12-31), the cost model
applied (10 bps commission + 5 bps slippage per side, from each spec's
`cost_model`), and the execution conventions of §3 in summary form.

`screen.py` HARD-REFUSES a non-dry run unless a `note` whose text starts with
`screen-protocol-v1:` already exists in the chain — the protocol provably
predates every verdict. Any future change to engine semantics after a verdict
exists requires chaining a new `screen-protocol-v2` note before re-running;
the guard checks for the protocol version compiled into `screen.py`
(`PROTOCOL = "screen-protocol-v1"`).

## 3. Execution semantics (engine.py)

**Timing.** All signals/indicators are computed on bar close t; entries and
signal-exits fill at open(t+1). No same-bar signal-and-fill.

**Warmup.** Every indicator requires its full lookback of history before it
emits values; until ALL of a spec's indicators (including gates' 365-bar
percentile window) are warm, no entries are taken. No partial-window values.

**Entry executors** (one open position per asset at a time; a new signal while
in a position is ignored):

| Block | Semantics |
|---|---|
| `ma_cross` | Long while SMA(fast) > SMA(slow); enter on cross-up, signal-exit on cross-down (barriers still apply). No shorts (grammar has no direction param) |
| `channel_breakout` | Enter long when close(t) > max(high of prior `lookback` bars); if `direction: both`, enter short on close(t) < min(low of prior lookback bars). Barrier/time exits only |
| `zscore_reversion` | z = (close − SMA(lookback)) / stdev(lookback). Enter long at z ≤ −z_entry; if `both`, short at z ≥ +z_entry. Barrier/time exits only |
| `trend_scan` | OLS of close on time over backward windows {20, 30, …, max_lookback}; take the window with max \|t-stat\| of slope; enter long if that t ≥ t_min. Long-only |

**Gates** (`regime`/`filter` roles): evaluated at signal time; block NEW
entries only, never force-close an open position. `regime_ma`: entries allowed
while close > SMA(ma_len). `vol_percentile`: realized vol = stdev of daily
log returns over `lookback`; entries allowed while its percentile rank over
the trailing 365 bars ≤ max_pctile.

**Exits.**
- `atr_stop`: stop = entry ∓ mult × ATR(atr_len) at entry (Wilder ATR). If a
  spec carries multiple stops, the tightest applies.
- `r_multiple`: target = entry ± r × |entry − stop|.
- Barrier checks each bar against high/low. Same-bar stop AND target touch →
  **stop fills** (conservative). Open gaps through a barrier fill at the open
  price, not the barrier price.
- **Exit rules v7** (D15, 2026-09-03; `version: 2` specs; full semantics in
  `docs/2026-09-03-exit-rules-v7-design.md`, chained note
  `docs/notes/exit-rules-v7.md`): no time stop of any kind (`time_stop`
  retired) and no implicit exit. Every stop is a LEVEL placed by an indicator
  at the signal bar (`atr_stop`/`atr_stop_dense`, `swing_stop`, `ma_stop`,
  `channel_stop`, `band_stop`; `pct_stop` retired), fixed at entry, no
  trailing; a level not strictly on the adverse side of the entry makes the
  signal ineligible (counted as `stop_invalid`). Declared `exit` blocks
  (`ma_crossunder`, `channel_exit`, `zscore_revert`, `tstat_decay`,
  `regime_flip`) are evaluated on close t and fill at open t+1, after the
  barrier checks at that bar. `exit_reason` is `stop`, `target` or
  `signal:<type>`; an open position at sample end is never a trade
  (`open_at_end`). Metrics record `exit_reasons`, `open_at_end`,
  `stop_invalid`; no gate reads them.
- Legacy (`version: 1`) semantics unchanged, frozen by the golden in
  `pipeline/test_exit_rules_v7.py`: `pct_stop` = entry × (1 ∓ pct);
  `time_stop` closes at the open of (entry_bar + max_bars); `ma_cross*`
  entries carry an implicit cross-down signal exit filled at next open.

**Sizing** (`risk` role): `fixed_fraction` risks f of current equity per
trade: notional = f × equity / stop_distance_pct. `vol_target`: notional =
equity × (ann_vol / realized_ann_vol(lookback)). Both capped at 1.0 × equity
(no leverage). Position P&L accrues to the per-asset book at trade close.

**Costs.** Applied per side from the spec's `cost_model`: commission_per_side
+ slippage_ticks (both fractions of notional; 10 + 5 bps for v1 specs).

**Documented conventions** (implemented behavior, recorded pre-results):
- Signals are edges, not levels: an `ma_cross` cross-up that fires while a
  gate is closed is NOT re-honored when the gate later opens — the next
  fresh cross is required. Gate-blocked signals are lost, not queued.
- Barriers do not apply on the entry fill bar itself (checks start the
  following bar) — conservative for daily bars with unknown intrabar order.
- (Legacy `version: 1` only.) If a time-stop deadline coincides with a
  same-bar gap through the stop, the exit price is the open either way;
  `exit_reason` records `time`.
- A zero stop distance (degenerate flat data) skips the entry rather than
  sizing it.

**Multi-asset.** Each asset in `universe.assets` runs an independent book
with an equal share of capital; combined equity curve = mean of per-asset
equity curves; `trades` = sum of per-asset counts; metrics computed on the
combined curve.

## 4. Gate + lifecycle writes (screen.py)

Per spec, metrics per SCHEMA: `{trades, net_pnl, win_rate, max_dd}` where
net_pnl is the combined book's total fractional return, max_dd the peak-to-
trough fraction of the combined curve.

Gate: PASS iff trades ≥ 40 AND net_pnl > 0. Chained per spec, in order:

1. `state_change` proposed → screened, reason "screen run <run-date>"
2. `verdict` stage=screened, verdict=pass|fail, metrics, artifacts_hash
3. `state_change` screened → gauntlet (pass) or screened → graveyard (fail,
   reason `trade_count` | `net_negative` — trade_count wins if both fail)

All 22 specs are screened in one run; a partial-write guard mirrors the
Composer's (loud PARTIAL WRITE warning on mid-batch failure).

## 5. Artifacts

`artifacts/<strategy_id>/` committed to the repo:
- `trades.csv` — asset, side, entry_date, entry_px, exit_date, exit_px,
  exit_reason, return_net
- `equity.csv` — date, combined_equity
- `config.json` — full spec, PROTOCOL string, cutoff, data file sha256s

`artifacts_hash` = sha256 of the three files' bytes concatenated in the fixed
order trades/equity/config (each normalized to `\n` line endings). The
verdict carries the hash; the bundle makes it reproducible.

## 6. Testing (offline; engine tests need no network — data_fetch tested
against a canned response fixture)

- Per-executor unit tests on hand-built synthetic bars with hand-computed
  expected trades (each entry type, both stop types, target, time stop,
  same-bar tie-break, gap fill, gate blocking, sizing cap).
- Determinism: run_spec twice → identical output.
- **Fence test**: screen.py passes only bars ≤ cutoff to the engine; a spec
  whose only signals occur post-fence yields zero trades.
- Protocol guard: real run without the protocol note → refused, exit 1;
  dry-run allowed.
- Gate boundaries: 39 trades/positive → graveyard(trade_count); 40/negative →
  graveyard(net_negative); 40/positive → gauntlet.
- Integration: tmp registry with registered blocks + accepted card + 2 specs
  → screen → verify_registry.py exits 0; funnel shows gauntlet/graveyard;
  graveyard is terminal.
- Live-chain regression: current 368-entry registry still validates.

## Out of scope

Gauntlet battery (walk-forward, Monte Carlo, DSR), quarantine wiring into the
forward-test log, scheduling/automation. The holdout (2024+) remains
untouched by everything in this build.
