"""Offline tests for the gauntlet battery (no network, no API).

Run: python -m pytest pipeline/test_gauntlet.py -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from .engine import simulate_asset
from .test_screen import breakout_spec_blocks, target_hit_bars, COST


# ---------------- engine notional_frac ----------------

def test_spec_bars_selects_the_spec_s_own_cell():
    """Every run_spec call in the gauntlet must be handed the bars of the
    spec's declared cell. The old code passed `_1d` bars to every spec, so a
    15m strategy would have been judged on daily data while its manifest named
    the 15m cell, and the screen -- cell-aware since Task 6b -- would have
    judged the same spec on different bars. Latent while all 80 registered
    specs were 1d; live the moment gen-4 uses the declared grid.
    """
    from .gauntlet import _spec_bars
    bars_by_cell = {("ETHUSDT", "1d"): [{"date": "daily"}],
                    ("ETHUSDT", "15m"): [{"date": "intraday"}]}

    spec = {"universe": {"assets": ["ETHUSDT"], "timeframe": "15m"}}
    assert _spec_bars(bars_by_cell, spec) == {"ETHUSDT": [{"date": "intraday"}]}

    # a spec with no timeframe is a legacy daily, same rule as the screen's
    legacy = {"universe": {"assets": ["ETHUSDT"]}}
    assert _spec_bars(bars_by_cell, legacy) == {"ETHUSDT": [{"date": "daily"}]}


def test_trades_record_notional_frac():
    book = simulate_asset(breakout_spec_blocks(), target_hit_bars(), COST)
    t = book["trades"][0]
    # f=0.01, stop distance 5% -> notional_frac = 0.2 of equity at entry
    assert t["notional_frac"] == pytest.approx(0.2)


def test_screen_artifacts_still_write(tmp_path):
    # screen's trades.csv has fixed fieldnames; the new key must not break it
    from .screen import write_artifacts
    from .engine import run_spec
    from .test_screen import make_screen_spec
    result = run_spec(make_screen_spec(), {"BTCUSD": target_hit_bars()})
    bundle = write_artifacts(tmp_path, make_screen_spec(), result,
                             "2023-12-31", {"BTCUSD_1d": "x"},
                             {"BTCUSD_1d": "2024-01-05"})
    assert (bundle / "trades.csv").exists()


from .stats import (normal_cdf, inv_normal_cdf, moments, sharpe, percentile)


# ---------------- stats: distributions + moments ----------------

def test_normal_cdf_known_points():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(1.959964) == pytest.approx(0.975, abs=1e-4)
    assert normal_cdf(-2.575829) == pytest.approx(0.005, abs=1e-4)


def test_inv_normal_cdf_known_quantiles():
    assert inv_normal_cdf(0.975) == pytest.approx(1.959964, abs=1e-4)
    assert inv_normal_cdf(0.5) == pytest.approx(0.0, abs=1e-6)
    assert inv_normal_cdf(0.005) == pytest.approx(-2.575829, abs=1e-4)


def test_inv_is_inverse_of_cdf():
    for p in (0.01, 0.1, 0.5, 0.9, 0.99):
        assert normal_cdf(inv_normal_cdf(p)) == pytest.approx(p, abs=1e-6)


def test_moments_hand_case():
    mean, std, skew, kurt = moments([1.0, 2.0, 3.0, 4.0])
    assert mean == pytest.approx(2.5)
    assert std == pytest.approx(math.sqrt(1.25))
    assert skew == pytest.approx(0.0)
    assert kurt == pytest.approx(1.64)


def test_sharpe_and_zero_std():
    assert sharpe([0.01, 0.02, 0.03]) == pytest.approx(
        0.02 / moments([0.01, 0.02, 0.03])[1])
    assert sharpe([0.01, 0.01]) == 0.0


def test_percentile_linear_interpolation():
    xs = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
    assert percentile(xs, 0.5) == pytest.approx(3.0)
    assert percentile(xs, 0.0) == pytest.approx(1.0)
    assert percentile(xs, 1.0) == pytest.approx(5.0)
    assert percentile(xs, 0.25) == pytest.approx(2.0)


from .stats import psr, expected_max_sharpe, bootstrap_paths


# ---------------- stats: PSR / E[max SR] / bootstrap ----------------

def test_psr_hand_case():
    # sr_hat=0.1/period, sr_star=0, T=101, normal returns (skew 0, kurt 3):
    # denom = sqrt(1 + (3-1)/4 * 0.01) = sqrt(1.005); z = 0.1*10/1.00249...
    assert psr(0.1, 0.0, 101, 0.0, 3.0) == pytest.approx(0.8407, abs=0.001)


def test_psr_higher_sr_higher_prob():
    assert psr(0.2, 0.0, 101, 0.0, 3.0) > psr(0.1, 0.0, 101, 0.0, 3.0)


def test_expected_max_sharpe_conventions():
    assert expected_max_sharpe(1, 0.01) == 0.0
    assert expected_max_sharpe(10, 0.0) == 0.0
    e10 = expected_max_sharpe(10, 0.01)
    e100 = expected_max_sharpe(100, 0.01)
    assert 0 < e10 < e100


def test_bootstrap_deterministic_and_ruin():
    contribs = [0.05, -0.02, 0.01, -0.01]
    a = bootstrap_paths(contribs, 200, seed=42)
    b = bootstrap_paths(contribs, 200, seed=42)
    assert a == b
    c = bootstrap_paths(contribs, 200, seed=43)
    assert a != c
    # single always-ruinous trade: every path dips to 0.4 <= 0.5
    r = bootstrap_paths([-0.6], 50, seed=1)
    assert r["p_ruin"] == 1.0
    assert r["terminals"][0] == pytest.approx(0.4)


def test_bootstrap_no_ruin_on_positive_contribs():
    r = bootstrap_paths([0.01, 0.02], 100, seed=7)
    assert r["p_ruin"] == 0.0
    assert all(t > 1.0 for t in r["terminals"])


def test_screen_trades_csv_bytes_are_format_stable(tmp_path):
    # committed trades.csv artifact bytes depend on this exact format;
    # a trade carrying notional_frac must produce byte-identical CSV output
    from .screen import write_artifacts
    from .engine import run_spec
    from .test_screen import make_screen_spec
    result = run_spec(make_screen_spec(), {"BTCUSD": target_hit_bars()})
    bundle = write_artifacts(tmp_path, make_screen_spec(), result,
                             "2023-12-31", {"BTCUSD_1d": "x"},
                             {"BTCUSD_1d": "2024-01-05"})
    raw = (bundle / "trades.csv").read_bytes()
    assert b"notional_frac" not in raw
    assert raw.startswith(b"asset,side,entry_date,entry_px,exit_date,"
                          b"exit_px,exit_reason,return_net\n")


from .gauntlet import (split_trades, contributions, compound, evaluate_spec,
                       DSR_MIN, DECAY_MIN_PCT, MC_P05_MIN, P_RUIN_MAX,
                       SR_FLOOR, PURGE_BARS)


def trade(entry_date, ret, frac=0.2):
    return {"entry_date": entry_date, "return_net": ret, "notional_frac": frac}


GOOD_IS = [trade("2022-01-01", 0.05)] * 30 + [trade("2022-06-01", -0.02)] * 20
GOOD_OOS = [trade("2024-02-01", 0.05)] * 12 + [trade("2024-06-01", -0.02)] * 8
STEADY_RETURNS = [0.001, 0.002, -0.001, 0.0015, 0.001] * 200  # T=1000, SR~high


def eval_with(is_t=GOOD_IS, oos_t=GOOD_OOS, stress_oos=None, returns=None,
              group_n=4, group_var=0.0001):
    if stress_oos is None:
        stress_oos = [trade(t["entry_date"], t["return_net"] - 0.001)
                      for t in oos_t]
    if returns is None:
        returns = STEADY_RETURNS
    # vols of 1.0 make normalized decay == raw decay, preserving these tests'
    # original semantics under protocol-v2
    return evaluate_spec(is_t, oos_t, stress_oos, returns, 1.0, 1.0,
                         group_n, group_var, seed=12345)


# ---------------- battery evaluation ----------------

def test_split_and_contributions():
    all_t = GOOD_IS + GOOD_OOS
    is_t, oos_t = split_trades(all_t, "2023-12-31")
    assert len(is_t) == 50 and len(oos_t) == 20
    assert contributions([trade("x", 0.05, 0.2)]) == [pytest.approx(0.01)]
    assert compound([0.01, 0.01]) == pytest.approx(1.01 * 1.01 - 1)


def test_all_gates_pass():
    passed, reason, metrics, mc = eval_with()
    assert passed and reason is None
    assert set(metrics) == {"is_edge_per_trade", "oos_edge_per_trade",
                            "edge_decay_pct", "mc_p05_equity", "p_ruin",
                            "deflated_sharpe", "sibling_group_n",
                            "cost_stress_net_pnl", "trials_n", "registered_n",
                            "trials_sr_var", "expected_max_sharpe", "protocol",
                            "is_edge_raw", "oos_edge_raw", "is_vol", "oos_vol",
                            "train_sharpe", "pbo",
                            # protocol-v5 records the null alongside the
                            # observed value; see test_gen5.py
                            "pbo_n_distinct", "pbo_percentile",
                            "pbo_null_p05", "pbo_null_p95", "pbo_null_draws",
                            # batch review rider: the PBO verdict label is now
                            # chained too, not just printed (see test_gen5's
                            # test_the_verdict_records_the_null_it_was_judged_against)
                            "pbo_verdict",
                            # protocol-v6 records the plateau outcome too
                            "plateau_ok"}
    assert metrics["sibling_group_n"] == 4


def test_oos_negative_fails_first():
    bad_oos = [trade("2024-02-01", -0.05)] * 20
    passed, reason, metrics, _ = eval_with(oos_t=bad_oos)
    assert not passed and reason == "oos_negative"


def test_edge_decay_fails():
    # OOS positive but per-trade edge decayed far more than 25%
    weak_oos = [trade("2024-02-01", 0.004)] * 20   # edge 0.0008 vs IS 0.0044
    passed, reason, _, _ = eval_with(oos_t=weak_oos)
    assert not passed and reason == "edge_decay"


def test_is_edge_nonpositive_fails_edge_decay():
    flat_is = [trade("2022-01-01", 0.0)] * 50
    passed, reason, _, _ = eval_with(is_t=flat_is)
    assert not passed and reason == "edge_decay"


def test_mc_p05_fails_on_volatile_contribs():
    # violent but net-positive alternation (1.30 x 0.78 = +1.4%/pair): OOS
    # nets positive and edge holds, but the resampled P05 path is deep
    # underwater (log-space sigma ~2.1 over 70 draws)
    wild_is = [trade("2022-01-01", 0.30, 1.0),
               trade("2022-02-01", -0.22, 1.0)] * 25
    wild_oos = [trade("2024-02-01", 0.30, 1.0),
                trade("2024-03-01", -0.22, 1.0)] * 10
    passed, reason, _, _ = eval_with(is_t=wild_is, oos_t=wild_oos)
    assert not passed and reason in ("mc_p05", "p_ruin")  # both legitimate here


def test_weak_dsr_curve_no_longer_gates():
    """protocol-v3: this curve's deflated Sharpe is ~0 and it still passes.
    The five robustness gates are what this stage tests; DSR moved to the
    quarantine -> live gate."""
    noisy = [0.001, -0.001] * 500      # SR ~ 0
    passed, reason, metrics, _ = eval_with(returns=noisy)
    assert passed and reason is None
    assert metrics["deflated_sharpe"] < DSR_MIN


def test_cost_stress_fails():
    fragile_stress = [trade("2024-02-01", -0.001)] * 20
    passed, reason, _, _ = eval_with(stress_oos=fragile_stress)
    assert not passed and reason == "cost_stress"


def test_psr_clamp_fails_closed():
    # inputs violating Pearson's inequality (impossible from moments()) must
    # fail closed, not saturate to an auto-pass
    assert psr(0.5, 0.0, 101, 10.0, 1.0) == 0.0


import csv
import subprocess
import sys

from .registry import Registry
from .gauntlet import (run as gauntlet_run, select_survivors,
                       PROTOCOL as G_PROTOCOL)
from .plateau import qualifies
from .test_screen import (screening_registry, chain_protocol_note,
                          write_data_dir, dated_target_hit_bars)

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent


def run_verifier(log_path):
    return subprocess.run(
        [sys.executable, str(LAYER / "verify_registry.py"), str(log_path)],
        capture_output=True, text=True)


def gauntlet_registry(tmp_path):
    """Registry with one strategy advanced to gauntlet state."""
    reg, spec = screening_registry(tmp_path)
    reg.append("note", {"text": "screen-protocol-v1: test anchor"})
    reg.record_state_change(spec["strategy_id"], "screened", "test")
    reg.record_verdict(spec["strategy_id"], "screened", "pass",
                       {"trades": 50, "net_pnl": 0.5, "win_rate": 0.5,
                        "max_dd": -0.1}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "gauntlet", None)
    return reg, spec


def chain_gauntlet_note(reg):
    reg.append("note", {"text": f"{G_PROTOCOL}: test anchor"})


# ---------------- selection ----------------

LOOKBACK_GRID = [20, 35, 55, 75, 100]


def sib(sid, lookback, score, passed, tc_fail=False):
    return {"sid": sid, "axes": {"lookback": lookback}, "score": score,
            "screen_trade_count_fail": tc_fail, "gauntlet_passed": passed}


def rows_for(family_by_group):
    """The `rows` argument select_survivors still takes but no longer reads —
    built here exactly as main() builds it, so the tests cannot accidentally
    start depending on it."""
    return [{"sid": s["sid"], "group": g, "passed": s["gauntlet_passed"],
             "dsr": s["score"]}
            for g, fam in family_by_group.items() for s in fam]


def test_gauntlet_dry_run_writes_nothing(tmp_path, capsys):
    reg, spec = gauntlet_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    n_before = sum(1 for _ in reg.entries())
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data), "--dry-run"])
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_before
    assert "DRY RUN" in capsys.readouterr().out


def test_gauntlet_detects_orphan(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    chain_gauntlet_note(reg)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "pass",
                       {"x": 1}, "0" * 64)   # verdict but still gauntlet-state
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data), "--dry-run"])
    assert rc == 1


def test_gauntlet_full_run_chains_and_verifies(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    chain_gauntlet_note(reg)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    art = tmp_path / "art"
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(art)])
    assert rc == 0
    states = reg.strategy_states()
    # the test spec has 1 trade, all IS-dated -> oos_negative -> graveyard
    assert states[spec["strategy_id"]] == "graveyard"
    verdicts = [e for e in reg.entries() if e["entry_type"] == "verdict"
                and e["payload"]["stage"] == "gauntlet"]
    assert len(verdicts) == 1
    v = verdicts[0]["payload"]
    assert v["verdict"] == "fail"
    assert set(v["metrics"]) >= {"is_edge_per_trade", "oos_edge_per_trade",
                                 "edge_decay_pct", "mc_p05_equity", "p_ruin",
                                 "deflated_sharpe", "sibling_group_n",
                                 "cost_stress_net_pnl"}
    bundle = art / spec["strategy_id"] / "gauntlet"
    assert (bundle / "oos_trades.csv").exists()
    assert (bundle / "mc_summary.json").exists()
    assert (bundle / "config.json").exists()
    from .screen import bundle_hash
    assert v["artifacts_hash"] == bundle_hash(
        bundle, names=("oos_trades.csv", "mc_summary.json", "config.json"))
    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout


def test_gauntlet_no_gauntlet_strategies(tmp_path, capsys):
    reg, spec = screening_registry(tmp_path)
    chain_gauntlet_note(reg)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data)])
    assert rc == 0
    assert "No strategies" in capsys.readouterr().out


# ---------------- protocol-v4 end to end ----------------
#
# The live funnel is fully resolved (nothing is in `gauntlet` state any more),
# so `python -m pipeline.gauntlet --dry-run` returns before it reaches a single
# line of protocol-v4 code. This synthetic run is therefore the ONLY exercise
# the new paths get: a real sibling sweep over a dense axis, through main(),
# with verdicts and state changes actually chained.

from datetime import datetime, timedelta

from .blocks import BLOCK_TYPES, block_type_payload
from .test_pipeline import make_card

V4_BASE = 100.0
V4_BARS = 1000
V4_START = datetime(2022, 1, 1)
V4_CUTOFF = "2023-12-31"
V4_LOOKBACKS = (20, 35, 55, 75, 100)
# A lone high with no breakout close, placed so it sits inside the lookback-75
# and lookback-100 windows of the first spike but outside the 20/35/55 ones.
# Without it those two long lookbacks would trade the first spike (nothing
# precedes it) and the family would have no genuinely dead long arm.
V4_WICK = 90


def v4_spike_positions():
    """Breakout triples separated by a uniform 60-bar gap.

    A spike fires for lookback L only when the gap back to the previous spike
    is at least L + 3 (the previous triple's 120 high has to fall out of the
    window). 60 >= L + 3 for L in {20, 35, 55}, so those three take every
    spike and post byte-identical trades; 60 < L + 3 for L in {75, 100}, so
    those two take none. This is deliberately a real plateau with a real
    cliff beyond it — protocol-v4's edge_of_grid rule additionally requires
    35 and 55 need registered neighbours on BOTH sides to claim it, and only
    35 has that (its neighbours 20 and 55 are both healthy; 55's high
    neighbour, 75, is dead), so 35 is the sole two-sided-healthy candidate
    even though its point score never distinguishes it from 20 or 55.
    """
    out, p, gap = [], 150, 60
    while p < V4_BARS - 5:
        out.append(p)
        p += gap
    return out


def v4_bars():
    """Flat at 100 with periodic 3-bar breakout triples: signal close at 110,
    entry at the next open (110), then a 120 high that takes the +5% target.
    Every trade is a winner by construction, so the Monte-Carlo and ruin gates
    are satisfied and the sibling differences are pure turnover."""
    bars = []
    for i in range(V4_BARS):
        d = (V4_START + timedelta(days=i)).strftime("%Y-%m-%d")
        bars.append({"date": d, "open": V4_BASE, "high": V4_BASE,
                     "low": V4_BASE, "close": V4_BASE, "volume": 1.0})
    bars[V4_WICK]["high"] = 130.0
    for s in v4_spike_positions():
        bars[s].update(high=110.0, close=110.0)
        bars[s + 1].update(open=110.0, high=110.0, low=110.0, close=110.0)
        bars[s + 2].update(open=110.0, high=120.0, low=110.0, close=V4_BASE)
    return bars


def v4_sweep_registry(tmp_path):
    """One family of five siblings sweeping channel_breakout_dense.lookback,
    all sitting in `gauntlet` state with the protocol-v4 note chained."""
    from .common import content_id
    reg = Registry(tmp_path / "reg.jsonl")
    for key in BLOCK_TYPES:
        reg.register_block_type(block_type_payload(*key))
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    specs = []
    for lb in V4_LOOKBACKS:
        spec = {
            "strategy_id": None, "version": 1,
            "created_utc": "2026-08-17T00:00:00Z",
            "name": f"v4 sweep lb={lb}", "family": "v4_sweep",
            "universe": {"assets": ["BTCUSD"], "asset_class": "crypto",
                         "timeframe": "1d", "session": "24x7"},
            "blocks": [
                {"role": "entry", "type": "channel_breakout_dense",
                 "params": {"lookback": lb, "direction": "long"}},
                {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
                {"role": "target", "type": "r_multiple", "params": {"r": 1.0}},
                {"role": "exit", "type": "time_stop", "params": {"max_bars": 40}},
                {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
            ],
            "provenance": {"card_ids": [card["card_id"]],
                           "parent_strategy_id": None,
                           "sibling_group_id": "v4_sweep-t", "generation": 0},
            "generator": {"agent": "composer", "model": "m",
                          "pipeline_version": "g1.0.0", "run_id": "t"},
            "cost_model": dict(COST),
        }
        spec["strategy_id"] = content_id(spec, "strategy_id")
        reg.register_strategy(spec)
        specs.append(spec)
    reg.append("note", {"text": "screen-protocol-v1: test anchor"})
    for s in specs:
        sid = s["strategy_id"]
        reg.record_state_change(sid, "screened", "test")
        reg.record_verdict(sid, "screened", "pass",
                           {"trades": 18, "net_pnl": 0.18, "win_rate": 1.0,
                            "max_dd": -0.01}, "0" * 64)
        reg.record_state_change(sid, "gauntlet", None)
    chain_gauntlet_note(reg)
    return reg, {lb: s["strategy_id"] for lb, s in zip(V4_LOOKBACKS, specs)}


# ---------------------------------------------------------------------------
# RETIRED BY PROTOCOL-V6 (registry entry 2514), not deleted for convenience.
#
# Six tests lived here asserting one-slot-per-group selection, its tie-break,
# the plateau gate's edge_of_grid disqualification, the protocol-v4 end-to-end
# sweep and the PBO family kill. v6 removed all of those mechanisms from the
# gate battery on the principle that every edge is judged STANDALONE, so each
# test asserted a standard no longer in force. They were retired rather than
# left to fail or, worse, quietly adjusted until they passed against different
# behaviour.
#
# Coverage did not vanish with them. pipeline/test_gen6.py pins the replacement
# invariants: the battery is six standalone gates, a group verdict can no
# longer change an individual's outcome, every gate passer is promoted, and the
# end-to-end run over protocol-v4's own fixture now sends all three live
# siblings to quarantine instead of one. plateau.py and pbo.py keep their unit
# tests in test_plateau.py and test_pbo.py, because both are still COMPUTED and
# RECORDED under v6 -- they simply stopped deciding.
# ---------------------------------------------------------------------------
