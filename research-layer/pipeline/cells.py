"""The declared search space: 5 assets x 6 timeframes = 30 cells (crypto).

Search-Space-First. This module is the authoritative definition and the only
place the grid is written down. Nothing may derive a grid from whatever files
happen to be on disk - a space discovered from the filesystem is a space nobody
declared, and every cell tested has to enter the trial denominator.

A CELL is one asset at one timeframe. It is the unit of survival: Coen's
requirement is that a strategy working only on 15m ETH must not be excluded,
which makes (asset, timeframe) the thing that lives or dies.

CLASSES extends the same contract to other asset classes (fx first). A class
entry in CLASSES is a DECLARATION of a space, not a decision to search it:
declaring "fx" here does not put a single fx trial into any denominator.
Only LIVE_CLASSES gates what a generation may sweep, because activating a
class moves the trial denominator (spec s2/s7) - that is a decision Coen makes
in its own reviewed commit, never a side effect of adding a class dict here.
Crypto's entry in CLASSES is a read-only mirror of ASSETS/TIMEFRAMES above;
every existing crypto caller (all_cells, phase_cells, validate_cell with two
positional args) keeps its exact current behaviour untouched.
"""
from __future__ import annotations

ASSETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT")
TIMEFRAMES = ("15m", "30m", "1h", "4h", "12h", "1d")

# Phase 1 defers the two most expensive timeframes. 15m alone is 74.4% of all
# bars in the grid, and both it and 30m are deferred until the chain has
# produced its first survivor - not because the data is missing (all 30 cells
# are cached as of 2026-08-17) but because they are the least likely to
# survive costs at that bar size. Phase 2 is everything.
PHASE_1_TIMEFRAMES = ("1h", "4h", "12h", "1d")

# Declared but not yet active (see CLASSES/LIVE_CLASSES docstring above).
FX_ASSETS = ("EUR", "GBP", "AUD", "NZD", "JPY", "CAD", "CHF", "SEK", "NOK", "MXN", "SGD", "ZAR")
FX_COST_MODEL = {"commission_per_side": 0.00005, "slippage_ticks": 0.00010,
                 "short_financing_per_year": -0.015}   # 1.5 bps/side split so cost-stress (2x slippage) keeps bite
FX_ERAS = (("pre_gfc", "1999-01-01", "2007-12-31"), ("gfc_zirp", "2008-01-01", "2015-12-31"),
           ("tightening", "2016-01-01", "2021-12-31"), ("post_2022", "2022-01-01", "9999-12-31"))

# fx cells carry single-fix daily bars (open=high=low=close, §3), so any block
# whose semantics require a real intrabar range distinct from close would be
# silently fed a degenerate input rather than erroring. Track 2a's addendum
# (T4-rider-3) moves this from composer.py's "any non-crypto class" coupling
# to a per-class declaration here; composer.RANGE_REQUIRING carries the same
# four values (test-pinned equal, never re-declared independently).
FX_EXCLUDED_BLOCK_TYPES = frozenset({"channel_breakout", "channel_breakout_dense",
                                      "atr_stop", "atr_stop_dense"})

# Track 2a (SP4 addendum docs/2026-08-24-sp4-track2a-addendum.md): the second
# non-crypto class, declared but NOT activated (see LIVE_CLASSES below).
# Assets are the manifest's equity-index ETF lane, in manifest order.
EQUITY_ETF_ASSETS = ("SPY", "QQQ", "IWM", "DIA", "MDY", "EFA", "EEM", "EWJ",
                     "EWG", "EWU", "EWA", "EWC", "EWH", "FXI", "EWZ", "EWY")
EQUITY_ETF_COST_MODEL = {"commission_per_side": 0.00010, "slippage_ticks": 0.00010,
                         "short_financing_per_year": -0.005}   # Phase C table: 2.0 bps/side, -0.5%/yr short financing
EQUITY_ETF_ERAS = (("dotcom_gfc", "1993-01-01", "2008-12-31"), ("qe_bull", "2009-01-01", "2019-12-31"),
                   ("covid_cycle", "2020-01-01", "2021-12-31"), ("post_2022", "2022-01-01", "9999-12-31"))

