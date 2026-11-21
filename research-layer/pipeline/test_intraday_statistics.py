"""Statistics on daily marks (2b-1 design s3.3, Ruling R3): an hourly input
that only moves at UTC day boundaries must give EXACTLY the daily result."""
from __future__ import annotations

import random

from . import bars, gauntlet, gauntlet_core, plateau, regime, walkforward


def _days(n):
    return [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]


def _daily_equity(n=80, seed=1):
    rng = random.Random(seed)
    eq, out = 1.0, []
    for d in _days(n):
        eq *= 1 + rng.uniform(-0.02, 0.025)
        out.append((d, eq))
    return out


def _hourly_from_daily(daily):
    """24 bars per day, flat within the day at that day's value."""
    return [(f"{d} {h:02d}:00:00", v) for d, v in daily for h in range(24)]


def test_dated_returns_and_curve_returns_equal_daily():
    daily = _daily_equity()
    hourly = _hourly_from_daily(daily)
    assert [r for _, r in gauntlet_core.daily_returns_with_dates(hourly)] == \
        [r for _, r in gauntlet_core.daily_returns_with_dates(daily)]
    assert [d for d, _ in gauntlet_core.daily_returns_with_dates(hourly)] == \
        [d for d, _ in gauntlet_core.daily_returns_with_dates(daily)]
    assert gauntlet.daily_returns_from_curve(hourly) == \
        gauntlet.daily_returns_from_curve(daily)


def test_annualized_sharpe_equal_daily():
    daily = _daily_equity()
    assert plateau.annualized_sharpe(_hourly_from_daily(daily)) == \
        plateau.annualized_sharpe(daily)


def test_window_vol_equal_daily():
    daily = _daily_equity(seed=2)
    dbars = {"X": [{"date": d, "close": v} for d, v in daily]}
    hbars = {"X": [{"date": d, "close": v} for d, v in _hourly_from_daily(daily)]}
    assert gauntlet_core.window_vol(hbars, ["X"], "", "9999-12-31") == \
        gauntlet_core.window_vol(dbars, ["X"], "", "9999-12-31")


def test_clustering_series_uses_the_daily_return_not_the_last_hour():
    """The defect: intersect_returns kept the LAST per-bar return per date.
    Here the last hour of each day is noise; the daily return is the signal."""
    daily = _daily_equity(seed=3)
    hourly = []
    rng = random.Random(9)
    for (d, v), prev in zip(daily, [daily[0][1]] + [x[1] for x in daily[:-1]]):
        for h in range(23):
            hourly.append((f"{d} {h:02d}:00:00", v * (1 + rng.uniform(-0.01, 0.01))))
        hourly.append((f"{d} 23:00:00", v))     # the day closes at the daily value
    got = gauntlet_core.daily_returns_with_dates(hourly)
    want = gauntlet_core.daily_returns_with_dates(daily)
    assert got == want
    aligned, dates = gauntlet.intersect_returns({"a": got, "b": want})
    assert list(aligned["a"]) == list(aligned["b"])


def test_walkforward_and_regime_membership_unchanged_for_tradfi_dates():
    """Ruling R4 / Review Focus 1: tradfi 1d dates carry ' 00:00:00'."""
    dates = [f"{d} 00:00:00" for d in _days(60)]
    trades = [{"entry_date": dates[i], "return_net": 0.01 * (1 if i % 3 else -1)}
              for i in range(0, 60, 5)]
    wf = walkforward.walkforward_report(trades, dates, n_folds=3, purge_bars=2)
    assert sum(f["n_trades"] for f in wf["folds"]) == len(trades)
    rows = [{"date": d, "close": 100 + i} for i, d in enumerate(dates)]
    labels = regime.regime_by_date(rows, ma_len=5)
    split = regime.regime_split(trades, labels)
    assert split["unlabelled"]["n"] == sum(1 for t in trades
                                           if t["entry_date"] < dates[4])


def test_walkforward_and_regime_match_intraday_trades_to_daily_marks():
    days = _days(60)
    hourly_dates = [f"{d} {h:02d}:00:00" for d in days for h in range(24)]
    trades = [{"entry_date": f"{days[i]} 13:00:00", "return_net": 0.01}
              for i in range(0, 60, 5)]
    marks = [d for d, _ in bars.daily_marks([(d, 0) for d in hourly_dates])]
    wf = walkforward.walkforward_report(trades, marks, n_folds=3, purge_bars=2)
    assert sum(f["n_trades"] for f in wf["folds"]) == len(trades)
    rows = [{"date": d, "close": 100 + i} for i, d in enumerate(marks)]
    split = regime.regime_split(trades, regime.regime_by_date(rows, ma_len=5))
    assert split["unlabelled"]["n"] == 1      # only days[0]'s trade precedes the MA


