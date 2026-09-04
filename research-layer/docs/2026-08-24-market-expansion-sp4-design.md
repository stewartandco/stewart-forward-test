# Market expansion sub-project 4: non-crypto universes in the research layer

**Status: APPROVED by Coen 2026-08-24 in session (scope, routing, track order, data
architecture each confirmed). Spec written same day.**

Parent project: vault `project_market_expansion.md` (sub-project 4 of the 2026-08-23
decomposition). Sibling evidence: W3 Phase C (`trading-systems/docs/w3-phaseC-results-20260823.md`)
ran the same free-lane universe as ONE pooled CTA sleeve and recorded 0/0/18 DEAD; this
sub-project tests composer-bred families PER CELL instead, which is a different question,
on the same honestly-limited data.

## 0. Decisions (Coen, 2026-08-24)

| # | Decision |
|---|---|
| D1 | Grid = **as large as the free lane allows**: all 38 pinned instruments become cells (12 FX + 16 equity-index ETFs + 8 bond ETFs + GLD/SLV), daily timeframe only (the lane has no intraday). Per-asset isolation stays absolute: a cell is tested on its own data with its own metrics, never pooled. |
| D2 | Routing = **B+C**: crypto cells keep today's unrestricted card feed (no mid-funnel behaviour change); non-crypto cells receive class-matched cards + `cross`-tagged cards; futures/rates cards proxy-route to the nearest ETF/FX cells with `routed_via: "proxy"` recorded on the registration so Norgate-native cells can re-test them later. |
| D3 | Build order = **one class per track, shipped smooth and bugless before the next**: Track 1 FX (12 cells) → Track 2a equity-index ETFs (16) → Track 2b bond + metal ETFs (10) → Track 3 futures (Norgate-gated, native cells + proxy re-tests). |
| D4 | Data architecture = **pinned snapshot adapter** (option A): the research layer copies series out of trading-systems, verifies each against `free_universe_manifest.json` sha256, refuses unpinned or mismatched series, and records the manifest `snapshot_utc` on every registration built from it. No runtime cross-repo reads, no duplicate fetcher. |

This spec covers the standing framework plus Track 1 (FX) in full. Tracks 2a/2b reuse the
framework with their own short addendum specs at track start; Track 3 gets its own spec
after the Norgate purchase decision.

## 1. Why (and why now)

The estate's FUTURES/FOREX/STOCKS surfaces are honest empty states because every one of the
chain's registrations is `asset_class: crypto`. The corpus is 97% non-crypto (equities 640,
cross-asset 447, futures 342, rates 197, FX 61, crypto 39): the reading pipeline has been
harvesting knowledge the composer cannot spend. The free data lane (merged 2026-08-23,
refreshed daily 08:50, pinned 38/38) provides the first non-crypto data the layer can
consume without a purchase. The Norgate futures decision (~2026-08-30) proceeds in parallel
and does not gate Tracks 1-2.

## 2. Declared space (Search-Space-First)

`pipeline/cells.py` generalises from the hardcoded 5x6 crypto grid to class-keyed grids.
The module stays THE authoritative space definition; nothing derives a grid from disk.

- `crypto`: 5 assets x 6 timeframes, session `24x7` — byte-for-byte today's behaviour.
- `fx` (Track 1): assets `EUR GBP AUD NZD JPY CAD CHF SEK NOK MXN SGD ZAR` (the manifest's
  fx lane, USD-per-foreign), timeframes `("1d",)`, session `fx_5d`.
- `equity_etf` / `bond_etf` / `metal_etf` (Tracks 2a/2b): declared at track start from the
  same manifest lanes; listed in the addendum specs, not before.

`cell_id` stays `f"{asset}_{timeframe}"` (ids are disjoint from crypto tickers).
`validate_cell` remains loud and class-aware. A `TRACKS` mapping records which classes are
LIVE for composition; declaring a class's cells and activating them are separate, explicit
steps so the denominator (§7) never moves by accident.

## 3. Data: the pinned snapshot adapter

New `pipeline/tradfi_data.py`, run as an explicit command (`python -m pipeline.tradfi_data
snapshot --classes fx`), never implicitly:

1. Reads `TRADING_SYSTEMS_ROOT` (default `E:\Users\Coen\Claude\trading-systems`) —
   read-only, same convention as Morpheus's connectors.
2. Loads `results/tradfi/free_universe_manifest.json`; for each requested instrument, loads
   the stored series via the producer's own loader (`tradfi/free_fetch.load`) and verifies
   it against the manifest row using the producer's hashing routine. Unpinned, missing, or
   hash-mismatched series are REFUSED (named in the error, nothing partial written).
3. Writes `research-layer/data/<ID>_1d.csv` in the existing loader convention
   (`date,open,high,low,close,volume`).
4. Writes `research-layer/data/tradfi_snapshot_manifest.json`: per-series manifest row copy
   + `snapshot_utc` + the source manifest's `snapshot_utc`. Every registration on these
   cells records the snapshot id in its universe provenance.

