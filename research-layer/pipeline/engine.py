"""Pure deterministic block-spec interpreter on daily OHLCV bars.

No registry knowledge, no IO, no randomness: same spec + bars -> identical
output. Execution semantics per docs/2026-08-13-screen-design.md §3:
signals on close t, fills at open t+1; warmup enforced; same-bar stop+target
-> stop; gaps fill at open; no leverage.
"""
from __future__ import annotations

import math

from . import cells

# SP4 Task P4: bumped by hand on ANY change to this module that can alter a
# simulated number (trade fills, equity, sizing) -- never on a comment-only
# or refactor-with-no-numeric-effect edit. pipeline/simcache.py (Task P1)
# folds this into its cache key so an on-disk cache entry built under a
# prior engine revision is never served as if it still matched today's
# arithmetic. Bumped to "e2" by the float-stdev change below (was implicitly
# "e1" pre-SP4; no prior constant existed so there is nothing to diff against).
ENGINE_REV = "e2"


# ---------------- indicators (aligned lists; None during warmup) ----------

def _sample_stdev(values: list[float]) -> float:
    """Two-pass float sample stdev (n-1 denominator): mean, then mean of
    squared deviations, then sqrt. Same (n-1) semantics as
    `statistics.stdev`, computed in plain floats instead of exact Fraction
    arithmetic -- statistics.stdev converts every value to a Fraction and
    only rounds back to float at the very end, which is exact but pays for
    it on every call; this pays with float rounding in the last few ulps
    instead (see the same-answer proof in test_engine_classes.py)."""
    n = len(values)
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(var)


def sma(values: list[float], n: int) -> list:
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        out[i] = sum(values[i - n + 1:i + 1]) / n
    return out


def stdev(values: list[float], n: int) -> list:
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        out[i] = _sample_stdev(values[i - n + 1:i + 1])
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


def realized_ann_vol(closes: list[float], n: int, periods_per_year: int = 365) -> list:
    """Annualized stdev of daily log returns over n returns.

    periods_per_year defaults to 365 (crypto, 24x7) and is threaded from the
    spec's class config (spec s10 item 4) for non-crypto sessions -- fx_5d
    uses 261. Crypto callers that never pass it get the exact prior
    sqrt(365) arithmetic."""
    rets = [None] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out = [None] * len(closes)
    for i in range(n, len(closes)):
        window = rets[i - n + 1:i + 1]
        out[i] = _sample_stdev(window) * math.sqrt(periods_per_year) if len(window) > 1 else None
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

    elif block["type"] in ("channel_breakout", "channel_breakout_dense"):
        lb = p["lookback"]
        for i in range(lb, n):
            hi = max(b["high"] for b in bars[i - lb:i])
            lo = min(b["low"] for b in bars[i - lb:i])
            if closes[i] > hi:
                sig[i] = 1
            elif p["direction"] == "both" and closes[i] < lo:
                sig[i] = -1

    elif block["type"] in ("zscore_reversion", "zscore_reversion_dense"):
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

    elif block["type"] in ("trend_scan_ds", "trend_scan_dense"):
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

    elif block["type"] in ("ma_cross_ds", "ma_cross_dense"):
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
        elif g["type"] in ("regime_ma_short", "regime_ma_short_dense"):
            ma = sma(closes, p["ma_len"])
            for i in range(n):
                if ma[i] is None or closes[i] >= ma[i]:
                    mask[i] = False
        elif g["type"] in ("vol_percentile", "vol_percentile_dense"):
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
        elif s["type"] in ("atr_stop", "atr_stop_dense"):
            atr = atr_series[(p["atr_len"],)][i]
            if atr is None:
                return None
            dist = p["mult"] * atr
        else:
            raise ValueError(f"no executor for stop type {s['type']!r}")
        candidates.append(dist)
    return entry_px - side * min(candidates)


# ---------------- D15 exit rules v7 (spec version >= 2) --------------------
#
# docs/2026-09-03-exit-rules-v7-design.md §1/§3. Retired types stay in the
# grammar (chained schemas are immutable) and are refused HERE for version
# >= 2; version 1 runs them unchanged -- the quarantine forward runner
# re-simulates every legacy sid daily and their record must not move.
RETIRED_STOP_TYPES = {"pct_stop"}
RETIRED_EXIT_TYPES = {"time_stop"}

# Sentinel returned by _stop_price when an indicator-placed level is not on
# the adverse side of the entry: the signal is ineligible and the caller
# counts it (metrics `stop_invalid`) instead of silently skipping it.
STOP_INVALID = "invalid"

