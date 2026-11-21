"""bars.py: bar-size arithmetic and daily marks (2b-1 design s3.1)."""
from __future__ import annotations

import pytest

from . import bars


def test_bars_per_day_table_and_1d_everywhere():
    assert bars.BARS_PER_DAY_24X7 == {"15m": 96, "30m": 48, "1h": 24, "4h": 6,
                                      "12h": 2, "1d": 1}
    for tf, n in bars.BARS_PER_DAY_24X7.items():
        assert bars.bars_per_day("24x7", tf) == n
    for session in ("24x7", "fx_5d", "us_equity_5d", None, "weird"):
        assert bars.bars_per_day(session, "1d") == 1


def test_bars_per_year_matches_session_periods_for_1d():
    assert bars.bars_per_year("24x7", "1d") == 365
    assert bars.bars_per_year("fx_5d", "1d") == 261
    assert bars.bars_per_year("us_equity_5d", "1d") == 252
    assert bars.bars_per_year("24x7", "1h") == 8760
    assert bars.bars_per_year("24x7", "15m") == 35040


def test_bars_per_year_missing_session_falls_back_to_365():
    assert bars.bars_per_year(None, "1d") == 365
    assert bars.bars_per_year("not-a-session", "1d") == 365


@pytest.mark.parametrize("session", ["fx_5d", "us_equity_5d", None, "weird"])
def test_intraday_outside_24x7_is_refused(session):
    with pytest.raises(bars.IntradayUnsupported, match="24x7"):
        bars.bars_per_day(session, "1h")


def test_unknown_timeframe_is_refused():
    with pytest.raises(bars.IntradayUnsupported, match="2h"):
        bars.bars_per_day("24x7", "2h")


def test_daily_marks_keeps_last_per_utc_date_both_formats():
    s = [("2024-01-01 00:00:00", 1.0), ("2024-01-01 23:00:00", 2.0),
         ("2024-01-02 00:00:00", 3.0), ("2024-01-02 05:00:00", 4.0)]
    assert bars.daily_marks(s) == [("2024-01-01 23:00:00", 2.0),
                                   ("2024-01-02 05:00:00", 4.0)]
    d = [("2024-01-01", 1.0), ("2024-01-02", 2.0)]
    assert bars.daily_marks(d) == d


def test_daily_marks_identity_on_unique_daily_dates():
    d = [("2024-01-01 00:00:00", 1.0), ("2024-01-02 00:00:00", 1.1),
         ("2024-01-03 00:00:00", 0.9)]
    assert bars.daily_marks(d) == d


def test_daily_marks_collapses_duplicates():
    d = [("2024-01-01", 1.0), ("2024-01-01", 1.5), ("2024-01-02", 2.0)]
    assert bars.daily_marks(d) == [("2024-01-01", 1.5), ("2024-01-02", 2.0)]


def test_daily_marks_day_with_missing_hours():
    s = [("2024-01-01 22:00:00", 1.0),          # 23:00 missing
         ("2024-01-02 00:00:00", 1.1), ("2024-01-02 01:00:00", 1.2),
         ("2024-01-04 03:00:00", 1.3)]           # 2024-01-03 missing entirely
    assert bars.daily_marks(s) == [("2024-01-01 22:00:00", 1.0),
                                   ("2024-01-02 01:00:00", 1.2),
                                   ("2024-01-04 03:00:00", 1.3)]


def test_daily_marks_rejects_out_of_order():
    with pytest.raises(ValueError, match="out of order"):
        bars.daily_marks([("2024-01-02", 1.0), ("2024-01-01", 2.0)])


def test_daily_marks_empty():
    assert bars.daily_marks([]) == []


def test_daily_bars_last_bar_dict_per_date():
    rows = [{"date": "2024-01-01 22:00:00", "close": 1.0},
            {"date": "2024-01-01 23:00:00", "close": 2.0},
            {"date": "2024-01-02 00:00:00", "close": 3.0}]
    assert bars.daily_bars(rows) == [rows[1], rows[2]]