# Track 2b (SP4 addendum docs/2026-08-27-sp4-track2b-addendum.md): the third
# and fourth non-crypto classes, declared but NOT activated (see LIVE_CLASSES
# below). Assets are the manifest's bond-ETF lane, in manifest order.
BOND_ETF_ASSETS = ("SHY", "IEF", "TLT", "TIP", "LQD", "HYG", "EMB", "BND")
BOND_ETF_COST_MODEL = {"commission_per_side": 0.00010, "slippage_ticks": 0.00010,
                       "short_financing_per_year": -0.005}   # Phase C table: 2.0 bps/side, -0.5%/yr short financing
# 2022 named deliberately (addendum): the bond bear is the era that would
# expose long-bias here, unlike equity_etf's covid_cycle carve-out.
BOND_ETF_ERAS = (("pre_gfc", "2002-07-26", "2008-12-31"), ("zirp", "2009-01-01", "2015-12-31"),
                 ("hike_cut_cycle", "2016-01-01", "2021-12-31"), ("post_2022", "2022-01-01", "9999-12-31"))

METAL_ETF_ASSETS = ("GLD", "SLV")
METAL_ETF_COST_MODEL = {"commission_per_side": 0.00010, "slippage_ticks": 0.00010,
                        "short_financing_per_year": -0.0075}   # Phase C table: metals short financing -0.75%/yr
METAL_ETF_ERAS = (("pre_gfc", "2004-11-18", "2008-12-31"), ("zirp", "2009-01-01", "2015-12-31"),
                  ("hike_cut_cycle", "2016-01-01", "2021-12-31"), ("post_2022", "2022-01-01", "9999-12-31"))

