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
            # convention: the first warm bar counts as a cross if already
            # fast>slow (state init 0 during warmup) — enter-on-first-eligible
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

    elif block["type"] == "trend_scan_ds":
        windows = list(range(20, p["max_lookback"] + 1, 10))
        allow_long = p["direction"] in ("long", "both")
        allow_short = p["direction"] in ("short", "both")
        for i in range(max(windows) - 1, n):
            best = max((trend_tstat(closes[i - w + 1:i + 1]) for w in windows),
                       key=abs)
            if allow_long and best >= p["t_min"]:
                sig[i] = 1
            elif allow_short and best <= -p["t_min"]:
                sig[i] = -1

    elif block["type"] == "ma_cross_ds":
        fast, slow = sma(closes, p["fast"]), sma(closes, p["slow"])
        allow = {"long": (1,), "short": (-1,), "both": (1, -1)}[p["direction"]]
        for i in range(n):
            if fast[i] is None or slow[i] is None:
                continue
            state[i] = 1 if fast[i] > slow[i] else -1
            # same enter-on-first-eligible convention as ma_cross: state is 0
            # during warmup, so the first warm bar counts as a cross
            if state[i] != state[i - 1] and state[i] in allow:
                sig[i] = state[i]

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
        elif g["type"] == "regime_ma_short":
            ma = sma(closes, p["ma_len"])
            for i in range(n):
                if ma[i] is None or closes[i] >= ma[i]:
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


# ---------------- single-asset simulator -----------------------------------

def _tightest_stop(stops: list[dict], entry_px: float, side: int,
                   atr_series: dict, i: int) -> float | None:
    """Stop price from the tightest of the spec's stop blocks; None if any
    ATR stop is still warming up."""
    candidates = []
    for s in stops:
        p = s["params"]
        if s["type"] == "pct_stop":
            dist = entry_px * p["pct"]
        elif s["type"] == "atr_stop":
            atr = atr_series[(p["atr_len"],)][i]
            if atr is None:
                return None
            dist = p["mult"] * atr
        else:
            raise ValueError(f"no executor for stop type {s['type']!r}")
        candidates.append(dist)
    return entry_px - side * min(candidates)


