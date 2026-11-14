"""2b-2 design s3.1 / s3.3: the cell key and the day-complete rule."""
from __future__ import annotations

from pathlib import Path

import pytest

from .qcells import (cell_key, split_cell_key, is_intraday_key, spec_cells,
                     cell_path, last_slot, complete_days)


def test_daily_key_is_the_bare_asset_and_intraday_names_the_tf():
    assert cell_key("BTCUSD", "1d") == "BTCUSD"
    assert cell_key("BTCUSDT", "1h") == "BTCUSDT@1h"
    assert split_cell_key("BTCUSD") == ("BTCUSD", "1d")
    assert split_cell_key("BTCUSDT@1h") == ("BTCUSDT", "1h")
    assert not is_intraday_key("BTCUSD") and is_intraday_key("BTCUSDT@15m")


@pytest.mark.parametrize("bad", ["", "@1h", "BTC@", "BTC@1d", "A@1h@x", None])
def test_split_cell_key_refuses_a_malformed_key(bad):
    with pytest.raises(ValueError):
        split_cell_key(bad)


@pytest.mark.parametrize("bad_asset", ["", "BTC@X"])
def test_cell_key_refuses_a_bad_asset(bad_asset):
    with pytest.raises(ValueError):
        cell_key(bad_asset, "1h")


def test_spec_cells_follows_the_universe_order_and_defaults_to_1d():
    spec = {"universe": {"assets": ["ETHUSDT", "BTCUSDT"], "timeframe": "1h",
                         "session": "24x7"}}
    assert spec_cells(spec) == ["ETHUSDT@1h", "BTCUSDT@1h"]
    assert spec_cells({"universe": {"assets": ["SPY"]}}) == ["SPY"]


def test_cell_path_names_the_timeframe_file(tmp_path):
    assert cell_path(tmp_path, "BTCUSD") == Path(tmp_path) / "BTCUSD_1d.csv"
    assert cell_path(tmp_path, "BTCUSDT@1h") == Path(tmp_path) / "BTCUSDT_1h.csv"


@pytest.mark.parametrize("tf,slot", [("15m", "23:45:00"), ("30m", "23:30:00"),
                                     ("1h", "23:00:00"), ("4h", "20:00:00"),
                                     ("12h", "12:00:00")])
def test_last_slot_is_the_day_s_final_bar_open(tf, slot):
    assert last_slot("2026-10-10", tf) == f"2026-10-10 {slot}"


def _hours(day, hours):
    return [{"date": f"{day} {h:02d}:00:00"} for h in hours]


def test_a_day_is_complete_with_its_last_slot():
    bars = _hours("2026-10-10", range(24))
    assert complete_days(bars, "1h") == ["2026-10-10"]


def test_the_newest_day_without_its_last_slot_is_not_complete():
    bars = _hours("2026-10-09", range(24)) + _hours("2026-10-10", range(23))
    assert complete_days(bars, "1h") == ["2026-10-09"]


def test_a_day_with_a_missing_hour_completes_once_a_later_day_exists():
    bars = (_hours("2026-10-09", [h for h in range(24) if h != 23])
            + _hours("2026-10-10", [0]))
    assert complete_days(bars, "1h") == ["2026-10-09"]


def test_no_bars_no_days():
    assert complete_days([], "1h") == []