def test_resolved_periods_is_bars_per_year():
    """The simcache key's periods_per_year is the engine's bars-per-year; for
    a 1d spec it is exactly the prior SESSION_PERIODS value."""
    assert gauntlet._resolved_periods(
        {"universe": {"session": "24x7", "timeframe": "1h"}}) == 8760
    assert gauntlet._resolved_periods(
        {"universe": {"session": "24x7", "timeframe": "1d"}}) == 365
    assert gauntlet._resolved_periods(
        {"universe": {"session": "fx_5d", "timeframe": "1d"}}) == 261
    assert gauntlet._resolved_periods({"universe": {"session": "24x7"}}) == 365
    assert gauntlet._resolved_periods({"universe": {}}) == 365


# ---- end to end: an hourly twin through the gauntlet's two verdict paths ----
#
# Task 9 (2b-1 addendum A): the daily-mark wiring INSIDE evaluate_standalone
# and gauntlet._evaluate_candidate (train_dates, the regime ruler's bars) is
# pinned by running a real engine twin pair. The 1h bars move only in the
# LAST hour of each UTC day (hours 0-22 sit flat at the prior close, hour 23
# carries the daily bar's OHLC), so the daily marks of the 1h run are the 1d
# run. The strategy reads only bar-local facts (ma_cross_ds 1/2 = "up close",
# swing_stop 1 = the prior bar's low), and the price path never has two up
# days in a row and never undoes an up day the next day, so both twins take
# the same trades on the same UTC dates at the same prices.

# 295 train days (not divisible by 3): were train_dates the raw HOURLY
# stamps, the first walk-forward fold boundary would fall inside day 98
# (295 // 3), putting that day in two folds; the fixture enters a trade on day
# 98, so that defect changes a fold's n_trades (pinned below).
TWIN_CUTOFF_DAY = 295


def _twin_closes():
    pats = [((0.03, -0.01), 120), ((0.01, -0.006, -0.006, -0.006), 100),
            ((0.03, -0.01), 80), ((0.01, -0.005, -0.005), 60),
            ((0.01, -0.006, -0.006, -0.006), 60)]
    closes = [100.0]
    for pat, n in pats:
        for i in range(n):
            closes.append(closes[-1] * (1 + pat[i % len(pat)]))
    return closes


def _twin_bars():
    import datetime as _dt
    closes = _twin_closes()
    day0 = _dt.date(2022, 1, 1)
    daily, hourly = [], []
    for k, c in enumerate(closes):
        d = (day0 + _dt.timedelta(days=k)).isoformat()
        o = closes[k - 1] if k else c
        bar = {"open": o, "high": max(o, c), "low": min(o, c), "close": c,
               "volume": 1.0}
        daily.append(dict(bar, date=d))
        for h in range(23):
            hourly.append({"date": f"{d} {h:02d}:00:00", "open": o, "high": o,
                           "low": o, "close": o, "volume": 1.0})
        hourly.append(dict(bar, date=f"{d} 23:00:00"))
    cutoff = (day0 + _dt.timedelta(days=TWIN_CUTOFF_DAY - 1)).isoformat()
    return daily, hourly, cutoff


