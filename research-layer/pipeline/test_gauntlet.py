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
