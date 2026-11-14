"""Cell keys for the quarantine recorder (2b-2 design s3.1, s3.3).

A DAILY cell is named by its bare asset ("BTCUSD"), exactly as every
quarantine row and snapshot has always named it, so daily records are
byte-identical. An INTRADAY cell is "ASSET@TF" ("BTCUSDT@1h"). One key names
the row's `asset`, the snapshot's digest key and the price file, so a 1d and
a 1h strategy on the same asset can never share provenance, and verifier
invariant 9 (a row's asset must be an earlier snapshot key) needs no change.

Pure functions, no I/O. Intraday is 24x7 only (2b-1).
"""
from __future__ import annotations

from pathlib import Path

from . import bars as _bars

SEP = "@"
INTRADAY_SESSION = "24x7"


def cell_key(asset: str, tf: str) -> str:
    if not isinstance(asset, str) or not asset or SEP in asset:
        raise ValueError(f"bad asset {asset!r} for a cell key")
    return asset if tf == "1d" else f"{asset}{SEP}{tf}"


def split_cell_key(key: str) -> tuple[str, str]:
    if not isinstance(key, str) or not key:
        raise ValueError(f"bad cell key {key!r}")
    if SEP not in key:
        return key, "1d"
    asset, tf = key.split(SEP, 1)
    if not asset or not tf or SEP in tf or tf == "1d":
        raise ValueError(f"bad cell key {key!r}")
    return asset, tf


def is_intraday_key(key: str) -> bool:
    return SEP in key


def spec_cells(spec: dict) -> list[str]:
    universe = spec["universe"]
    tf = universe.get("timeframe", "1d")
    return [cell_key(a, tf) for a in universe["assets"]]


def cell_path(data_dir, key: str) -> Path:
    asset, tf = split_cell_key(key)
    return Path(data_dir) / f"{asset}_{tf}.csv"


def last_slot(day: str, tf: str) -> str:
    """The open time of the day's final bar: D 00:00 UTC + (n - 1) x bar."""
    n = _bars.bars_per_day(INTRADAY_SESSION, tf)
    minutes = (1440 // n) * (n - 1)
    return f"{day} {minutes // 60:02d}:{minutes % 60:02d}:00"


def complete_days(bars: list[dict], tf: str) -> list[str]:
    """Sorted UTC days of an intraday cell's bars that are COMPLETE (s3.3).

    Every day but the newest has a bar on a later day, so it is complete even
    with an hour missing (an hour the exchange never published must not make
    a day owed forever). The newest day is complete only when its last slot
    is present; the fetcher writes closed bars only, so that slot is there
    exactly when the day is over."""
    days = sorted({b["date"][:10] for b in bars})
    if not days:
        return []
    stamps = {b["date"] for b in bars}
    newest = days[-1]
    return [d for d in days if d != newest or last_slot(d, tf) in stamps]
