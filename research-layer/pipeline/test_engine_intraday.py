"""Engine bar mechanics at intraday timeframes (2b-1 design s3.2)."""
from __future__ import annotations

import math

import pytest

from . import bars as _bars
from . import engine


def test_realized_ann_vol_scales_by_bars_per_year():
    # alternating +r/-r log returns: sample stdev is known exactly
    r = 0.01
    closes = [100.0]
    for i in range(60):
        closes.append(closes[-1] * math.exp(r if i % 2 == 0 else -r))
    v = engine.realized_ann_vol(closes, 20, _bars.bars_per_year("24x7", "1h"))
    window = [r if i % 2 == 0 else -r for i in range(40, 60)]
    m = sum(window) / len(window)
    sd = math.sqrt(sum((x - m) ** 2 for x in window) / (len(window) - 1))
    assert v[-1] == pytest.approx(sd * math.sqrt(8760))


def _pct_spec(session, tf):
    return [{"type": "vol_percentile", "params": {"lookback": 5, "max_pctile": 1.0}}]


def test_percentile_window_is_365_daily_bars_scaled():
    # False until the 365-day window holds only defined vols: realized_ann_vol
    # is None for the first `lookback` (5) bars, so the first True index is
    # window + 4.
    n = 400
    rows = [{"date": f"d{i:04d}", "close": 100.0 + (i % 7)} for i in range(n * 24 + 50)]
    mask_1d = engine.gate_mask(_pct_spec("24x7", "1d"), rows[:n], 1)
    assert mask_1d.index(True) == 365 + 4       # window 365 bars, as before
    mask_1h = engine.gate_mask(_pct_spec("24x7", "1h"), rows, 24)
    assert mask_1h.index(True) == 365 * 24 + 4  # one year of 1h bars


def test_fx_daily_percentile_window_unchanged():
    rows = [{"date": f"d{i:04d}", "close": 1.0 + (i % 5) / 100} for i in range(400)]
    # fx 1d: bars_per_day is 1, so the window stays 365 (Ruling R2), never 261
    assert engine.gate_mask(_pct_spec("fx_5d", "1d"), rows, 1).index(True) == 365 + 4


def test_gate_mask_default_is_todays_behaviour():
    rows = [{"date": f"d{i:04d}", "close": 100.0 + (i % 3)} for i in range(400)]
    assert engine.gate_mask(_pct_spec("24x7", "1d"), rows) == \
        engine.gate_mask(_pct_spec("24x7", "1d"), rows, 1)


def test_run_spec_resolves_bars_per_year(monkeypatch):
    seen = []
    real = engine.simulate_asset

    def spy(blocks, rows, cost_model, periods_per_year=365, **kw):
        seen.append(periods_per_year)
        return real(blocks, rows, cost_model, periods_per_year, **kw)
    monkeypatch.setattr(engine, "simulate_asset", spy)
    base = {"blocks": [], "cost_model": {"commission_per_side": 0.0, "slippage_ticks": 0.0}}
    rows = [{"date": f"2024-01-{i + 1:02d}", "open": 1, "high": 1, "low": 1,
             "close": 1, "volume": 1} for i in range(5)]
    for session, tf, want in (("24x7", "1h", 8760), ("24x7", "1d", 365),
                              ("fx_5d", "1d", 261), (None, "1d", 365)):
        seen.clear()
        spec = dict(base, universe={"assets": ["X"], "session": session, "timeframe": tf})
        if session is None:
            del spec["universe"]["session"]
        try:
            engine.run_spec(spec, {"X": rows})
        except Exception:
            pass   # an empty block list may not simulate; the resolved value is what is pinned
        assert seen and seen[0] == want, (session, tf, seen)


def test_run_spec_refuses_intraday_outside_24x7():
    spec = {"blocks": [], "cost_model": {}, "universe": {"assets": ["X"],
            "session": "fx_5d", "timeframe": "1h"}}
    with pytest.raises(_bars.IntradayUnsupported):
        engine.run_spec(spec, {"X": []})


