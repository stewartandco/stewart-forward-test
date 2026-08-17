"""protocol-v4 tests: dense twin block types and their behavioural and
schema parity with the coarse grammar they shadow, plus the sweepable-axis
rule (only dense block types may carry a sweep axis) and the sibling cap
(raised from 25 to 60 so a family with two dense geometry axes plus a sizing
arm still fits)."""
import json
from pathlib import Path

import pytest

from pipeline.blocks import BLOCK_TYPES
from pipeline.composer import composition_fingerprint

REGISTRY = Path(__file__).resolve().parent.parent / "registry_log.jsonl"

DENSE = [("entry", "channel_breakout_dense"), ("entry", "ma_cross_dense"),
         ("entry", "trend_scan_dense"), ("stop", "atr_stop_dense")]

# Single source of truth for "which (role, type) pairs exist" — imported by
# test_composer.py and test_gen2.py so a future grammar change fails with a
# readable diff naming what was added or removed, instead of a bare count.
EXPECTED_BLOCK_TYPES = frozenset({
    ("entry", "ma_cross"),
    ("entry", "channel_breakout"),
    ("entry", "zscore_reversion"),
    ("entry", "trend_scan"),
    ("regime", "regime_ma"),
    ("filter", "vol_percentile"),
    ("stop", "atr_stop"),
    ("stop", "pct_stop"),
    ("target", "r_multiple"),
    ("exit", "time_stop"),
    ("risk", "fixed_fraction"),
    ("risk", "vol_target"),
    ("entry", "trend_scan_ds"),
    ("entry", "ma_cross_ds"),
    ("regime", "regime_ma_short"),
    ("entry", "channel_breakout_dense"),
    ("entry", "ma_cross_dense"),
    ("entry", "trend_scan_dense"),
    ("stop", "atr_stop_dense"),
})

# Each dense entry twin next to the coarse/direction-capable twin it must
# stay behaviourally and schema-identical to (see composer.preflight_block_types
# for why the coarse side can never move).
TWINS = [
    (("entry", "channel_breakout"), ("entry", "channel_breakout_dense")),
    (("entry", "ma_cross_ds"), ("entry", "ma_cross_dense")),
    (("entry", "trend_scan_ds"), ("entry", "trend_scan_dense")),
    (("stop", "atr_stop"), ("stop", "atr_stop_dense")),
]


def test_dense_types_exist_with_expected_grids():
    assert BLOCK_TYPES[("entry", "channel_breakout_dense")]["lookback"]["grid"] == [20, 35, 55, 75, 100]
    assert BLOCK_TYPES[("entry", "ma_cross_dense")]["fast"]["grid"] == [5, 8, 13, 20, 34]
    assert BLOCK_TYPES[("entry", "ma_cross_dense")]["slow"]["grid"] == [50, 80, 130, 200]
    assert BLOCK_TYPES[("entry", "trend_scan_dense")]["max_lookback"]["grid"] == [60, 75, 90, 105, 120]
    assert BLOCK_TYPES[("stop", "atr_stop_dense")]["mult"]["grid"] == [1.5, 2.0, 2.5, 3.0, 3.5]


def test_coarse_types_are_untouched():
    """The chained schemas must not move, or preflight_block_types aborts."""
    assert BLOCK_TYPES[("entry", "channel_breakout")]["lookback"]["grid"] == [20, 55, 100]
    assert BLOCK_TYPES[("stop", "atr_stop")]["mult"]["grid"] == [1.5, 2.0, 3.0]
    assert BLOCK_TYPES[("risk", "fixed_fraction")]["f"]["grid"] == [0.01, 0.02]


@pytest.mark.parametrize("coarse_key,dense_key", TWINS)
def test_dense_twin_has_same_param_keys_as_coarse(coarse_key, dense_key):
    """A param added to only one side of a twin would be silently inert in
    the shared engine handler rather than a crash — guard the key sets."""
    assert set(BLOCK_TYPES[coarse_key]) == set(BLOCK_TYPES[dense_key])


