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


from .engine import entry_signals, gate_mask


def flat_bars(n, px=100.0):
    return [{"date": f"d{i}", "open": px, "high": px, "low": px,
             "close": px, "volume": 1.0} for i in range(n)]


def ramp_bars(n, start=100.0, step=1.0):
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"date": f"d{i}", "open": c, "high": c, "low": c,
                    "close": c, "volume": 1.0})
    return out


# ---------------- entry signals ----------------

def test_ma_cross_signal_and_state():
    # 6 flat bars then a jump: fast sma(2) crosses above slow sma(4)
    bars = flat_bars(6) + ramp_bars(4, start=110.0, step=5.0)
    sig, state = entry_signals({"role": "entry", "type": "ma_cross",
                                "params": {"fast": 2, "slow": 4}}, bars)
    # state is +1 while fast>slow; the cross bar emits +1 in sig
    assert 1 in sig
    first = sig.index(1)
    assert state[first] == 1 and state[first - 1] in (0, None)


def test_channel_breakout_long_only_by_default():
    bars = flat_bars(6) + [{"date": "b", "open": 100, "high": 111, "low": 100,
                            "close": 111, "volume": 1.0}]
    spec = {"role": "entry", "type": "channel_breakout",
            "params": {"lookback": 5, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert sig[-1] == 1


def test_zscore_reversion_long_at_negative_z():
    bars = flat_bars(10) + [{"date": "b", "open": 90, "high": 90, "low": 90,
                             "close": 90, "volume": 1.0}]
    spec = {"role": "entry", "type": "zscore_reversion",
            "params": {"lookback": 5, "z_entry": 1.5, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert sig[-1] == 1          # 90 is far below the flat-100 mean


def test_trend_scan_long_on_strong_trend():
    bars = ramp_bars(130)
    spec = {"role": "entry", "type": "trend_scan",
            "params": {"max_lookback": 60, "t_min": 3.0}}
    sig, _ = entry_signals(spec, bars)
    assert sig[-1] == 1


def test_signals_none_during_warmup():
    bars = flat_bars(3)
    spec = {"role": "entry", "type": "channel_breakout",
            "params": {"lookback": 5, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert all(s == 0 for s in sig)


# ---------------- gates ----------------

def test_regime_ma_gate_blocks_below_ma():
    down = [{"date": f"d{i}", "open": 100 - i, "high": 100 - i, "low": 100 - i,
             "close": 100 - i, "volume": 1.0} for i in range(120)]
    mask = gate_mask([{"role": "regime", "type": "regime_ma",
                       "params": {"ma_len": 100}}], down)
    assert mask[-1] is False     # falling market: close < sma(100)


def test_vol_percentile_gate_and_warmup():
    bars = flat_bars(500)
    mask = gate_mask([{"role": "filter", "type": "vol_percentile",
                       "params": {"lookback": 90, "max_pctile": 1.0}}], bars)
    assert mask[10] is False     # warmup (needs 365-bar percentile window)
    assert mask[-1] is True      # max_pctile 1.0 admits everything once warm
