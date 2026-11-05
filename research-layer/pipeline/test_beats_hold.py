import math
import random
from datetime import date, timedelta

import pytest

from . import cells, livegate
from .beats_hold import (forward_hold_series, gauntlet_beats_hold,
                         hold_oos_returns, paired_sharpe_pvalue)

CUT = "2024-01-01"
START = date(2024, 1, 2)


def _ps(cls="crypto"):
    cm = cells.CLASSES[cls]["cost_model"]
    return cm["commission_per_side"] + cm["slippage_ticks"]


def _spec(assets=("BTCUSDT",), cls="crypto"):
    return {"strategy_id": "x",
            "universe": {"asset_class": cls, "assets": list(assets)}}


def _daily(n=40, base=100.0, skip=()):
    rows = []
    for i in range(n):
        if i in skip:
            continue
        d = (START + timedelta(days=i)).isoformat()
        c = base * (1 + 0.01 * i + 0.003 * ((-1) ** i))
        rows.append({"date": d, "open": c * 0.99, "high": c * 1.01,
                     "low": c * 0.98, "close": c})
    return rows


def test_hold_returns_single_asset_compound_to_net_hold():
    rows = _daily()
    rets = hold_oos_returns(_spec(), {"BTCUSDT": rows}, CUT)
    assert len(rets) == 40
    prod = 1.0
    for _, r in rets:
        prod *= 1 + r
    ps = _ps()
    expect = rows[-1]["close"] / rows[0]["open"] * (1 - ps) ** 2
    assert abs((prod - 1) - (expect - 1)) < 1e-12


def test_hold_uses_daily_marks_on_intraday():
    daily = _daily()
    hourly = []
    for r in daily:
        for h in range(24):
            c = r["close"] if h == 23 else r["close"] * (1 + 0.001 * (h - 23))
            o = r["open"] if h == 0 else c
            hourly.append({"date": f"{r['date']} {h:02d}:00:00", "open": o,
                           "high": c, "low": c, "close": c})
    a = hold_oos_returns(_spec(), {"BTCUSDT": hourly}, CUT)
    assert len(a) == 40
    b = hold_oos_returns(_spec(), {"BTCUSDT": daily}, CUT)
    assert [d for d, _ in a] == [d for d, _ in b]
    for (_, x), (_, y) in zip(a, b):
        assert abs(x - y) < 1e-12


def test_hold_multi_asset_is_mean_of_levels_and_drops_incomplete_dates():
    a = _daily(base=100.0)
    b = _daily(base=50.0, skip=(10,))
    spec = _spec(("BTCUSDT", "ETHUSDT"))
    rets = hold_oos_returns(spec, {"BTCUSDT": a, "ETHUSDT": b}, CUT)
    missing = a[10]["date"]
    assert missing not in [d for d, _ in rets]
    assert len(rets) == 39
    ps = _ps()
    la = {r["date"]: r["close"] / a[0]["open"] * (1 - ps) for r in a}
    lb = {r["date"]: r["close"] / b[0]["open"] * (1 - ps) for r in b}
    dates = [r["date"] for r in a if r["date"] != missing]
    lv = [(la[d] + lb[d]) / 2 for d in dates]
    lv[-1] *= (1 - ps)
    prev = 1.0
    for (d, r), want in zip(rets, lv):
        assert abs(r - (want / prev - 1)) < 1e-12
        prev = want


def _setup():
    spec = _spec()
    bars = {"BTCUSDT": _daily()}
    return spec, bars, hold_oos_returns(spec, bars, CUT)


def test_gate_passes_when_strategy_sharpe_beats_hold():
    spec, bars, hold = _setup()
    strat = [(d, r + 0.01 + 0.002 * ((-1) ** i)) for i, (d, r) in enumerate(hold)]
    ok, rec = gauntlet_beats_hold(spec, bars, strat, CUT)
    assert ok is True
    assert rec["strategy_sharpe"] > rec["hold_sharpe"]
    assert rec["n_days"] == 40 and rec["window"] == "oos"


def test_gate_fails_when_it_does_not():
    spec, bars, hold = _setup()
    strat = [(d, r - 0.01 + 0.002 * ((-1) ** i)) for i, (d, r) in enumerate(hold)]
    ok, rec = gauntlet_beats_hold(spec, bars, strat, CUT)
    assert ok is False
    assert rec["strategy_sharpe"] < rec["hold_sharpe"]


