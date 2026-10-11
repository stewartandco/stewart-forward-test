"""2b-2 design s3.2 / s3.4: an intraday cell's day is its daily mark plus
the day's trade counts; a daily spec's row is unchanged."""
from __future__ import annotations

from .quarantine import observe_day, day_events
from .test_intraday_statistics import _twin_bars, _twin_spec

KEY_H = "BTCUSD@1h"


def _upto(rows, day):
    return [b for b in rows if b["date"][:10] <= day]


def test_hourly_twin_records_the_daily_twin_s_day():
    daily, hourly, _ = _twin_bars()
    sd, sh = _twin_spec("1d"), _twin_spec("1h")
    entered = daily[150]["date"]
    checked = 0
    for k in range(151, 200):
        day = daily[k]["date"]
        rd = observe_day(sd, {"BTCUSD": _upto(daily, day)}, day, entered)[0]
        rh = observe_day(sh, {KEY_H: _upto(hourly, day)}, day, entered)[0]
        assert rh["asset"] == KEY_H and rd["asset"] == "BTCUSD"
        for f in ("date", "price", "position_frac", "equity"):
            assert rh[f] == rd[f], (day, f)
        assert set(rd) == {"strategy_id", "date", "asset", "action", "price",
                           "position_frac", "equity"}
        assert set(rh) == set(rd) | {"entries", "exits"}
        if rd["action"] != "hold":
            checked += 1
            assert rh["action"] == rd["action"], day
        assert rh["exits"] == (1 if rd["action"] == "exit" else 0), day
    assert checked > 5          # the fixture really trades in the window


def test_the_rebase_point_is_the_entry_day_s_last_bar():
    daily, hourly, _ = _twin_bars()
    sh = _twin_spec("1h")
    entered, day = daily[150]["date"], daily[151]["date"]
    r_day = observe_day(sh, {KEY_H: _upto(hourly, day)}, day, entered)[0]
    r_mark = observe_day(sh, {KEY_H: _upto(hourly, day)}, day,
                         entered + " 23:00:00")[0]
    assert r_day["equity"] == r_mark["equity"]


def test_day_events_counts_every_round_trip():
    bars = [{"date": f"2026-10-10 {h:02d}:00:00"} for h in range(24)]
    trades = [
        {"side": "long", "entry_date": "2026-10-10 01:00:00",
         "exit_date": "2026-10-10 03:00:00"},
        {"side": "short", "entry_date": "2026-10-10 05:00:00",
         "exit_date": "2026-10-10 09:00:00"},
        {"side": "long", "entry_date": "2026-10-09 22:00:00",
         "exit_date": "2026-10-10 00:00:00"},
    ]
    pos = {"side": 1, "entry_i": 12}
    assert day_events(trades, pos, bars, "2026-10-10") == (3, 3, "enter_long")
    assert day_events(trades[:2], None, bars, "2026-10-10") == (2, 2, "exit")
    assert day_events([], None, bars, "2026-10-10") == (0, 0, "hold")


def test_observe_day_passes_the_bar_size_mechanics_to_the_engine(monkeypatch):
    """2b-2 s3.4: the engine sizes and finances per BAR, so the recorder must
    hand it the bar-size arithmetic (8760 / 24 for 1h), exactly as the
    screen and gauntlet do; a daily spec keeps 365 and no bars_per_day."""
    from . import quarantine
    real = quarantine.simulate_asset
    calls = []

    def spy(blocks, bars, cost_model, periods_per_year, **kw):
        calls.append((periods_per_year, kw))
        return real(blocks, bars, cost_model, periods_per_year, **kw)

    monkeypatch.setattr(quarantine, "simulate_asset", spy)
    daily, hourly, _ = _twin_bars()
    entered, day = daily[150]["date"], daily[151]["date"]

    observe_day(_twin_spec("1h"), {KEY_H: _upto(hourly, day)}, day, entered)
    assert len(calls) == 1
    ppy, kw = calls[0]
    assert ppy == 8760
    assert kw.get("bars_per_day") == 24

    calls.clear()
    observe_day(_twin_spec("1d"), {"BTCUSD": _upto(daily, day)}, day, entered)
    assert len(calls) == 1
    ppy, kw = calls[0]
    assert ppy == 365
    assert kw.get("bars_per_day", 1) == 1
