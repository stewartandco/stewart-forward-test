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

# The authoritative class registry. Declaring a class here does NOT activate it:
# LIVE_CLASSES gates what generations may sweep (spec s2: activation moves the
# trial denominator, declaration never does).
CLASSES = {
    "crypto": {"assets": ASSETS, "timeframes": TIMEFRAMES, "session": "24x7",
               "periods_per_year": 365, "bar_kind": "ohlcv",
               "cost_model": None,      # crypto cost stays composer.COST_MODEL (unchanged path)
               "eras": ()},
    "fx": {"assets": FX_ASSETS, "timeframes": ("1d",), "session": "fx_5d",
           "periods_per_year": 261, "bar_kind": "single_fix",
           "cost_model": FX_COST_MODEL, "eras": FX_ERAS},
}
LIVE_CLASSES = ("crypto", "fx")   # fx ACTIVATED 2026-08-24 (Coen), first real generation same day

SESSION_PERIODS = {"24x7": 365, "fx_5d": 261}

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
del _cls, _spec, _asset


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
