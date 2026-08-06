"""Offline tests for the Composer (no API calls).

Run: python -m pytest pipeline/test_composer.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
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


from .composer import validate_family, expand_family, SIBLING_CAP_DEFAULT


def good_family(**overrides):
    fam = {
        "family": "zscore_dip_buyer",
        "rationale": "Mean reversion PT/SL asymmetry per accepted cards.",
        "card_ids": ["aaaaaaaaaaaaaaaa"],
        "assets": ["BTCUSD"],
        "blocks": [
            {"role": "entry", "type": "zscore_reversion",
             "params": {"lookback": 60, "z_entry": 2.0, "direction": "long"}},
            {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "sweep": [
            {"block": 0, "param": "z_entry", "values": [1.5, 2.0, 2.5]},
            {"block": 1, "param": "mult", "values": [1.5, 2.0, 3.0]},
        ],
    }
    fam.update(overrides)
    return fam


ACCEPTED = {"aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"}


# ---------------- family validation ----------------

def test_valid_family_passes():
    assert validate_family(good_family(), ACCEPTED, 25) == []


def test_family_citing_unaccepted_card_rejected():
    errs = validate_family(good_family(card_ids=["aaaaaaaaaaaaaaaa", "cccccccccccccccc"]), ACCEPTED, 25)
    assert any("cccccccccccccccc" in e and "not accepted" in e for e in errs)


def test_family_with_unknown_block_rejected():
    fam = good_family()
    fam["blocks"][0]["type"] = "orb_breakout"
    assert any("unknown block type" in e for e in validate_family(fam, ACCEPTED, 25))


def test_family_missing_stop_rejected():
    fam = good_family()
    fam["blocks"] = [b for b in fam["blocks"] if b["role"] != "stop"]
    fam["sweep"] = fam["sweep"][:1]
    assert any("stop" in e for e in validate_family(fam, ACCEPTED, 25))


def test_family_with_two_entries_rejected():
    fam = good_family()
    fam["blocks"].append({"role": "entry", "type": "ma_cross",
                          "params": {"fast": 10, "slow": 100}})
    assert any("exactly one entry" in e for e in validate_family(fam, ACCEPTED, 25))


def test_sweep_values_off_grid_rejected():
    fam = good_family(sweep=[{"block": 0, "param": "z_entry", "values": [2.0, 9.9]}])
    assert any("not a subset" in e for e in validate_family(fam, ACCEPTED, 25))


def test_sweep_unknown_param_rejected():
    fam = good_family(sweep=[{"block": 0, "param": "zz", "values": [2.0]}])
    assert any("zz" in e for e in validate_family(fam, ACCEPTED, 25))


def test_sibling_cap_rejects_not_clips():
    fam = good_family(sweep=[
        {"block": 0, "param": "z_entry", "values": [1.5, 2.0, 2.5]},
        {"block": 0, "param": "lookback", "values": [20, 60, 90]},
        {"block": 1, "param": "mult", "values": [1.5, 2.0, 3.0]},
    ])  # 27 siblings
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("sibling" in e and "cap" in e for e in errs)


def test_bad_family_name_rejected():
    errs = validate_family(good_family(family="Bad Name!"), ACCEPTED, 25)
    assert any("family name" in e for e in errs)


def test_bad_asset_rejected():
    errs = validate_family(good_family(assets=["SOLUSD"]), ACCEPTED, 25)
    assert any("SOLUSD" in e for e in errs)


def test_sweep_int_value_accepted_against_float_grid():
    fam = good_family(sweep=[{"block": 0, "param": "z_entry", "values": [2, 2.5]}])
    assert validate_family(fam, ACCEPTED, 25) == []


def test_duplicate_sweep_axis_rejected():
    fam = good_family(sweep=[
        {"block": 0, "param": "z_entry", "values": [1.5, 2.0]},
        {"block": 0, "param": "z_entry", "values": [2.5]},
    ])
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("duplicate sweep axis" in e for e in errs)


# ---------------- deterministic expansion ----------------

TS = "2026-08-06T12:00:00Z"


def test_expansion_count_is_axis_product():
    specs = expand_family(good_family(), "run1", "claude-opus-5", TS)
    assert len(specs) == 9  # 3 z_entry x 3 mult


def test_expansion_is_deterministic():
    a = expand_family(good_family(), "run1", "claude-opus-5", TS)
    b = expand_family(good_family(), "run1", "claude-opus-5", TS)
    assert [s["strategy_id"] for s in a] == [s["strategy_id"] for s in b]


def test_siblings_share_group_and_vary_swept_params():
    specs = expand_family(good_family(), "run1", "claude-opus-5", TS)
    groups = {s["provenance"]["sibling_group_id"] for s in specs}
    assert groups == {"zscore_dip_buyer-run1"}
    z_values = {s["blocks"][0]["params"]["z_entry"] for s in specs}
    assert z_values == {1.5, 2.0, 2.5}
    lookbacks = {s["blocks"][0]["params"]["lookback"] for s in specs}
    assert lookbacks == {60}  # base param untouched


def test_no_sweep_yields_single_spec():
    specs = expand_family(good_family(sweep=[]), "run1", "claude-opus-5", TS)
    assert len(specs) == 1


def test_expanded_specs_are_schema_valid():
    schema = json.loads((LAYER / "schemas" / "strategy_spec.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    for spec in expand_family(good_family(), "run1", "claude-opus-5", TS):
        validator.validate(spec)


def test_names_are_unique_and_bounded():
    specs = expand_family(good_family(), "run1", "claude-opus-5", TS)
    names = [s["name"] for s in specs]
    assert len(set(names)) == len(names)
    assert all(len(n) <= 120 for n in names)
