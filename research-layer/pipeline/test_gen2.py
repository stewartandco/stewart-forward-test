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

def test_grammar_matches_expected_block_types():
    from .test_gen4 import EXPECTED_BLOCK_TYPES
    assert set(BLOCK_TYPES) == EXPECTED_BLOCK_TYPES


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


import copy

from .registry import Registry
from .composer import (composition_fingerprint, registered_fingerprints,
                       validate_family, expand_family, run as composer_run)
from .blocks import BLOCK_TYPES, block_type_payload
from .test_composer import good_family, ACCEPTED
from .test_pipeline import make_card

TS = "2026-08-15T00:00:00Z"


# ---------------- composition fingerprint ----------------

def test_fingerprint_ignores_identity_fields():
    a = expand_family(good_family(sweep=[]), "runA", "modelA", TS)[0]
    b = expand_family(good_family(sweep=[]), "runB", "modelB",
                      "2027-01-01T00:00:00Z")[0]
    assert a["strategy_id"] != b["strategy_id"]      # content-addressed differ
    assert composition_fingerprint(a) == composition_fingerprint(b)


def test_fingerprint_ignores_block_and_asset_order():
    a = expand_family(good_family(sweep=[], assets=["BTCUSD", "ETHUSD"]),
                      "r", "m", TS)[0]
    b = copy.deepcopy(a)
    b["blocks"] = list(reversed(b["blocks"]))
    b["universe"]["assets"] = ["ETHUSD", "BTCUSD"]
    assert composition_fingerprint(a) == composition_fingerprint(b)


def test_fingerprint_changes_with_any_param():
    a = expand_family(good_family(sweep=[]), "r", "m", TS)[0]
    b = copy.deepcopy(a)
    b["blocks"][0]["params"]["t_min"] = 2.5
    assert composition_fingerprint(a) != composition_fingerprint(b)


def test_fingerprint_snaps_params_defensively():
    # an unsnapped int must not fingerprint differently from its float grid
    # value — the guard cannot depend on callers having run _snap_to_grid
    a = expand_family(good_family(sweep=[]), "r", "m", TS)[0]
    b = copy.deepcopy(a)
    b["blocks"][0]["params"]["t_min"] = 2      # grid value is 2.0
    assert composition_fingerprint(a) == composition_fingerprint(b)


def test_fingerprint_distinguishes_universe_fields():
    a = expand_family(good_family(sweep=[]), "r", "m", TS)[0]
    b = copy.deepcopy(a)
    b["universe"]["asset_class"] = "futures"
    assert composition_fingerprint(a) != composition_fingerprint(b)


def test_registered_fingerprints_maps_to_ids(tmp_path):
    from .test_composer import register_grammar
    reg = Registry(tmp_path / "log.jsonl")
    register_grammar(reg)
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    spec = expand_family(good_family(sweep=[], card_ids=[card["card_id"]]),
                         "r", "m", TS)[0]
    reg.register_strategy(spec)
    fps = registered_fingerprints(reg)
    assert fps[composition_fingerprint(spec)] == spec["strategy_id"]


# ---------------- rule 8: mutually exclusive regimes ----------------

def test_family_with_both_regime_gates_rejected():
    fam = good_family()
    fam["blocks"].append({"role": "regime", "type": "regime_ma",
                          "params": {"ma_len": 100}})
    fam["blocks"].append({"role": "regime", "type": "regime_ma_short",
                          "params": {"ma_len": 100}})
    errs = validate_family(fam, ACCEPTED, 25)
    assert any("regime_ma and regime_ma_short" in e for e in errs)


from .composer import PROPOSAL_SCHEMA, SYSTEM_PROMPT
from .test_composer import register_grammar


def seeded_registry_with_spec(tmp_path):
    """Registry with grammar, an accepted card, and one registered strategy
    built from good_family. Returns (registry, card_id, registered_spec)."""
    reg = Registry(tmp_path / "reg.jsonl")
    register_grammar(reg)
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    spec = expand_family(good_family(sweep=[], card_ids=[card["card_id"]]),
                         "seed-run", "m", TS)[0]
    reg.register_strategy(spec)
    return reg, card["card_id"], spec


# ---------------- rule 7: no resurrection ----------------

