"""Offline tests for Composer gen-3 + gauntlet protocol-v3, rev 2.

No network, no API, no writes outside tmp_path.

Run: python -m pytest pipeline/test_gen3b.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .gauntlet import (PROTOCOL, FAIL_ORDER, DSR_MIN, evaluate_spec)

LAYER = Path(__file__).resolve().parent.parent


def gt(entry_date, ret, frac=0.2):
    return {"entry_date": entry_date, "return_net": ret, "notional_frac": frac}


G_IS = [gt("2022-01-01", 0.05)] * 30 + [gt("2022-06-01", -0.02)] * 20
G_OOS = [gt("2024-02-01", 0.05)] * 12 + [gt("2024-06-01", -0.02)] * 8
STEADY = [0.001, 0.002, -0.001, 0.0015, 0.001] * 200


def geval(trials_n=3, trials_var=0.0001, returns=None, group_n=4,
          registered_n=None):
    """Every robustness gate passes on these fixtures; only the DSR inputs
    vary, so any failure isolates to the retired gate."""
    stress = [gt(t["entry_date"], t["return_net"] - 0.001) for t in G_OOS]
    return evaluate_spec(G_IS, G_OOS, stress,
                         STEADY if returns is None else returns,
                         1.0, 1.0, trials_n, trials_var, seed=12345,
                         group_n=group_n, registered_n=registered_n)


# ---------------- protocol-v3: the DSR gate is gone ----------------

def test_protocol_is_v3():
    assert PROTOCOL == "gauntlet-protocol-v3"


def test_fail_order_excludes_dsr():
    assert FAIL_ORDER == ("oos_negative", "edge_decay", "mc_p05", "p_ruin",
                          "cost_stress")
    assert "dsr" not in FAIL_ORDER


def test_low_dsr_passes_all_five_gates():
    """The whole point of rev 2: a strategy the retired gate would have
    killed outright now passes, because the five robustness gates are what
    this stage is for. trials_n=500 with variance 4.0 drives the hurdle far
    above anything this system produces, so DSR collapses toward zero."""
    passed, reason, metrics, _ = geval(trials_n=500, trials_var=4.0)
    assert passed is True
    assert reason is None
    assert metrics["deflated_sharpe"] < DSR_MIN


def test_metrics_carry_registered_n():
    _, _, metrics, _ = geval(trials_n=3, registered_n=56)
    assert metrics["trials_n"] == 3
    assert metrics["registered_n"] == 56
    assert metrics["sibling_group_n"] == 4


def test_registered_n_defaults_to_trials_n():
    _, _, metrics, _ = geval(trials_n=7, registered_n=None)
    assert metrics["registered_n"] == 7


def test_mc_summary_carries_the_full_cone():
    """Graduation review needs P25-P75; v2 stored only p05/p25/p50."""
    _, _, _, mc = geval()
    assert set(mc) == {"seed", "paths", "p05", "p25", "p50", "p75",
                       "p_ruin", "ruin_level"}
    assert mc["p05"] <= mc["p25"] <= mc["p50"] <= mc["p75"]


def test_dsr_still_ranks_even_though_it_no_longer_gates():
    """DSR remains the sibling-selection statistic, so it must still respond
    to the trial count.

    The comparison uses a SHORT return series on purpose. DSR is a normal CDF
    of z = (sr_hat - sr_star) * sqrt(T - 1) / sqrt(under). Over the full
    T=1000 STEADY series that z is ~+18 at both trial counts, and normal_cdf
    saturates to exactly 1.0 in float64 well before then, so both DSRs come
    back 1.0 and the ordering is unobservable. At T=25 the z values land in
    the CDF's responsive band and the ranking is visible."""
    short = STEADY[:25]
    _, _, few, _ = geval(trials_n=3, returns=short)
    _, _, many, _ = geval(trials_n=56, returns=short)
    assert 0.0 < many["deflated_sharpe"] < few["deflated_sharpe"] < 1.0
