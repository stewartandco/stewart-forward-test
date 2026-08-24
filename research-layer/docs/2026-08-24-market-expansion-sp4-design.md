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
| D2 | Routing = **B+C**: crypto cells keep today's unrestricted card feed (no mid-funnel behaviour change); non-crypto cells receive class-matched cards + cross-asset cards; futures/rates cards proxy-route to the nearest ETF/FX cells with `routed_via: "proxy"` recorded on the registration so Norgate-native cells can re-test them later. |
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
| `cross_asset` / untagged | all live classes |

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
  (2008-01→2015-12), `tightening` (2016-01→2021-12), `post_2022` (2022-01→now). Era-bearing
  gates (recency, era depth) use these cuts for fx cells; crypto cells keep their existing
  cuts. ETF era cuts are declared in the 2a/2b addenda (histories differ per fund).

## 7. Protocol and N accounting

- Funnel = protocol-v6 unchanged: same gates (dual recency → sharpe floor → oos sign →
  edge decay → mc_p05 → p_ruin → cost stress), same recorded-not-gated statistics (PBO,
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
