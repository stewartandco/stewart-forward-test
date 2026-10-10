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
