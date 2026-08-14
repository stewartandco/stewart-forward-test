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


from .engine import simulate_asset


def breakout_spec_blocks(pct=0.05, r=1.0, max_bars=40, f=0.01):
    return [
        {"role": "entry", "type": "channel_breakout",
         "params": {"lookback": 5, "direction": "long"}},
        {"role": "stop", "type": "pct_stop", "params": {"pct": pct}},
        {"role": "target", "type": "r_multiple", "params": {"r": r}},
        {"role": "exit", "type": "time_stop", "params": {"max_bars": max_bars}},
        {"role": "risk", "type": "fixed_fraction", "params": {"f": f}},
    ]


COST = {"commission_per_side": 0.001, "slippage_ticks": 0.0005}


def target_hit_bars():
    bars = flat_bars(6)                                   # warmup, prior high 100
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})            # breakout close
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})            # entry at open 111
    bars.append({"date": "hit", "open": 112, "high": 120, "low": 112,
                 "close": 118, "volume": 1.0})            # target 116.55 hit
    return bars


# ---------------- simulator ----------------

def test_long_breakout_target_hit_math():
    book = simulate_asset(breakout_spec_blocks(), target_hit_bars(), COST)
    assert len(book["trades"]) == 1
    t = book["trades"][0]
    assert t["side"] == "long"
    assert t["entry_px"] == pytest.approx(111.0)
    # stop = 111*(1-0.05) = 105.45; distance 5.55; target = 116.55
    assert t["exit_px"] == pytest.approx(116.55)
    assert t["exit_reason"] == "target"
    gross = 116.55 / 111.0 - 1
    net = gross - 0.0015 * 2
    assert t["return_net"] == pytest.approx(net)
    # sizing: f=0.01, stop distance 5% -> notional = 0.2x equity
    assert book["equity"][-1] == pytest.approx(1 + 0.2 * net)


def test_same_bar_stop_and_target_stop_wins():
    bars = flat_bars(6)
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})
    bars.append({"date": "wide", "open": 111, "high": 120, "low": 100,
                 "close": 111, "volume": 1.0})            # touches both barriers
    book = simulate_asset(breakout_spec_blocks(), bars, COST)
    assert book["trades"][0]["exit_reason"] == "stop"
    assert book["trades"][0]["exit_px"] == pytest.approx(105.45)


def test_gap_through_stop_fills_at_open():
    bars = flat_bars(6)
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})
    bars.append({"date": "gap", "open": 90, "high": 95, "low": 88,
                 "close": 92, "volume": 1.0})             # opens far below stop
    book = simulate_asset(breakout_spec_blocks(), bars, COST)
    assert book["trades"][0]["exit_px"] == pytest.approx(90.0)
    assert book["trades"][0]["exit_reason"] == "stop"


def test_time_stop_exits_at_deadline_open():
    bars = flat_bars(6)
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})
    for i in range(4):                                    # drift, no barriers
        bars.append({"date": f"h{i}", "open": 112, "high": 113, "low": 111,
                     "close": 112, "volume": 1.0})
    book = simulate_asset(breakout_spec_blocks(max_bars=3), bars, COST)
    assert book["trades"][0]["exit_reason"] == "time"
    assert book["trades"][0]["exit_px"] == pytest.approx(112.0)


def test_one_position_at_a_time():
    # continuous new highs would re-signal every bar; only one open trade
    bars = flat_bars(6) + ramp_bars(10, start=101.0, step=2.0)
    blocks = breakout_spec_blocks(pct=0.05, r=3.0, max_bars=40)
    book = simulate_asset(blocks, bars, COST)
    assert len(book["trades"]) <= 1


def test_notional_cap_no_leverage():
    # tight stop 1% would imply 1.0/0.01 = 100x sizing at f=1.0 -> capped at 1x
    bars = target_hit_bars()
    blocks = breakout_spec_blocks(pct=0.01, r=1.0, f=1.0)
    book = simulate_asset(blocks, bars, COST)
    t = book["trades"][0]
    # stop 109.89, distance 1.11 -> target 112.11, hit on 'hit' bar
    gross = 112.11 / 111.0 - 1
    net = gross - 0.003
    assert book["equity"][-1] == pytest.approx(1 + 1.0 * net)  # notional 1x, not 100x


def test_signal_while_in_position_is_ignored_not_queued():
    # sig fires again at the fill bar's close (111 > rolling high 110);
    # it must not open a phantom same-bar position after the target exit
    book = simulate_asset(breakout_spec_blocks(), target_hit_bars(), COST)
    assert len(book["trades"]) == 1
    assert book["equity"][-1] == pytest.approx(
        1 + 0.2 * book["trades"][0]["return_net"])
