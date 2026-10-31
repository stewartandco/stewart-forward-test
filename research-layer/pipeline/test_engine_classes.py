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

from .engine import ENGINE_REV, realized_ann_vol, run_spec


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


# ---------------- Test 4: SP4 Task P4 -- float stdev, same answer ----------
#
# Both `statistics.stdev` call sites in engine.py (the rolling `stdev()`
# window used by zscore_reversion, and `realized_ann_vol`'s window used by
# vol_target sizing) were replaced by a two-pass float sample stdev with
# identical (n-1) semantics. `statistics.stdev` builds every value as an
# exact Fraction and only rounds to float once, at the very end; the
# two-pass float version accumulates ordinary float rounding at each step,
# so results can differ from the pre-edit engine by a few ulps.
#
# Same-answer proof, capture-first: the two fixtures below were run through
# the PRE-EDIT engine (statistics.stdev, no ENGINE_REV constant) on
# 2026-08-27 via a scratch script BEFORE engine.py was touched for SP4 Task
# P4, and the full trades + final equity pinned verbatim as the CAPTURED_*
# constants. The fixtures were chosen to exercise both call sites:
#   * ZSCORE_CRYPTO -- zscore_reversion_dense entry drives the rolling
#     `stdev()` loop (engine.py's line-28 site) on every warm bar.
#   * FX_VOLTARGET -- o=h=l=c bars, vol_target risk (drives
#     `realized_ann_vol`'s stdev, engine.py's line-82 site), fx_5d session
#     (periods_per_year=261), and a short_financing_per_year key present in
#     the cost model (SP4 Track 1 Task 3 path, financing accrual is
#     independent of the stdev change and must stay inert/correct here too).
#
# assert_reproduces_captured compares every float field with abs=1e-9,
# which is many orders of magnitude below any precision this pipeline ever
# records (trades/equity are reported to a handful of significant digits;
# gauntlet-level metrics round far coarser than that) -- a real behaviour
# change would show up as a difference many orders larger than 1e-9, not a
# borderline one. Non-float fields (side, dates, exit_reason, asset) are
# compared exactly, since the edit cannot touch those at all.

def zscore_crypto_spec():
    return {
        "strategy_id": "regression-zscore-crypto",
        "universe": {"assets": ["BTCUSDT"], "asset_class": "crypto",
                     "timeframe": "1d", "session": "24x7"},
        "blocks": [
            {"role": "entry", "type": "zscore_reversion_dense",
             "params": {"lookback": 20, "z_entry": 1.5, "direction": "both"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.03}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}},
        ],
        "cost_model": {"commission_per_side": 0.0005, "slippage_ticks": 0.0002},
    }


def fx_closes(n=300):
    return [1.10 + 0.05 * math.sin(i / 12.0) + i * 0.0003 for i in range(n)]


def fx_voltarget_spec():
    return {
        "strategy_id": "regression-fx-voltarget",
        "universe": {"assets": ["EURUSD"], "asset_class": "fx",
                     "timeframe": "1d", "session": "fx_5d"},
        "blocks": [
            {"role": "entry", "type": "ma_cross_dense",
             "params": {"fast": 8, "slow": 21, "direction": "both"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.02}},
            {"role": "risk", "type": "vol_target",
             "params": {"lookback": 20, "ann_vol": 0.05}},
        ],
        "cost_model": {"commission_per_side": 0.00005, "slippage_ticks": 0.00010,
                        "short_financing_per_year": -0.02},
    }