def test_gate_pairs_on_date_intersection():
    spec, bars, hold = _setup()
    strat = [(d, 0.001 * (1 + (i % 3))) for i, (d, _) in enumerate(hold)][5:37]
    ok, rec = gauntlet_beats_hold(spec, bars, strat, CUT)
    assert rec["n_days"] == 32
    extra = [("2023-12-31", 0.5)] + strat
    _, rec2 = gauntlet_beats_hold(spec, bars, extra, CUT)
    assert rec2["n_days"] == 32


def test_flat_strategy_fails_gate():
    spec, bars, hold = _setup()
    strat = [(d, 0.0) for d, _ in hold]
    ok, rec = gauntlet_beats_hold(spec, bars, strat, CUT)
    assert ok is False
    assert rec["strategy_sharpe"] is None


def test_class_without_benchmark_is_not_applicable(monkeypatch):
    spec, bars, hold = _setup()
    monkeypatch.setitem(cells.CLASSES["crypto"], "benchmark", None)
    assert gauntlet_beats_hold(
        spec, bars, [(d, 0.01) for d, _ in hold], CUT) == (True, None)


def test_fx_cell_uses_fx_cost_model_and_basis():
    rows = _daily()
    spec = _spec(("EURUSD",), cls="fx")
    bars = {"EURUSD": rows}
    rets = hold_oos_returns(spec, bars, CUT)
    prod = math.prod(1 + r for _, r in rets)
    ps = _ps("fx")
    assert abs(prod - rows[-1]["close"] / rows[0]["open"] * (1 - ps) ** 2) < 1e-12
    strat = [(d, r + 0.001 * ((-1) ** i)) for i, (d, r) in enumerate(rets)]
    _, rec = gauntlet_beats_hold(spec, bars, strat, CUT)
    assert rec["basis"] == "price returns, carry excluded on both sides"


