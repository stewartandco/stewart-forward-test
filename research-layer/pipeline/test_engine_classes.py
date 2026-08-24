"""Engine tests for per-session annualisation + short financing (SP4 Track 1
Task 3). Crypto arithmetic must stay byte-identical: spec s10 item 4
(periods_per_year threaded from class config, crypto default 365 unchanged)
and item 5 (short_financing_per_year in the cost model, accrued per bar held
short, absent key -> 0.0 -> crypto path untouched).

Run: python -m pytest pipeline/test_engine_classes.py -q
"""
from __future__ import annotations

import datetime
import math

import pytest

from .engine import realized_ann_vol, run_spec


def mk_bars(closes, start="2024-01-01"):
    """o=h=l=c bars, volume 0, consecutive WEEKDAYS from `start`."""
    d = datetime.date.fromisoformat(start)
    out = []
    i = 0
    while i < len(closes):
        if d.weekday() < 5:
            c = closes[i]
            out.append({"date": d.isoformat(), "open": c, "high": c, "low": c,
                        "close": c, "volume": 0.0})
            i += 1
        d += datetime.timedelta(days=1)
    return out


# ---------------- Test 1: crypto path byte-identical -----------------------

def crypto_closes(n=300):
    return [100.0 + 20.0 * math.sin(i / 15.0) + i * 0.05 for i in range(n)]


def crypto_spec(cost_model=None, session="24x7"):
    return {
        "strategy_id": "regression-crypto",
        "universe": {"assets": ["BTCUSDT"], "asset_class": "crypto",
                     "timeframe": "1d", "session": session},
        "blocks": [
            {"role": "entry", "type": "ma_cross", "params": {"fast": 10, "slow": 30}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.03}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}},
        ],
        "cost_model": cost_model or {"commission_per_side": 0.0005, "slippage_ticks": 0.0002},
    }


# Captured 2026-08-24 by running this EXACT fixture through the pre-Task-3
# engine.py (git 875e1f7, before periods_per_year/financing were added), via
# scratch script capture_baseline.py. cost_model has no financing key and
# session is "24x7", so run_spec derives periods_per_year=365 either way --
# these numbers must reproduce byte-identically after the Task-3 edit.
CAPTURED_TRADES = [
    {'side': 'long', 'entry_date': '2024-02-12', 'entry_px': 119.68594853651364,
     'exit_date': '2024-02-20', 'exit_px': 115.30926361102301, 'exit_reason': 'stop',
     'return_net': -0.03796807652867777, 'notional_frac': 0.6666666666666656, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-04-26', 'entry_px': 91.57466724255357,
     'exit_date': '2024-07-04', 'exit_px': 117.24170071443044, 'exit_reason': 'signal',
     'return_net': 0.27888530427408115, 'notional_frac': 0.666666666666666, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-09-06', 'entry_px': 97.11807513324506,
     'exit_date': '2024-11-13', 'exit_px': 122.22048394860258, 'exit_reason': 'signal',
     'return_net': 0.25707308836091786, 'notional_frac': 0.6666666666666673, 'asset': 'BTCUSDT'},
]
CAPTURED_FINAL_EQUITY = 1.630188407385364


def test_crypto_path_byte_identical():
    bars = mk_bars(crypto_closes(300))
    result = run_spec(crypto_spec(), {"BTCUSDT": bars})
    assert result["trades"] == CAPTURED_TRADES
    assert result["equity"][-1][1] == CAPTURED_FINAL_EQUITY


def test_crypto_path_unaffected_by_financing_key_present():
    # A financing key present in the cost model must not move a long-only
    # book at all: the accrual only fires when the open position is short.
    bars = mk_bars(crypto_closes(300))
    cost_model = {"commission_per_side": 0.0005, "slippage_ticks": 0.0002,
                  "short_financing_per_year": -0.015}
    result = run_spec(crypto_spec(cost_model=cost_model, session="fx_5d"),
                       {"BTCUSDT": bars})
    assert result["trades"] == CAPTURED_TRADES
    assert result["equity"][-1][1] == CAPTURED_FINAL_EQUITY


# ---------------- Test 2: short financing accrues on short bars only -------

def short_closes():
    """20 flat bars (warmup, fast==slow, no signal) then a 30-bar decline
    (fast(5) drops below slow(15) quickly -> one short entry, sustained
    decline keeps state=-1 through the deadline -> no early signal exit)."""
    flat = [100.0] * 20
    decline = [100.0 - 2.0 * (i + 1) for i in range(30)]
    return flat + decline


MAX_BARS = 5  # time_stop deadline -> the one short trade is exactly L=5 bars