# Captured 2026-08-27 by running these two fixtures through the pre-P4
# engine.py (statistics.stdev, no ENGINE_REV constant), via a scratch
# capture script, before any P4 edit was made.
ZSCORE_CRYPTO_TRADES = [
    {'side': 'long', 'entry_date': '2024-02-19', 'entry_px': 116.21171763476649, 'exit_date': '2024-02-23', 'exit_px': 112.26002743642928, 'exit_reason': 'stop', 'return_net': -0.03540423192054258, 'notional_frac': 0.6666666666666673, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-02-26', 'entry_px': 111.14545253271623, 'exit_date': '2024-02-29', 'exit_px': 107.57951408324566, 'exit_reason': 'stop', 'return_net': -0.033483529898993655, 'notional_frac': 0.6666666666666672, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-03-01', 'entry_px': 106.33514292581768, 'exit_date': '2024-03-06', 'exit_px': 102.51518452706608, 'exit_reason': 'stop', 'return_net': -0.037323762301391825, 'notional_frac': 0.6666666666666669, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-03-07', 'entry_px': 101.2325171314484, 'exit_date': '2024-03-12', 'exit_px': 97.43917795946338, 'exit_reason': 'stop', 'return_net': -0.03887154846559286, 'notional_frac': 0.666666666666668, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-03-13', 'entry_px': 96.21242152943152, 'exit_date': '2024-03-18', 'exit_px': 92.7244590282331, 'exit_reason': 'stop', 'return_net': -0.03765272543557645, 'notional_frac': 0.6666666666666673, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-03-19', 'entry_px': 91.64386838173435, 'exit_date': '2024-03-22', 'exit_px': 88.71845270180808, 'exit_reason': 'stop', 'return_net': -0.033321564765694076, 'notional_frac': 0.6666666666666675, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-03-25', 'entry_px': 87.86395009384144, 'exit_date': '2024-03-29', 'exit_px': 85.15400991343064, 'exit_reason': 'stop', 'return_net': -0.03224245788536133, 'notional_frac': 0.6666666666666665, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-04-23', 'entry_px': 88.59471024888025, 'exit_date': '2024-04-26', 'exit_px': 91.57466724255357, 'exit_reason': 'stop', 'return_net': -0.03503583429870731, 'notional_frac': 0.6666666666666654, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-04-29', 'entry_px': 92.68603516511382, 'exit_date': '2024-05-02', 'exit_px': 96.308416047247, 'exit_reason': 'stop', 'return_net': -0.04048227248776086, 'notional_frac': 0.6666666666666664, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-05-03', 'entry_px': 97.59482496060807, 'exit_date': '2024-05-08', 'exit_px': 101.61416466285388, 'exit_reason': 'stop', 'return_net': -0.04258394293824634, 'notional_frac': 0.666666666666666, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-05-09', 'entry_px': 102.98821194365007, 'exit_date': '2024-05-14', 'exit_px': 107.13098409700987, 'exit_reason': 'stop', 'return_net': -0.0416256925834048, 'notional_frac': 0.6666666666666653, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-05-15', 'entry_px': 108.49907190349869, 'exit_date': '2024-05-20', 'exit_px': 112.4830246114244, 'exit_reason': 'stop', 'return_net': -0.03811877222571185, 'notional_frac': 0.6666666666666671, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-05-21', 'entry_px': 113.75197638133062, 'exit_date': '2024-05-24', 'exit_px': 117.30608480656049, 'exit_reason': 'stop', 'return_net': -0.03264436636876915, 'notional_frac': 0.6666666666666665, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-05-27', 'entry_px': 118.38973197437578, 'exit_date': '2024-05-31', 'exit_px': 122.09863059100172, 'exit_reason': 'stop', 'return_net': -0.032727874088174166, 'notional_frac': 0.666666666666666, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-06-28', 'entry_px': 121.13794195748227, 'exit_date': '2024-07-04', 'exit_px': 117.24170071443044, 'exit_reason': 'stop', 'return_net': -0.03356367374327156, 'notional_frac': 0.6666666666666677, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-07-05', 'entry_px': 116.13800077794372, 'exit_date': '2024-07-10', 'exit_px': 112.59672463265215, 'exit_reason': 'stop', 'return_net': -0.03189196750047811, 'notional_frac': 0.6666666666666665, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-07-11', 'entry_px': 111.35779828200496, 'exit_date': '2024-07-16', 'exit_px': 107.54550850906715, 'exit_reason': 'stop', 'return_net': -0.0356346008250225, 'notional_frac': 0.6666666666666655, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-07-17', 'entry_px': 106.26247086253007, 'exit_date': '2024-07-22', 'exit_px': 102.45926453739368, 'exit_reason': 'stop', 'return_net': -0.037190682206670364, 'notional_frac': 0.6666666666666655, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-07-23', 'entry_px': 101.22634897204243, 'exit_date': '2024-07-26', 'exit_px': 97.71168105478485, 'exit_reason': 'stop', 'return_net': -0.03612088001739837, 'notional_frac': 0.6666666666666663, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-07-29', 'entry_px': 96.6195777822126, 'exit_date': '2024-08-01', 'exit_px': 93.65250624812916, 'exit_reason': 'stop', 'return_net': -0.03210880252417818, 'notional_frac': 0.6666666666666661, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-08-02', 'entry_px': 92.78195135904068, 'exit_date': '2024-08-09', 'exit_px': 89.49449156774386, 'exit_reason': 'stop', 'return_net': -0.03683210444642675, 'notional_frac': 0.6666666666666665, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-09-02', 'entry_px': 93.0871430752682, 'exit_date': '2024-09-05', 'exit_px': 96.0201754857056, 'exit_reason': 'stop', 'return_net': -0.03290845877895092, 'notional_frac': 0.6666666666666655, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-09-06', 'entry_px': 97.11807513324506, 'exit_date': '2024-09-11', 'exit_px': 100.70740680443694, 'exit_reason': 'stop', 'return_net': -0.03835843092305284, 'notional_frac': 0.6666666666666673, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-09-12', 'entry_px': 101.98541435526343, 'exit_date': '2024-09-17', 'exit_px': 105.98791649103381, 'exit_reason': 'stop', 'return_net': -0.04064582903421636, 'notional_frac': 0.6666666666666661, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-09-18', 'entry_px': 107.35922322006026, 'exit_date': '2024-09-23', 'exit_px': 111.50255971093145, 'exit_reason': 'stop', 'return_net': -0.03999320481835415, 'notional_frac': 0.6666666666666677, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-09-24', 'entry_px': 112.87376150796453, 'exit_date': '2024-09-27', 'exit_px': 116.87564056396943, 'exit_reason': 'stop', 'return_net': -0.036854467030608504, 'notional_frac': 0.6666666666666679, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-09-30', 'entry_px': 118.15334073653281, 'exit_date': '2024-10-03', 'exit_px': 121.74147029414446, 'exit_reason': 'stop', 'return_net': -0.031768413920794034, 'notional_frac': 0.6666666666666675, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2024-10-04', 'entry_px': 122.8388818459387, 'exit_date': '2024-10-10', 'exit_px': 126.61329875249245, 'exit_reason': 'stop', 'return_net': -0.03212656515456984, 'notional_frac': 0.6666666666666657, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-11-07', 'entry_px': 126.06015853893346, 'exit_date': '2024-11-13', 'exit_px': 122.22048394860258, 'exit_reason': 'stop', 'return_net': -0.031859065218016555, 'notional_frac': 0.6666666666666673, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-11-14', 'entry_px': 121.127973777076, 'exit_date': '2024-11-20', 'exit_px': 116.37923728656341, 'exit_reason': 'stop', 'return_net': -0.040604292307012055, 'notional_frac': 0.6666666666666657, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-11-21', 'entry_px': 115.12487424949767, 'exit_date': '2024-11-26', 'exit_px': 111.29265312120903, 'exit_reason': 'stop', 'return_net': -0.03468751630150295, 'notional_frac': 0.6666666666666672, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-11-27', 'entry_px': 110.01186299544636, 'exit_date': '2024-12-02', 'exit_px': 106.2419336666987, 'exit_reason': 'stop', 'return_net': -0.0356683891182145, 'notional_frac': 0.6666666666666655, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-12-03', 'entry_px': 105.02879095047666, 'exit_date': '2024-12-06', 'exit_px': 101.59825600686136, 'exit_reason': 'stop', 'return_net': -0.034062805241972846, 'notional_frac': 0.6666666666666676, 'asset': 'BTCUSDT'},
    {'side': 'long', 'entry_date': '2024-12-09', 'entry_px': 100.5420531550385, 'exit_date': '2024-12-13', 'exit_px': 96.88295842931403, 'exit_reason': 'stop', 'return_net': -0.037793674198019975, 'notional_frac': 0.6666666666666667, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2025-01-10', 'entry_px': 97.58384965751658, 'exit_date': '2025-01-16', 'exit_px': 101.55334355187432, 'exit_reason': 'stop', 'return_net': -0.042077775147108795, 'notional_frac': 0.6666666666666675, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2025-01-17', 'entry_px': 102.69123200355514, 'exit_date': '2025-01-22', 'exit_px': 106.37795867359804, 'exit_reason': 'stop', 'return_net': -0.037301085205748416, 'notional_frac': 0.666666666666668, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2025-01-23', 'entry_px': 107.68042656336138, 'exit_date': '2025-01-28', 'exit_px': 111.73077769838893, 'exit_reason': 'stop', 'return_net': -0.039014553213570766, 'notional_frac': 0.6666666666666677, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2025-01-29', 'entry_px': 113.10928718243267, 'exit_date': '2025-02-03', 'exit_px': 117.24754419325905, 'exit_reason': 'stop', 'return_net': -0.03798635920984832, 'notional_frac': 0.6666666666666667, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2025-02-04', 'entry_px': 118.60818199352703, 'exit_date': '2025-02-07', 'exit_px': 122.55241216223355, 'exit_reason': 'stop', 'return_net': -0.034654283999747834, 'notional_frac': 0.666666666666668, 'asset': 'BTCUSDT'},
    {'side': 'short', 'entry_date': '2025-02-10', 'entry_px': 123.80252836267637, 'exit_date': '2025-02-14', 'exit_px': 128.3392724013627, 'exit_reason': 'stop', 'return_net': -0.03804500312462171, 'notional_frac': 0.6666666666666665, 'asset': 'BTCUSDT'},
]
ZSCORE_CRYPTO_FINAL_EQUITY = 0.3691288926061309

FX_VOLTARGET_TRADES = [
    {'side': 'long', 'entry_date': '2024-01-30', 'entry_px': 1.1554992973436968, 'exit_date': '2024-02-12', 'exit_px': 1.1389236072051978, 'exit_reason': 'signal', 'return_net': -0.014645045623657127, 'notional_frac': 1.0, 'asset': 'EURUSD'},
    {'side': 'long', 'entry_date': '2024-04-02', 'entry_px': 1.0845229837214805, 'exit_date': '2024-05-28', 'exit_px': 1.1596780420824866, 'exit_reason': 'signal', 'return_net': 0.06899780141967635, 'notional_frac': 1.0, 'asset': 'EURUSD'},
    {'side': 'long', 'entry_date': '2024-07-17', 'entry_px': 1.1091435070181077, 'exit_date': '2024-09-10', 'exit_px': 1.1835398561752362, 'exit_reason': 'signal', 'return_net': 0.06677549445710615, 'notional_frac': 1.0, 'asset': 'EURUSD'},
    {'side': 'long', 'entry_date': '2024-10-30', 'entry_px': 1.1304290782834778, 'exit_date': '2024-12-25', 'exit_px': 1.2042699969642303, 'exit_reason': 'signal', 'return_net': 0.06502114229835418, 'notional_frac': 1.0, 'asset': 'EURUSD'},
]
FX_VOLTARGET_FINAL_EQUITY = 1.2250335420553198


def assert_reproduces_captured(actual_trades, actual_final_equity,
                                expected_trades, expected_final_equity):
    assert len(actual_trades) == len(expected_trades)
    for a, e in zip(actual_trades, expected_trades):
        assert set(a) == set(e)
        for k in e:
            if isinstance(e[k], float):
                assert a[k] == pytest.approx(e[k], abs=1e-9), (k, a, e)
            else:
                assert a[k] == e[k], (k, a, e)
    assert actual_final_equity == pytest.approx(expected_final_equity, abs=1e-9)


def test_engine_rev_is_declared():
    # Contract: bumped by hand on ANY change that can alter a simulated
    # number; consumed by pipeline/simcache.py (Task P1) as a cache-key
    # component. This P4 change bumped it to "e2".
    assert ENGINE_REV == "e2"


def test_zscore_reversion_stdev_path_reproduces_pre_p4_capture():
    bars = mk_bars(crypto_closes(300))
    result = run_spec(zscore_crypto_spec(), {"BTCUSDT": bars})
    assert_reproduces_captured(result["trades"], result["equity"][-1][1],
                                ZSCORE_CRYPTO_TRADES, ZSCORE_CRYPTO_FINAL_EQUITY)


def test_fx_voltarget_stdev_path_reproduces_pre_p4_capture():
    bars = mk_bars(fx_closes(300))
    result = run_spec(fx_voltarget_spec(), {"EURUSD": bars})
    assert_reproduces_captured(result["trades"], result["equity"][-1][1],
                                FX_VOLTARGET_TRADES, FX_VOLTARGET_FINAL_EQUITY)