def test_run_drops_family_reproposing_registered_composition(tmp_path, capsys):
    reg, cid, spec = seeded_registry_with_spec(tmp_path)
    rc = composer_run(
        ["--registry", str(reg.log_path), "--run-id", "gen2", "--dry-run"],
        propose_fn=lambda cards: [good_family(sweep=[], card_ids=[cid])])
    assert rc == 0
    out = capsys.readouterr().out
    assert "already registered" in out
    assert spec["strategy_id"] in out


def test_run_drops_within_run_duplicate_composition(tmp_path, capsys):
    reg, cid, _ = seeded_registry_with_spec(tmp_path)
    fam_a = good_family(sweep=[], card_ids=[cid], family="fam_a")
    fam_b = good_family(sweep=[], card_ids=[cid], family="fam_b")
    fam_a["blocks"][0]["params"]["t_min"] = 2.5   # differs from registered
    fam_b["blocks"][0]["params"]["t_min"] = 2.5   # collides with fam_a
    rc = composer_run(
        ["--registry", str(reg.log_path), "--run-id", "gen2", "--dry-run"],
        propose_fn=lambda cards: [fam_a, fam_b])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fam_a" in out and "DROPPED family fam_b" in out


def test_run_allows_genuinely_new_composition(tmp_path, capsys):
    reg, cid, _ = seeded_registry_with_spec(tmp_path)
    fam = good_family(sweep=[], card_ids=[cid], family="fresh")
    fam["blocks"][0]["params"]["max_lookback"] = 90    # not the registered one
    rc = composer_run(
        ["--registry", str(reg.log_path), "--run-id", "gen2", "--dry-run"],
        propose_fn=lambda cards: [fam])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DROPPED" not in out
    assert "1 families kept" in out


def test_run_drops_family_with_duplicate_sibling_compositions(tmp_path, capsys):
    reg, cid, _ = seeded_registry_with_spec(tmp_path)
    fam = good_family(card_ids=[cid], family="mirrored")
    # a second stop of the same type + mirrored sweep axes expand to two
    # siblings that are the SAME composition under different ids
    fam["blocks"].append({"role": "stop", "type": "atr_stop_dense",
                          "params": {"atr_len": 14, "mult": 2.0}})
    fam["sweep"] = [{"block": 1, "param": "mult", "values": [2.0, 3.0]},
                    {"block": 4, "param": "mult", "values": [3.0, 2.0]}]
    rc = composer_run(
        ["--registry", str(reg.log_path), "--run-id", "gen2", "--dry-run"],
        propose_fn=lambda cards: [fam])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DROPPED family mirrored" in out
    assert "same composition" in out


# ---------------- prompt + schema ----------------

def test_proposal_schema_requires_regime_hypothesis():
    item = PROPOSAL_SCHEMA["properties"]["families"]["items"]
    assert "regime_hypothesis" in item["properties"]
    assert "regime_hypothesis" in item["required"]


def test_system_prompt_states_gen1_failure_and_new_types():
    assert "trend_scan_ds" in SYSTEM_PROMPT
    assert "regime_ma_short" in SYSTEM_PROMPT
    assert "long-biased" in SYSTEM_PROMPT


from .gauntlet import (PROTOCOL as G_PROTOCOL, window_vol, evaluate_spec,
                       DECAY_MIN_PCT)


def gtrade(entry_date, ret, frac=0.2):
    return {"entry_date": entry_date, "return_net": ret, "notional_frac": frac}


G_IS = [gtrade("2022-01-01", 0.05)] * 30 + [gtrade("2022-06-01", -0.02)] * 20
STEADY = [0.001, 0.002, -0.001, 0.0015, 0.001] * 200


def geval(is_t, oos_t, is_vol, oos_vol, stress=None, returns=None):
    if stress is None:
        stress = [gtrade(t["entry_date"], t["return_net"] - 0.001) for t in oos_t]
    return evaluate_spec(is_t, oos_t, stress, returns or STEADY,
                         is_vol, oos_vol, 4, 0.0001, seed=99)


# ---------------- protocol version ----------------

def test_protocol_is_current():
    # migrated with the gauntlet: v3 retired the DSR gate and v4 added the
    # Sharpe floor, PBO and plateau gates, so the gen-2 suite's anchor tracks
    # the current protocol string rather than the one it was written against.
    assert G_PROTOCOL == "gauntlet-protocol-v4"


# ---------------- window_vol ----------------

