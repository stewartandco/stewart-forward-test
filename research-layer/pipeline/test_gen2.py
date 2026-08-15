"""Offline tests for Composer gen-2 + gauntlet protocol-v2 (no network/API).

Run: python -m pytest pipeline/test_gen2.py -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from .blocks import BLOCK_TYPES, CONSTRAINTS, validate_block


# ---------------- grammar additions ----------------

def test_grammar_has_fifteen_types():
    assert len(BLOCK_TYPES) == 15


def test_new_types_present_with_direction_grids():
    ts = BLOCK_TYPES[("entry", "trend_scan_ds")]
    assert ts["direction"]["grid"] == ["long", "short", "both"]
    assert ts["max_lookback"]["grid"] == [60, 90, 120]
    assert ts["t_min"]["grid"] == [2.0, 3.0]
    mc = BLOCK_TYPES[("entry", "ma_cross_ds")]
    assert mc["direction"]["grid"] == ["long", "short", "both"]
    assert mc["fast"]["grid"] == [5, 10, 20]
    assert mc["slow"]["grid"] == [50, 100, 200]
    assert BLOCK_TYPES[("regime", "regime_ma_short")]["ma_len"]["grid"] == [100, 200]


def test_existing_types_unchanged():
    # the conflict guard refuses changed params_schema, so these must be exact
    assert BLOCK_TYPES[("entry", "trend_scan")] == {
        "max_lookback": {"type": "int", "grid": [60, 90, 120]},
        "t_min": {"type": "float", "grid": [2.0, 3.0]},
    }
    assert "direction" not in BLOCK_TYPES[("entry", "ma_cross")]


def test_ma_cross_ds_validates_and_constrains():
    assert validate_block("entry", "ma_cross_ds",
                          {"fast": 10, "slow": 100, "direction": "both"}) == []
    assert CONSTRAINTS[("entry", "ma_cross_ds")]({"fast": 60, "slow": 50})


def test_new_blocks_reject_off_grid_direction():
    errs = validate_block("entry", "trend_scan_ds",
                          {"max_lookback": 60, "t_min": 2.0, "direction": "sideways"})
    assert any("not on grid" in e for e in errs)


from .engine import entry_signals, gate_mask, simulate_asset
from .test_screen import flat_bars, ramp_bars, COST


def falling_bars(n, start=200.0, step=-1.0):
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"date": f"d{i}", "open": c, "high": c, "low": c,
                    "close": c, "volume": 1.0})
    return out


# ---------------- new entry executors ----------------

def test_trend_scan_ds_short_fires_on_downtrend():
    bars = falling_bars(130)
    spec = {"role": "entry", "type": "trend_scan_ds",
            "params": {"max_lookback": 60, "t_min": 3.0, "direction": "short"}}
    sig, _ = entry_signals(spec, bars)
    assert sig[-1] == -1


def test_trend_scan_ds_long_only_suppresses_short():
    bars = falling_bars(130)
    spec = {"role": "entry", "type": "trend_scan_ds",
            "params": {"max_lookback": 60, "t_min": 3.0, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert all(s == 0 for s in sig)


def test_trend_scan_ds_both_fires_either_way():
    up = ramp_bars(130)
    down = falling_bars(130)
    spec = {"role": "entry", "type": "trend_scan_ds",
            "params": {"max_lookback": 60, "t_min": 3.0, "direction": "both"}}
    assert entry_signals(spec, up)[0][-1] == 1
    assert entry_signals(spec, down)[0][-1] == -1


def test_ma_cross_ds_state_is_signed_and_shorts_fire():
    # rise then fall: fast crosses above, later below
    bars = ramp_bars(60, start=100.0, step=2.0) + falling_bars(60, start=220.0, step=-4.0)
    spec = {"role": "entry", "type": "ma_cross_ds",
            "params": {"fast": 5, "slow": 50, "direction": "both"}}
    sig, state = entry_signals(spec, bars)
    assert 1 in sig and -1 in sig
    assert state[-1] == -1


def test_ma_cross_ds_long_only_ignores_downcross():
    bars = ramp_bars(60, start=100.0, step=2.0) + falling_bars(60, start=220.0, step=-4.0)
    spec = {"role": "entry", "type": "ma_cross_ds",
            "params": {"fast": 5, "slow": 50, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert 1 in sig and -1 not in sig


# ---------------- regime_ma_short gate ----------------

def test_regime_ma_short_mirrors_regime_ma():
    down = falling_bars(150, start=200.0, step=-0.5)
    short_mask = gate_mask([{"role": "regime", "type": "regime_ma_short",
                             "params": {"ma_len": 100}}], down)
    long_mask = gate_mask([{"role": "regime", "type": "regime_ma",
                            "params": {"ma_len": 100}}], down)
    assert short_mask[-1] is True     # below its MA in a downtrend
    assert long_mask[-1] is False
    assert short_mask[10] is False    # warmup blocks both


# ---------------- generalized signal exit ----------------

def test_ma_cross_signal_exit_unchanged_for_v1_type():
    # regression: long-only ma_cross must behave exactly as before.
    # The decline must be gentle enough that SMA(5) crosses below SMA(50)
    # BEFORE price reaches the 15% stop — otherwise the stop branch (checked
    # earlier in the exit chain) preempts and the signal path is unreachable.
    bars = ramp_bars(60, start=100.0, step=2.0) + falling_bars(30, start=220.0, step=-2.0)
    blocks = [
        {"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 50}},
        {"role": "stop", "type": "pct_stop", "params": {"pct": 0.15}},
        {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
    ]
    book = simulate_asset(blocks, bars, COST)
    assert book["trades"], "expected at least one trade"
    assert any(t["exit_reason"] == "signal" for t in book["trades"])


def test_ma_cross_ds_short_exits_on_state_flip():
    bars = falling_bars(60, start=220.0, step=-2.0) + ramp_bars(30, start=100.0, step=6.0)
    blocks = [
        {"role": "entry", "type": "ma_cross_ds",
         "params": {"fast": 5, "slow": 50, "direction": "short"}},
        {"role": "stop", "type": "pct_stop", "params": {"pct": 0.30}},
        {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
    ]
    book = simulate_asset(blocks, bars, COST)
    assert book["trades"], "expected a short trade"
    assert book["trades"][0]["side"] == "short"