def test_all_80_existing_fingerprints_unchanged():
    """Adding dense block types must not perturb a single chained fingerprint.

    Proven by recomputing every chained fingerprint against a grammar with the
    dense types REMOVED and requiring identical output. Comparing
    composition_fingerprint(p) to itself would be a tautology that passes even
    if every fingerprint had drifted.
    """
    payloads = []
    for line in REGISTRY.open(encoding="utf-8"):
        e = json.loads(line)
        if e.get("entry_type") == "strategy_registered":
            payloads.append(e["payload"])
    assert len(payloads) == 80
    with_dense = [composition_fingerprint(p) for p in payloads]
    original = dict(BLOCK_TYPES)
    try:
        BLOCK_TYPES.clear()
        BLOCK_TYPES.update({k: v for k, v in original.items() if k not in DENSE})
        without_dense = [composition_fingerprint(p) for p in payloads]
    finally:
        BLOCK_TYPES.clear()
        BLOCK_TYPES.update(original)
    assert with_dense == without_dense
    assert len(set(with_dense)) == 80, "fingerprint collision among chained specs"


from pipeline.engine import run_spec


def _spec(entry_type, entry_params, stop_type="atr_stop", stop_params=None):
    return {
        "strategy_id": "t" * 16,
        "universe": {"assets": ["BTCUSD"], "timeframe": "1d",
                     "session": "24x7", "asset_class": "crypto"},
        "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005},
        "blocks": [
            {"role": "entry", "type": entry_type, "params": entry_params},
            {"role": "stop", "type": stop_type,
             "params": stop_params or {"atr_len": 14, "mult": 2.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 20}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
    }


def _load_bars():
    from pipeline.screen import load_bars
    root = Path(__file__).resolve().parent.parent
    return {"BTCUSD": load_bars(root / "data", "BTCUSD", "9999-12-31")}


def test_channel_breakout_dense_twin_reproduces_its_coarse_twin_exactly():
    """A shared grid point must give byte-identical trades, or the twins have
    drifted and every fingerprint argument in the spec is void."""
    bars = _load_bars()
    entry_params = {"lookback": 55, "direction": "both"}
    coarse = run_spec(_spec("channel_breakout", entry_params,
                            "atr_stop", {"atr_len": 14, "mult": 2.0}), bars)
    dense = run_spec(_spec("channel_breakout_dense", entry_params,
                           "atr_stop_dense", {"atr_len": 14, "mult": 2.0}), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_ma_cross_dense_twin_reproduces_its_coarse_twin_exactly():
    bars = _load_bars()
    entry_params = {"fast": 5, "slow": 50, "direction": "both"}
    coarse = run_spec(_spec("ma_cross_ds", entry_params), bars)
    dense = run_spec(_spec("ma_cross_dense", entry_params), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_trend_scan_dense_twin_reproduces_its_coarse_twin_exactly():
    bars = _load_bars()
    entry_params = {"max_lookback": 60, "t_min": 2.0, "direction": "both"}
    coarse = run_spec(_spec("trend_scan_ds", entry_params), bars)
    dense = run_spec(_spec("trend_scan_dense", entry_params), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


from pipeline.composer import validate_family, SIBLING_CAP_DEFAULT, SWEEPABLE_TYPES


def _fam(sweep_type, sweep_param, values):
    return {
        "family": "t", "rationale": "t", "card_ids": [],
        "assets": ["BTCUSD"],
        "blocks": [
            {"role": "entry", "type": sweep_type,
             "params": {"lookback": 55, "direction": "both"}
             if "channel" in sweep_type else {"max_lookback": 60, "t_min": 2.0,
                                              "direction": "both"}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "sweep": [{"block": 0, "param": sweep_param, "values": values}],
    }


def test_sweeping_a_coarse_type_is_rejected():
    errs = validate_family(_fam("channel_breakout", "lookback", [20, 55]),
                           accepted_ids=set(), sibling_cap=60)
    assert any("not sweepable" in e for e in errs), errs


def test_sweeping_a_dense_type_is_accepted():
    errs = validate_family(_fam("channel_breakout_dense", "lookback", [35, 55, 75]),
                           accepted_ids=set(), sibling_cap=60)
    assert not [e for e in errs if "sweepable" in e], errs


def test_sweepable_set_is_exactly_the_dense_types():
    assert SWEEPABLE_TYPES == {("entry", "channel_breakout_dense"),
                               ("entry", "ma_cross_dense"),
                               ("entry", "trend_scan_dense"),
                               ("stop", "atr_stop_dense")}


def test_sibling_cap_is_sixty():
    assert SIBLING_CAP_DEFAULT == 60
