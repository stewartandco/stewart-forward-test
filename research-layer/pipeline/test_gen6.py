"""protocol-v6 tests: every edge standalone.

v6 removes the three mechanisms that decided a strategy's fate on something
other than its own performance -- one-winner-per-group selection, the PBO gate
and its family kill, and the plateau gate -- and keeps all three as recorded
numbers. Chained at registry entry 2514.

The live protocol pins (PROTOCOL, FAIL_ORDER, thresholds) live HERE now, moved
from test_gen5.py for the reason they were moved there from test_gen4.py: they
track whatever protocol is current, and a copy per generation means one of them
silently asserting a superseded standard.
"""
import pytest

from . import gauntlet as gauntlet_mod
from .gauntlet import (PROTOCOL, FAIL_ORDER, SR_FLOOR, DECAY_MIN_PCT,
                       MC_P05_MIN, P_RUIN_MAX, evaluate_spec, select_survivors,
                       run as gauntlet_run)
from .test_gauntlet import (v4_sweep_registry, v4_bars, V4_CUTOFF,
                            write_data_dir, GOOD_IS, GOOD_OOS, STEADY_RETURNS,
                            trade)


def test_protocol_is_v6():
    assert PROTOCOL == "gauntlet-protocol-v6"


def test_the_battery_is_six_gates_and_every_one_is_standalone():
    """v6's founding invariant. Each name here is a property of the strategy
    alone: its own trades, its own returns, its own train Sharpe, its own
    trades re-run under doubled slippage. Nothing reads a sibling."""
    assert FAIL_ORDER == ("sharpe_floor", "oos_negative", "edge_decay",
                          "mc_p05", "p_ruin", "cost_stress")
    for retired in ("pbo", "pbo_underpowered", "plateau", "dsr"):
        assert retired not in FAIL_ORDER


def test_retained_thresholds_did_not_move():
    """v6 removes gates; it tightens or loosens none of the survivors."""
    assert SR_FLOOR == 0.4
    assert DECAY_MIN_PCT == -25.0
    assert MC_P05_MIN == 1.0
    assert P_RUIN_MAX == 0.05


def geval(**kw):
    stress = [trade(t["entry_date"], t["return_net"] - 0.001) for t in GOOD_OOS]
    return evaluate_spec(GOOD_IS, GOOD_OOS, stress, STEADY_RETURNS, 1.0, 1.0,
                         4, 0.0001, seed=12345, **kw)


def test_a_group_verdict_can_no_longer_bury_a_strategy_that_passed():
    """The whole point. Under v5 a family-level PBO result decided an
    individual's fate; under v6 the same inputs cannot change a verdict."""
    passed_clean, reason_clean, _, _ = geval()
    worst = {"verdict": "kill", "pbo": 0.99, "percentile": 0.99,
             "member_pass": False, "n_distinct": 2}
    passed_killed, reason_killed, _, _ = geval(pbo_status=worst,
                                               plateau_ok=False)
    assert passed_clean and reason_clean is None
    assert passed_killed and reason_killed is None, (
        "a group-level input changed a standalone verdict")


def test_pbo_and_plateau_are_still_recorded():
    """Removed as RULES, kept as numbers. A verdict that stopped carrying them
    would destroy the evidence the next protocol argument needs."""
    _, _, metrics, _ = geval(pbo_status={"verdict": "fail", "pbo": 0.689,
                                         "percentile": 0.62, "n_distinct": 5,
                                         "null_p05": 0.11, "null_p95": 0.86,
                                         "null_draws": 200, "member_pass": False},
                             plateau_ok=False)
    assert metrics["pbo"] == 0.689
    assert metrics["pbo_percentile"] == 0.62
    assert metrics["pbo_n_distinct"] == 5
    assert metrics["pbo_null_draws"] == 200
    assert metrics["plateau_ok"] is False


def test_every_gate_passer_is_promoted_not_just_one():
    """Selection is retired. Under v3 the highest DSR won the group; under v4
    and v5 the strongest neighbourhood floor did; 7 strategies passed every
    gate on their own evidence and were graveyarded as sibling_not_selected
    because a similar sibling scored higher. That cannot happen under v6."""
    rows = [{"sid": "a", "group": "g", "passed": True, "dsr": 0.99},
            {"sid": "b", "group": "g", "passed": True, "dsr": 0.98},
            {"sid": "c", "group": "g", "passed": True, "dsr": 0.97},
            {"sid": "d", "group": "g", "passed": False, "dsr": 0.99}]
    quarantine, not_selected = select_survivors(rows, {}, {})
    assert quarantine == {"a", "b", "c"}
    assert not_selected == set(), "sibling_not_selected is retired under v6"


def test_selection_reads_no_group_state_at_all():
    """select_survivors keeps its signature so callers are unchanged, but the
    grids and families it used to consult must no longer alter the outcome."""
    rows = [{"sid": "a", "group": "g", "passed": True, "dsr": 0.5}]
    bare = select_survivors(rows, {}, {})
    loaded = select_survivors(
        rows,
        {"g": {"x.y": [1, 2, 3]}},
        {"g": [{"sid": "a", "axes": {"x.y": 2}, "score": 0.1,
                "screen_trade_count_fail": True, "gauntlet_passed": True}]})
    assert bare == loaded == ({"a"}, set())


def test_v6_end_to_end_promotes_the_whole_plateau(tmp_path, capsys):
    """protocol-v4's own fixture, which v5 refused as underpowered and v4
    promoted exactly one member of. Lookbacks 20, 35 and 55 post identical
    trades and all three clear every standalone gate; 75 and 100 are dead and
    fail oos_negative on their own evidence. Under v6 all three live ones
    reach quarantine, and the two dead ones are buried for their own failure.
    """
    reg, by_lb = v4_sweep_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": v4_bars()})
    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art"),
                       "--cutoff", V4_CUTOFF])
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 -> quarantine" in out
    assert "sibling_not_selected" not in out

    states = {}
    for e in reg.entries():
        if e["entry_type"] == "state_change":
            states[e["payload"]["strategy_id"]] = e["payload"]["to"]
    assert states[by_lb[20]] == "quarantine"
    assert states[by_lb[35]] == "quarantine"
    assert states[by_lb[55]] == "quarantine"

    reasons = {e["payload"]["strategy_id"]: e["payload"]["reason"]
               for e in reg.entries() if e["entry_type"] == "state_change"
               and e["payload"]["to"] == "graveyard"}
    # buried for their OWN failure, never for a family's
    assert reasons[by_lb[75]] == "oos_negative"
    assert reasons[by_lb[100]] == "oos_negative"
