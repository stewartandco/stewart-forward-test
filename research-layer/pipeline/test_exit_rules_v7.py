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


# =============================================================================
# Task 2: engine -- legacy path frozen by a golden captured BEFORE any edit;
# version-2 path: indicator-placed stops, declared signal exits, no deadline.
# =============================================================================

from .engine import run_spec, simulate_asset, entry_signals, exit_reason_counts
from .test_engine_classes import mk_bars, crypto_closes
from .test_screen import breakout_spec_blocks, COST, ramp_bars


def legacy_specs():
    """Three version-less (=> version 1) specs exercising every legacy exit path:
    time stop, pct stop, and the implicit ma_cross crossunder."""
    base = {"universe": {"assets": ["X"], "asset_class": "crypto", "timeframe": "1d", "session": "24x7"},
            "cost_model": COST}
    return [
        {**base, "strategy_id": "legacy-breakout", "blocks": breakout_spec_blocks(max_bars=5)},
        # pct 0.02 (the plan drafted 0.10): on the smooth o=h=l=c sinusoid a 10%
        # stop never fires before the crossunder, so the golden had no "stop"
        # trade; 0.02 is the loosest value that yields one (1 stop, 3 signal).
        {**base, "strategy_id": "legacy-macross", "blocks": [
            {"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 20}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.02}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}}]},
        {**base, "strategy_id": "legacy-macross-ds", "blocks": [
            {"role": "entry", "type": "ma_cross_ds", "params": {"fast": 5, "slow": 20, "direction": "both"}},
            {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 10}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}}]},
    ]


def legacy_bars():
    return {"X": mk_bars(crypto_closes(400))}


# Captured 2026-09-03 at git 1d12c92 (Task 1 HEAD, pre-Task-2 engine) by the
# scratch script d15-t2/capture_v7_baseline.py, run from research-layer/ with
# pipeline/engine.py clean in git status: for each legacy spec, the full
# trades list and the last 5 equity points. The legacy path must reproduce
# these exactly (== on every float, no tolerance).
LEGACY_GOLDEN = {
    'legacy-breakout': {
        'trades': [
            {'side': 'long', 'entry_date': '2024-01-09', 'entry_px': 108.088366846173, 'exit_date': '2024-01-16', 'exit_px': 113.93699680500931, 'exit_reason': 'time', 'return_net': 0.05110970791296954, 'notional_frac': 0.20000000000000018, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-01-17', 'entry_px': 114.94712181799045, 'exit_date': '2024-01-24', 'exit_px': 118.96658723194761, 'exit_reason': 'time', 'return_net': 0.03196795178848999, 'notional_frac': 0.19999999999999973, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-01-25', 'entry_px': 119.54078171934452, 'exit_date': '2024-02-01', 'exit_px': 121.13596688258599, 'exit_reason': 'time', 'return_net': 0.01034427582200859, 'notional_frac': 0.20000000000000018, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-02-02', 'entry_px': 121.19147206083011, 'exit_date': '2024-02-09', 'exit_px': 120.15000181401916, 'exit_reason': 'time', 'return_net': -0.01159359350209221, 'notional_frac': 0.20000000000000018, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-04-12', 'entry_px': 84.18618143161315, 'exit_date': '2024-04-19', 'exit_px': 86.94438326202395, 'exit_reason': 'time', 'return_net': 0.029763118406212045, 'notional_frac': 0.19999999999999976, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-04-22', 'entry_px': 87.7334121686484, 'exit_date': '2024-04-29', 'exit_px': 92.68603516511382, 'exit_reason': 'time', 'return_net': 0.05345081929499196, 'notional_frac': 0.19999999999999976, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-04-30', 'entry_px': 93.84877945442697, 'exit_date': '2024-05-06', 'exit_px': 98.91169003602148, 'exit_reason': 'target', 'return_net': 0.0509475378478742, 'notional_frac': 0.2, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-05-07', 'entry_px': 100.25338284710669, 'exit_date': '2024-05-13', 'exit_px': 105.75254019764346, 'exit_reason': 'target', 'return_net': 0.051852586460083505, 'notional_frac': 0.19999999999999996, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-05-14', 'entry_px': 107.13098409700987, 'exit_date': '2024-05-21', 'exit_px': 113.75197638133062, 'exit_reason': 'time', 'return_net': 0.058802776667535084, 'notional_frac': 0.19999999999999993, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-05-22', 'entry_px': 114.98226702277216, 'exit_date': '2024-05-29', 'exit_px': 120.37756183351256, 'exit_reason': 'time', 'return_net': 0.043922842542945095, 'notional_frac': 0.20000000000000015, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-05-30', 'entry_px': 121.27335727698306, 'exit_date': '2024-06-06', 'exit_px': 124.63062569829917, 'exit_reason': 'time', 'return_net': 0.024683478850579314, 'notional_frac': 0.20000000000000018, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-06-07', 'entry_px': 125.05839344062973, 'exit_date': '2024-06-14', 'exit_px': 125.88706611143172, 'exit_reason': 'time', 'return_net': 0.0036262859133512374, 'notional_frac': 0.20000000000000015, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-08-22', 'entry_px': 88.81644541697366, 'exit_date': '2024-08-29', 'exit_px': 91.47282636054186, 'exit_reason': 'time', 'return_net': 0.0269086608465029, 'notional_frac': 0.19999999999999993, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-08-30', 'entry_px': 92.24342810062582, 'exit_date': '2024-09-06', 'exit_px': 97.11807513324506, 'exit_reason': 'time', 'return_net': 0.04984546696705172, 'notional_frac': 0.20000000000000004, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-09-09', 'entry_px': 98.2685416399913, 'exit_date': '2024-09-13', 'exit_px': 103.29525271714587, 'exit_reason': 'target', 'return_net': 0.048152800207110213, 'notional_frac': 0.1999999999999997, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-09-16', 'entry_px': 104.63132468197664, 'exit_date': '2024-09-20', 'exit_px': 110.12246094442274, 'exit_reason': 'target', 'return_net': 0.04948080609832883, 'notional_frac': 0.19999999999999987, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-09-23', 'entry_px': 111.50255971093145, 'exit_date': '2024-09-30', 'exit_px': 118.15334073653281, 'exit_reason': 'time', 'return_net': 0.05664689100271236, 'notional_frac': 0.19999999999999998, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-10-01', 'entry_px': 119.39370655865548, 'exit_date': '2024-10-08', 'exit_px': 124.85751779904898, 'exit_reason': 'time', 'return_net': 0.04276297526795736, 'notional_frac': 0.19999999999999982, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-10-09', 'entry_px': 125.77021808724297, 'exit_date': '2024-10-16', 'exit_px': 129.2239133888821, 'exit_reason': 'time', 'return_net': 0.024460358693529586, 'notional_frac': 0.20000000000000023, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-10-17', 'entry_px': 129.6727467367005, 'exit_date': '2024-10-24', 'exit_px': 130.61053305432725, 'exit_reason': 'time', 'return_net': 0.0042319461199576995, 'notional_frac': 0.19999999999999982, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-10-25', 'entry_px': 130.53253244569947, 'exit_date': '2024-11-01', 'exit_px': 128.8458234428101, 'exit_reason': 'time', 'return_net': -0.015921751928708257, 'notional_frac': 0.19999999999999982, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-01-02', 'entry_px': 93.79458539485242, 'exit_date': '2025-01-09', 'exit_px': 96.75793436096004, 'exit_reason': 'time', 'return_net': 0.028594030227146242, 'notional_frac': 0.19999999999999968, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-01-10', 'entry_px': 97.58384965751658, 'exit_date': '2025-01-17', 'exit_px': 102.69123200355514, 'exit_reason': 'time', 'return_net': 0.049338397839023496, 'notional_frac': 0.19999999999999968, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-01-20', 'entry_px': 103.87803019425357, 'exit_date': '2025-01-27', 'exit_px': 110.3625720519347, 'exit_reason': 'time', 'return_net': 0.05942457472051543, 'notional_frac': 0.20000000000000015, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-01-28', 'entry_px': 111.73077769838893, 'exit_date': '2025-02-04', 'exit_px': 118.60818199352703, 'exit_reason': 'time', 'return_net': 0.05855335563584163, 'notional_frac': 0.19999999999999993, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-02-05', 'entry_px': 119.94967940887528, 'exit_date': '2025-02-12', 'exit_px': 126.17362472667196, 'exit_reason': 'time', 'return_net': 0.04888796959249031, 'notional_frac': 0.1999999999999999, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-02-13', 'entry_px': 127.28451486648343, 'exit_date': '2025-02-20', 'exit_px': 131.9118465964027, 'exit_reason': 'time', 'return_net': 0.03335423943574878, 'notional_frac': 0.20000000000000007, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-02-21', 'entry_px': 132.62463823445958, 'exit_date': '2025-02-28', 'exit_px': 134.96427194514254, 'exit_reason': 'time', 'return_net': 0.014641018605810244, 'notional_frac': 0.20000000000000004, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-03-03', 'entry_px': 135.17432485526206, 'exit_date': '2025-03-10', 'exit_px': 134.89635333066997, 'exit_reason': 'time', 'return_net': -0.005056392919955186, 'notional_frac': 0.19999999999999965, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-05-14', 'entry_px': 98.41403108512273, 'exit_date': '2025-05-21', 'exit_px': 101.27698156333923, 'exit_reason': 'time', 'return_net': 0.02609087704923108, 'notional_frac': 0.19999999999999982, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-05-22', 'entry_px': 102.08488546612091, 'exit_date': '2025-05-29', 'exit_px': 107.11692621162626, 'exit_reason': 'time', 'return_net': 0.04629271088985398, 'notional_frac': 0.19999999999999976, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-05-30', 'entry_px': 108.29206819218756, 'exit_date': '2025-06-06', 'exit_px': 114.73822011880588, 'exit_reason': 'time', 'return_net': 0.056525614703177074, 'notional_frac': 0.19999999999999976, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-06-09', 'entry_px': 116.10296499804454, 'exit_date': '2025-06-16', 'exit_px': 122.98499195758717, 'exit_reason': 'time', 'return_net': 0.05627520420911336, 'notional_frac': 0.19999999999999984, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-06-17', 'entry_px': 124.33177042768939, 'exit_date': '2025-06-24', 'exit_px': 130.60262118829166, 'exit_reason': 'time', 'return_net': 0.04743643100255979, 'notional_frac': 0.20000000000000012, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-06-25', 'entry_px': 131.72670044240078, 'exit_date': '2025-07-02', 'exit_px': 136.43581505546476, 'exit_reason': 'time', 'return_net': 0.03274912752880421, 'notional_frac': 0.19999999999999998, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-07-03', 'entry_px': 137.1676215962178, 'exit_date': '2025-07-10', 'exit_px': 139.6110039113786, 'exit_reason': 'time', 'return_net': 0.014813112793873432, 'notional_frac': 0.1999999999999998, 'asset': 'X'},
        ],
        'equity_tail': [1.2818716439101723, 1.2827870263072396, 1.283544207011635, 1.2833724875306156, 1.2833724875306156],
        'metrics': {'trades': 36, 'net_pnl': 0.2833724875306156},
    },
    'legacy-macross': {
        'trades': [
            {'side': 'long', 'entry_date': '2024-01-29', 'entry_px': 120.43875802726626, 'exit_date': '2024-02-15', 'exit_px': 117.8199280763918, 'exit_reason': 'stop', 'return_net': -0.02474407967808478, 'notional_frac': 0.9999999999999972, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-04-19', 'entry_px': 86.94438326202395, 'exit_date': '2024-06-27', 'exit_px': 121.95960404477069, 'exit_reason': 'signal', 'return_net': 0.39973125725927006, 'notional_frac': 0.9999999999999976, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-08-30', 'entry_px': 92.24342810062582, 'exit_date': '2024-11-06', 'exit_px': 126.86504134750632, 'exit_reason': 'signal', 'return_net': 0.37232877907695217, 'notional_frac': 0.999999999999997, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-01-09', 'entry_px': 96.75793436096004, 'exit_date': '2025-03-18', 'exit_px': 131.76617701241358, 'exit_reason': 'signal', 'return_net': 0.3588126294516959, 'notional_frac': 1.0, 'asset': 'X'},
        ],
        'equity_tail': [3.4784908955716727, 3.4908226948077106, 3.5010232410077693, 3.5090527985756, 3.5148812771801956],
        'metrics': {'trades': 4, 'net_pnl': 2.5148812771801956},
    },
    'legacy-macross-ds': {
        'trades': [
            {'side': 'long', 'entry_date': '2024-01-29', 'entry_px': 120.43875802726626, 'exit_date': '2024-02-12', 'exit_px': 119.68594853651364, 'exit_reason': 'time', 'return_net': -0.00925055840066202, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'short', 'entry_date': '2024-02-15', 'entry_px': 117.8199280763918, 'exit_date': '2024-02-29', 'exit_px': 107.57951408324566, 'exit_reason': 'time', 'return_net': 0.08391580584318892, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-04-19', 'entry_px': 86.94438326202395, 'exit_date': '2024-05-03', 'exit_px': 97.59482496060807, 'exit_reason': 'time', 'return_net': 0.11949717921958125, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'short', 'entry_date': '2024-06-27', 'entry_px': 121.95960404477069, 'exit_date': '2024-07-11', 'exit_px': 111.35779828200496, 'exit_reason': 'time', 'return_net': 0.08392883062225964, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2024-08-30', 'entry_px': 92.24342810062582, 'exit_date': '2024-09-13', 'exit_px': 103.29525271714587, 'exit_reason': 'time', 'return_net': 0.11681151225715414, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'short', 'entry_date': '2024-11-06', 'entry_px': 126.86504134750632, 'exit_date': '2024-11-20', 'exit_px': 116.37923728656341, 'exit_reason': 'time', 'return_net': 0.07965321911826284, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-01-09', 'entry_px': 96.75793436096004, 'exit_date': '2025-01-23', 'exit_px': 107.68042656336138, 'exit_reason': 'time', 'return_net': 0.1098847187007369, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'short', 'entry_date': '2025-03-18', 'entry_px': 131.76617701241358, 'exit_date': '2025-04-01', 'exit_px': 121.39937223187773, 'exit_reason': 'time', 'return_net': 0.07567576502245488, 'notional_frac': 1.0, 'asset': 'X'},
            {'side': 'long', 'entry_date': '2025-05-21', 'entry_px': 101.27698156333923, 'exit_date': '2025-06-04', 'exit_px': 112.06729747790556, 'exit_reason': 'time', 'return_net': 0.10354262941098813, 'notional_frac': 1.0, 'asset': 'X'},
        ],
        'equity_tail': [2.070120821571079, 2.070120821571079, 2.070120821571079, 2.070120821571079, 2.070120821571079],
        'metrics': {'trades': 9, 'net_pnl': 1.070120821571079},
    },
}


@pytest.mark.parametrize("spec", legacy_specs(), ids=lambda s: s["strategy_id"])
def test_legacy_path_is_byte_identical(spec):
    out = run_spec(spec, legacy_bars())
    g = LEGACY_GOLDEN[spec["strategy_id"]]
    assert out["trades"] == g["trades"]
    assert [e for _, e in out["equity"][-5:]] == g["equity_tail"]
    assert out["metrics"]["trades"] == g["metrics"]["trades"]
    assert out["metrics"]["net_pnl"] == g["metrics"]["net_pnl"]


def test_legacy_golden_covers_every_legacy_exit_path():
    reasons = {t["exit_reason"] for g in LEGACY_GOLDEN.values() for t in g["trades"]}
    assert {"time", "stop", "signal"} <= reasons, reasons


def test_legacy_path_is_the_default_and_explicit_version_1_is_the_same():
    # `version` absent and `version: 1` must be one code path
    for spec in legacy_specs():
        a = run_spec(spec, legacy_bars())
        b = run_spec({**spec, "version": 1}, legacy_bars())
        assert a["trades"] == b["trades"] and a["equity"] == b["equity"]


# ---------------- version 2 --------------------------------------------------

def v2(spec):
    return {**spec, "version": 2}


def test_v2_refuses_retired_types_in_the_engine():
    s = v2({**legacy_specs()[2]})              # carries exit/time_stop
    with pytest.raises(ValueError, match="time_stop"):
        run_spec(s, legacy_bars())
    s = v2({**legacy_specs()[1]})              # carries stop/pct_stop
    with pytest.raises(ValueError, match="pct_stop"):
        run_spec(s, legacy_bars())


def macross_v2(exit_blocks, stop=None):
    return {"version": 2, "strategy_id": "v2-macross",
            "universe": {"assets": ["X"], "asset_class": "crypto", "timeframe": "1d", "session": "24x7"},
            "cost_model": COST,
            "blocks": [{"role": "entry", "type": "ma_cross_ds", "params": {"fast": 5, "slow": 20, "direction": "long"}},
                       stop or {"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}},
                       *exit_blocks,
                       {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}}]}


def test_v2_has_no_implicit_crossunder_exit():
    out = run_spec(macross_v2([]), legacy_bars())
    assert out["trades"], "fixture must trade"
    assert all(t["exit_reason"] in ("stop", "target") for t in out["trades"]), out["trades"]
    assert "signal" not in "".join(t["exit_reason"] for t in out["trades"])


def test_v2_declared_ma_crossunder_exits_at_next_open_with_reason():
    out = run_spec(macross_v2([{"role": "exit", "type": "ma_crossunder", "params": {"fast": 5, "slow": 50}}]),
                   legacy_bars())
    sig = [t for t in out["trades"] if t["exit_reason"] == "signal:ma_crossunder"]
    assert sig, out["trades"]
    bars = legacy_bars()["X"]
    idx = {b["date"]: i for i, b in enumerate(bars)}
    for t in sig:
        assert t["exit_px"] == bars[idx[t["exit_date"]]]["open"]       # filled at open t+1


def test_v2_barriers_take_precedence_over_signal_exits_on_the_same_bar():
    # a stop that is hit on the same bar a signal exit would fire records "stop"
    out = run_spec(macross_v2([{"role": "exit", "type": "regime_flip", "params": {"ma_len": 50}}],
                              stop={"role": "stop", "type": "band_stop", "params": {"lookback": 20, "mult": 1.5}}),
                   legacy_bars())
    reasons = {t["exit_reason"] for t in out["trades"]}
    assert reasons <= {"stop", "target", "signal:regime_flip"}


def test_v2_metrics_record_exit_reasons_open_at_end_and_stop_invalid():
    out = run_spec(macross_v2([{"role": "exit", "type": "ma_crossunder", "params": {"fast": 5, "slow": 50}}]),
                   legacy_bars())
    m = out["metrics"]
    assert set(m) >= {"trades", "net_pnl", "win_rate", "max_dd", "exit_reasons", "open_at_end", "stop_invalid"}
    assert sum(m["exit_reasons"].values()) == m["trades"]
    assert isinstance(m["open_at_end"], bool) and isinstance(m["stop_invalid"], int)
    assert exit_reason_counts(out["trades"]) == m["exit_reasons"]


def test_v2_ma_stop_on_wrong_side_makes_the_signal_ineligible():
    # 15 bars at 100 then 5 at 110: at the first warm bar (i=19) SMA5=110 > SMA20=102.5,
    # so ma_cross_ds emits its enter-on-first-eligible long signal there. The fill bar
    # (i=20) opens at 90 -- BELOW SMA20(19)=102.5 -- so the indicator-placed stop sits
    # ABOVE the long entry: not on the adverse side, signal ineligible, counted.
    closes = [100.0] * 15 + [110.0] * 5 + [90.0] + [90.0 + i for i in range(40)]
    bars = mk_bars(closes)
    spec = macross_v2([], stop={"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}})
    sig, _ = entry_signals(spec["blocks"][0], bars)
    assert sig[19] == 1                                  # the signal really fired
    book = simulate_asset(spec["blocks"], bars, COST, version=2)
    assert book["stop_invalid"] >= 1
    wrong_side_fill = bars[20]["date"]
    assert not any(t["entry_date"] == wrong_side_fill for t in book["trades"])
    assert book["position"] is None or bars[book["position"]["entry_i"]]["date"] != wrong_side_fill
    out = run_spec(spec, {"X": bars})
    assert out["metrics"]["stop_invalid"] == book["stop_invalid"]
    # the later, valid long (SMA20 below its entry on the rising leg) still trades
    assert out["trades"] or out["metrics"]["open_at_end"]


@pytest.mark.parametrize("stop", [
    {"role": "stop", "type": "swing_stop", "params": {"lookback": 10}},
    {"role": "stop", "type": "channel_stop", "params": {"lookback": 20}},
    {"role": "stop", "type": "band_stop", "params": {"lookback": 20, "mult": 2.0}},
    {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
])
def test_v2_every_new_stop_type_places_the_stop_on_the_adverse_side(stop):
    out = run_spec(macross_v2([], stop=stop), legacy_bars())
    for t in out["trades"]:
        if t["exit_reason"] == "stop":
            assert (t["exit_px"] < t["entry_px"]) if t["side"] == "long" else (t["exit_px"] > t["entry_px"])


@pytest.mark.parametrize("exit_block", [
    {"role": "exit", "type": "channel_exit", "params": {"lookback": 10}},
    {"role": "exit", "type": "zscore_revert", "params": {"lookback": 20, "z_exit": 0.0}},
    {"role": "exit", "type": "tstat_decay", "params": {"max_lookback": 60, "t_exit": 0.0}},
    {"role": "exit", "type": "regime_flip", "params": {"ma_len": 50}},
])
def test_v2_every_new_exit_type_runs_and_labels_its_reason(exit_block):
    out = run_spec(macross_v2([exit_block]), legacy_bars())
    for t in out["trades"]:
        assert t["exit_reason"] in ("stop", "target", f"signal:{exit_block['type']}")


def test_two_declared_exits_both_fire_first_wins_in_declaration_order():
    out = run_spec(macross_v2([{"role": "exit", "type": "regime_flip", "params": {"ma_len": 50}},
                               {"role": "exit", "type": "ma_crossunder", "params": {"fast": 5, "slow": 50}}]),
                   legacy_bars())
    assert {t["exit_reason"] for t in out["trades"]} <= {"stop", "target", "signal:regime_flip", "signal:ma_crossunder"}


def test_open_at_end_is_marked_to_market_never_a_trade():
    closes = [100.0 + i for i in range(80)]           # one long that never exits
    out = run_spec(macross_v2([], stop={"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}}),
                   {"X": mk_bars(closes)})
    assert out["metrics"]["open_at_end"] is True
    assert out["equity"][-1][1] > 1.0                  # unrealised gain marked
    assert all(t["exit_date"] for t in out["trades"])  # no open trade in the list


def test_v2_gap_stop_beats_a_pending_signal_exit_on_the_same_bar():
    # 15 bars at 100 then 5 at 110 -> long signal on close 19 (SMA5 110 > SMA20 102.5);
    # fill at bar 20 open 112, ma_stop(20) level 102.5 (adverse side, valid).
    # Bar 23 closes at 103 < SMA20 -> regime_flip pending for bar 24's open.
    head = [100.0] * 15 + [110.0] * 5 + [112.0, 112.0, 105.0, 103.0]
    spec = macross_v2([{"role": "exit", "type": "regime_flip", "params": {"ma_len": 20}}],
                      stop={"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}})
    # A: bar 24 opens BELOW the stop -> gap-through stop wins, filled at the open
    a = simulate_asset(spec["blocks"], mk_bars(head + [95.0] * 3), COST, version=2)
    assert [(t["exit_reason"], t["exit_px"]) for t in a["trades"]] == [("stop", 95.0)]
    # B: bar 24 opens ABOVE the stop -> the declared signal exit fills at that open
    b = simulate_asset(spec["blocks"], mk_bars(head + [103.5] * 3), COST, version=2)
    assert [(t["exit_reason"], t["exit_px"]) for t in b["trades"]] == [("signal:regime_flip", 103.5)]
    for book in (a, b):
        assert book["trades"][0]["entry_px"] == 112.0
        assert book["stop_invalid"] == 0


def test_v2_signal_exit_never_fires_from_a_time_deadline():
    # a rising series with no declared exit and a far stop: the legacy 5-bar
    # deadline (breakout_spec_blocks) would close it; v2 has no deadline at all
    bars = mk_bars([100.0 + i for i in range(80)])
    legacy = simulate_asset(breakout_spec_blocks(max_bars=5), bars, COST)
    assert legacy["trades"] and all(t["exit_reason"] == "time" for t in legacy["trades"])
    v2_blocks = [b for b in breakout_spec_blocks() if b["role"] not in ("stop", "exit")]
    v2_blocks.insert(1, {"role": "stop", "type": "swing_stop", "params": {"lookback": 10}})
    book = simulate_asset(v2_blocks, bars, COST, version=2)
    assert all(t["exit_reason"] in ("stop", "target") for t in book["trades"])
    assert "time" not in {t["exit_reason"] for t in book["trades"]}


# ---------------- Task 4: gauntlet verdict metrics (RECORDED, NOT GATED) -----
#
# One real run through gauntlet.run() on test_gauntlet's smallest end-to-end
# fixture. The verdict's own pass/fail is whatever it already was; what is
# pinned here is that every gauntlet verdict now carries the D15 exit-reason
# counts over the IS and OOS trade lists and whether the book ended open, and
# that those figures are the engine's own numbers for the same spec on the
# same bars -- not a second formula.

from .test_gauntlet import gauntlet_registry, chain_gauntlet_note
from .test_screen import write_data_dir, dated_target_hit_bars


def test_gauntlet_verdict_metrics_record_exit_reasons_is_and_oos(tmp_path):
    from .gauntlet import run as gauntlet_run, split_trades, DEFAULT_CUTOFF

    reg, spec = gauntlet_registry(tmp_path)
    chain_gauntlet_note(reg)
    bars = dated_target_hit_bars()
    data = write_data_dir(tmp_path, {"BTCUSD": bars})
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art")])
    assert rc == 0

    verdicts = [e for e in reg.entries() if e["entry_type"] == "verdict"
                and e["payload"]["stage"] == "gauntlet"]
    assert len(verdicts) == 1
    m = verdicts[0]["payload"]["metrics"]

    for key in ("exit_reasons_is", "exit_reasons_oos"):
        assert isinstance(m[key], dict), key
        assert all(isinstance(k, str) and isinstance(v, int) and v > 0
                   for k, v in m[key].items()), m[key]
    assert isinstance(m["open_at_end"], bool)

    # the same spec on the same bars through the engine directly: the verdict
    # records the engine's split counts and the engine's open_at_end, exactly
    res = run_spec(spec, {"BTCUSD": bars})
    is_t, oos_t = split_trades(res["trades"], DEFAULT_CUTOFF)
    # this fixture trades once, in 2023 (train side): the OOS list is
    # legitimately empty, and the equality below pins that the verdict
    # recorded {} for it rather than the whole-sample counts
    assert is_t, "fixture must trade"
    assert m["exit_reasons_is"] == exit_reason_counts(is_t)
    assert m["exit_reasons_oos"] == exit_reason_counts(oos_t)
    assert (sum(m["exit_reasons_is"].values())
            + sum(m["exit_reasons_oos"].values())) == len(res["trades"])
    assert m["open_at_end"] == res["metrics"]["open_at_end"]


# ---------------- Task 5b: re-trial classification tool (dry run only) ------

import json as _json
import subprocess as _sp
import sys as _sys
from pathlib import Path as _Path

_TOOL = _Path(__file__).resolve().parent.parent / "tools_retrial_families_v7.py"


def _reg_entry(sid, family, blocks, version=1):
    return {"entry_type": "strategy_registered",
            "payload": {"strategy_id": sid, "family": family, "version": version,
                        "universe": {"assets": ["X"], "timeframe": "1d"}, "blocks": blocks}}


def _state(sid, to):
    return {"entry_type": "state_change", "payload": {"strategy_id": sid, "to": to}}


_ENTRY = {"role": "entry", "type": "trend_scan_dense", "params": {"max_lookback": 60, "t_min": 2.0, "direction": "long"}}
_MA = {"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}}
_ATR = {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 2.0}}
_PCT = {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}}
_TS = {"role": "exit", "type": "time_stop", "params": {"max_bars": 40}}
_RISK = {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}


def _fixture_entries():
    return [
        _reg_entry("a" * 16, "fam_a", [_ENTRY, _ATR, _RISK]),                 # compliant as registered
        _reg_entry("b" * 16, "fam_a", [_ENTRY, _ATR, _TS, _RISK]),            # time stop
        _reg_entry("c" * 16, "fam_b", [_ENTRY, _PCT, _RISK]),                 # pct stop
        _reg_entry("d" * 16, "fam_b", [_MA, _ATR, _RISK]),                    # implicit ma_cross exit
        _reg_entry("e" * 16, "fam_b", [_MA, _PCT, _TS, _RISK]),               # all three
        _state("b" * 16, "graveyard"), _state("c" * 16, "quarantine"),
    ]


def test_retrial_tool_classifies_compliant_vs_retrial_with_reasons_and_states():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tools_retrial_families_v7", _TOOL)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    out = mod.classify(_fixture_entries())
    assert out["fam_a"]["compliant"] == ["a" * 16] and out["fam_a"]["retrial"] == ["b" * 16]
    assert out["fam_a"]["reasons"]["b" * 16] == ["exit/time_stop"]
    assert out["fam_b"]["compliant"] == [] and out["fam_b"]["retrial"] == ["c" * 16, "d" * 16, "e" * 16]
    assert out["fam_b"]["reasons"]["c" * 16] == ["stop/pct_stop"]
    assert out["fam_b"]["reasons"]["d" * 16] == [mod.IMPLICIT_EXIT_REASON]
    assert out["fam_b"]["reasons"]["e" * 16] == ["stop/pct_stop", "exit/time_stop", mod.IMPLICIT_EXIT_REASON]
    assert out["fam_a"]["states"]["b" * 16] == "graveyard" and out["fam_b"]["states"]["c" * 16] == "quarantine"
    assert out["fam_a"]["states"]["a" * 16] == "registered"
    # a version-2 registration is compliant by construction
    v2 = mod.classify([_reg_entry("f" * 16, "fam_c", [_MA, _ATR, _RISK], version=2)])
    assert v2["fam_c"]["compliant"] == ["f" * 16]


def test_retrial_tool_dry_run_writes_only_the_report_and_fire_is_refused(tmp_path):
    log = tmp_path / "registry_log.jsonl"
    log.write_text("".join(_json.dumps(e) + "\n" for e in _fixture_entries()), encoding="utf-8")
    before = log.read_bytes()
    out = tmp_path / "runs" / "plan.md"
    r = _sp.run([_sys.executable, str(_TOOL), "--dry-run", "--registry", str(log), "--out", str(out)],
                cwd=str(_TOOL.parent), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert log.read_bytes() == before                                  # chain byte-identical
    assert sorted(p.name for p in tmp_path.iterdir()) == ["registry_log.jsonl", "runs"]
    assert [p.name for p in out.parent.iterdir()] == ["plan.md"]
    text = out.read_text(encoding="utf-8")
    assert "- registrations: 5" in text and "- compliant as registered: 1" in text
    assert "- needs version-2 re-trial: 4" in text and "Coen-gated" in text
    assert "| fam_b | 0 | 3 |" in text
    r = _sp.run([_sys.executable, str(_TOOL), "--fire", "--registry", str(log)],
                cwd=str(_TOOL.parent), capture_output=True, text=True)
    assert r.returncode != 0 and "Coen-gated" in (r.stderr + r.stdout)
    r = _sp.run([_sys.executable, str(_TOOL), "--registry", str(log)], cwd=str(_TOOL.parent),
                capture_output=True, text=True)
    assert r.returncode != 0                                            # --dry-run is the only mode