def test_run_spec_passes_the_bar_size_to_simulate_asset(monkeypatch):
    """Task 9 mutant 15: run_spec hands simulate_asset bars_per_day (the
    percentile window's scale), 24 for a 1h spec and 1 for a 1d spec."""
    seen = []
    real = engine.simulate_asset

    def spy(blocks, rows, cost_model, periods_per_year=365, **kw):
        seen.append(kw.get("bars_per_day"))
        return real(blocks, rows, cost_model, periods_per_year, **kw)
    monkeypatch.setattr(engine, "simulate_asset", spy)
    base = {"blocks": [], "cost_model": {"commission_per_side": 0.0, "slippage_ticks": 0.0}}
    rows = [{"date": f"2024-01-{i + 1:02d}", "open": 1, "high": 1, "low": 1,
             "close": 1, "volume": 1} for i in range(5)]
    for session, tf, want in (("24x7", "1h", 24), ("24x7", "15m", 96),
                              ("24x7", "1d", 1), ("fx_5d", "1d", 1)):
        seen.clear()
        spec = dict(base, universe={"assets": ["X"], "session": session, "timeframe": tf})
        try:
            engine.run_spec(spec, {"X": rows})
        except Exception:
            pass   # an empty block list may not simulate; the kwarg is what is pinned
        assert seen == [want], (session, tf, seen)


# ---- 2b-1 final review I1: run_spec's max_dd is a statistic, so it is
# measured on DAILY marks (spec s2), never on the per-bar path ----

def _hour(day, h):
    return f"2024-01-{day:02d} {h:02d}:00:00"


def _dip_bars():
    """24x7 1h bars, o=h=l=c except the breakout bar. Day 1: a breakout at
    01:00 (the long fills at 02:00's open, 101) then flat at 101. Day 2: the
    price dips to 80 for hours 0-11 and recovers to 95 for hours 12-23, so the
    day's LAST bar (its daily mark) is 95 while the path's trough is 80."""
    rows = []

    def bar(d, o, h, l, c):
        rows.append({"date": d, "open": o, "high": h, "low": l, "close": c,
                     "volume": 1.0})
    bar(_hour(1, 0), 100.0, 100.0, 100.0, 100.0)
    bar(_hour(1, 1), 100.0, 101.0, 100.0, 101.0)      # close > prior high
    for h in range(2, 24):
        bar(_hour(1, h), 101.0, 101.0, 101.0, 101.0)
    for h in range(24):
        c = 80.0 if h < 12 else 95.0
        bar(_hour(2, h), c, c, c, c)
    return rows


def _dip_spec():
    # legacy (version 1) channel breakout: stateless, no implicit exit; a
    # 50% pct_stop sits far below the dip; f=0.5 over a 0.5 stop -> notional 1
    return {"universe": {"assets": ["X"], "session": "24x7", "timeframe": "1h"},
            "blocks": [
                {"role": "entry", "type": "channel_breakout",
                 "params": {"lookback": 1, "direction": "long"}},
                {"role": "stop", "type": "pct_stop", "params": {"pct": 0.5}},
                {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.5}}],
            "cost_model": {"commission_per_side": 0.0, "slippage_ticks": 0.0}}


def test_max_dd_is_measured_on_daily_marks():
    res = engine.run_spec(_dip_spec(), {"X": _dip_bars()})
    assert len(res["trades"]) == 0 and res["metrics"]["open_at_end"]
    per_bar = [v for _, v in res["equity"]]
    marks = [v for _, v in _bars.daily_marks(res["equity"])]
    assert marks == pytest.approx([1.0, 95.0 / 101.0])
    # the per-bar path dips to 80 intraday; the daily marks only to 95
    assert engine.max_drawdown(per_bar) == pytest.approx(1 - 80.0 / 101.0)
    assert res["metrics"]["max_dd"] == -engine.max_drawdown(marks)
    assert res["metrics"]["max_dd"] == pytest.approx(-(1 - 95.0 / 101.0))


def test_max_dd_equal_for_the_hourly_daily_twins():
    from .test_intraday_statistics import _twin_bars, _twin_spec
    daily, hourly, _ = _twin_bars()
    res_h = engine.run_spec(_twin_spec("1h"), {"BTCUSD": hourly})
    res_d = engine.run_spec(_twin_spec("1d"), {"BTCUSD": daily})
    assert res_d["metrics"]["max_dd"] < 0
    assert res_h["metrics"]["max_dd"] == res_d["metrics"]["max_dd"]
