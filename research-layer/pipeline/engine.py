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
    if len(window_closes) < 3:
        raise ValueError("trend_tstat needs >= 3 points")
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


# ---------------- entry signals + gates ------------------------------------

def entry_signals(block: dict, bars: list[dict]) -> tuple[list[int], list[int]]:
    """Per-bar entry signal (+1 long, -1 short, 0 none), evaluated on close.
    Also returns a per-bar desired-state list (used by ma_cross signal exits;
    zeros for stateless entries). Warmup bars emit 0."""
    p = block["params"]
    closes = [b["close"] for b in bars]
    n = len(bars)
    sig = [0] * n
    state = [0] * n

    if block["type"] == "ma_cross":
        fast, slow = sma(closes, p["fast"]), sma(closes, p["slow"])
        for i in range(n):
            if fast[i] is None or slow[i] is None:
                continue
            state[i] = 1 if fast[i] > slow[i] else 0
            if state[i] == 1 and (i == 0 or state[i - 1] == 0):
                sig[i] = 1

    elif block["type"] == "channel_breakout":
        lb = p["lookback"]
        for i in range(lb, n):
            hi = max(b["high"] for b in bars[i - lb:i])
            lo = min(b["low"] for b in bars[i - lb:i])
            if closes[i] > hi:
                sig[i] = 1
            elif p["direction"] == "both" and closes[i] < lo:
                sig[i] = -1

    elif block["type"] == "zscore_reversion":
        mean, sd = sma(closes, p["lookback"]), stdev(closes, p["lookback"])
        for i in range(n):
            if mean[i] is None or sd[i] is None or sd[i] == 0:
                continue
            z = (closes[i] - mean[i]) / sd[i]
            if z <= -p["z_entry"]:
                sig[i] = 1
            elif p["direction"] == "both" and z >= p["z_entry"]:
                sig[i] = -1

    elif block["type"] == "trend_scan":
        windows = list(range(20, p["max_lookback"] + 1, 10))
        for i in range(max(windows) - 1, n):
            best = max((trend_tstat(closes[i - w + 1:i + 1]) for w in windows),
                       key=abs)
            if best >= p["t_min"]:
                sig[i] = 1

    else:
        raise ValueError(f"no executor for entry type {block['type']!r}")
    return sig, state


def gate_mask(gates: list[dict], bars: list[dict]) -> list[bool]:
    """AND of all regime/filter blocks; False during any gate's warmup."""
    closes = [b["close"] for b in bars]
    n = len(bars)
    mask = [True] * n
    for g in gates:
        p = g["params"]
        if g["type"] == "regime_ma":
            ma = sma(closes, p["ma_len"])
            for i in range(n):
                if ma[i] is None or closes[i] <= ma[i]:
                    mask[i] = False
        elif g["type"] == "vol_percentile":
            vol = realized_ann_vol(closes, p["lookback"])
            for i in range(n):
                r = percentile_rank(vol, i, 365)
                if r is None or r > p["max_pctile"]:
                    mask[i] = False
        else:
            raise ValueError(f"no executor for gate type {g['type']!r}")
    return mask