def _with_cutoff_bar(rows, suffix=""):
    cut_bar = {"date": CUT, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    return [dict(r, date=r["date"] + suffix) for r in [cut_bar] + rows]


@pytest.mark.parametrize("suffix", ["", " 00:00:00"])
def test_cutoff_day_bar_excluded_on_both_sides(suffix):
    spec = _spec()
    clean = {"BTCUSDT": _daily()}
    bars = {"BTCUSDT": _with_cutoff_bar(_daily(), suffix)}
    want = hold_oos_returns(spec, clean, CUT)
    assert hold_oos_returns(spec, bars, CUT) == want

    strat = [(d + suffix, 0.001 * (1 + (i % 3))) for i, (d, _) in enumerate(want)]
    _, rec_clean = gauntlet_beats_hold(spec, clean, strat, CUT)
    ok, rec = gauntlet_beats_hold(spec, bars, [(CUT + suffix, 0.5)] + strat, CUT)
    assert rec["n_days"] == 40
    assert rec == rec_clean


def test_empty_oos_returns_empty_and_gate_fails():
    spec = _spec()
    pre = [dict(r, date=(date(2023, 11, 1) + timedelta(days=i)).isoformat())
           for i, r in enumerate(_daily())]
    bars = {"BTCUSDT": pre}
    assert hold_oos_returns(spec, bars, CUT) == []
    strat = [((START + timedelta(days=i)).isoformat(), 0.001 * (i % 3))
             for i in range(40)]
    passed, rec = gauntlet_beats_hold(spec, bars, strat, CUT)
    assert passed is False
    assert rec["n_days"] == 0
    assert rec["hold_sharpe"] is None


# ---- Task 4: forward hold series -------------------------------------------

def _qrow(d, asset, price, equity=1.0):
    return {"strategy_id": "s", "date": d, "asset": asset, "action": "hold",
            "price": price, "position_frac": 0.0, "equity": equity}


def _qrows():
    days = [(START + timedelta(days=i)).isoformat() for i in range(5)]
    rows = []
    for i, d in enumerate(days):
        rows.append(_qrow(d, "BTCUSD", 100.0 * (1 + 0.02 * i), 1 + 0.01 * i))
        if i != 2:
            rows.append(_qrow(d, "ETHUSD", 50.0 * (1 - 0.01 * i), 1 - 0.01 * i))
    # BTCUSD alone on an earlier date: its rebase point, but not a kept date
    early = (START - timedelta(days=1)).isoformat()
    rows.insert(0, _qrow(early, "BTCUSD", 80.0, 1.0))
    return days, early, rows


def test_forward_hold_rebases_at_first_row_and_drops_incomplete_dates():
    days, early, rows = _qrows()
    got = forward_hold_series(rows)
    kept = [d for i, d in enumerate(days) if i != 2]
    assert [d for d, _ in got] == kept
    for (d, v), i in zip(got, [0, 1, 3, 4]):
        btc = 100.0 * (1 + 0.02 * i) / 80.0
        eth = 50.0 * (1 - 0.01 * i) / 50.0
        assert abs(v - (btc + eth) / 2) < 1e-12


def test_forward_hold_dates_match_forward_series():
    _, _, rows = _qrows()
    assert [d for d, _ in forward_hold_series(rows)] ==         [d for d, _ in livegate.forward_series(rows)]


# ---- Task 4: paired Sharpe-difference test ---------------------------------

def _noise(n, sd, seed):
    rng = random.Random(seed)
    return [rng.gauss(0.0, sd) for _ in range(n)]


def test_pvalue_is_deterministic_for_a_seed():
    a, b = _noise(120, 0.01, 1), _noise(120, 0.01, 2)
    assert paired_sharpe_pvalue(a, b, seed=7) == paired_sharpe_pvalue(a, b, seed=7)


def test_pvalue_rejects_a_clear_difference():
    hold = _noise(250, 0.01, 3)
    eps = _noise(250, 0.001, 4)
    strat = [h + 0.002 + e for h, e in zip(hold, eps)]
    assert paired_sharpe_pvalue(strat, hold, seed=11) < 0.05


def test_pvalue_does_not_reject_identical_series():
    hold = _noise(250, 0.01, 5)
    p = paired_sharpe_pvalue(list(hold), hold, seed=11)
    assert p > 0.5
    assert p == 1.0          # se(y) == 0 exactly: cannot reject


def test_pvalue_flat_strategy_is_one():
    hold = _noise(250, 0.01, 6)
    assert paired_sharpe_pvalue([0.0] * 250, hold, seed=1) == 1.0
    assert paired_sharpe_pvalue(hold, [0.0] * 250, seed=1) == 1.0


def test_pvalue_constant_nonzero_series_is_one():
    # 7/997 repeated: float np.var is a tiny POSITIVE number, not 0
    hold = _noise(250, 0.01, 6)
    assert paired_sharpe_pvalue([7 / 997] * 250, hold, seed=1) == 1.0


def test_pvalue_short_record_is_one():
    a, b = _noise(9, 0.01, 7), _noise(9, 0.01, 8)
    assert paired_sharpe_pvalue([x + 0.01 for x in a], b, seed=1, block=5) == 1.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_pvalue_non_finite_statistic_is_one(bad):
    hold = _noise(60, 0.01, 9)
    strat = [h + 0.002 for h in hold]
    strat[17] = bad
    assert paired_sharpe_pvalue(strat, hold, seed=1) == 1.0


def test_pvalue_degenerate_resamples_count_against_rejection():
    # strat is non-zero on one day only: every resample that misses that day
    # has zero variance and contributes t* = +inf, about (3/4)^4 of them here
    hold = _noise(20, 0.01, 10)
    strat = [0.0] * 20
    strat[9] = 0.05
    assert paired_sharpe_pvalue(strat, hold, seed=1, block=5) > 0.25


def test_pvalue_length_mismatch_raises():
    with pytest.raises(ValueError):
        paired_sharpe_pvalue([0.01, 0.02] * 10, [0.01] * 21, seed=1)


def test_pvalue_full_bootstrap_worse_strategy_is_not_rejected():
    # non-degenerate pair, strat's Sharpe clearly BELOW hold's: the one-sided
    # test of SR(strat) > SR(hold) must not reject
    hold = [h + 0.002 for h in _noise(300, 0.01, 12)]
    strat = [h - 0.004 + e for h, e in zip(hold, _noise(300, 0.002, 13))]
    assert paired_sharpe_pvalue(strat, hold, seed=5) > 0.5