# Indicator series each new stop / exit type reads, keyed ("sma"|"stdev", n);
# computed once per simulate_asset call (v2 only) and shared across blocks.
_IND_SERIES_NEEDS = {
    "ma_stop":       lambda p: [("sma", p["ma_len"])],
    "band_stop":     lambda p: [("sma", p["lookback"]), ("stdev", p["lookback"])],
    "ma_crossunder": lambda p: [("sma", p["fast"]), ("sma", p["slow"])],
    "zscore_revert": lambda p: [("sma", p["lookback"]), ("stdev", p["lookback"])],
    "regime_flip":   lambda p: [("sma", p["ma_len"])],
}


def _indicator_series(blocks: list[dict], closes: list[float]) -> dict:
    out: dict = {}
    for b in blocks:
        for key, n in _IND_SERIES_NEEDS.get(b["type"], lambda p: [])(b["params"]):
            if (key, n) not in out:
                out[(key, n)] = sma(closes, n) if key == "sma" else stdev(closes, n)
    return out


def _indicator_stop(s: dict, bars: list[dict], i: int, side: int,
                    series: dict) -> float | None:
    """D15 indicator-placed stop LEVEL at signal bar i (fixed at entry, no
    trailing). None while the indicator is warming up. The caller decides
    eligibility (the level must be on the adverse side of the entry)."""
    p, t = s["params"], s["type"]
    if t in ("swing_stop", "channel_stop"):
        # both = the extreme of the `lookback` bars BEFORE the signal bar,
        # [i-lookback, i); they differ only in the grid they sweep (design §1)
        lb = p["lookback"]
        if i < lb:
            return None
        window = bars[i - lb:i]
        return min(b["low"] for b in window) if side == 1 else max(b["high"] for b in window)
    if t == "ma_stop":
        return series[("sma", p["ma_len"])][i]
    if t == "band_stop":
        ma = series[("sma", p["lookback"])][i]
        sd = series[("stdev", p["lookback"])][i]
        if ma is None or sd is None:
            return None
        return ma - p["mult"] * sd if side == 1 else ma + p["mult"] * sd
    raise ValueError(f"no executor for stop type {t!r}")


def _stop_price(stops: list[dict], bars: list[dict], entry_px: float, side: int,
                atr_series: dict, ind_series: dict, i: int, version: int):
    """Stop price for a position entered at entry_px from signal bar i.
    version 1: today's _tightest_stop (pct/atr distances) unchanged.
    version 2: every stop type as a LEVEL; None while any indicator warms
    up; a level not strictly on the adverse side of the entry (wrong side
    or exactly at it) makes the signal ineligible -> STOP_INVALID; with
    several stops the tightest adverse level wins."""
    if version < 2:
        return _tightest_stop(stops, entry_px, side, atr_series, i)
    levels = []
    for s in stops:
        if s["type"] in ("atr_stop", "atr_stop_dense"):
            atr = atr_series[(s["params"]["atr_len"],)][i]
            if atr is None:
                return None
            levels.append(entry_px - side * s["params"]["mult"] * atr)
        else:
            lvl = _indicator_stop(s, bars, i, side, ind_series)
            if lvl is None:
                return None
            levels.append(lvl)
    if any(side * (entry_px - lvl) <= 0 for lvl in levels):
        return STOP_INVALID
    return max(levels) if side == 1 else min(levels)      # tightest


def signal_exit(block: dict, bars: list[dict], closes: list[float], i: int,
                side: int, series: dict) -> bool:
    """D15 indicator-EVENT exit evaluated on close i for a position of `side`.
    True means: exit at the open of bar i+1 (the caller fills it). False
    during the indicator's warmup."""
    p, t = block["params"], block["type"]
    if t == "ma_crossunder":
        f = series[("sma", p["fast"])][i]
        s = series[("sma", p["slow"])][i]
        if f is None or s is None:
            return False
        return f < s if side == 1 else f > s
    if t == "channel_exit":
        lb = p["lookback"]
        if i < lb:
            return False
        window = bars[i - lb:i]
        return (closes[i] < min(b["low"] for b in window)) if side == 1 \
            else (closes[i] > max(b["high"] for b in window))
    if t == "zscore_revert":
        ma = series[("sma", p["lookback"])][i]
        sd = series[("stdev", p["lookback"])][i]
        if ma is None or sd is None or sd == 0:
            return False
        z = (closes[i] - ma) / sd
        return z >= -p["z_exit"] if side == 1 else z <= p["z_exit"]
    if t == "tstat_decay":
        windows = list(range(20, p["max_lookback"] + 1, 10))
        if i < max(windows) - 1:
            return False
        best = max((trend_tstat(closes[i - w + 1:i + 1]) for w in windows), key=abs)
        return (best <= p["t_exit"]) if side == 1 else (best >= -p["t_exit"])
    if t == "regime_flip":
        ma = series[("sma", p["ma_len"])][i]
        if ma is None:
            return False
        return closes[i] < ma if side == 1 else closes[i] > ma
    raise ValueError(f"no executor for exit type {t!r}")


