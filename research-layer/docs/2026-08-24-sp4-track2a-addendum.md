# SP4 Track 2a addendum: equity-index ETF cells

**Status: DRAFT pending Track 1's first real fx generation completing cleanly (spec §8 /
D3 sequencing). Parent spec `2026-08-24-market-expansion-sp4-design.md`; this addendum
declares only what §2 deferred to track start.**

## Class declaration (goes into `cells.CLASSES["equity_etf"]`)

- `assets` (16, manifest order): SPY QQQ IWM DIA MDY EFA EEM EWJ EWG EWU EWA EWC EWH FXI EWZ EWY
- `timeframes`: `("1d",)` · `session`: `us_equity_5d` · `periods_per_year`: 252
  (`SESSION_PERIODS` gains `us_equity_5d: 252`)
- `bar_kind`: `"ohlcv"` — REAL OHLC ranges (Tiingo daily bars), so range-requiring blocks
  are ELIGIBLE. This forces the T4-rider-3 change: block exclusions move from
  "any non-crypto class" to a per-class declaration (`excluded_block_types` in the class
  entry: fx = the RANGE_REQUIRING four; equity_etf = empty).
- `cost_model`: `{"commission_per_side": 0.00010, "slippage_ticks": 0.00010,
  "short_financing_per_year": -0.005}` (Phase C table: 2.0 bps/side split evenly;
  short financing -0.5%/yr).
- `max_end_lag_days`: 4 (Tiingo EOD publishes next morning; weekend + one holiday of
  headroom — verify the observed lag at snapshot time and correct before approval).
- `eras` (recorded, not gated): `dotcom_gfc` (1993-01-01→2008-12-31), `qe_bull`
  (2009-01-01→2019-12-31), `covid_cycle` (2020-01-01→2021-12-31), `post_2022`
  (2022-01-01→9999-12-31).

## Honesty limits (carried on every surface)

1. Split-adjusted PRICE returns; dividends excluded — long equity edges systematically
   understated (worst for high-yield markets). 2. Survivorship: funds alive in 2026.
3. Histories are ragged (SPY 1993 vs FXI 2004) — the declared per-class comparability and
   intersection machinery from Track 1 already handles this; `trials_common_days` will
   shrink to the youngest member's overlap when clustering spans the class.

## Routing

`equities` + `cross` cards → equity_etf cells (per the parent spec's table). The
futures→equity_etf PROXY lane (index futures cards) ships HERE per §10.8: a futures-tagged
card routes to equity_etf only when its topics match a declared index-futures topic set
(declared in the composer as data, reviewed at build time), and the registration records
`routed_via: "proxy"`.

## Build deltas vs Track 1 (expected small)

1. `cells.py`: class entry + `SESSION_PERIODS` + per-class `excluded_block_types` field
   (fx keeps its four; crypto empty; consumer in composer switches from the
   "any non-crypto" coupling to this field).
2. `tradfi_data.py`: no code change expected — `bar_kind: "ohlcv"` writes real OHLCV
   through the existing path; verify volume and the close/close_raw choice (adjusted
   `close` is what the sha canon pins; the CSV must carry the SAME adjusted series).
3. Composer: nothing beyond the exclusions-field switch + proxy topic set; the per-class
   prompt/schema helpers generalise as built (verify the equity_etf sentence renders
   honestly: names dividends-excluded and survivorship).
4. Screen/gauntlet: nothing — per-class machinery is already generic.
5. Dry-run harness: `--asset-class` parameterisation (currently fx-only? verify) + one
   equity fixture family run before the real generation (same ship bar as Track 1).

## Ship bar

Identical to §8: snapshot pin-verified → tests (crypto AND fx byte-identical this time) →
dry-run generation on equity_etf cells → Coen's go → activation (`LIVE_CLASSES` gains
`equity_etf`) → first real generation → Track 2b.


## Pre-registration: benchmark-relative control (B1, Coen 2026-08-26)

Declared BEFORE implementation (this commit) per v6's record-don't-gate discipline:

- New class field `benchmark`: `"self"` for equity_etf (and bond/metal at 2b), `None` for
  crypto and fx. Declared in `cells.CLASSES`, consumed by the gauntlet.
- Every gauntlet verdict on a cell whose class declares `benchmark: "self"` RECORDS
  `metrics["benchmark_relative"]`: `{"window": "oos", "strategy_net", "buy_hold_net"
  (same-window buy-and-hold of the cell's own asset, one round trip of the class cost
  model), "excess" (strategy minus buy-hold), "basis": "price returns, dividends excluded
  on both sides"}`. Classes with `benchmark: None` carry NO key (absence = not applicable,
  never a null placeholder).
- RECORDED, NOT GATED. Any future gate on `excess` requires its own pre-registration.
- Motivation (eq-gen1, 2026-08-26): 96/96 quarantine passes were LONG index-ETF entries in
  14 sibling groups -- an absolute Sharpe floor cannot distinguish edge from 30 years of
  equity drift. The control makes the comparison a chain row instead of a caveat.
- One-off report (NOT chain data): `tools_benchmark_backfill_report.py` recomputes the
  same quantity for the 96 existing eq-gen1 quarantine occupants from committed artifacts
  and writes `docs/runs/2026-08-26-eq-gen1-benchmark-report.md`. The chain itself gains
  `benchmark_relative` only on verdicts written after this feature ships.
