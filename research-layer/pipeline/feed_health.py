"""Feed health for the market-data CSVs (2b-3 C1, C2).

Two failures used to pass silently: a whole UTC day missing inside a file, and
a feed that fetches successfully but brings nothing new. This module finds
both and returns plain ledger items `{"source", "key", "reason"}`;
`pipeline/degraded.py` adds the timestamps and the Ops Sentinel escalates them
(WARN, then FAIL after 3 days). Nothing here writes a ledger.

The source names fit 1d cells too; they are the design's intraday_hole /
intraday_stale.

Dates are the UTC bar OPEN time, either "YYYY-MM-DD HH:MM:SS" (the USDT files)
or a bare "YYYY-MM-DD" (the legacy BTCUSD/ETHUSD 1d files, 00:00 UTC).
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .crypto_data import DATE_FMT, SKEW_MARGIN_MS, TF_MS
from .qcells import cell_path, split_cell_key

HOLE_SOURCE = "feed_hole"
STALE_SOURCE = "feed_stale"
STALE_GRACE_S = 60
ACK_FILE = "intraday_holes_ack.json"
_SHOWN = 10


def _day(d: str) -> date:
    return date.fromisoformat(d[:10])


def find_holes(dates: list[str]) -> list[str]:
    """UTC days strictly between the first and last bar's day with no bar."""
    days = {_day(d) for d in dates}
    if not days:
        return []
    first, last = min(days), max(days)
    out, cur = [], first + timedelta(days=1)
    while cur < last:
        if cur not in days:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _open_ms(last_bar_open: str) -> int:
    if len(last_bar_open) == 10:
        dt = datetime.strptime(last_bar_open, "%Y-%m-%d")
    else:
        dt = datetime.strptime(last_bar_open, DATE_FMT)
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _expected_close_ms(tf: str, now_ms: int, end_ms: int | None,
                       grace_s: int) -> int:
    """Close of the newest bar the fetcher would have written by `now_ms`.

    Same rule as `crypto_data.fetch_symbol`: a bar counts once it closed more
    than SKEW_MARGIN_MS ago, plus a grace; `end_ms` caps it (the refresh stops
    at 00:00 UTC of today). Binance klines are UTC epoch-aligned.
    """
    cutoff = now_ms - SKEW_MARGIN_MS - grace_s * 1000
    if end_ms is not None:
        cutoff = min(cutoff, end_ms)
    return cutoff - cutoff % TF_MS[tf]


def is_stale(last_bar_open: str, tf: str, now_ms: int, *,
             end_ms: int | None = None, grace_s: int = STALE_GRACE_S) -> bool:
    """True when the last bar closes before the newest closed bar's close."""
    expected = _expected_close_ms(tf, now_ms, end_ms, grace_s)
    return _open_ms(last_bar_open) + TF_MS[tf] < expected


def load_acks(data_dir: Path) -> tuple[dict[str, set[str]], str | None]:
    """`({cell key: {acked days}}, None)`, or `({}, note)` if unreadable.

    A malformed file honours NOTHING: a half-trusted ack would hide a hole.
    """
    path = Path(data_dir) / ACK_FILE
    if not path.exists():
        return {}, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level must be an object")
        acks: dict[str, set[str]] = {}
        for key, entry in raw.items():
            if not isinstance(entry, dict):
                raise ValueError(f"{key!r}: entry must be an object")
            days = entry.get("dates")
            if not isinstance(days, list) or not all(isinstance(d, str) for d in days):
                raise ValueError(f"{key!r}: dates must be a list of strings")
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{key!r}: reason must be non-empty text")
            for d in days:
                if len(d) != 10 or date.fromisoformat(d).isoformat() != d:
                    raise ValueError(f"{key!r}: bad date {d!r}")
            acks[key] = set(days)
        return acks, None
    except Exception as exc:  # noqa: BLE001 - any failure means honour nothing
        return {}, f"{type(exc).__name__}: {exc}"


def _read_dates(path: Path) -> list[str]:
    """The `date` column only, streamed. Raises on anything unusable."""
    dates: list[str] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or "date" not in header:
            raise ValueError("no date column")
        idx = header.index("date")
        for row in reader:
            if row:
                dates.append(row[idx])
    if not dates:
        raise ValueError("no bars")
    for d in (dates[0], dates[-1]):
        _open_ms(d)
    return dates


def _scan_one(data_dir: Path, key: str, now_ms: int, stale: bool,
              acked: set[str], end_ms: int | None) -> list[dict]:
    _, tf = split_cell_key(key)
    dates = _read_dates(cell_path(data_dir, key))
    items: list[dict] = []
    holes = find_holes(dates)
    done = [h for h in holes if h in acked]
    open_ = [h for h in holes if h not in acked]
    if done:
        print(f"{key}  acknowledged hole(s): {', '.join(done)}")
    if open_:
        tail = f", ... (first {_SHOWN})" if len(open_) > _SHOWN else ""
        items.append({"source": HOLE_SOURCE, "key": key,
                      "reason": f"{len(open_)} day(s) with no bars: "
                                f"{', '.join(open_[:_SHOWN])}{tail}"})
    if stale:
        last = max(dates)
        if is_stale(last, tf, now_ms, end_ms=end_ms):
            exp_open = (_expected_close_ms(tf, now_ms, end_ms, STALE_GRACE_S)
                        - TF_MS[tf])
            exp_s = (datetime.fromtimestamp(exp_open / 1000, tz=timezone.utc)
                     .strftime(DATE_FMT))
            last_s = datetime.fromtimestamp(_open_ms(last) / 1000,
                                            tz=timezone.utc).strftime(DATE_FMT)
            items.append({"source": STALE_SOURCE, "key": key,
                          "reason": f"last closed bar {last_s} ends before "
                                    f"{exp_s} (the newest closed {tf} bar)"})
    return items


def scan(data_dir: Path, keys: list[str], *, now_ms: int, stale_keys: set[str],
         acks: dict[str, set[str]], end_ms: int | None = None) -> list[dict]:
    """Ledger items for every key. Never raises: an unusable file is one item."""
    out: list[dict] = []
    for key in keys:
        try:
            out.extend(_scan_one(Path(data_dir), key, now_ms, key in stale_keys,
                                 acks.get(key, set()), end_ms))
        except Exception as exc:  # noqa: BLE001 - a scan must never crash a run
            out.append({"source": HOLE_SOURCE, "key": key,
                        "reason": f"cannot scan: {type(exc).__name__}: {exc}"})
    return out
