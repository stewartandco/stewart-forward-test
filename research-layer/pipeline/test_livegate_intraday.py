"""2b-2 design s3.7: closed forward trades = sum of exits on intraday rows;
daily rows count exit actions as before."""
from __future__ import annotations

from .livegate import closed_forward_trades, forward_series


def test_intraday_rows_sum_their_exits():
    rows = [{"date": "2026-10-10", "asset": "BTCUSDT@1h", "action": "exit",
             "equity": 1.0, "entries": 3, "exits": 3},
            {"date": "2026-10-11", "asset": "BTCUSDT@1h", "action": "hold",
             "equity": 1.01, "entries": 0, "exits": 0},
            {"date": "2026-10-12", "asset": "BTCUSDT@1h", "action": "enter_long",
             "equity": 1.02, "entries": 2, "exits": 1}]
    assert closed_forward_trades(rows) == 4


def test_daily_rows_count_exit_actions_unchanged():
    rows = [{"date": "2026-10-10", "asset": "SPY", "action": "exit", "equity": 1.0},
            {"date": "2026-10-11", "asset": "SPY", "action": "hold", "equity": 1.0},
            {"date": "2026-10-12", "asset": "SPY", "action": "exit", "equity": 1.0}]
    assert closed_forward_trades(rows) == 2


def test_forward_series_treats_a_cell_key_as_its_asset():
    rows = [{"date": "2026-10-10", "asset": "BTCUSDT@1h", "equity": 1.0},
            {"date": "2026-10-11", "asset": "BTCUSDT@1h", "equity": 1.1}]
    assert forward_series(rows) == [("2026-10-10", 1.0), ("2026-10-11", 1.1)]


# 2b-3 C5: the train cutoff is a DATE. An intraday trade's entry_date carries a
# time ("2023-12-31 05:00:00"), and the old string comparison against the bare
# date "2023-12-31" put every cutoff-day trade AFTER the cutoff.

def _train_with_trades(monkeypatch, entry_dates, cutoff):
    from . import engine, screen
    from .livegate import train_contributions
    trades = [{"entry_date": d, "return_net": 0.1 * (i + 1), "notional_frac": 1.0}
              for i, d in enumerate(entry_dates)]
    monkeypatch.setattr(screen, "load_bars", lambda *a, **k: [])
    monkeypatch.setattr(engine, "run_spec", lambda spec, bars: {"trades": trades})
    spec = {"universe": {"assets": ["BTCUSDT"], "timeframe": "1h"}}
    return train_contributions(spec, "unused", cutoff)


def test_a_trade_entered_on_the_cutoff_day_intraday_counts(monkeypatch):
    got = _train_with_trades(
        monkeypatch, ["2023-12-31 05:00:00", "2024-01-01 00:00:00"], "2023-12-31")
    assert got == [0.1]


def test_daily_entry_dates_are_unchanged(monkeypatch):
    got = _train_with_trades(monkeypatch, ["2023-12-31", "2024-01-01"], "2023-12-31")
    assert got == [0.1]
