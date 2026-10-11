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
