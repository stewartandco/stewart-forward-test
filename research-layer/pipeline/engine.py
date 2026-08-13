"""Pure deterministic block-spec interpreter on daily OHLCV bars.

No registry knowledge, no IO, no randomness: same spec + bars -> identical
output. Execution semantics per docs/2026-08-13-screen-design.md §3:
signals on close t, fills at open t+1; warmup enforced; same-bar stop+target
-> stop; gaps fill at open; no leverage.
"""
from __future__ import annotations

import math
import statistics


# ---------------- indicators (aligned lists; None during warmup) ----------

def sma(values: list[float], n: int) -> list:
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        out[i] = sum(values[i - n + 1:i + 1]) / n
    return out


def stdev(values: list[float], n: int) -> list:
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        out[i] = statistics.stdev(values[i - n + 1:i + 1])
    return out


def atr_wilder(bars: list[dict], n: int) -> list:
    """Wilder ATR; warm from index n (needs n TRs, TR needs a prior close)."""
    out = [None] * len(bars)
    trs = []
    prev_close = None
    for i, b in enumerate(bars):
        if prev_close is None:
            tr = b["high"] - b["low"]
        else:
            tr = max(b["high"] - b["low"], abs(b["high"] - prev_close),
                     abs(b["low"] - prev_close))
        trs.append(tr)
        prev_close = b["close"]
        if i == n:
            out[i] = sum(trs[1:n + 1]) / n          # first ATR: mean of TRs 1..n
        elif i > n:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out


def trend_tstat(window_closes: list[float]) -> float:
    """t-stat of the OLS slope of close vs time over the window.
    Perfect line -> +/-inf (sign of slope); flat -> 0."""
    n = len(window_closes)
    xs = list(range(n))
    mx = (n - 1) / 2
    my = sum(window_closes) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, window_closes))
    slope = sxy / sxx
    resid = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, window_closes))
    if resid == 0:
        return math.copysign(float("inf"), slope) if slope != 0 else 0.0
    se = math.sqrt(resid / (n - 2) / sxx)
    return slope / se


def realized_ann_vol(closes: list[float], n: int) -> list:
    """Annualized stdev of daily log returns over n returns (sqrt(365))."""
    rets = [None] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out = [None] * len(closes)
    for i in range(n, len(closes)):
        window = rets[i - n + 1:i + 1]
        out[i] = statistics.stdev(window) * math.sqrt(365) if len(window) > 1 else None
    return out


def percentile_rank(values: list[float], i: int, window: int) -> float | None:
    """Rank of values[i] among the trailing `window` values ending at i
    (inclusive): fraction <= current."""
    lo = i - window + 1
    if lo < 0:
        return None
    win = values[lo:i + 1]
    if any(v is None for v in win):
        return None
    return sum(1 for v in win if v <= values[i]) / len(win)
