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

def test_grammar_matches_expected_block_types_with_required_roles():
    from .test_gen4 import EXPECTED_BLOCK_TYPES
    roles = {role for role, _ in BLOCK_TYPES}
    assert set(BLOCK_TYPES) == EXPECTED_BLOCK_TYPES
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
    # Real invariant: every grammar entry got registered. Never needs
    # bumping when the grammar grows.
    assert len(reg.block_types()) == len(BLOCK_TYPES)


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
    # protocol-v4: only dense block types may carry a sweep axis, so the
    # default fixture sweeps trend_scan_dense (entry) and atr_stop_dense
    # (stop) rather than the coarse zscore_reversion/atr_stop grammar.
    fam = {
        "family": "trend_scan_family",
        "rationale": "Trend continuation signal, dense-grid threshold sweep, PT/SL risk.",
        "card_ids": ["aaaaaaaaaaaaaaaa"],
        "assets": ["BTCUSD"],
        "blocks": [
            {"role": "entry", "type": "trend_scan_dense",
             "params": {"max_lookback": 60, "t_min": 2.0, "direction": "long"}},
            {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 2.0}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "sweep": [
            {"block": 0, "param": "t_min", "values": [2.0, 2.5, 3.0]},
            # contiguous on atr_stop_dense's grid [1.5, 2.0, 2.5, 3.0, 3.5] --
            # [1.5, 2.0, 3.0] would skip 2.5 and fail validate_family's
            # neighbourhood-contiguity rule (protocol-v4).
            {"block": 1, "param": "mult", "values": [1.5, 2.0, 2.5]},
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
    fam = good_family(sweep=[{"block": 0, "param": "t_min", "values": [2.0, 9.9]}])
    assert any("not a subset" in e for e in validate_family(fam, ACCEPTED, 25))


def test_sweep_unknown_param_rejected():
    fam = good_family(sweep=[{"block": 0, "param": "zz", "values": [2.0]}])
    assert any("zz" in e for e in validate_family(fam, ACCEPTED, 25))


def test_two_value_sweep_axis_rejected():
    """protocol-v4: plateau selection (pipeline/plateau.py) requires a
    registered sibling on both sides of every swept axis, so a two-value
    sweep can never produce a survivor -- both of its points are grid
    edges. The Composer must not let a family burn a generation on that."""
    fam = good_family(sweep=[{"block": 0, "param": "t_min", "values": [2.0, 2.5]}])
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("t_min" in e and "at least 3" in e for e in errs)


def test_single_value_sweep_axis_rejected():
    fam = good_family(sweep=[{"block": 0, "param": "t_min", "values": [2.0]}])
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("t_min" in e and "at least 3" in e for e in errs)


def test_sibling_cap_rejects_not_clips():
    fam = good_family(sweep=[
        {"block": 0, "param": "t_min", "values": [2.0, 2.5, 3.0]},
        {"block": 0, "param": "max_lookback", "values": [60, 75, 90, 105, 120]},
        {"block": 1, "param": "mult", "values": [1.5, 2.0, 2.5, 3.0, 3.5]},
    ])  # 3 x 5 x 5 = 75 siblings
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("sibling" in e and "cap" in e for e in errs)


def test_bad_family_name_rejected():
    errs = validate_family(good_family(family="Bad Name!"), ACCEPTED, 25)
    assert any("family name" in e for e in errs)


def test_bad_asset_rejected():
    errs = validate_family(good_family(assets=["SOLUSD"]), ACCEPTED, 25)
    assert any("SOLUSD" in e for e in errs)


def test_sweep_int_value_accepted_against_float_grid():
    fam = good_family(sweep=[{"block": 0, "param": "t_min", "values": [2, 2.5, 3.0]}])
    assert validate_family(fam, ACCEPTED, 25) == []


def test_duplicate_sweep_axis_rejected():
    fam = good_family(sweep=[
        {"block": 0, "param": "t_min", "values": [2.0, 2.5]},
        {"block": 0, "param": "t_min", "values": [3.0]},
    ])
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("duplicate sweep axis" in e for e in errs)


# ---------------- deterministic expansion ----------------

TS = "2026-08-06T12:00:00Z"


def test_expansion_count_is_axis_product():
    specs = expand_family(good_family(), "run1", "claude-opus-5", TS)
    assert len(specs) == 9  # 3 t_min x 3 mult


def test_expansion_is_deterministic():
    a = expand_family(good_family(), "run1", "claude-opus-5", TS)
    b = expand_family(good_family(), "run1", "claude-opus-5", TS)
    assert [s["strategy_id"] for s in a] == [s["strategy_id"] for s in b]


def test_siblings_share_group_and_vary_swept_params():
    specs = expand_family(good_family(), "run1", "claude-opus-5", TS)
    groups = {s["provenance"]["sibling_group_id"] for s in specs}
    assert groups == {"trend_scan_family-run1"}
    t_values = {s["blocks"][0]["params"]["t_min"] for s in specs}
    assert t_values == {2.0, 2.5, 3.0}
    lookbacks = {s["blocks"][0]["params"]["max_lookback"] for s in specs}
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


def test_long_family_name_truncation_keeps_names_unique():
    fam = good_family(family="zscore_dip_buyer_with_a_very_long_descriptive_family_name_for_realism_and_extra_padding")
    specs = expand_family(fam, "run1", "claude-opus-5", TS)
    names = [s["name"] for s in specs]
    assert len(set(names)) == len(names)
    assert all(len(n) <= 120 for n in names)


def test_int_and_float_sweep_values_hash_identically():
    a = expand_family(good_family(sweep=[{"block": 0, "param": "t_min", "values": [2]}]),
                      "run1", "claude-opus-5", TS)
    b = expand_family(good_family(sweep=[{"block": 0, "param": "t_min", "values": [2.0]}]),
                      "run1", "claude-opus-5", TS)
    assert [s["strategy_id"] for s in a] == [s["strategy_id"] for s in b]


def test_asset_order_does_not_change_identity():
    a = expand_family(good_family(assets=["BTCUSD", "ETHUSD"]), "run1", "claude-opus-5", TS)
    b = expand_family(good_family(assets=["ETHUSD", "BTCUSD"]), "run1", "claude-opus-5", TS)
    assert [s["strategy_id"] for s in a] == [s["strategy_id"] for s in b]


def test_duplicate_values_in_sweep_axis_rejected():
    fam = good_family(sweep=[{"block": 0, "param": "t_min", "values": [2.0, 2.0, 2.5]}])
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("duplicate values" in e for e in errs)


from .composer import run as composer_run


def seeded_registry(tmp_path):
    """Registry with one accepted card; returns (registry_path, card_id)."""
    reg = Registry(tmp_path / "reg.jsonl")
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    return tmp_path / "reg.jsonl", card["card_id"]


# ---------------- run() paths ----------------

def test_run_dry_run_writes_nothing(tmp_path, capsys):
    path, cid = seeded_registry(tmp_path)
    n_before = sum(1 for _ in Registry(path).entries())
    rc = composer_run(
        ["--registry", str(path), "--run-id", "t1", "--dry-run"],
        propose_fn=lambda cards: [good_family(card_ids=[cid])])
    assert rc == 0
    assert sum(1 for _ in Registry(path).entries()) == n_before
    outp = capsys.readouterr().out
    assert "DRY RUN" in outp and "9 sibling" in outp


def test_run_registers_blocks_then_specs(tmp_path):
    path, cid = seeded_registry(tmp_path)
    rc = composer_run(
        ["--registry", str(path), "--run-id", "t1"],
        propose_fn=lambda cards: [good_family(card_ids=[cid])])
    assert rc == 0
    reg = Registry(path)
    # Real invariant: every grammar entry got registered. Never needs
    # bumping when the grammar grows.
    assert len(reg.block_types()) == len(BLOCK_TYPES)
    states = reg.strategy_states()
    assert len(states) == 9 and set(states.values()) == {"proposed"}
    out = run_verifier(path)
    assert out.returncode == 0, out.stdout


def test_run_drops_invalid_family_loudly(tmp_path, capsys):
    path, cid = seeded_registry(tmp_path)
    bad = good_family(card_ids=["nope"], family="badfam")
    ok = good_family(card_ids=[cid])
    rc = composer_run(
        ["--registry", str(path), "--run-id", "t1"],
        propose_fn=lambda cards: [bad, ok])
    assert rc == 0
    outp = capsys.readouterr().out
    assert "DROPPED family badfam" in outp
    assert len(Registry(path).strategy_states()) == 9


def test_run_rejects_duplicate_family_names(tmp_path, capsys):
    path, cid = seeded_registry(tmp_path)
    rc = composer_run(
        ["--registry", str(path), "--run-id", "t1"],
        propose_fn=lambda cards: [good_family(card_ids=[cid]),
                                  good_family(card_ids=[cid])])
    assert rc == 0
    assert len(Registry(path).strategy_states()) == 9  # second dropped
    assert "duplicate family name" in capsys.readouterr().out


def test_run_errors_when_no_accepted_cards(tmp_path):
    reg = Registry(tmp_path / "empty.jsonl")
    card = make_card()
    reg.register_card(card)  # pending only
    rc = composer_run(["--registry", str(tmp_path / "empty.jsonl"), "--run-id", "t1"],
                      propose_fn=lambda cards: [])
    assert rc == 1


def test_run_aborts_on_grammar_conflict(tmp_path, capsys):
    path, cid = seeded_registry(tmp_path)
    reg = Registry(path)
    from .blocks import BLOCK_TYPES as GRAMMAR
    role, btype = next(iter(GRAMMAR))
    reg.register_block_type({"role": role, "type": btype,
                             "params_schema": {"different": {"type": "int", "grid": [1]}}})
    rc = composer_run(["--registry", str(path), "--run-id", "t1", "--dry-run"],
                      propose_fn=lambda cards: [good_family(card_ids=[cid])])
    assert rc == 1
    assert "GRAMMAR CONFLICT" in capsys.readouterr().out


def test_run_partial_write_warns(tmp_path, capsys, monkeypatch):
    path, cid = seeded_registry(tmp_path)
    orig = Registry.register_strategy
    calls = {"n": 0}

    def flaky(self, spec):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        return orig(self, spec)

    monkeypatch.setattr(Registry, "register_strategy", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        composer_run(["--registry", str(path), "--run-id", "t1"],
                     propose_fn=lambda cards: [good_family(card_ids=[cid])])
    err = capsys.readouterr().err
    assert "PARTIAL WRITE: 2/9" in err


def test_verifier_honors_acceptance_revocation(tmp_path):
    reg, card = _chained_strategy_setup(tmp_path)
    reg.review_card(card["card_id"], "rejected", "tester", reject_reason="duplicate")
    spec = make_strategy([card["card_id"]])
    reg.append("strategy_registered", spec)   # bypass writer guard on purpose
    out = run_verifier(tmp_path / "log.jsonl")
    assert out.returncode != 0
    assert "not accepted" in out.stdout


def test_run_max_families_truncation_is_loud(tmp_path, capsys):
    path, cid = seeded_registry(tmp_path)
    fams = [good_family(card_ids=[cid], family=f"fam_{i}") for i in range(3)]
    rc = composer_run(["--registry", str(path), "--run-id", "t1", "--dry-run",
                       "--max-families", "2"],
                      propose_fn=lambda cards: fams)
    assert rc == 0
    assert "1 families beyond --max-families 2 discarded" in capsys.readouterr().out


def test_normalize_proposal_converts_param_lists():
    from .composer import normalize_proposal
    fams = [{"blocks": [{"role": "entry", "type": "ma_cross",
                         "params": [{"name": "fast", "value": 10},
                                    {"name": "slow", "value": 100}]}]}]
    out = normalize_proposal(fams)
    assert out[0]["blocks"][0]["params"] == {"fast": 10, "slow": 100}


# ---------------- universe sweep across the declared grid ----------------

from . import composer
from pipeline import cells as cells_mod


def _family(blocks):
    return {"family": "f", "blocks": blocks,
            "universe": {"asset_class": "crypto", "assets": ["BTCUSD"],
                         "timeframe": "1d"},
            "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005}}


def test_expand_universe_produces_one_spec_per_cell():
    base = _family([{"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 50}}])
    out = composer.expand_universe(base, cells_mod.phase_cells(1))
    assert len(out) == 20
    assert {(s["universe"]["assets"][0], s["universe"]["timeframe"]) for s in out} \
        == set(cells_mod.phase_cells(1))


def test_each_cell_is_a_single_asset_book():
    """A cell is one asset at one timeframe - the unit of survival. The 2-asset
    mean-combine path stays for the legacy BTC+ETH specs only."""
    base = _family([{"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 50}}])
    for s in composer.expand_universe(base, [("ETHUSDT", "15m")]):
        assert s["universe"]["assets"] == ["ETHUSDT"]
        assert s["universe"]["timeframe"] == "15m"


def test_cells_fingerprint_differently_so_they_register_side_by_side():
    """composition_fingerprint hashes assets and timeframe, so the same blocks
    on two cells are genuinely different strategies and the resurrection guard
    does not collide them."""
    base = _family([{"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 50}}])
    a, b = composer.expand_universe(base, [("ETHUSDT", "15m"), ("BTCUSDT", "4h")])
    assert composer.composition_fingerprint(a) != composer.composition_fingerprint(b)


def test_the_same_cell_twice_still_fingerprints_identically():
    base = _family([{"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 50}}])
    a = composer.expand_universe(base, [("ETHUSDT", "15m")])[0]
    b = composer.expand_universe(base, [("ETHUSDT", "15m")])[0]
    assert composer.composition_fingerprint(a) == composer.composition_fingerprint(b)


def test_undeclared_cells_are_refused():
    base = _family([{"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 50}}])
    with pytest.raises(ValueError):
        composer.expand_universe(base, [("DOGEUSDT", "1h")])


def test_each_cell_gets_its_own_sibling_group(tmp_path):
    """Coen, 2026-08-18: one sibling group PER CELL.

    expand_universe deep-copies the spec, so provenance rode along unchanged
    and one parameter set across 30 cells would have landed in ONE group.
    select_survivors keeps exactly one winner per group, so 29 of 30 cells
    would have been discarded -- the precise outcome this function's own
    docstring exists to prevent ("excluding a strategy that only works on 15m
    ETH is exactly what this exists to prevent").

    Selection, PBO and the plateau gate are all per-group, so scoping the group
    to the cell is what makes a cell the unit of survival rather than the unit
    of competition.
    """
    base = _family([{"role": "entry", "type": "ma_cross",
                     "params": {"fast": 5, "slow": 50}}])
    base["provenance"] = {"sibling_group_id": "trend-run7"}

    out = composer.expand_universe(
        base, [("ETHUSDT", "15m"), ("BTCUSDT", "4h"), ("SOLUSDT", "1d")])

    groups = [s["provenance"]["sibling_group_id"] for s in out]
    assert len(set(groups)) == 3, "each cell must be its own sibling group"
    # and the id must still say which family and run it came from
    assert all(g.startswith("trend-run7") for g in groups), groups
    assert set(groups) == {"trend-run7:ETHUSDT_15m", "trend-run7:BTCUSDT_4h",
                           "trend-run7:SOLUSDT_1d"}


def test_cell_scoped_group_ids_are_deterministic():
    """Same family, same cell, same id -- the ids end up on the chain."""
    base = _family([{"role": "entry", "type": "ma_cross",
                     "params": {"fast": 5, "slow": 50}}])
    base["provenance"] = {"sibling_group_id": "trend-run7"}
    a = composer.expand_universe(base, [("ETHUSDT", "15m")])[0]
    b = composer.expand_universe(base, [("ETHUSDT", "15m")])[0]
    assert a["provenance"]["sibling_group_id"] == b["provenance"]["sibling_group_id"]


def test_expansion_without_provenance_is_left_alone():
    """Not every caller carries provenance (sweep_measure builds bare specs).
    Inventing a group id for one would be worse than leaving it absent."""
    base = _family([{"role": "entry", "type": "ma_cross",
                     "params": {"fast": 5, "slow": 50}}])
    base.pop("provenance", None)
    out = composer.expand_universe(base, [("ETHUSDT", "15m")])
    assert "sibling_group_id" not in out[0].get("provenance", {})


# --- routable_cards extraction (loop plan Task 2) ---

def _card(cid, asset_classes=None, topics=None):
    # NOTE (adjusted from the plan's draft): composer.py reads topics from
    # (card.get("tags") or {}).get("topics") -- nested under "tags", the
    # same place asset_classes lives (see composer.py's INDEX_FUTURES_PROXY_
    # TOPICS/METALS_PROXY_TOPICS matching, and test_composer_equity.py's own
    # fixture cards) -- not a top-level "topics" key. A top-level key, as
    # the plan's draft had it, is silently invisible to the proxy-lane match
    # and would make the equity-proxy test pass for the wrong reason (or, as
    # first written, fail outright).
    tags = {}
    if asset_classes is not None:
        tags["asset_classes"] = asset_classes
    if topics is not None:
        tags["topics"] = topics
    return {
        "card_id": cid,
        "claim": f"claim {cid}",
        "tags": tags,
        "review": {"status": "accepted", "reject_reason": None},
    }


def test_routable_cards_crypto_is_unrestricted():
    from .composer import routable_cards
    accepted = {"a": _card("a", ["equities"]), "b": _card("b", None)}
    cards, meta = routable_cards(accepted, "crypto")
    assert set(cards) == {"a", "b"}
    assert meta["routed_card_ids"] is None      # unrestricted: no routing applied


def test_routable_cards_fx_filters_on_tags():
    from .composer import routable_cards
    accepted = {
        "fx1": _card("fx1", ["fx"]),
        "crs": _card("crs", None),              # missing tags -> ["cross"] default
        "eq1": _card("eq1", ["equities"]),
    }
    cards, meta = routable_cards(accepted, "fx")
    assert set(cards) == {"fx1", "crs"}
    assert set(meta["routed_card_ids"]) == {"fx1", "crs"}
    # NOTE (adjusted from the plan's draft): the fx branch never reaches any
    # of the equity_etf/metal_etf/bond_etf elif arms in the real run()
    # code, so proxy_routed_card_ids is never assigned away from its None
    # initializer for fx -- it stays None, not []. drift_record() relies on
    # exactly this (None -> key omitted entirely; see
    # test_composer_fx.py::test_fx_card_routing, which asserts
    # `"proxy_routed_card_ids" not in drift` for fx). Asserting `== []` here
    # would be asserting behaviour the real code does not have.
    assert meta["proxy_routed_card_ids"] is None


def test_routable_cards_equity_proxy_lane_recorded():
    from .composer import routable_cards, INDEX_FUTURES_PROXY_TOPICS
    topic = sorted(INDEX_FUTURES_PROXY_TOPICS)[0]
    accepted = {
        "eq1": _card("eq1", ["equities"]),
        "fut": _card("fut", ["futures"], topics=[topic]),
    }
    cards, meta = routable_cards(accepted, "equity_etf")
    assert "eq1" in cards and "fut" in cards
    assert "fut" in meta["proxy_routed_card_ids"]
