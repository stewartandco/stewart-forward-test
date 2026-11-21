# SP4 Track 2b addendum: bond + metal ETF cells

**Status: Coen's go 2026-08-27 ("track 2b"). Declared here before any code.** Parent spec
`2026-08-24-sp4-track2a-addendum.md` framework + `2026-08-24-market-expansion-sp4-design.md`;
B1 pre-registration applies (both classes declare `benchmark: "self"`).

Context the declaration is made under, honestly: eq-gen1's 96 passes went 0/96 against
own-ETF buy-and-hold. Bond and metal ETFs are also drifting underlyings; every verdict here
carries `benchmark_relative` from birth, so a repeat of the beta illusion is visible on the
chain row itself, not in a post-hoc report.

## Class declarations (into `cells.CLASSES`)

- `bond_etf`: assets (manifest order) `SHY IEF TLT TIP LQD HYG EMB BND` (8; histories
  2002-2007 starts, ragged — the intersection machinery handles it); timeframes `("1d",)`;
  session `us_equity_5d` (NYSE-listed ETFs, same calendar as equity_etf; SESSION_PERIODS
  already has the entry); `periods_per_year` 252; `bar_kind` "ohlcv";
  `excluded_block_types` frozenset() (real ranges); `cost_model`
  `{"commission_per_side": 0.00010, "slippage_ticks": 0.00010, "short_financing_per_year": -0.005}`;
  `max_end_lag_days` 4 (same Tiingo cadence as equity_etf — re-verify observed lag at
  snapshot, record the one-liner); `benchmark` "self"; eras (recorded):
  `pre_gfc` (2002-07-26..2008-12-31), `zirp` (2009-01-01..2015-12-31),
  `hike_cut_cycle` (2016-01-01..2021-12-31), `post_2022` (2022-01-01..9999-12-31)
  (2022 named deliberately: the bond bear is the era that would expose long-bias here).
- `metal_etf`: assets `GLD SLV` (2); everything as bond_etf except `cost_model`
  short financing -0.0075 (Phase C table) and eras
  `pre_gfc` (2004-11-18..2008-12-31) then the same three cuts.

## Routing (declared; one deviation from the parent table, stated)

- `bond_etf` receives `rates` + `cross` cards. The parent spec's table calls rates→bond_etf
  a PROXY (rates cards are largely about rate FUTURES/derivatives, not the ETFs); every
  family citing a rates-tagged card therefore records `provenance.routed_via: "proxy"` +
  `proxy_card_ids` (the registration-level machinery from 885df6d), enumerable for
  Norgate-native re-tests.
- `metal_etf` receives `commodities` + `cross` cards natively (the enum has `commodities`;
  the parent table omitted it — DEVIATION, declared here: commodities cards about gold/
  silver are the closest native population), plus futures-tagged cards whose topics match a
  measured `METALS_PROXY_TOPICS` constant (same pattern as INDEX_FUTURES_PROXY_TOPICS:
  measure the real corpus through `Registry.cards(status=...)`, list the evidence in the
  build report, proxy-record citations).
- Crypto cells stay unrestricted (D2/C, unchanged).

## Build deltas (expected small; framework is generic)

1. `cells.py`: the two class entries + tests. LIVE_CLASSES unchanged until activation.
2. `composer.py`: ROUTING entries for the two classes; `METALS_PROXY_TOPICS` (measured);
   prompt helper must render honest class sentences (bond: "price returns, dividends and
   coupon distributions excluded — bond ETF longs materially understated"; metal: "price
   returns; GLD/SLV track spot via trusts"); schema enum: verify `universe.asset_class`
   accepts the two new values (add additively if not — same pattern as equity_etf).
3. `tools_dryrun_fx.py`: the `--asset-class` choices derive from CLASSES; verify the two
   new classes appear and the data-presence guard names the right snapshot commands.
4. Snapshot + dry runs per class (throwaway chains) = ship-bar step 3, controller-run.
5. NOTHING else: screen/gauntlet/benchmark/cache/parallel paths are class-generic as of
   b0846f4.

## Ship bar

Per parent §8: declare (this doc) → build + review → snapshot pin-verified → dry-run
generation per class → **Coen's activation call** (the two classes can activate together
or separately — his choice at the gate) → real generation(s) → SP4's free-lane grid is
then COMPLETE at 68 cells (30 crypto + 12 fx + 16 equity + 8 bond + 2 metal), and what
remains of market expansion is Track 3 (Norgate futures, ~08-30 decision).
