"""protocol-v5 tests: the amended PBO gate.

v5 changes ONE gate. The boundary tie counts as a half event, the gate counts
DISTINCT configurations and fails closed below four, and the fixed 0.20/0.50
lines are replaced by a test against each family's own permutation null.
Evidence is chained at registry entry 2511, the protocol itself at 2512.

SUPERSEDED BY PROTOCOL-V6 (registry entry 2514), which removed the PBO gate
and its family kill from the battery on the principle that every edge is judged
standalone. What v5 CONTRIBUTED survives and is still tested: the half-event
boundary tie, the distinct-configuration count and the permutation null are all
still computed and recorded, and their unit tests live in test_pbo.py. What is
gone is v5's GATING semantics, and the tests asserting those were retired here
rather than left to assert a standard no longer in force. The live protocol
pins moved on to test_gen6.py for the same reason they arrived here from
test_gen4.py.
"""
import pytest

from . import gauntlet as gauntlet_mod
from .gauntlet import (PROTOCOL, FAIL_ORDER, SR_FLOOR, PBO_MIN_DISTINCT,
                       PBO_PASS_PCTILE, PBO_KILL_PCTILE, PBO_NULL_DRAWS,
                       evaluate_spec, run as gauntlet_run)
from .test_gauntlet import (v4_sweep_registry, v4_bars, V4_CUTOFF,
                            write_data_dir, GOOD_IS, GOOD_OOS, STEADY_RETURNS,
                            trade)


def test_v5_thresholds():
    assert SR_FLOOR == 0.4               # retained from v4 unchanged
    assert PBO_MIN_DISTINCT == 4
    assert PBO_PASS_PCTILE == 0.05
    assert PBO_KILL_PCTILE == 0.95
    # 200 -> 50 was a DECLARED parameter change (perf plan P3, Coen 2026-08-26):
    # the null is recorded-not-gated, and per-verdict null_draws carries the value.
    assert PBO_NULL_DRAWS == 50


def test_v4s_fixed_pbo_lines_are_withdrawn_not_merely_unused():
    """A constant left lying around gets read again. v5 withdraws both."""
    assert not hasattr(gauntlet_mod, "PBO_PASS")
    assert not hasattr(gauntlet_mod, "PBO_KILL")


# ---------------- the gate battery ----------------

def geval(pbo_status):
    stress = [trade(t["entry_date"], t["return_net"] - 0.001) for t in GOOD_OOS]
    return evaluate_spec(GOOD_IS, GOOD_OOS, stress, STEADY_RETURNS, 1.0, 1.0,
                         4, 0.0001, seed=12345, pbo_status=pbo_status)


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