def exit_reason_counts(trades: list[dict]) -> dict[str, int]:
    """{exit_reason: n} over closed trades; empty dict for none. RECORDED,
    never gated (design §3/§4)."""
    out: dict[str, int] = {}
    for t in trades:
        out[t["exit_reason"]] = out.get(t["exit_reason"], 0) + 1
    return dict(sorted(out.items()))


def simulate_asset(blocks: list[dict], bars: list[dict], cost_model: dict,
                   periods_per_year: int = 365, *, version: int = 1) -> dict:
    """Run one asset's book.

    `version` is the spec's registration version (D15 exit rules v7):
      * 1 (default, and every spec without the key) -- the legacy path, kept
        byte-for-byte: deadline time stop, pct_stop, and the implicit
        ma_cross* crossunder exit. Frozen by the golden in
        test_exit_rules_v7.py.
      * >= 2 -- no deadline, no implicit exit; retired block types raise;
        indicator-placed stops (swing/ma/channel/band + atr) and declared
        `exit` blocks evaluated on close t and filled at open t+1, AFTER the
        barrier checks (gap stop, gap target, intrabar stop, intrabar target).

    Returns {trades, equity, position, stop_invalid}:
      * trades   — closed round trips; an open position at sample end is
                   NEVER a trade (it is marked to market in `equity`)
      * equity   — daily mark-to-market curve, starting at 1.0
      * stop_invalid — signals dropped because the stop level was not on the
                   adverse side of the entry (v2; always 0 on the legacy path)
      * position — the OPEN position at the end of the run, or None if flat.
        A copy, so mutating it cannot corrupt a finished book. Its fields:
          side          +1 long / -1 short
          entry_px      fill price
          entry_i       index into the `bars` list PASSED TO THIS CALL, and
                        meaningless against any other list
          stop, target  prices (target None if the spec has no target block)
          deadline      time-stop bar index, or None (always None under v2)
          exit_pending  "signal:<type>" when a declared exit fired on the
                        last close and would fill at the next open (v2;
                        always None on the legacy path)
          notional      absolute capital at risk
          notional_frac notional as a fraction of equity at entry
    """
    by_role: dict[str, list[dict]] = {}
    for b in blocks:
        by_role.setdefault(b["role"], []).append(b)
    entry = by_role["entry"][0]
    gates = by_role.get("regime", []) + by_role.get("filter", [])
    stops = by_role["stop"]
    targets = by_role.get("target", [])
    exits = by_role.get("exit", [])
    risk = by_role["risk"][0]
    legacy = version < 2
    if legacy:
        time_stops = exits                       # today's semantics, untouched
        signal_exits: list[dict] = []
    else:
        for s in stops:
            if s["type"] in RETIRED_STOP_TYPES:
                raise ValueError(f"stop type {s['type']!r} is retired under "
                                 f"exit-rules-v7 (spec version {version})")
        for x in exits:
            if x["type"] in RETIRED_EXIT_TYPES:
                raise ValueError(f"exit type {x['type']!r} is retired under "
                                 f"exit-rules-v7 (spec version {version})")
        time_stops = []
        signal_exits = exits

    sig, state = entry_signals(entry, bars)
    mask = gate_mask(gates, bars) if gates else [True] * len(bars)
    atr_series = {}
    for s in stops:
        if s["type"] in ("atr_stop", "atr_stop_dense"):
            atr_series[(s["params"]["atr_len"],)] = atr_wilder(bars, s["params"]["atr_len"])
    closes = [b["close"] for b in bars]
    # D15: the sma/stdev series the new stops and exits read, once per spec
    ind_series = {} if legacy else _indicator_series(stops + signal_exits, closes)
    vol_series = (realized_ann_vol(closes, risk["params"]["lookback"], periods_per_year)
                  if risk["type"] == "vol_target" else None)

    per_side = cost_model["commission_per_side"] + cost_model["slippage_ticks"]
    # Spec s10 item 5: short_financing_per_year accrues per bar held SHORT,
    # divided by the session's periods_per_year. Absent key -> 0.0 per bar,
    # so a crypto cost_model (no financing key) is arithmetic-identical to
    # before this field existed.
    # TRAP (spec s10.11): periods_per_year silently defaults to 365 on any
    # caller that forgets to derive it from the spec's session, so a
    # mis-stamped or missing-session fx spec would financing-accrue at the
    # crypto rate with no error -- closed in practice by every caller
    # deriving it from cells.SESSION_PERIODS (run_spec here, quarantine.py's
    # observe_day), never by trusting this default.
    fin_per_bar = cost_model.get("short_financing_per_year", 0.0) / periods_per_year
    equity, curve, trades = 1.0, [], []
    stop_invalid = 0
    pos = None  # {side, entry_px, entry_i, stop, target, deadline, notional, exit_pending}

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
            elif legacy and entry["type"] in ("ma_cross", "ma_cross_ds", "ma_cross_dense") \
                    and state[i - 1] != pos["side"]:
                exit_px, exit_reason = b["open"], "signal"       # legacy implicit cross-down exit
            elif not legacy and pos["exit_pending"] is not None:
                exit_px, exit_reason = b["open"], pos["exit_pending"]   # declared exit, open t+1
            if exit_px is not None:
                gross = pos["side"] * (exit_px / pos["entry_px"] - 1)
                net = gross - 2 * per_side
                if pos["side"] == -1:
                    # per-bar financing over the whole holding period, added
                    # at exit alongside the round-trip's costs (spec s10.5)
                    net += fin_per_bar * (i - pos["entry_i"])
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
            stop = _stop_price(stops, bars, entry_px, side, atr_series, ind_series,
                               i - 1, version)
            if stop is STOP_INVALID:
                stop_invalid += 1
                stop = None
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
                           "notional_frac": frac, "exit_pending": None}

        # --- daily mark-to-market
        mtm = equity
        if pos is not None:
            unreal = pos["side"] * (b["close"] / pos["entry_px"] - 1)
            if pos["side"] == -1:
                unreal += fin_per_bar * (i - pos["entry_i"])
            mtm = equity + pos["notional"] * unreal
        curve.append(mtm)

        # --- D15: declared signal exits are evaluated on THIS close and
        # filled at the next open, exactly like entries (a position opened at
        # this bar's open is eligible on this close). First declared exit
        # that fires wins; barriers at the next bar still take precedence.
        if not legacy and pos is not None:
            pos["exit_pending"] = None
            for x in signal_exits:
                if signal_exit(x, bars, closes, i, pos["side"], ind_series):
                    pos["exit_pending"] = f"signal:{x['type']}"
                    break

    # An open position never appears in `trades` (those are closed round
    # trips), so the quarantine forward runner has no other way to read the
    # current size without re-deriving the entry/stop/sizing logic here.
    # Copied out so callers cannot reach into the simulator's live state.
    return {"trades": trades, "equity": curve,
            "position": dict(pos) if pos is not None else None,
            "stop_invalid": stop_invalid}


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
    # Spec s10 item 4: periods_per_year is derived from the universe's
    # session via the class registry (crypto 24x7 -> 365, fx_5d -> 261);
    # an unrecognised/missing session falls back to 365 (today's behaviour).
    periods_per_year = cells.SESSION_PERIODS.get(spec["universe"].get("session"), 365)
    # D15: a spec without `version` is a legacy (version 1) registration
    version = spec.get("version", 1)
    books = {}
    for asset in spec["universe"]["assets"]:
        books[asset] = simulate_asset(spec["blocks"], bars_by_asset[asset],
                                      spec["cost_model"], periods_per_year,
                                      version=version)
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
        # D15: RECORDED, never gated -- why trades closed, whether any book
        # ended with a position still open (marked to market in equity, never
        # a closed trade), and how many signals were dropped because the
        # indicator-placed stop was not on the adverse side of the entry.
        "exit_reasons": exit_reason_counts(trades),
        "open_at_end": any(books[a]["position"] is not None for a in books),
        "stop_invalid": sum(books[a]["stop_invalid"] for a in books),
    }
    return {"trades": trades, "equity": list(zip(dates, combined)),
            "metrics": metrics}
