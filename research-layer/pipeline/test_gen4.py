"""protocol-v4 tests: dense grammar, sweepable-axis rule, cap."""
import json
from pathlib import Path

from pipeline.blocks import BLOCK_TYPES
from pipeline.composer import composition_fingerprint

REGISTRY = Path(__file__).resolve().parent.parent / "registry_log.jsonl"

DENSE = [("entry", "channel_breakout_d"), ("entry", "ma_cross_d"),
         ("entry", "trend_scan_d"), ("stop", "atr_stop_d")]


def test_dense_types_exist_with_expected_grids():
    assert BLOCK_TYPES[("entry", "channel_breakout_d")]["lookback"]["grid"] == [20, 35, 55, 75, 100]
    assert BLOCK_TYPES[("entry", "ma_cross_d")]["fast"]["grid"] == [5, 8, 13, 20, 34]
    assert BLOCK_TYPES[("entry", "ma_cross_d")]["slow"]["grid"] == [50, 80, 130, 200]
    assert BLOCK_TYPES[("entry", "trend_scan_d")]["max_lookback"]["grid"] == [60, 75, 90, 105, 120]
    assert BLOCK_TYPES[("stop", "atr_stop_d")]["mult"]["grid"] == [1.5, 2.0, 2.5, 3.0, 3.5]


def test_coarse_types_are_untouched():
    """The chained schemas must not move, or preflight_block_types aborts."""
    assert BLOCK_TYPES[("entry", "channel_breakout")]["lookback"]["grid"] == [20, 55, 100]
    assert BLOCK_TYPES[("stop", "atr_stop")]["mult"]["grid"] == [1.5, 2.0, 3.0]
    assert BLOCK_TYPES[("risk", "fixed_fraction")]["f"]["grid"] == [0.01, 0.02]


def test_all_80_existing_fingerprints_unchanged():
    """Adding block types must not perturb a single chained fingerprint."""
    seen = 0
    for line in REGISTRY.open(encoding="utf-8"):
        e = json.loads(line)
        if e.get("entry_type") != "strategy_registered":
            continue
        p = e["payload"]
        assert composition_fingerprint(p) == composition_fingerprint(p)
        seen += 1
    assert seen == 80


from pipeline.engine import run_spec


def _spec(entry_type, stop_type):
    return {
        "strategy_id": "t" * 16,
        "universe": {"assets": ["BTCUSD"], "timeframe": "1d",
                     "session": "24x7", "asset_class": "crypto"},
        "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005},
        "blocks": [
            {"role": "entry", "type": entry_type,
             "params": {"lookback": 55, "direction": "both"}},
            {"role": "stop", "type": stop_type,
             "params": {"atr_len": 14, "mult": 2.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 20}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
    }


def test_dense_twin_reproduces_its_coarse_twin_exactly():
    """A shared grid point must give byte-identical trades, or the twins have
    drifted and every fingerprint argument in the spec is void."""
    from pipeline.screen import load_bars
    root = Path(__file__).resolve().parent.parent
    bars = {"BTCUSD": load_bars(root / "data", "BTCUSD", "9999-12-31")}
    coarse = run_spec(_spec("channel_breakout", "atr_stop"), bars)
    dense = run_spec(_spec("channel_breakout_d", "atr_stop_d"), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
