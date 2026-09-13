# SP5 crypto grid addendum: the 100-asset declared universe

**Status: Coen's go 2026-08-28 (the D1-D12 design dialogue). Written 2026-08-29, BEFORE the
activation it authorises.** Parent spec `2026-08-28-market-data-universe-design.md` (s2 universe
rule, s3 activation gate, s4 crypto migration, s5 rotation, s9 honesty limits, s10 phasing);
class-declaration precedent `2026-08-27-sp4-track2b-addendum.md`; B1 benchmark pre-registration
precedent `2026-08-24-sp4-track2a-addendum.md`.

**Ordering, stated plainly rather than smoothed over.** The 2a and 2b addenda were written before
their code. This one was not: the Phase 2 declaration commits (371667f, f2b31da, 91b095e, 0d7b2d8,
e0b18a4) landed first, and `cells.py` cites this file by path from inside them. That is defensible
here for exactly one reason, and only that reason. Phase 2 is BEHAVIOUR-FROZEN:
`ACTIVE_CELLS["crypto"]` is `{"assets": (), "timeframes": ()}`, so `active_cells("crypto")` is the
empty list and NOTHING sweeps any of the 600 cells declared below. No registration, no verdict, no
trial. What this addendum governs is the PHASE 3 ACTIVATION, which has not happened and which is
Coen's own reviewed commit. Against that event this document is early, which is where it needed to
be.

Context the declaration is made under, honestly: crypto is the only ALREADY-LIVE class ever
expanded here. `LIVE_CLASSES` cannot stage that, which is why the cell-level gate (s3) was built
first and why declaring 95 new assets moved nothing.

## Class declaration (the literal values now in `cells.CLASSES["crypto"]`)

- `assets`: 100 Binance USDT spot symbols in MANIFEST ORDER (CoinGecko market-cap rank at
  selection), written as a literal in `cells.ASSETS`: `BTCUSDT ETHUSDT BNBUSDT XRPUSDT SOLUSDT
  TRXUSDT ZECUSDT DOGEUSDT ...` through `YFIUSDT GASUSDT`. The tuple is never re-sorted (order is
  part of the declaration) and is pinned equal to the manifest by
  `test_crypto_assets_are_the_pinned_universe_manifest`.
- `timeframes`: `("15m", "30m", "1h", "4h", "12h", "1d")` (6, unchanged by SP5). 100 x 6 = 600
  DECLARED cells.
- `session` `"24x7"`; `periods_per_year` 365; `bar_kind` `"ohlcv"`; `eras` `()`;
  `excluded_block_types` `frozenset()` (real ranges, no block excluded).
- `cost_model`: `{"commission_per_side": 0.001, "slippage_ticks": 0.0005}` -- the LITERAL values of
  `composer.COST_MODEL`, now declared where every other class declares them (design s4). Declaring
  them changes nothing on the legacy path, which still reads `composer.COST_MODEL`.
- `max_end_lag_days` 0, RE-VERIFIED at build time against the real snapshot: all 100 `<SYM>_1d`
  entries in `data/crypto_snapshot_manifest.json` carry `last_date 2026-08-27` at
  `fetched_utc 2026-08-28T15:26:07Z`. One end date across all 100, so the within-class spread that
  `screen.assert_cells_comparable` measures is 0 days.
- `benchmark`: `None` through Phase 2. DEFERRED DELIBERATELY, and this is a CORRECTION to the parent
  spec, which put the flip to `"self"` on the declaration commit (s4/s7). That was wrong: while
  crypto is served by the legacy pooled path its specs are named from `composer.ALLOWED_ASSETS`
  (BTCUSD/ETHUSD) and are the 2-asset pooled book, and `gauntlet._benchmark_relative` requires
  exactly one asset per cell for a `"self"` class. Flipping the field alone turns every crypto
  verdict into a raise. See the Ship bar below for the coupling.

PROVENANCE: `data/crypto_universe_manifest.json`, selected `2026-08-28T15:19:44Z`, source
`coingecko /coins/markets keyless + binance /api/v3/klines first/last-bar probe`. 100 admitted, 207
excluded, every exclusion carrying its reason: 136 no Binance USDT spot pair, 35 history under 2y,
30 stablecoin, 5 Binance pair inactive, 1 wrapped/staked/bridged. The manifest is PINNED (the
selection script refuses to overwrite one) and is the provenance of the declaration, NEVER a runtime
input: the grid stays declared in code (Search-Space-First). Re-selection is a new declared event
with its own manifest, never a silent refresh.

Legacy continuity is untouched: the 155 pooled BTCUSD/ETHUSD registrations on the chain (verified by
count) and their verdicts stand, the engine's 2-asset mean-combine path stays so they can be
re-simulated forever, and the `BTCUSD` (legacy) vs `BTCUSDT` (cell) naming split persists on the
chain and stays documented.

