"""D15 exit rules v7: grammar, engine, composer, verifier pins.
Run: python -m pytest pipeline/test_exit_rules_v7.py -q
"""
from __future__ import annotations

import pytest

from .blocks import BLOCK_TYPES, RETIRED_TYPES, validate_block, retired_reason

NEW_STOPS = {("stop", "swing_stop"), ("stop", "ma_stop"), ("stop", "channel_stop"), ("stop", "band_stop")}
NEW_EXITS = {("exit", "ma_crossunder"), ("exit", "channel_exit"), ("exit", "zscore_revert"),
             ("exit", "tstat_decay"), ("exit", "regime_flip")}


def test_retired_types_stay_in_the_grammar_with_their_chained_schema():
    # chained schemas are immutable (composer.preflight_block_types): retiring must not edit them
    assert BLOCK_TYPES[("exit", "time_stop")] == {"max_bars": {"type": "int", "grid": [10, 20, 40]}}
    assert BLOCK_TYPES[("stop", "pct_stop")] == {"pct": {"type": "float", "grid": [0.05, 0.10, 0.15]}}
    assert set(RETIRED_TYPES) == {("exit", "time_stop"), ("stop", "pct_stop")}
    assert "calendar" in retired_reason("exit", "time_stop")
    assert retired_reason("entry", "ma_cross") is None


def test_validate_block_refuses_retired_types_for_v2_only():
    assert validate_block("exit", "time_stop", {"max_bars": 20}) == []                 # legacy default
    assert validate_block("exit", "time_stop", {"max_bars": 20}, version=1) == []
    errs = validate_block("exit", "time_stop", {"max_bars": 20}, version=2)
    assert errs and "retired" in errs[0]
    errs = validate_block("stop", "pct_stop", {"pct": 0.05}, version=2)
    assert errs and "retired" in errs[0]
    assert validate_block("stop", "atr_stop", {"atr_len": 14, "mult": 2.0}, version=2) == []


@pytest.mark.parametrize("key", sorted(NEW_STOPS | NEW_EXITS))
def test_new_types_exist_and_every_grid_has_three_contiguous_values(key):
    schema = BLOCK_TYPES[key]
    assert schema, key
    for p, s in schema.items():
        assert len(s["grid"]) >= 3, (key, p)
        assert s["grid"] == sorted(s["grid"]), (key, p)


def test_new_type_grids_are_exactly_the_spec():
    assert BLOCK_TYPES[("stop", "swing_stop")] == {"lookback": {"type": "int", "grid": [10, 20, 40]}}
    assert BLOCK_TYPES[("stop", "ma_stop")] == {"ma_len": {"type": "int", "grid": [20, 50, 100]}}
    assert BLOCK_TYPES[("stop", "channel_stop")] == {"lookback": {"type": "int", "grid": [20, 55, 100]}}
    assert BLOCK_TYPES[("stop", "band_stop")] == {"lookback": {"type": "int", "grid": [20, 40, 60]},
                                                  "mult": {"type": "float", "grid": [1.5, 2.0, 2.5, 3.0]}}
    assert BLOCK_TYPES[("exit", "ma_crossunder")] == {"fast": {"type": "int", "grid": [5, 8, 13, 20, 34]},
                                                      "slow": {"type": "int", "grid": [50, 80, 130, 200]}}
    assert BLOCK_TYPES[("exit", "channel_exit")] == {"lookback": {"type": "int", "grid": [10, 20, 40]}}
    assert BLOCK_TYPES[("exit", "zscore_revert")] == {"lookback": {"type": "int", "grid": [20, 40, 60, 90]},
                                                      "z_exit": {"type": "float", "grid": [0.0, 0.5, 1.0]}}
    assert BLOCK_TYPES[("exit", "tstat_decay")] == {"max_lookback": {"type": "int", "grid": [60, 90, 120]},
                                                    "t_exit": {"type": "float", "grid": [0.0, 0.5, 1.0]}}
    assert BLOCK_TYPES[("exit", "regime_flip")] == {"ma_len": {"type": "int", "grid": [50, 100, 150, 200, 250]}}


def test_ma_crossunder_constraint_fast_lt_slow():
    assert validate_block("exit", "ma_crossunder", {"fast": 34, "slow": 50}, version=2) == []
    errs = validate_block("exit", "ma_crossunder", {"fast": 34, "slow": 50}, version=2)  # valid
    assert errs == []
    # the constraint only bites when fast >= slow; no grid pair satisfies that today, so
    # assert the constraint function directly
    from .blocks import CONSTRAINTS
    assert CONSTRAINTS[("exit", "ma_crossunder")]({"fast": 50, "slow": 50}) == ["ma_crossunder: fast must be < slow"]
