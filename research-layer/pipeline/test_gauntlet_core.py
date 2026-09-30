"""gauntlet_core: the standalone six-gate battery (Build 2a, 2026-09-30)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import gauntlet, gauntlet_core
from .test_gauntlet import GOOD_IS, GOOD_OOS, STEADY_RETURNS

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