**FX OHLC honesty (declared, adapter-enforced):** FRED H.10 series are ONE daily spot fix.
The CSV carries `open=high=low=close=fix`, `volume=0`, and the snapshot manifest marks
`bar_kind: "single_fix"`. Consequences, stated wherever the data is described: true range
degenerates to |close - prev close|, so ATR-family blocks measure close-to-close movement,
not intraday range; any block whose semantics REQUIRE a real high/low distinct from close
(e.g. range-position blocks) is excluded from fx cells by the composer's block eligibility
(§4), not silently fed degenerate inputs.

Refresh policy: a snapshot is taken at track start and then deliberately (before a
generation run), never on a schedule — generations pin to the snapshot they were bred on.

## 4. Composer routing (rule 1, made executable)

A declared table in `pipeline/composer.py` — data, not inference:

| Card `asset_class` | Eligible cell classes |
|---|---|
| `crypto` | crypto |
| `fx` | fx + crypto (crypto stays unrestricted per D2) |
| `equities` | equity_etf + crypto |
| `rates` | bond_etf (proxy, recorded) + crypto |
| `futures` | nearest lane by underlying: index→equity_etf, rates→bond_etf, metals→metal_etf, fx→fx (all proxy, recorded) + crypto |
| `cross` / untagged | all live classes |