def simulate_asset(blocks: list[dict], bars: list[dict], cost_model: dict) -> dict:
    """Run one asset's book. Returns {trades, equity} where equity is the
    daily mark-to-market curve starting at 1.0."""
    by_role: dict[str, list[dict]] = {}
    for b in blocks:
        by_role.setdefault(b["role"], []).append(b)
    entry = by_role["entry"][0]
    gates = by_role.get("regime", []) + by_role.get("filter", [])
    stops = by_role["stop"]
    targets = by_role.get("target", [])
    time_stops = by_role.get("exit", [])
    risk = by_role["risk"][0]

    sig, state = entry_signals(entry, bars)
    mask = gate_mask(gates, bars) if gates else [True] * len(bars)
    atr_series = {}
    for s in stops:
        if s["type"] == "atr_stop":
            atr_series[(s["params"]["atr_len"],)] = atr_wilder(bars, s["params"]["atr_len"])
    closes = [b["close"] for b in bars]
    vol_series = (realized_ann_vol(closes, risk["params"]["lookback"])
                  if risk["type"] == "vol_target" else None)

    per_side = cost_model["commission_per_side"] + cost_model["slippage_ticks"]
    equity, curve, trades = 1.0, [], []
    pos = None  # {side, entry_px, entry_i, stop, target, deadline, notional}

    for i, b in enumerate(bars):
        was_flat = pos is None      # flat at the close of bar i-1 (signal time)

        # --- exits first (at this bar), for a position opened earlier
        if pos is not None and i > pos["entry_i"]:
            exit_px = exit_reason = None
            side = pos["side"]
            if pos["deadline"] is not None and i >= pos["deadline"]:
                exit_px, exit_reason = b["open"], "time"
            elif side * (pos["stop"] - b["open"]) >= 0:          # gap through stop
                exit_px, exit_reason = b["open"], "stop"
            elif pos["target"] is not None and side * (b["open"] - pos["target"]) >= 0:
                exit_px, exit_reason = b["open"], "target"       # gap through target
            elif (side == 1 and b["low"] <= pos["stop"]) or \
                 (side == -1 and b["high"] >= pos["stop"]):      # intrabar stop (wins ties)
                exit_px, exit_reason = pos["stop"], "stop"
            elif pos["target"] is not None and (
                    (side == 1 and b["high"] >= pos["target"]) or
                    (side == -1 and b["low"] <= pos["target"])):
                exit_px, exit_reason = pos["target"], "target"
            elif (entry["type"] in ("ma_cross", "ma_cross_ds")
                  and state[i - 1] != pos["side"]):
                exit_px, exit_reason = b["open"], "signal"       # cross-down exit
            if exit_px is not None:
                gross = pos["side"] * (exit_px / pos["entry_px"] - 1)
                net = gross - 2 * per_side
                equity += pos["notional"] * net
                trades.append({"side": "long" if pos["side"] == 1 else "short",
                               "entry_date": bars[pos["entry_i"]]["date"],
                               "entry_px": pos["entry_px"],
                               "exit_date": b["date"], "exit_px": exit_px,
                               "exit_reason": exit_reason, "return_net": net,
                               "notional_frac": pos["notional_frac"]})
                pos = None

        # --- entries: signal from previous close, honored only if we were
        # flat AT SIGNAL TIME (design: signals while in a position are
        # ignored, not queued) — prevents same-bar exit->reenter
        if was_flat and pos is None and i > 0 and sig[i - 1] != 0 and mask[i - 1]:
            side, entry_px = sig[i - 1], b["open"]
            stop = _tightest_stop(stops, entry_px, side, atr_series, i - 1)
            if stop is not None and abs(entry_px - stop) > 0:
                dist = abs(entry_px - stop)
                target = None
                if targets:
                    target = entry_px + side * targets[0]["params"]["r"] * dist
                deadline = (i + time_stops[0]["params"]["max_bars"]
                            if time_stops else None)
                if risk["type"] == "fixed_fraction":
                    notional = risk["params"]["f"] / (dist / entry_px)
                else:                                            # vol_target
                    rv = vol_series[i - 1]
                    if rv is None or rv == 0:
                        notional = None
                    else:
                        notional = risk["params"]["ann_vol"] / rv
                if notional is not None:
                    frac = min(notional, 1.0)
                    pos = {"side": side, "entry_px": entry_px, "entry_i": i,
                           "stop": stop, "target": target,
                           "deadline": deadline, "notional": frac * equity,
                           "notional_frac": frac}

        # --- daily mark-to-market
        mtm = equity
        if pos is not None:
            unreal = pos["side"] * (b["close"] / pos["entry_px"] - 1)
            mtm = equity + pos["notional"] * unreal
        curve.append(mtm)

    return {"trades": trades, "equity": curve}


# ---------------- spec runner + metrics ------------------------------------

def max_drawdown(curve: list[float]) -> float:
    peak, dd = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return dd


def run_spec(spec: dict, bars_by_asset: dict[str, list[dict]]) -> dict:
    """Run a strategy spec: one independent equal-capital book per universe
    asset, combined by mean. Returns {trades, equity, metrics}; equity is
    [(date, combined_equity)] over the shortest common calendar."""
    books = {}
    for asset in spec["universe"]["assets"]:
        books[asset] = simulate_asset(spec["blocks"], bars_by_asset[asset],
                                      spec["cost_model"])
    n = min(len(bars_by_asset[a]) for a in books)
    dates = [bars_by_asset[next(iter(books))][i]["date"] for i in range(n)]
    for a in books:
        for i in range(n):
            if bars_by_asset[a][i]["date"] != dates[i]:
                raise ValueError(
                    f"calendar misalignment: {a} bar {i} is "
                    f"{bars_by_asset[a][i]['date']}, expected {dates[i]}")
    combined = [sum(books[a]["equity"][i] for a in books) / len(books)
                for i in range(n)]
    trades = [dict(t, asset=a) for a in books for t in books[a]["trades"]]
    trades.sort(key=lambda t: (t["entry_date"], t["asset"]))
    wins = sum(1 for t in trades if t["return_net"] > 0)
    metrics = {
        "trades": len(trades),
        "net_pnl": combined[-1] - 1 if combined else 0.0,
        "win_rate": wins / len(trades) if trades else 0.0,
        "max_dd": -max_drawdown(combined) if combined else 0.0,
    }
    return {"trades": trades, "equity": list(zip(dates, combined)),
            "metrics": metrics}