# The authoritative class registry. Declaring a class here does NOT activate it:
# LIVE_CLASSES gates what generations may sweep (spec s2: activation moves the
# trial denominator, declaration never does).
# max_end_lag_days: the honest ceiling on how many calendar days a class's
# own data can lag a same-day fetch, DECLARED per source rather than derived
# from any one observed gap (real-run finding, 2026-08-24). crypto is 0: a
# 24x7 feed fetched today ends today. fx's snapshot adapter reads FRED H.10,
# which is a WEEKLY release of the prior week's daily fixes, posted Mondays
# -- a fetch run just before that Monday release can see up to ~9 calendar
# days of lag (the whole prior week plus the weekend before it), so 10 is
# declared as the ceiling with a day of headroom, not the exact observed
# value. screen.assert_cells_comparable's cross-class allowance derives from
# this field; it must never be widened to paper over a real gap without
# updating the declaration here first.
# B1 (SP4 Track 2a addendum, pre-registered 2026-08-26): `benchmark` declares
# whether the gauntlet records a same-OOS-window buy-and-hold control against
# the cell's own asset. `"self"` for a class whose cells are single-name
# assets with an honest long buy-and-hold (equity_etf; bond/metal declare
# their own value at 2b, not inherited from here). `None` for crypto and fx:
# crypto's cells are the ...USDT grid (a "buy and hold BTC" comparison is a
# different, not-yet-declared question) and fx has no long-only drift to
# separate from skill in the first place. RECORDED, NOT GATED -- see
# gauntlet.py's benchmark_relative computation and the addendum's
# pre-registration for the exact shape.
CLASSES = {
    "crypto": {"assets": ASSETS, "timeframes": TIMEFRAMES, "session": "24x7",
               "periods_per_year": 365, "bar_kind": "ohlcv",
               "cost_model": None,      # crypto cost stays composer.COST_MODEL (unchanged path)
               "eras": (), "max_end_lag_days": 0,
               "excluded_block_types": frozenset(), "benchmark": None},
    "fx": {"assets": FX_ASSETS, "timeframes": ("1d",), "session": "fx_5d",
           "periods_per_year": 261, "bar_kind": "single_fix",
           "cost_model": FX_COST_MODEL, "eras": FX_ERAS,
           "max_end_lag_days": 10,
           "excluded_block_types": FX_EXCLUDED_BLOCK_TYPES, "benchmark": None},
    # Track 2a: declared, NOT in LIVE_CLASSES. bar_kind "ohlcv" (real Tiingo
    # daily OHLC), so no block type is excluded on range grounds -- unlike fx.
    # max_end_lag_days 4 VERIFIED (track 2a review, 2026-08-25) against the
    # real snapshot, not carried from the draft unverified: all 16 equity
    # parquets end 2026-08-21 (Fri), fetched_utc 2026-08-23T00:32Z --
    # observed lag 2 calendar days (a weekend fetch after Friday's
    # same-evening Tiingo publish). 4 is declared as the ceiling with 2 days
    # of headroom over that observation (covers a run against a holiday
    # weekend, not just an ordinary one), not the exact observed value --
    # same convention as fx's max_end_lag_days above.
    "equity_etf": {"assets": EQUITY_ETF_ASSETS, "timeframes": ("1d",), "session": "us_equity_5d",
                   "periods_per_year": 252, "bar_kind": "ohlcv",
                   "cost_model": EQUITY_ETF_COST_MODEL, "eras": EQUITY_ETF_ERAS,
                   "max_end_lag_days": 4,
                   "excluded_block_types": frozenset(), "benchmark": "self"},
    # Track 2b: declared, NOT in LIVE_CLASSES. bar_kind "ohlcv" (real Tiingo
    # daily OHLC via the same free_bond_etf_*/free_metal_etf_* lanes as
    # equity_etf's free_equity_etf_* lane), so no block type is excluded on
    # range grounds -- same reasoning as equity_etf, unlike fx.
    # max_end_lag_days 4 RE-VERIFIED (track 2b build, 2026-08-27) against the
    # real trading-systems parquets, not carried from the addendum draft
    # unverified: all 8 bond_etf parquets (SHY IEF TLT TIP LQD HYG EMB BND)
    # and both metal_etf parquets (GLD SLV) end 2026-08-26 (Wed), fetched_utc
    # 2026-08-27T00:51:24Z-00:51:54Z -- observed lag 1 calendar day (a fetch
    # run just after midnight UTC picking up the same day's Tiingo publish).
    # 4 is declared as the ceiling with 3 days of headroom over that
    # observation (covers a run against a holiday weekend), not the exact
    # observed value -- same convention as fx/equity_etf's max_end_lag_days.
    "bond_etf": {"assets": BOND_ETF_ASSETS, "timeframes": ("1d",), "session": "us_equity_5d",
                "periods_per_year": 252, "bar_kind": "ohlcv",
                "cost_model": BOND_ETF_COST_MODEL, "eras": BOND_ETF_ERAS,
                "max_end_lag_days": 4,
                "excluded_block_types": frozenset(), "benchmark": "self"},
    "metal_etf": {"assets": METAL_ETF_ASSETS, "timeframes": ("1d",), "session": "us_equity_5d",
                 "periods_per_year": 252, "bar_kind": "ohlcv",
                 "cost_model": METAL_ETF_COST_MODEL, "eras": METAL_ETF_ERAS,
                 "max_end_lag_days": 4,
                 "excluded_block_types": frozenset(), "benchmark": "self"},
}
LIVE_CLASSES = ("crypto", "fx", "equity_etf", "bond_etf", "metal_etf")   # fx 08-24, equity_etf 08-25, bond+metal 08-27 (Coen each time)
# All five classes are LIVE as of 2026-08-27: crypto+fx from SP4 track 1,
# equity_etf activated Track 2a (2026-08-25), bond_etf+metal_etf activated
# Track 2b (2026-08-27) -- each activation was Coen's call, made after that
# class's dry-run ship-bar step (spec s8), per the CLASSES/LIVE_CLASSES
# docstring above.

SESSION_PERIODS = {"24x7": 365, "fx_5d": 261, "us_equity_5d": 252}

