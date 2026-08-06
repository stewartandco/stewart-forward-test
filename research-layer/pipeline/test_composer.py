"""Offline tests for the Composer (no API calls).

Run: python -m pytest pipeline/test_composer.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from .blocks import BLOCK_TYPES, validate_block, block_type_payload

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent


def run_verifier(log_path):
    return subprocess.run(
        [sys.executable, str(LAYER / "verify_registry.py"), str(log_path)],
        capture_output=True, text=True)


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


def test_constraint_wiring_via_validate_block(monkeypatch):
    from . import blocks
    monkeypatch.setitem(blocks.BLOCK_TYPES, ("entry", "ma_cross"), {
        "fast": {"type": "int", "grid": [5, 10, 60]},
        "slow": {"type": "int", "grid": [50, 100, 200]},
    })
    errs = validate_block("entry", "ma_cross", {"fast": 60, "slow": 50})
    assert errs == ["ma_cross: fast must be < slow"]


def test_block_type_payload_shape():
    p = block_type_payload("risk", "vol_target")
    assert p["role"] == "risk" and p["type"] == "vol_target"
    assert set(p["params_schema"]) == {"ann_vol", "lookback"}


from .registry import Registry
from .test_pipeline import make_card, make_strategy


def register_grammar(reg):
    """Chain every grammar block type (idempotent helper used across tests)."""
    from .blocks import BLOCK_TYPES
    existing = reg.block_types()
    for (role, btype) in BLOCK_TYPES:
        if (role, btype) not in existing:
            reg.register_block_type(block_type_payload(role, btype))


# ---------------- registry block-type support ----------------

def test_block_types_roundtrip(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    assert reg.block_types() == set()
    register_grammar(reg)
    assert ("entry", "ma_cross") in reg.block_types()
    assert len(reg.block_types()) == 12


def test_register_grammar_is_idempotent(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    register_grammar(reg)
    n = sum(1 for _ in reg.entries())
    register_grammar(reg)
    assert sum(1 for _ in reg.entries()) == n


def test_register_block_type_conflicting_schema_rejected(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    reg.register_block_type({"role": "entry", "type": "ma_cross",
                             "params_schema": {"fast": {"type": "int", "grid": [5]}}})
    with pytest.raises(ValueError, match="conflicts|conflicting"):
        reg.register_block_type({"role": "entry", "type": "ma_cross",
                                 "params_schema": {"fast": {"type": "int", "grid": [5, 99]}}})


def test_register_strategy_rejects_unregistered_block_type(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    with pytest.raises(ValueError, match="block type"):
        reg.register_strategy(make_strategy([card["card_id"]]))


# ---------------- verifier extensions ----------------

def _chained_strategy_setup(tmp_path, accept=True):
    reg = Registry(tmp_path / "log.jsonl")
    from .test_pipeline import register_example_blocks
    register_example_blocks(reg)
    card = make_card()
    reg.register_card(card)
    if accept:
        reg.review_card(card["card_id"], "accepted", "tester")
    return reg, card


def test_verifier_accepts_strategy_with_registered_blocks(tmp_path):
    reg, card = _chained_strategy_setup(tmp_path)
    reg.register_strategy(make_strategy([card["card_id"]]))
    out = run_verifier(tmp_path / "log.jsonl")
    assert out.returncode == 0, out.stdout


def test_verifier_rejects_unregistered_block_type(tmp_path):
    reg, card = _chained_strategy_setup(tmp_path)
    spec = make_strategy([card["card_id"]])
    spec["blocks"][0]["type"] = "never_registered"
    reg.append("strategy_registered", spec)   # bypass writer guard on purpose
    out = run_verifier(tmp_path / "log.jsonl")
    assert out.returncode != 0
    assert "unregistered block type" in out.stdout


def test_verifier_rejects_citation_of_unaccepted_card(tmp_path):
    reg, card = _chained_strategy_setup(tmp_path, accept=False)
    spec = make_strategy([card["card_id"]])
    reg.append("strategy_registered", spec)   # bypass writer guard on purpose
    out = run_verifier(tmp_path / "log.jsonl")
    assert out.returncode != 0
    assert "not accepted" in out.stdout


def test_example_log_still_valid():
    out = run_verifier(LAYER / "examples" / "registry_log.example.jsonl")
    assert out.returncode == 0, out.stdout


def test_live_registry_still_valid():
    live = LAYER / "registry_log.jsonl"
    if not live.exists():
        pytest.skip("no live registry")
    out = run_verifier(live)
    assert out.returncode == 0, out.stdout
