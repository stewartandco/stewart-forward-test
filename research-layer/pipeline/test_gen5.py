"""protocol-v5 tests: the amended PBO gate.

v5 changes ONE gate. The boundary tie counts as a half event, the gate counts
DISTINCT configurations and fails closed below four, and the fixed 0.20/0.50
lines are replaced by a test against each family's own permutation null.
Evidence is chained at registry entry 2511, the protocol itself at 2512.

The live protocol pins (PROTOCOL, FAIL_ORDER, thresholds) live HERE rather than
in test_gen4.py, because they track whatever protocol is current: keeping a
copy in each generation's file would mean one of them silently asserting a
superseded standard.
"""
import pytest

from . import gauntlet as gauntlet_mod
from .gauntlet import (PROTOCOL, FAIL_ORDER, SR_FLOOR, PBO_MIN_DISTINCT,
                       PBO_PASS_PCTILE, PBO_KILL_PCTILE, PBO_NULL_DRAWS,
                       evaluate_spec, run as gauntlet_run)
from .test_gauntlet import (v4_sweep_registry, v4_bars, V4_CUTOFF,
                            write_data_dir, GOOD_IS, GOOD_OOS, STEADY_RETURNS,
                            trade)


def test_protocol_is_v5():
    assert PROTOCOL == "gauntlet-protocol-v5"


def test_fail_order_puts_underpowered_before_the_pbo_test_itself():
    """A family the gate cannot see into is rejected before any attempt to
    judge it, so an uncomputable null can never be read as a lenient one."""
    assert FAIL_ORDER == ("sharpe_floor", "oos_negative", "edge_decay",
                          "mc_p05", "p_ruin", "cost_stress",
                          "pbo_underpowered", "pbo", "plateau")
    assert "dsr" not in FAIL_ORDER


def test_v5_thresholds():
    assert SR_FLOOR == 0.4               # retained from v4 unchanged
    assert PBO_MIN_DISTINCT == 4
    assert PBO_PASS_PCTILE == 0.05
    assert PBO_KILL_PCTILE == 0.95
    assert PBO_NULL_DRAWS == 200


def test_v4s_fixed_pbo_lines_are_withdrawn_not_merely_unused():
    """A constant left lying around gets read again. v5 withdraws both."""
    assert not hasattr(gauntlet_mod, "PBO_PASS")
    assert not hasattr(gauntlet_mod, "PBO_KILL")


# ---------------- the gate battery ----------------

def geval(pbo_status):
    stress = [trade(t["entry_date"], t["return_net"] - 0.001) for t in GOOD_OOS]
    return evaluate_spec(GOOD_IS, GOOD_OOS, stress, STEADY_RETURNS, 1.0, 1.0,
                         4, 0.0001, seed=12345, pbo_status=pbo_status)


def test_an_underpowered_family_fails_and_never_passes():
    """The whole point of amendment 2. v4's own plateau gate settled that a
    gate passing on the absence of evidence is not a gate; the same reasoning
    binds here and in the same direction."""
    passed, reason, _, _ = geval({"verdict": "underpowered", "pbo": None,
                                  "member_pass": False})
    assert not passed and reason == "pbo_underpowered"


def test_a_family_at_the_middle_of_its_own_null_fails():
    """Generation 4's two real candidates sat at the 62nd and 72nd percentile
    of their own nulls. Under v4's fixed line they were convicted as overfit;
    under v5 they simply fail to demonstrate anything, which is the honest
    reading of a value that is unremarkable against its own null."""
    passed, reason, _, _ = geval({"verdict": "fail", "pbo": 0.689,
                                  "percentile": 0.62, "member_pass": False})
    assert not passed and reason == "pbo"


def test_a_family_at_the_bottom_of_its_own_null_passes():
    passed, reason, _, _ = geval({"verdict": "pass", "pbo": 0.03,
                                  "percentile": 0.01, "member_pass": True})
    assert passed and reason is None


def test_a_caller_that_supplies_no_status_is_not_gated():
    """Direct callers written against earlier protocols keep their meaning;
    main() always supplies a status."""
    passed, reason, _, _ = geval(None)
    assert passed and reason is None