def short_spec(cost_model, session="fx_5d"):
    return {
        "strategy_id": "forced-short",
        "universe": {"assets": ["EUR"], "asset_class": "fx",
                     "timeframe": "1d", "session": session},
        "blocks": [
            {"role": "entry", "type": "ma_cross_dense",
             "params": {"fast": 5, "slow": 15, "direction": "short"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.5}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": MAX_BARS}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.05}},
        ],
        "cost_model": cost_model,
    }


def test_short_financing_accrues_only_on_short_bars():
    bars = mk_bars(short_closes())
    no_fin = {"commission_per_side": 0.00005, "slippage_ticks": 0.00010}
    with_fin = dict(no_fin, short_financing_per_year=-0.015)

    r_old = run_spec(short_spec(no_fin), {"EUR": bars})
    r_new = run_spec(short_spec(with_fin), {"EUR": bars})

    assert len(r_old["trades"]) == 1 and len(r_new["trades"]) == 1
    old_t, new_t = r_old["trades"][0], r_new["trades"][0]
    assert old_t["side"] == "short"
    # same fill/exit prices and dates -- only the net return should move
    assert new_t["entry_px"] == old_t["entry_px"]
    assert new_t["exit_px"] == old_t["exit_px"]
    assert new_t["entry_date"] == old_t["entry_date"]
    assert new_t["exit_date"] == old_t["exit_date"]

    expected_delta = (-0.015 / 261) * MAX_BARS
    assert new_t["return_net"] - old_t["return_net"] == pytest.approx(
        expected_delta, abs=1e-12)


def test_long_only_path_with_financing_key_accrues_nothing():
    # ma_cross_dense with direction="long" on a rising series -> a long
    # trade; the financing key must not move it even though it is present.
    closes = [100.0] * 20 + [100.0 + 2.0 * (i + 1) for i in range(30)]
    bars = mk_bars(closes)
    no_fin = {"commission_per_side": 0.00005, "slippage_ticks": 0.00010}
    with_fin = dict(no_fin, short_financing_per_year=-0.015)

    def long_spec(cost_model):
        return {
            "strategy_id": "forced-long",
            "universe": {"assets": ["EUR"], "asset_class": "fx",
                         "timeframe": "1d", "session": "fx_5d"},
            "blocks": [
                {"role": "entry", "type": "ma_cross_dense",
                 "params": {"fast": 5, "slow": 15, "direction": "long"}},
                {"role": "stop", "type": "pct_stop", "params": {"pct": 0.5}},
                {"role": "exit", "type": "time_stop", "params": {"max_bars": MAX_BARS}},
                {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.05}},
            ],
            "cost_model": cost_model,
        }

    r_old = run_spec(long_spec(no_fin), {"EUR": bars})
    r_new = run_spec(long_spec(with_fin), {"EUR": bars})
    assert len(r_old["trades"]) == 1 and len(r_new["trades"]) == 1
    assert r_old["trades"][0]["side"] == "long"
    assert r_new["trades"] == r_old["trades"]
    assert r_new["equity"][-1][1] == r_old["equity"][-1][1]


# ---------------- Test 3: periods_per_year threads into vol -----------------

def test_periods_per_year_threads_into_vol():
    closes = [100.0 * (1.004 ** i) + 3.0 * math.sin(i / 4.0) for i in range(60)]
    n = 10
    base = realized_ann_vol(closes, n)                     # default 365
    scaled = realized_ann_vol(closes, n, periods_per_year=261)
    factor = math.sqrt(261 / 365)
    assert len(base) == len(scaled)
    for b, s in zip(base, scaled):
        if b is None:
            assert s is None
        else:
            assert s == pytest.approx(b * factor)
    # default arg is exactly 365
    assert realized_ann_vol(closes, n) == realized_ann_vol(closes, n, periods_per_year=365)


def test_run_spec_threads_periods_per_year_into_vol_target_sizing():
    # Same fixed_fraction-shaped test but risk=vol_target: sizing depends on
    # realized_ann_vol, which depends on periods_per_year via the spec's
    # universe session -- fx_5d (261) must size differently than 24x7 (365)
    # for an otherwise-identical spec/bars.
    closes = crypto_closes(300)
    bars = mk_bars(closes)

    def vt_spec(session):
        return {
            "strategy_id": "vt-threading",
            "universe": {"assets": ["BTCUSDT"], "asset_class": "crypto",
                         "timeframe": "1d", "session": session},
            "blocks": [
                {"role": "entry", "type": "ma_cross", "params": {"fast": 10, "slow": 30}},
                {"role": "stop", "type": "pct_stop", "params": {"pct": 0.03}},
                {"role": "risk", "type": "vol_target",
                 "params": {"lookback": 20, "ann_vol": 0.02}},
            ],
            "cost_model": {"commission_per_side": 0.0005, "slippage_ticks": 0.0002},
        }

    r_365 = run_spec(vt_spec("24x7"), {"BTCUSDT": bars})
    r_261 = run_spec(vt_spec("fx_5d"), {"BTCUSDT": bars})
    assert len(r_365["trades"]) > 0 and len(r_261["trades"]) > 0
    fracs_365 = [t["notional_frac"] for t in r_365["trades"]]
    fracs_261 = [t["notional_frac"] for t in r_261["trades"]]
    assert fracs_365 != fracs_261
