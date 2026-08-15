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
