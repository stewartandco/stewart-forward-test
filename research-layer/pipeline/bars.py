"""Bar-size arithmetic for intraday cells (design:
docs/2026-10-10-intraday-correctness-design.md, Morpheus 2b-1).

The rule: bar MECHANICS use the bar size (engine vol sizing, financing, the
percentile window); every STATISTIC uses the equity curve marked once per UTC
day, so the pipeline's 365-based formulas stay correct at every timeframe. A
daily strategy's bars ARE its daily marks, so its arithmetic is unchanged.
"""
from __future__ import annotations

from . import cells

BARS_PER_DAY_24X7 = {"15m": 96, "30m": 48, "1h": 24, "4h": 6, "12h": 2, "1d": 1}
DEFAULT_PERIODS = 365   # the SESSION_PERIODS fallback run_spec has always used


class IntradayUnsupported(ValueError):
    """An intraday timeframe this build does not support (2b-1: 24x7 only)."""


def bars_per_day(session: str | None, tf: str) -> int:
    if tf == "1d":
        return 1
    if session != "24x7":
        raise IntradayUnsupported(
            f"intraday timeframe {tf!r} is supported only for session '24x7', "
            f"got session {session!r}")
    try:
        return BARS_PER_DAY_24X7[tf]
    except KeyError:
        raise IntradayUnsupported(f"unknown timeframe {tf!r}") from None


def bars_per_year(session: str | None, tf: str) -> int:
    return cells.SESSION_PERIODS.get(session, DEFAULT_PERIODS) * bars_per_day(session, tf)


def daily_marks(series):
    """The LAST (date, value) of each UTC date (key str(date)[:10]), in input
    order. Input must be sorted by time; out-of-order input raises."""
    out = []
    prev = None
    for d, v in series:
        s = str(d)
        if prev is not None and s < prev:
            raise ValueError(f"daily_marks: input out of order at {s!r} after {prev!r}")
        prev = s
        if out and str(out[-1][0])[:10] == s[:10]:
            out[-1] = (d, v)
        else:
            out.append((d, v))
    return out


def daily_bars(rows: list[dict]) -> list[dict]:
    """The last bar dict of each UTC date (close-based statistics)."""
    return [b for _, b in daily_marks([(b["date"], b) for b in rows])]