## Universe rule, as amended 2026-08-29

Top-100 by CoinGecko market cap on the selection date, walked in ascending rank order, admitting the
first 100 that survive every exclusion:

1. declared stablecoins (fixed id list), plus undeclared USD-peg suspects (price within 0.02 of 1.0
   with "usd" in the id or symbol);
2. wrapped / staked / bridged / restaked derivatives (declared marker substrings of the CoinGecko id
   or symbol);
3. no Binance USDT spot pair;
4. fewer than 730 days (2 years) of Binance USDT daily history at selection;
5. **THE AMENDMENT: the pair must be ACTIVELY TRADING** -- its latest daily bar must fall within 7
   days of selection.

Rule 5 was added after the FIRST real selection admitted five delisted pairs. Named, with the
manifest evidence: XMR (`monero`, XMRUSDT, last bar 2024-02-20), Lighter (`lighter`, LITUSDT, last
bar 2025-02-10), BTT (`bittorrent`, BTTUSDT, last bar 2022-01-17), DAI-on-pulsechain
(`dai-on-pulsechain`, DAIUSDT, last bar 2020-08-12) and BEAM (`beam-2`, BEAMUSDT, last bar
2023-01-26).

Two of those are TICKER COLLISIONS carrying the wrong asset's history, and the mechanism is worth
stating because it is the reason a liveness rule was needed rather than a longer exclusion list. The
script derives the pair from the ticker (`binance_symbol = symbol.upper() + "USDT"`), so a top-100
CoinGecko id whose ticker was previously used by a DIFFERENT, now-dead Binance listing resolves to
that dead listing's candles. `lighter` mapped to LITUSDT, which is Litentry's dead pair;
`dai-on-pulsechain` mapped to DAIUSDT, which is the MakerDAO DAI pair. Both would have entered the
declared grid with years of another asset's price history attached. The script already refuses two
walked coins that resolve to the SAME pair (an explicit `RuntimeError`: "two distinct coins share a
ticker"); what it could not see was one walked coin inheriting a DEAD listing's pair, since the dead
listing is not in the ranking to collide with. The liveness cutoff is what closes that.

## Honesty limits (numbered, carried on every surface that reports expanded-universe results)

1. **Survivorship.** The universe is selected by TODAY'S outcomes: today's top 100, backtested to
   listing date, is winners picked after the race. Assets that died or fell out of the top 100
   before today were never candidates. Documented, not corrected; forward quarantine is the honest
   arbiter.
2. **Ragged histories.** Listing dates span 2017 (BTCUSDT) to the 2-year floor, so per-cell OOS
   windows differ and `trials_common_days` shrinks toward the youngest member's overlap wherever
   clustering spans the class. Recorded per cell, never padded. Borderline histories (2.0 to 2.5
   years) produce thin OOS windows and are not special-cased.
3. **Benchmark basis is PRICE RETURNS ONLY.** Staking and funding yield are excluded on both sides,
   so a long buy-and-hold control understates a real holder's return. The control itself does not
   exist for crypto yet: it starts recording only at the Phase 3 activation commit, and it is
   RECORDED, NEVER GATED (B1). The 155 pooled legacy strategies predate cell benchmarks entirely;
   their audit (`tools_benchmark_backfill_report_crypto.py`) is a read-only report and their chain
   verdicts stand.
4. **More cells means more trials: pass counts are NEVER evidence by count.** A grid 20 times wider
   produces more survivors at any fixed bar, and that is arithmetic, not edge. "Productive, not
   good" is the gen-5 lesson and it applies with full force here. An honest zero on any tranche is
   an acceptable outcome (the bond/metal precedent).

## Routing

Crypto is UNRESTRICTED, verified at source rather than read off the routing table.
`composer.routable_cards` branches on `asset_class == "crypto"` BEFORE consulting `ROUTING` and
returns the whole accepted-card mapping unfiltered, with `routing`, `routed_card_ids` and
`proxy_routed_card_ids` all `None` on the drift record (None here means "no routing was applied at
all", not "applied and matched nothing"). `ROUTING["crypto"] = ("crypto",)` exists in the table but
is read by nothing; every non-crypto class takes the filtered branch. There is no crypto proxy lane.

This does not change at activation. Card routing is card-side and the branch above is keyed on the
class name, not on which expansion function serves it, so switching crypto to
`expand_family_for_class` leaves the card population identical. `pipeline/loop.py` computes its
watermark through the same `routable_cards`, so the loop counts exactly what the composer would
consume, before and after.

## Build deltas (Phase 2, as actually built)

1. `pipeline/cells.py` (371667f, f2b31da, 91b095e, e0b18a4): the `ACTIVE_CELLS` dict and
   `active_cells(cls)`; a `ValueError` when a declared class has no `ACTIVE_CELLS` entry; two
   private import-time checkers (`_assert_gate_axes`: each axis is `"all"` or a subset tuple that is
   duplicate-free and in declaration order; `_assert_gate_both_or_neither`: a live class's gate
   fills both axes or neither, so a half-filled activation cannot import clean and sweep nothing)
   plus a named sentinel for a gate missing an axis key; `ASSETS` widened 5 -> 100 from the pinned
   manifest; the crypto `CLASSES` entry gains its `cost_model` literal and keeps `benchmark: None`;
   the module docstring amended to say that growing an `ACTIVE_CELLS` entry is the denominator
   event and Coen's own reviewed commit.
2. `pipeline/composer.py` (f2b31da, 0d7b2d8): ONE call site changed --
   `expand_family_for_class` sweeps `cells.active_cells(asset_class)` instead of
   `cells.class_cells(asset_class)`. For the four tradfi classes the gate is `"all"`/`"all"`, so
   expansion is byte-identical to pre-SP5 and is test-pinned as such. Five stale prose passages that
   still described expansion as exhaustive over `class_cells` now name `active_cells` and state the
   distinction (declaration admits data and import work; activation admits sweeping): three in
   `composer.py`, one in `test_composer_fx.py`, one in `tools_dryrun_fx.py`. Comments only.
3. Tests, same pass: `test_cells.py` (gate coverage on BOTH axes, duplicate and order and half-gate
   refusals exercised against crafted gates through the shipped checkers, the manifest pin, the
   100x6 declared-grid count, crypto still inactive); `test_composer_equity.py` (active-set
   expansion, empty-set expansion, tradfi unchanged); `test_composer.py` (declared-grid count
   20 -> 400 at the phase-1 timeframes, and the "not a declared asset" case moved from DOGEUSDT,
   which is now declared, to BTTUSDT, which the manifest excluded yet which still has a CSV on disk
   -- on disk is not declared); `test_data_import.py` (same swap);
   `test_gauntlet_classes.py::test_crypto_benchmark_and_the_legacy_pooled_path_flip_together` (the
   Ship bar's coupling, pinned so it fails in EITHER direction).
4. Data landed earlier, at Phase 1: `data/crypto_universe_manifest.json` (pinned, re-selected under
   the amended rule at cc00afc) and `data/crypto_snapshot_manifest.json` plus the 100 `_1d` CSVs.
5. **NOTHING else.** The screen, the gauntlet, quarantine, the registry, the loop and the reader are
   class-generic and are untouched. `LIVE_CLASSES` is untouched. The composer's legacy pooled crypto
   branch (`ALLOWED_ASSETS`, `UNIVERSE_BASE`, `COST_MODEL`) is untouched and still serves every live
   crypto trigger. No scheduled task exists for the new grid; snapshots stay explicit, like tradfi.

## Ship bar

Phase 1 data and manifest landed -> declaration commits (this addendum's subject) -> full suite green
-> dry-run crypto generation on the DECLARED grid via explicit `--assets`/`--timeframes` override,
producing valid single-asset specs registered nowhere -> live loop behaviour verified unchanged
(crypto trigger still on the legacy pooled path) -> **Coen's activation call** -> activation
(`ACTIVE_CELLS` crypto gains the 1d tranche: 100 assets x `("1d",)`) -> first real generation.

**REQUIRED COMPANION CHANGE, and it is the reason the benchmark was deferred.** The activation commit
must, in ONE commit, populate `ACTIVE_CELLS["crypto"]`, switch the composer's crypto branch to
`expand_family_for_class` (single-asset per-cell specs), delete the legacy branch, AND flip
`benchmark` to `"self"`. Neither half is safe alone, in either direction: flip the benchmark without
the routing and every crypto verdict raises; switch the routing without the benchmark and the new
per-cell verdicts carry no control while the class that most needs one is the class being expanded
twentyfold. That coupling is test-pinned, so a half-flip fails rather than shipping green.
`gauntlet.py`'s `BENCHMARK_BASIS` already declares crypto's basis string, ready for it. Also in that
commit per design s10: `26_CryptoGridRefresh` registered through `setup_scheduler.bat` and
`$RETRY_TASKS` in the same pass, pinned in the sentinel manifest only after its exit code is proven
trustworthy.

Activation is the DENOMINATOR EVENT. Declaration moved nothing; activation grows registrations at
roughly 1,400 per generation under the 12-asset rotation (D6), and the quarantine pool's
Benjamini-Hochberg bar rises as new survivors enter the one shared pool.