def _twin_spec(tf):
    from . import cells
    return {
        # ONE id for both twins: the MC seed is the id, and a shared id makes
        # the simcache keys differ ONLY by periods_per_year
        "strategy_id": "c0ffee" * 10 + "abcd",
        "version": 2,
        "universe": {"asset_class": "crypto", "assets": ["BTCUSD"],
                     "session": "24x7", "timeframe": tf},
        "blocks": [
            {"role": "entry", "type": "ma_cross_ds",
             "params": {"fast": 1, "slow": 2, "direction": "long"}},
            {"role": "stop", "type": "swing_stop", "params": {"lookback": 1}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.0}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "provenance": {"sibling_group_id": "twin-" + tf},
        "cost_model": dict(cells.CLASSES["crypto"]["cost_model"]),
    }


def _assert_true_twins(res_h, res_d):
    """The fixture's own premise, checked: same trades, same daily marks."""
    assert len(res_d["trades"]) > 20
    key = [(str(t["entry_date"])[:10], t["return_net"], t["notional_frac"])
           for t in res_d["trades"]]
    assert [(str(t["entry_date"])[:10], t["return_net"], t["notional_frac"])
            for t in res_h["trades"]] == key
    assert [v for _, v in bars.daily_marks(res_h["equity"])] == \
        [v for _, v in res_d["equity"]]


def test_evaluate_standalone_hourly_twin_equals_daily():
    from .engine import run_spec
    daily, hourly, cutoff = _twin_bars()
    sh, sd = _twin_spec("1h"), _twin_spec("1d")
    res_d = run_spec(sd, {"BTCUSD": daily})
    _assert_true_twins(run_spec(sh, {"BTCUSD": hourly}), res_d)
    boundary_day = daily[TWIN_CUTOFF_DAY // 3]["date"]
    assert any(t["entry_date"] == boundary_day for t in res_d["trades"])
    out_h = gauntlet_core.evaluate_standalone(sh, {"BTCUSD": hourly}, cutoff,
                                              perturb=False)
    out_d = gauntlet_core.evaluate_standalone(sd, {"BTCUSD": daily}, cutoff,
                                              perturb=False)
    mh, md = out_h["metrics"], out_d["metrics"]
    assert md["walkforward"]["folds"][0]["n_trades"] > 0
    assert mh["walkforward"] == md["walkforward"]
    assert sum(b["n"] for k, b in md["regime"]["buckets"].items()
               if k != "unlabelled") > 0
    assert mh["regime"] == md["regime"]
    for k in ("train_sharpe", "is_vol", "oos_vol", "is_edge_per_trade",
              "oos_edge_per_trade", "edge_decay_pct", "cost_stress_net_pnl"):
        assert mh[k] == md[k], k
    assert (out_h["passed"], out_h["reason"]) == (out_d["passed"], out_d["reason"])


def test_evaluate_candidate_hourly_twin_equals_daily():
    from .cluster import effective_trials
    from .engine import run_spec
    daily, hourly, cutoff = _twin_bars()
    out = {}
    for spec, rows in ((_twin_spec("1h"), hourly), (_twin_spec("1d"), daily)):
        sid, sb = spec["strategy_id"], {"BTCUSD": rows}
        res = run_spec(spec, sb)
        dated = gauntlet_core.daily_returns_with_dates(res["equity"])
        rets = [r for _, r in dated]
        train_sharpe = gauntlet_core._annualized_sharpe_from_returns(
            [r for d, r in dated if gauntlet_core._date_le(d, cutoff)])
        trials_n, _labels, trials_var = effective_trials({sid: rets})
        family = [{"sid": sid, "axes": {}, "score": 1.0,
                   "screen_trade_count_fail": False, "gauntlet_passed": False}]
        payload = gauntlet._candidate_payload(
            spec, sb, res, rets, 1, 1, train_sharpe, trials_n, trials_var,
            family[0], family, {}, cutoff, False, "native", None, 0, 0)
        out[spec["universe"]["timeframe"]] = gauntlet._evaluate_candidate(payload)
    mh, md = out["1h"]["metrics"], out["1d"]["metrics"]
    assert md["haircut"]["window"] == "train"
    assert mh["haircut"] == md["haircut"]
    assert mh["walkforward"] == md["walkforward"]
    assert mh["regime"] == md["regime"]


def test_registry_series_keys_an_hourly_spec_by_its_bars_per_year():
    """Brief mutant 14: the simcache key's periods_per_year is the RESOLVED
    bars-per-year, so a 1h spec can never be served a 1d entry."""
    from . import cells, simcache
    daily, hourly, _ = _twin_bars()
    sh, sd = _twin_spec("1h"), _twin_spec("1d")
    bars_by_cell = {("BTCUSD", "1h"): hourly, ("BTCUSD", "1d"): daily}
    hashes = {cells.cell_id("BTCUSD", "1h"): "a" * 64,
              cells.cell_id("BTCUSD", "1d"): "a" * 64}

    class Cache:
        def __init__(self):
            self.keys = []

        def get(self, key):
            self.keys.append(key)
            return None

        def put(self, key, series, equity_len):
            pass

    cache = Cache()
    gauntlet.registry_series([sh, sd], bars_by_cell, hashes, cache, set())
    shas = {"BTCUSD": "a" * 64}
    rev = gauntlet.ENGINE_REV
    sid = sh["strategy_id"]
    assert sid == sd["strategy_id"]
    assert cache.keys == [simcache.cache_key(sid, shas, rev, 8760),
                          simcache.cache_key(sid, shas, rev, 365)]
    assert cache.keys[0] != cache.keys[1]