- Crypto cells receive every card (today's behaviour, unchanged — D2/C).
- Any non-native routing sets `routed_via: "proxy"` plus the proxy target on the
  registration, so Track 3 can enumerate exactly what deserves a native futures re-test.
- Block eligibility per class: blocks declare whether they require real OHLC range; fx
  cells (single-fix bars) exclude range-requiring blocks at composition time. The exclusion
  list is asserted in tests, not prose.

## 5. Costs and honesty limits (per class, declared)

Reused from the Phase C declared table (`trading-systems/strat/tradfi_costs.py`), applied
per-cell in screen and gauntlet exactly like crypto fees today:

- fx: 1.5 bps/side; short financing -1.5%/yr where the sizing block holds shorts.
- equity/bond ETF: 2.0 bps/side, short financing -0.5%/yr; metal ETF 2.0 bps / -0.75%/yr.

Standing honesty limitations, carried on every surface that describes these cells (vault
note, Morpheus microcopy when these cells ship, run manifests):
1. FX spot fixes exclude carry (no interest differential in returns).
2. ETF series are split-adjusted PRICE returns; dividends excluded (bond/equity longs
   understated). ETF universe is survivorship-alive-in-2026.
3. FX bars are single fixes (§3).
The label stays "CTA-lite: financials + metals", never breadth it does not have.

## 6. Eras, sessions, calendars

- Per-market own calendar (the Phase C outer-join lesson): signals and returns are computed
  on each market's own trading days; no cross-market alignment, no synthetic filling of
  holiday holes. `fx_5d` session = the series' own published fix days.
- Named eras for fx (history 1999→now): `pre_gfc` (1999-01→2007-12), `gfc_zirp`
  (2008-01→2015-12), `tightening` (2016-01→2021-12), `post_2022` (2022-01→now). No era GATE exists in
  protocol-v6; these cuts are RECORDED as per-era return summaries in fx gauntlet verdicts
  (record-don't-gate, consistent with v6), available to a future protocol change. ETF era cuts are declared in the 2a/2b addenda (histories differ per fund).

## 7. Protocol and N accounting

- Funnel = protocol-v6 unchanged: same gates, exactly as coded in `gauntlet.py` FAIL_ORDER
  (sharpe_floor → oos_negative → edge_decay → mc_p05 → p_ruin → cost_stress — there is NO
  recency gate in code; earlier prose claiming one was wrong), same recorded-not-gated statistics (PBO,
  self-perturbation), same quarantine accounting (`quarantine-live-protocol-v1` — its BH
  bar already rises with survivor count; new-class survivors enter the same pool).
- New cells enter the trial denominator at class ACTIVATION (the generation that first
  sweeps them), not at declaration. Fingerprints already hash `asset_class`; sibling
  accounting carries over unchanged.
- Screen fences (trade count floor etc.) apply per cell as today. Daily-bar cells will
  yield fewer trades per window than intraday crypto cells; the floor is NOT lowered —
  fewer trades failing the fence is the honest outcome, not a calibration problem.

## 8. Ship bar per track ("smooth and bugless")

A track ships when, in order:
1. Adapter snapshot pin-verified against the LIVE manifest (all series, hashes green).
2. Cells declared + routing table + block-eligibility unit-tested (including: crypto-only
   behaviour is byte-identical when no non-crypto class is live — regression fixture).
3. **Dry-run generation on the track's cells only**: full compose→screen→gauntlet pass, no
   API budget, nothing registered to the chain, results to a throwaway dir; reviewed.
4. Coen's go → first real generation on those cells (normal budget/protocol rules).
5. One clean real generation + vault/track notes updated → next track may start.

Track 1 additionally proves the framework itself (adapter, routing, calendars, eras); 2a/2b
are expected to be small addenda + activation.

## 9. Out of scope

- Norgate/futures anything (Track 3 spec waits for the purchase decision ~2026-08-30).
- Research-layer intraday for non-crypto (the lane is daily; no synthetic bars, ever).
- Morpheus surfaces (the market tabs already derive from artifacts; chain-funnel-by-market
  views are a later, separate Morpheus change once non-crypto registrations exist).
- Reader/probation changes (source pipeline is class-agnostic already).
- Any change to crypto cells, families, or the live quarantine.

## 10. Post-approval amendments (2026-08-24, from code verification; approved design intent unchanged)

1. **Gates**: §7 corrected — protocol-v6's coded FAIL_ORDER has six gates and no recency gate; the spec now names exactly the coded battery. Eras (§6) are recorded per-era summaries, not gate inputs.
2. **Vocabulary**: `cross` (the schema enum literal), not `cross_asset`.
3. **Adapter mechanics** (§3): FX parquets carry NaN open/high/low/volume (only `close` is real); the o=h=l=c fill is the ADAPTER's transformation. The manifest `sha256` is `free_integrity.series_sha256` — canonical `YYYY-MM-DD,{close:.6f}` lines over NaN-dropped, date-sorted closes — so the adapter re-verifies by reimplementing that 10-line canonicalisation (cross-repo code import is banned; direct parquet read follows the `data_import.py` precedent) with a parity test against the live manifest, and also honours the producer's integrity verdict file (refuse `fail`, fail-closed on unreadable).
4. **Annualisation**: `sqrt(365)` / `TRADING_DAYS=365` are hardcoded 24x7 assumptions; a `periods_per_year` (365 crypto, 261 fx_5d) is threaded from the class config through `realized_ann_vol`/`window_vol` with crypto defaults byte-identical.
5. **Financing**: the engine has no financing term; it gains `short_financing_per_year` in the cost model (fx -1.5%/yr, accrued per held short bar / periods_per_year); absent key = 0.0 = crypto byte-identical. The fx 1.5 bps/side declared cost splits commission_per_side 0.00005 + slippage_ticks 0.00010 so the cost-stress gate (doubles slippage) keeps a real bite.
6. **Mixed-class gauntlet**: `assert_cells_comparable` becomes per-class (fx ends Friday, crypto ends daily); the cross-strategy clustering for `effective_trials` aligns return series on the INTERSECTION calendar across classes (correlations estimated on common dates), recorded in the verdict; per-spec metrics stay on each cell's own calendar.
7. **FX block exclusions made concrete**: with single-fix bars the range-requiring set is `channel_breakout`, `channel_breakout_dense`, `atr_stop`, `atr_stop_dense`; the remaining stop for fx families is `pct_stop`. The engine's intrabar stop/target branches are unreachable on o=h=l=c bars (exits collapse to open-price fills) — accepted and recorded.
8. **Routing v1 narrowed**: Track 1 fx cells receive `fx` + `cross` cards only. Futures-card proxy routing needs an underlying-detection rule that cards do not reliably carry; it ships with Track 2 (ETF lanes map cleaner) and is recorded then. D2's intent (proxies recorded for Norgate re-test) is unchanged.
9. **Crypto by-name reality**: production crypto specs use legacy `BTCUSD`/`ETHUSD` daily assets while `cells.py` declares the `…USDT` grid; the generalisation keeps BOTH untouched (crypto behaviour byte-identical, regression-tested) and does NOT unify them — that cleanup is out of scope.

10. **vol_percentile window on fx (accepted deviation, T3 review):** `engine.percentile_rank(vol, i, 365)` uses a 365-BAR trailing window; on fx_5d bars that is ~17 calendar months, not one year. The percentile is scale-invariant (the inner annualisation is a no-op) and the window is declared in bars by design; accepted for Track 1 and recorded here rather than threaded per class.
11. **Quarantine threading (T3 review):** `quarantine.py`'s forward simulation gains the same `periods_per_year` derivation as `run_spec` (plan Task 5 Step 2b) so fx specs cannot silently diverge at the funnel's endpoint.

12. **D4 refinement: pinned-prefix verification (2026-08-27, found by the guard itself).** The
manifest's per-series sha256 is a FIRST-PIN snapshot (pin_universe never rewrites an existing
manifest) while the producer's parquets append bars daily -- so full-series sha comparison only
passes for data unchanged since the pin (fx and equity snapshots passed on borrowed time: the
H.10 weekly cadence and a 429-cache day respectively). The adapter therefore verifies the
PINNED PREFIX: the first `rows` (from the manifest row) of the (date,close) canon must hash
exactly to the manifest sha -- proving the declared history was never rewritten -- while
appended rows beyond the pin are covered by the producer's daily integrity verdict (already
consulted, fail-closed). The snapshot manifest records `sha256_verified: "pinned_prefix"` and
`rows_beyond_pin`. A prefix mismatch remains a hard refusal: rewritten history is the fraud
the pin exists to catch.
