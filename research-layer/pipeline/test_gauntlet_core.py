"""gauntlet_core: the standalone six-gate battery (Build 2a, 2026-09-30)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import gauntlet, gauntlet_core
from .engine import run_spec
from .test_gauntlet import GOOD_IS, GOOD_OOS, STEADY_RETURNS
from .test_screen import screening_registry, dated_target_hit_bars

LAYER = Path(__file__).resolve().parent.parent


def test_the_core_imports_no_clustering_or_pbo():
    """The worker's verdict path must not be able to compute group
    statistics: protocol-v6 retired every group gate, and the family kill
    survived in code (5 burials) precisely because it was reachable."""
    code = ("import sys; import pipeline.gauntlet_core; "
            "bad = [m for m in ('pipeline.cluster', 'pipeline.pbo') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=LAYER,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_gates_match_evaluate_spec_on_every_gate_field():
    kw = dict(is_vol=0.02, oos_vol=0.02, seed=7, train_sharpe=1.1)
    p1, r1, m1, mc1 = gauntlet_core.evaluate_gates(GOOD_IS, GOOD_OOS, GOOD_OOS, **kw)
    p2, r2, m2, mc2 = gauntlet.evaluate_spec(
        GOOD_IS, GOOD_OOS, GOOD_OOS, STEADY_RETURNS, 0.02, 0.02,
        trials_n=10, trials_sr_var=0.5, seed=7, train_sharpe=1.1)
    assert (p1, r1, mc1) == (p2, r2, mc2)
    for k, v in m1.items():
        if k == "protocol":
            continue
        assert m2[k] == v, k


def test_gates_carry_no_group_statistic():
    _, _, m, _ = gauntlet_core.evaluate_gates(
        GOOD_IS, GOOD_OOS, GOOD_OOS, 0.02, 0.02, 7, 1.1)
    for k in ("deflated_sharpe", "trials_n", "registered_n", "trials_sr_var",
              "expected_max_sharpe", "sibling_group_n", "pbo", "pbo_percentile",
              "pbo_family_kill", "plateau_ok", "haircut"):
        assert k not in m, k
    assert m["protocol"] == gauntlet_core.PROTOCOL_V61


def test_gauntlet_reexports_the_moved_names():
    for name in ("FAIL_ORDER", "split_trades", "contributions", "compound",
                 "window_vol", "stressed", "write_gauntlet_artifacts",
                 "DEFAULT_CUTOFF", "_spec_bars"):
        assert getattr(gauntlet, name) is getattr(gauntlet_core, name), name


# ---- evaluate_standalone, end to end on a one-trade fixture ----------------

FIXTURE_CUTOFF = "2023-01-21"   # the fixture's single trade enters after it (OOS)
GROUP_KEYS = ("haircut", "plateau_ok", "trials_n", "registered_n",
              "deflated_sharpe", "sibling_group_n", "sim_cache",
              "trials_alignment", "pbo", "pbo_family_kill")


def _fixture(tmp_path):
    _, spec = screening_registry(tmp_path)
    bars = {"BTCUSD": dated_target_hit_bars()}
    return spec, gauntlet_core._spec_bars({("BTCUSD", "1d"): bars["BTCUSD"]}, spec)


def test_standalone_result_shape_and_no_group_keys(tmp_path):
    spec, sb = _fixture(tmp_path)
    out = gauntlet_core.evaluate_standalone(spec, sb, FIXTURE_CUTOFF, perturb=False)
    assert set(out) == {"sid", "passed", "reason", "metrics", "mc_summary",
                        "oos_trades"}
    assert out["sid"] == spec["strategy_id"]
    for k in GROUP_KEYS:
        assert k not in out["metrics"], k
    assert out["metrics"]["protocol"] == gauntlet_core.PROTOCOL_V61
    assert out["metrics"]["perturbation"] is None
    assert out["metrics"]["walkforward"]["window"] == "train"
    assert out["metrics"]["regime"]["window"] == "oos"
    assert out["metrics"]["open_at_end"] is False
    assert len(out["oos_trades"]) == 1


def test_standalone_verdict_equals_gates_over_its_own_trades(tmp_path):
    spec, sb = _fixture(tmp_path)
    out = gauntlet_core.evaluate_standalone(spec, sb, FIXTURE_CUTOFF, perturb=False)
    res = run_spec(spec, sb)
    stress = run_spec(gauntlet_core.stressed(spec), sb)
    is_t, oos_t = gauntlet_core.split_trades(res["trades"], FIXTURE_CUTOFF)
    _, stress_oos = gauntlet_core.split_trades(stress["trades"], FIXTURE_CUTOFF)
    assets = spec["universe"]["assets"]
    dated = gauntlet_core.daily_returns_with_dates(res["equity"])
    train_sharpe = gauntlet_core._annualized_sharpe_from_returns(
        [r for d, r in dated if gauntlet_core._date_le(d, FIXTURE_CUTOFF)])
    passed, reason, metrics, mc = gauntlet_core.evaluate_gates(
        is_t, oos_t, stress_oos,
        gauntlet_core.window_vol(sb, assets, "", FIXTURE_CUTOFF),
        gauntlet_core.window_vol(sb, assets, FIXTURE_CUTOFF, "9999-12-31"),
        seed=int(spec["strategy_id"], 16) % (2 ** 31), train_sharpe=train_sharpe)
    assert (out["passed"], out["reason"]) == (passed, reason)
    assert out["mc_summary"] == mc
    assert out["oos_trades"] == oos_t
    for k, v in metrics.items():
        assert out["metrics"][k] == v, k


def test_standalone_seed_is_the_strategy_id_mod_2_31(tmp_path):
    spec, sb = _fixture(tmp_path)
    out = gauntlet_core.evaluate_standalone(spec, sb, FIXTURE_CUTOFF, perturb=False)
    assert out["mc_summary"]["seed"] == int(spec["strategy_id"], 16) % (2 ** 31)
    # a hand-made id with bit 30 set and bits above 31 set, so a wrong modulus
    # (2**30, 2**32) cannot coincide with the right one by luck of the hash
    forced = dict(spec, strategy_id="1" + "f" * 15 + "c0ffee11")
    out = gauntlet_core.evaluate_standalone(forced, sb, FIXTURE_CUTOFF,
                                            perturb=False)
    assert out["mc_summary"]["seed"] == int(forced["strategy_id"], 16) % (2 ** 31)
    assert out["mc_summary"]["seed"] == 0x40ffee11
