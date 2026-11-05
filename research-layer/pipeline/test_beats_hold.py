import math
from datetime import date, timedelta

from . import cells
from .beats_hold import gauntlet_beats_hold, hold_oos_returns

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
