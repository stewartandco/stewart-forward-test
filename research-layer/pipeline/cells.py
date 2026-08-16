"""The declared search space: 5 assets x 6 timeframes = 30 cells.

Search-Space-First. This module is the authoritative definition and the only
place the grid is written down. Nothing may derive a grid from whatever files
happen to be on disk - a space discovered from the filesystem is a space nobody
declared, and every cell tested has to enter the trial denominator.

A CELL is one asset at one timeframe. It is the unit of survival: Coen's
requirement is that a strategy working only on 15m ETH must not be excluded,
which makes (asset, timeframe) the thing that lives or dies.
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


def all_cells() -> list[tuple[str, str]]:
    """Every declared cell, in a fixed order so manifests diff cleanly."""
    return [(a, tf) for a in ASSETS for tf in TIMEFRAMES]


def phase_cells(phase: int) -> list[tuple[str, str]]:
    if phase == 1:
        return [(a, tf) for a in ASSETS for tf in PHASE_1_TIMEFRAMES]
    return all_cells()


def cell_id(asset: str, timeframe: str) -> str:
    return f"{asset}_{timeframe}"


def validate_cell(asset: str, timeframe: str) -> tuple[str, str]:
    """Refuse anything outside the declared grid, loudly."""
    if asset not in ASSETS:
        raise ValueError(f"{asset!r} is not a declared asset: {ASSETS}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"{timeframe!r} is not a declared timeframe: {TIMEFRAMES}")
    return asset, timeframe
