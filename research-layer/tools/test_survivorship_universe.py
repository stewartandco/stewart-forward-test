"""Tests for tools.survivorship_universe. No network: spans are synthetic."""
from __future__ import annotations

import json

import pytest

from tools import survivorship_universe as su


def _row(rank, symbol, stable=False, slug=None, price=5.0):
    return {"rank": rank, "symbol": symbol, "slug": slug or symbol.lower(),
            "stable": stable, "price": price}


OK = ("2020-01-01", "2026-10-31")


def test_walk_stops_at_target_admitted_not_at_rank_100():
    rows, spans = [], {}
    for i in range(1, 121):
        sym = f"C{i:03d}"
        stable = i % 10 == 3 and i <= 93  # 10 stables, ranks 3..93
        rows.append(_row(i, sym, stable=stable))
        spans[sym + "USDT"] = OK
    assert sum(r["stable"] for r in rows) == 10
    admitted, excluded = su.pit_walk(rows, spans)
    assert len(admitted) == 100
    assert admitted[-1] == "C110USDT"
    assert admitted == sorted(admitted)  # rank order == name order here
    assert {e["reason"] for e in excluded} == {"stable"}
    assert len(excluded) == 10


def test_walk_excludes_young_inactive_stable_nopair():
    rows = [_row(1, "YNG"), _row(2, "DED"), _row(3, "STB", stable=True),
            _row(4, "NOP"), _row(5, "GOOD")]
    spans = {"YNGUSDT": ("2023-01-01", "2026-10-31"),
             "DEDUSDT": ("2020-01-01", "2023-12-23"),
             "STBUSDT": OK, "NOPUSDT": None, "GOODUSDT": OK}
    admitted, excluded = su.pit_walk(rows, spans)
    assert admitted == ["GOODUSDT"]
    reasons = {e["symbol"]: e["reason"] for e in excluded}
    assert reasons == {"YNG": "history", "DED": "inactive",
                       "STB": "stable", "NOP": "no_pair"}


def test_walk_boundaries_inclusive():
    rows = [_row(1, "AAA"), _row(2, "BBB")]
    spans = {"AAAUSDT": ("2022-12-31", "2026-10-31"),
             "BBBUSDT": ("2020-01-01", "2023-12-24")}
    admitted, excluded = su.pit_walk(rows, spans)
    assert admitted == ["AAAUSDT", "BBBUSDT"]
    assert excluded == []


def test_walk_raises_on_duplicate_pair():
    rows = [_row(1, "DUP", slug="a"), _row(2, "DUP", slug="b")]
    with pytest.raises(RuntimeError):
        su.pit_walk(rows, {"DUPUSDT": OK})


def test_symbol_map_and_cut_applied():
    assert su.pair_for("ETHDYDX") == "DYDXUSDT"
    assert su.pair_for("btc") == "BTCUSDT"
    assert su.span_for("LUNAUSDT", {"LUNAUSDT": ("2022-05-01", "2022-05-12")}) \
        == ("2022-05-31", "2022-05-12")
    assert su.span_for("LUNAUSDT", {"LUNAUSDT": None}) is None
    rows = [_row(1, "ETHDYDX")]
    admitted, _ = su.pit_walk(rows, {"DYDXUSDT": OK})
    assert admitted == ["DYDXUSDT"]
    # the cut is applied before judging: a later cut makes the pair too young
    rows = [_row(1, "LUNA")]
    admitted, excl = su.pit_walk(
        rows, {"LUNAUSDT": ("2022-05-01", "2026-10-31")}, cuts={"LUNAUSDT": "2023-02-01"})
    assert admitted == [] and excl[0]["reason"] == "history"


def test_stablecoin_by_selector_classifier():
    rows = [_row(1, "USDX", slug="usdx-coin", price=1.0), _row(2, "GOOD")]
    spans = {"USDXUSDT": OK, "GOODUSDT": OK}
    admitted, excluded = su.pit_walk(rows, spans)
    assert admitted == ["GOODUSDT"]
    assert excluded[0]["reason"] == "stable"


def test_strata_partition():
    A = ["P1USDT", "P2USDT", "P3USDT", "P4USDT"]
    B = ["P1USDT", "P2USDT", "MATICUSDT", "XMRUSDT", "NEWUSDT"]
    out = su.strata(A, B)
    assert out["P1USDT"] == "S" and out["P2USDT"] == "S"
    assert out["P3USDT"] == "R" and out["P4USDT"] == "R"
    assert out["MATICUSDT"] == "N"
    assert out["XMRUSDT"] == "D"
    assert out["NEWUSDT"] == "F"
    assert set(out) == set(A) | set(B)
    assert set(out.values()) <= set(su.STRATA)


def test_constants_and_declared_shapes():
    assert (su.CUTOFF, su.END, su.HISTORY_FLOOR, su.ACTIVE_FROM, su.TARGET) == (
        "2023-12-31", "2026-10-31", "2022-12-31", "2023-12-24", 100)
    assert su.STRATA == ("S", "R", "F", "N", "D")
    assert su.SYMBOL_MAP == {"ETHDYDX": "DYDX"}
    assert su.CUTS == {"LUNAUSDT": "2022-05-31"}
    assert su.RENAMES["MKRUSDT"][0] == "SKYUSDT"
    assert su.RENAMES["MKRUSDT"][2] == 24000.0
    assert len(su.RENAMES) == 7
    assert set(su.DELISTED) == {"XMRUSDT", "XEMUSDT", "ELFUSDT", "KDAUSDT", "LRCUSDT"}
    assert all(v[1] is None and v[3] is None for v in su.RENAMES.values())


def test_load_cmc(tmp_path):
    p = tmp_path / "cmc.json"
    p.write_text(json.dumps({"data": [
        {"cmcRank": 2, "symbol": "USDT", "slug": "tether", "tags": ["stablecoin"],
         "quotes": [{"name": "USD", "price": 1.0}]},
        {"cmcRank": 1, "symbol": "BTC", "slug": "bitcoin", "tags": [],
         "quotes": [{"name": "BTC", "price": 1.0}, {"name": "USD", "price": 42000.0}]},
        {"cmcRank": 3, "symbol": "X", "slug": "x", "tags": [], "quotes": []},
    ]}))
    rows = su.load_cmc(p)
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert rows[0]["price"] == 42000.0 and rows[0]["stable"] is False
    assert rows[1]["stable"] is True
    assert rows[2]["price"] is None
    assert set(rows[0]) == {"rank", "symbol", "slug", "stable", "price"}


def test_declared_universe_is_the_active_cells_crypto_assets():
    from pipeline.cells import ACTIVE_CELLS
    assert su.declared_assets() == list(ACTIVE_CELLS["crypto"]["assets"])