# Every class's declared assets must be disjoint from every other class's -
# a ticker that could mean two different classes is a declaration bug, not a
# runtime ambiguity to paper over. Asserted once at import time.
_seen: dict[str, str] = {}
for _cls, _spec in CLASSES.items():
    for _asset in _spec["assets"]:
        if _asset in _seen:
            raise AssertionError(f"asset {_asset!r} declared in both class {_seen[_asset]!r} and {_cls!r}")
        _seen[_asset] = _cls
del _seen
for _cls, _spec in CLASSES.items():
    if SESSION_PERIODS.get(_spec["session"]) != _spec["periods_per_year"]:
        raise AssertionError(
            f"SESSION_PERIODS[{_spec['session']!r}] disagrees with CLASSES[{_cls!r}] periods_per_year")
for _cls, _spec in CLASSES.items():
    _lag = _spec.get("max_end_lag_days")
    if not isinstance(_lag, int) or isinstance(_lag, bool) or _lag < 0:
        raise AssertionError(
            f"CLASSES[{_cls!r}]['max_end_lag_days'] must be a declared "
            f"non-negative int, got {_lag!r}")
for _cls, _spec in CLASSES.items():
    _excl = _spec.get("excluded_block_types")
    if not isinstance(_excl, frozenset) or not all(isinstance(t, str) for t in _excl):
        raise AssertionError(
            f"CLASSES[{_cls!r}]['excluded_block_types'] must be a declared "
            f"frozenset of block-type strings, got {_excl!r}")
for _cls, _spec in CLASSES.items():
    _bench = _spec.get("benchmark")
    if _bench not in (None, "self"):
        raise AssertionError(
            f"CLASSES[{_cls!r}]['benchmark'] must be declared as None or "
            f"'self' (B1 addendum), got {_bench!r}")
del _cls, _spec, _asset, _lag, _excl, _bench


def all_cells() -> list[tuple[str, str]]:
    """Every declared crypto cell, in a fixed order so manifests diff cleanly."""
    return [(a, tf) for a in ASSETS for tf in TIMEFRAMES]


def phase_cells(phase: int) -> list[tuple[str, str]]:
    if phase == 1:
        return [(a, tf) for a in ASSETS for tf in PHASE_1_TIMEFRAMES]
    return all_cells()


def _class_spec(asset_class: str) -> dict:
    """CLASSES lookup that refuses unknown classes loudly (ValueError, not KeyError)."""
    if asset_class not in CLASSES:
        raise ValueError(f"{asset_class!r} is not a declared class: {sorted(CLASSES)}")
    return CLASSES[asset_class]


def class_cells(asset_class: str) -> list[tuple[str, str]]:
    """Every declared cell for one class (assets x timeframes, fixed order)."""
    spec = _class_spec(asset_class)
    return [(a, tf) for a in spec["assets"] for tf in spec["timeframes"]]


def class_of_asset(asset: str) -> str:
    """The one class whose declared assets contain this asset. ValueError otherwise."""
    for cls, spec in CLASSES.items():
        if asset in spec["assets"]:
            return cls
    declared = "; ".join(f"{c}: {spec['assets']}" for c, spec in CLASSES.items())
    raise ValueError(f"{asset!r} is not a declared asset of any class ({declared})")


def cell_id(asset: str, timeframe: str) -> str:
    return f"{asset}_{timeframe}"


def validate_cell(asset: str, timeframe: str, asset_class: str | None = None) -> tuple[str, str]:
    """Refuse anything outside the declared grid, loudly.

    asset_class=None (the default, used by every existing crypto caller)
    infers the class from the asset via class_of_asset - identical behaviour
    to the old crypto-only check when the asset is a crypto asset. Passing an
    explicit asset_class checks membership in that class only, so a cell that
    is valid for one class but named under another class is still rejected.
    """
    if asset_class is None:
        asset_class = class_of_asset(asset)
    spec = _class_spec(asset_class)
    if asset not in spec["assets"]:
        raise ValueError(f"{asset!r} is not a declared {asset_class} asset: {spec['assets']}")
    if timeframe not in spec["timeframes"]:
        raise ValueError(f"{timeframe!r} is not a declared {asset_class} timeframe: {spec['timeframes']}")
    return asset, timeframe