def test_the_verdict_records_the_null_it_was_judged_against():
    """An observed PBO cannot be read without its null. A chained verdict that
    carried only the number would force every later reader to recompute one."""
    _, _, metrics, _ = geval({"verdict": "fail", "pbo": 0.689,
                              "percentile": 0.62, "member_pass": False,
                              "n_distinct": 5, "null_p05": 0.21,
                              "null_p95": 0.94, "null_draws": 200})
    assert metrics["pbo"] == 0.689
    assert metrics["pbo_percentile"] == 0.62
    assert metrics["pbo_n_distinct"] == 5
    assert metrics["pbo_null_p05"] == 0.21
    assert metrics["pbo_null_p95"] == 0.94
    assert metrics["pbo_null_draws"] == 200


# ---------------- end to end ----------------

def test_v5_refuses_the_family_whose_swept_axis_did_not_bind(tmp_path, capsys):
    """The hole protocol-v5 closes, demonstrated on protocol-v4's own fixture.

    That fixture registers five siblings sweeping channel_breakout_dense
    .lookback, and 20, 35 and 55 post BYTE-IDENTICAL trades while 75 and 100
    take none: five grid points, two distinct configurations. Under v4 this
    family promoted lookback 35 to quarantine on the strength of a plateau
    made of siblings that were not actually different from each other. Under
    v5 the gate refuses to judge it at all, because there is nothing to select
    among and a low PBO there records only that a tiny difference was
    persistent.

    This is exactly the shape of generation 4's breakout_vol_state_filter,
    which scored the lowest PBO on the chain, 0.030, off five siblings with
    two distinct train curves and 0.999 pairwise correlation.
    """
    reg, by_lb = v4_sweep_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": v4_bars()})
    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art"),
                       "--cutoff", V4_CUTOFF])
    assert rc == 0
    out = capsys.readouterr().out
    assert "(5 configs, 2 distinct)" in out
    assert "-> underpowered" in out
    assert "0 -> quarantine" in out

    reasons = {e["payload"]["strategy_id"]: e["payload"]["reason"]
               for e in reg.entries() if e["entry_type"] == "state_change"
               and e["payload"]["to"] == "graveyard"
               and e["payload"]["from"] == "gauntlet"}
    # The member that v4 promoted is now refused, and refused for the family's
    # defect rather than for anything about itself.
    assert reasons[by_lb[35]] == "pbo_underpowered"
    # A member with an EARLIER failure still keeps its own first one: an
    # underpowered family must not overwrite a more specific reason either.
    assert reasons[by_lb[75]] == "oos_negative"


def test_the_null_is_seeded_off_the_group_so_a_rerun_reproduces_it(
        tmp_path, capsys, monkeypatch):
    """Every stochastic step in this pipeline is seeded off content rather
    than off the clock, so a rerun of the same chain reproduces the same
    verdict. The permutation null is no exception.

    The SAME registry is replayed twice rather than rebuilt twice: the fixture
    stamps its card with datetime.now(), so a rebuild that straddles a second
    boundary yields different strategy ids, a different sibling ordering and
    therefore a different draw sequence. That is fixture nondeterminism, not
    the gate's, and replaying one chain is what the chain actually promises.
    """
    import shutil
    monkeypatch.setattr(gauntlet_mod, "PBO_MIN_DISTINCT", 2)
    source, by_lb = v4_sweep_registry(tmp_path)
    out = []
    for i in range(2):
        d = tmp_path / f"run{i}"
        d.mkdir()
        log = d / "reg.jsonl"
        shutil.copyfile(source.log_path, log)
        from .registry import Registry
        reg = Registry(log)
        data = write_data_dir(d, {"BTCUSD": v4_bars()})
        assert gauntlet_run(["--registry", str(log),
                             "--data-dir", str(data),
                             "--artifacts-dir", str(d / "art"),
                             "--cutoff", V4_CUTOFF,
                             "--pbo-null-draws", "8"]) == 0
        verdict = next(e["payload"] for e in reg.entries()
                       if e["entry_type"] == "verdict"
                       and e["payload"].get("stage") == "gauntlet"
                       and e["payload"]["strategy_id"] == by_lb[35])
        out.append((verdict["metrics"]["pbo"],
                    verdict["metrics"]["pbo_percentile"],
                    verdict["metrics"]["pbo_null_p05"]))
        capsys.readouterr()
    assert out[0] == out[1]
