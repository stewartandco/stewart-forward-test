"""Offline tests for the Composer (no API calls).

Run: python -m pytest pipeline/test_composer.py -q
"""
from __future__ import annotations

import pytest

from .blocks import BLOCK_TYPES, validate_block, block_type_payload


# ---------------- block grammar ----------------

def test_grammar_has_twelve_types_with_required_roles():
    roles = {role for role, _ in BLOCK_TYPES}
    assert len(BLOCK_TYPES) == 12
    assert {"entry", "stop", "target", "exit", "risk", "filter", "regime"} <= roles


def test_valid_block_passes():
    assert validate_block("entry", "ma_cross", {"fast": 10, "slow": 100}) == []


def test_unknown_type_rejected():
    errs = validate_block("entry", "orb_breakout", {})
    assert errs and "unknown block type" in errs[0]


def test_off_grid_param_rejected():
    errs = validate_block("stop", "atr_stop", {"atr_len": 14, "mult": 2.5})
    assert any("not on grid" in e for e in errs)


def test_unknown_and_missing_params_rejected():
    errs = validate_block("target", "r_multiple", {"rr": 1.5})
    assert any("unknown param 'rr'" in e for e in errs)
    assert any("missing param 'r'" in e for e in errs)


def test_ma_cross_constraint_fast_below_slow():
    errs = validate_block("entry", "ma_cross", {"fast": 20, "slow": 50})
    assert errs == []
    errs = validate_block("entry", "ma_cross", {"fast": 20, "slow": 200})
    assert errs == []
    # grids fast {5,10,20} slow {50,100,200} never overlap — constraint still
    # guards future grid edits; test it directly:
    from .blocks import CONSTRAINTS
    assert CONSTRAINTS[("entry", "ma_cross")]({"fast": 60, "slow": 50})


def test_block_type_payload_shape():
    p = block_type_payload("risk", "vol_target")
    assert p["role"] == "risk" and p["type"] == "vol_target"
    assert set(p["params_schema"]) == {"ann_vol", "lookback"}