def test_window_vol_splits_on_cutoff():
    calm = [{"date": f"2022-01-{i+1:02d}", "close": 100.0} for i in range(28)]
    wild = [{"date": f"2024-01-{i+1:02d}",
             "close": 100.0 * (1.10 if i % 2 else 0.91)} for i in range(28)]
    bars = {"X": calm + wild}
    assert window_vol(bars, ["X"], "", "2023-12-31") == pytest.approx(0.0)
    assert window_vol(bars, ["X"], "2023-12-31", "9999-12-31") > 1.0


# ---------------- vol-normalized decay ----------------

def test_vol_collapse_alone_no_longer_fails():
    # raw edge halves, but so does vol -> normalized edge unchanged
    oos = [gtrade("2024-02-01", 0.025)] * 12 + [gtrade("2024-06-01", -0.01)] * 8
    raw_decay = (sum(t["return_net"] * t["notional_frac"] for t in oos) / len(oos)) \
        / (sum(t["return_net"] * t["notional_frac"] for t in G_IS) / len(G_IS)) - 1
    assert raw_decay < -0.4                       # would have failed v1
    passed, reason, metrics, _ = geval(G_IS, oos, 0.80, 0.40)
    assert metrics["edge_decay_pct"] == pytest.approx(0.0, abs=1e-6)
    assert passed, reason


def test_edge_falling_faster_than_vol_still_fails():
    oos = [gtrade("2024-02-01", 0.008)] * 12 + [gtrade("2024-06-01", -0.01)] * 8
    passed, reason, _, _ = geval(G_IS, oos, 0.80, 0.70)
    assert not passed and reason == "edge_decay"


def test_zero_vol_fails_closed():
    oos = [gtrade("2024-02-01", 0.05)] * 20
    passed, reason, _, _ = geval(G_IS, oos, 0.0, 0.40)
    assert not passed and reason == "edge_decay"


def test_metrics_carry_raw_and_normalized():
    oos = [gtrade("2024-02-01", 0.025)] * 12 + [gtrade("2024-06-01", -0.01)] * 8
    _, _, metrics, _ = geval(G_IS, oos, 0.80, 0.40)
    assert set(metrics) == {
        "is_edge_per_trade", "oos_edge_per_trade", "edge_decay_pct",
        "mc_p05_equity", "p_ruin", "deflated_sharpe", "sibling_group_n",
        "cost_stress_net_pnl", "trials_n", "registered_n", "trials_sr_var",
        "expected_max_sharpe", "protocol", "is_edge_raw", "oos_edge_raw",
        "is_vol", "oos_vol", "train_sharpe", "pbo"}
    assert metrics["is_edge_per_trade"] == pytest.approx(
        metrics["is_edge_raw"] / metrics["is_vol"])


# ---------------- write-free diagnostic ----------------

DIAG = Path(__file__).resolve().parent.parent / "diagnose_protocol_v2.py"


def test_diagnostic_has_no_write_path():
    # narrow to REGISTRY writes: list.append() is legitimate Python and must
    # not trip this guard
    src = DIAG.read_text(encoding="utf-8")
    for forbidden in ("record_verdict", "record_state_change",
                      "register_strategy", "register_block_type",
                      "register_card", "review_card", "registry.append",
                      "write_text"):
        assert forbidden not in src, f"diagnostic must not write: {forbidden}"


def test_diagnostic_runs_and_reports(tmp_path, capsys):
    import subprocess, sys
    reg, cid, spec = seeded_registry_with_spec(tmp_path)
    # push the spec to graveyard so the diagnostic finds it
    reg.record_state_change(spec["strategy_id"], "screened", "t")
    reg.record_verdict(spec["strategy_id"], "screened", "pass",
                       {"trades": 50, "net_pnl": 0.5, "win_rate": 0.5,
                        "max_dd": -0.1}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "gauntlet", None)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "fail",
                       {"edge_decay_pct": -60.0}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "graveyard", "edge_decay")
    from .test_screen import write_data_dir, dated_target_hit_bars
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    n_before = sum(1 for _ in reg.entries())
    out = subprocess.run(
        [sys.executable, str(DIAG), "--registry", str(reg.log_path),
         "--data-dir", str(data)],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert spec["strategy_id"] in out.stdout
    assert "PROTOCOL-V2 DIAGNOSTIC" in out.stdout
    # and it wrote nothing (count captured before, not hardcoded — the
    # grammar size and hence the entry count change across tasks)
    assert sum(1 for _ in reg.entries()) == n_before
