"""Offline tests for the screening engine (no network, no API).

Run: python -m pytest pipeline/test_screen.py -q
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from .data_fetch import klines_to_rows, write_csv, SYMBOLS


# ---------------- data fetcher ----------------

# Two Binance kline records (list-of-lists API shape; only idx 0-5 used):
# [open_time_ms, open, high, low, close, volume, ...]
FAKE_KLINES = [
    [1502928000000, "4261.48", "4485.39", "4200.74", "4285.08", "795.15"],
    [1503014400000, "4285.08", "4371.52", "3938.77", "4108.37", "1199.88"],
]


def test_klines_to_rows_converts_and_dates():
    rows = klines_to_rows(FAKE_KLINES)
    assert rows[0] == {"date": "2017-08-17", "open": 4261.48, "high": 4485.39,
                       "low": 4200.74, "close": 4285.08, "volume": 795.15}
    assert rows[1]["date"] == "2017-08-18"


def test_write_csv_roundtrip(tmp_path):
    rows = klines_to_rows(FAKE_KLINES)
    out = tmp_path / "BTCUSD_1d.csv"
    write_csv(rows, out)
    back = list(csv.DictReader(out.open()))
    assert len(back) == 2
    assert back[0]["date"] == "2017-08-17"
    assert float(back[0]["close"]) == 4285.08


def test_symbol_mapping():
    assert SYMBOLS == {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}


def test_write_csv_bytes_are_lf_only(tmp_path):
    rows = klines_to_rows(FAKE_KLINES)
    out = tmp_path / "x.csv"
    write_csv(rows, out)
    raw = out.read_bytes()
    assert b"\r" not in raw


from .engine import (sma, stdev, atr_wilder, trend_tstat, realized_ann_vol,
                     percentile_rank)


# ---------------- indicators ----------------
# All indicator functions take aligned lists and return lists of the same
# length with None during warmup.

def test_sma_basic_and_warmup():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_stdev_is_sample_stdev():
    out = stdev([1, 2, 3], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(1.0)


def test_atr_wilder_flat_ranges():
    # every bar: high-low = 2, no gaps -> TR = 2 always; ATR converges to 2
    bars = [{"open": 10, "high": 11, "low": 9, "close": 10}] * 6
    out = atr_wilder(bars, 3)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(2.0)
    assert out[5] == pytest.approx(2.0)


def test_trend_tstat_perfect_line_is_infinite():
    assert trend_tstat([1.0, 2.0, 3.0, 4.0]) == float("inf")


def test_trend_tstat_flat_is_zero():
    assert trend_tstat([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_realized_ann_vol_flat_is_zero():
    out = realized_ann_vol([100.0] * 10, 5)
    assert out[-1] == pytest.approx(0.0)


def test_percentile_rank():
    # rank of current value among trailing window values (incl. current)
    assert percentile_rank([1, 2, 3], 2, 3) == pytest.approx(1.0)
    assert percentile_rank([3, 2, 1], 2, 3) == pytest.approx(1 / 3)
