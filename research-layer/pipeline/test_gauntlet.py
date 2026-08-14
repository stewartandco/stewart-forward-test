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
                             "2023-12-31", {"BTCUSD": "x"})
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


def test_screen_trades_csv_bytes_are_format_stable(tmp_path):
    # the 22 committed artifact bundles' hashes depend on this exact format;
    # a trade carrying notional_frac must produce byte-identical CSV output
    from .screen import write_artifacts
    from .engine import run_spec
    from .test_screen import make_screen_spec
    result = run_spec(make_screen_spec(), {"BTCUSD": target_hit_bars()})
    bundle = write_artifacts(tmp_path, make_screen_spec(), result,
                             "2023-12-31", {"BTCUSD": "x"})
    raw = (bundle / "trades.csv").read_bytes()
    assert b"notional_frac" not in raw
    assert raw.startswith(b"asset,side,entry_date,entry_px,exit_date,"
                          b"exit_px,exit_reason,return_net\n")
