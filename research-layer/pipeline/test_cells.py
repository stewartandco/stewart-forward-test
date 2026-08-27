"""The declared search space. Search-Space-First: this is enumerated once, in
code, and everything else reads it - nothing derives a grid from whatever
happens to be on disk."""
import pytest

from pipeline import cells


def test_the_grid_is_five_assets_by_six_timeframes():
    assert len(cells.ASSETS) == 5
    assert len(cells.TIMEFRAMES) == 6
    assert len(cells.all_cells()) == 30


def test_the_grid_is_declared_in_a_fixed_order():
    """Order is part of the declaration: a run manifest that lists cells in a
    different order every time cannot be diffed."""
    assert cells.all_cells()[0] == ("BTCUSDT", "15m")
    assert cells.all_cells()[-1] == ("BNBUSDT", "1d")


def test_phase_one_excludes_the_two_most_expensive_timeframes():
    """15m is 74.4% of all bars in the grid; 30m is not cached yet."""
    p1 = cells.phase_cells(1)
    assert len(p1) == 20
    assert all(tf in ("1h", "4h", "12h", "1d") for _, tf in p1)


def test_phase_two_is_the_whole_declared_grid():
    assert set(cells.phase_cells(2)) == set(cells.all_cells())


def test_a_cell_names_itself_unambiguously():
    assert cells.cell_id("ETHUSDT", "15m") == "ETHUSDT_15m"


def test_unknown_cells_are_rejected_not_silently_accepted():
    """A typo'd cell must not become a 31st trial nobody declared."""
    with pytest.raises(ValueError):
        cells.validate_cell("DOGEUSDT", "1h")
    with pytest.raises(ValueError):
        cells.validate_cell("BTCUSDT", "3m")


def test_classes_registry_crypto_unchanged():
    c = cells.CLASSES["crypto"]
    assert c["assets"] == cells.ASSETS and c["timeframes"] == cells.TIMEFRAMES
    assert c["session"] == "24x7" and c["periods_per_year"] == 365 and c["bar_kind"] == "ohlcv"
    # a same-day 24x7 feed has no honest lag to declare
    assert c["max_end_lag_days"] == 0
    assert isinstance(c["max_end_lag_days"], int) and c["max_end_lag_days"] >= 0
    # back-compat aliases still the crypto grid, same objects
    assert cells.all_cells() == [(a, tf) for a in cells.ASSETS for tf in cells.TIMEFRAMES]
    assert cells.phase_cells(1) == [(a, tf) for a in cells.ASSETS for tf in cells.PHASE_1_TIMEFRAMES]


def test_fx_class_declared():
    c = cells.CLASSES["fx"]
    assert c["assets"] == ("EUR", "GBP", "AUD", "NZD", "JPY", "CAD", "CHF", "SEK", "NOK", "MXN", "SGD", "ZAR")
    assert c["timeframes"] == ("1d",) and c["session"] == "fx_5d"
    assert c["periods_per_year"] == 261 and c["bar_kind"] == "single_fix"
    assert c["cost_model"]["commission_per_side"] == 0.00005
    assert c["cost_model"]["slippage_ticks"] == 0.00010
    assert c["cost_model"]["short_financing_per_year"] == -0.015
    assert [e[0] for e in c["eras"]] == ["pre_gfc", "gfc_zirp", "tightening", "post_2022"]
    assert cells.class_cells("fx") == [(a, "1d") for a in c["assets"]]
    # FRED H.10 is a WEEKLY release of the prior week's daily fixes, posted
    # Mondays: a fetch just before that release can see up to ~9 calendar
    # days of lag, so 10 is declared as the honest ceiling.
    assert c["max_end_lag_days"] == 10
    assert isinstance(c["max_end_lag_days"], int) and c["max_end_lag_days"] >= 0


def test_live_classes_gates_activation():
    # fx activated 2026-08-24, equity_etf 2026-08-25 (Coen's go each time):
    # activation is the denominator event for a class's cells.
    assert cells.LIVE_CLASSES == ("crypto", "fx", "equity_etf")


def test_validate_cell_class_aware():
    assert cells.validate_cell("BTCUSDT", "1h") == ("BTCUSDT", "1h")          # crypto inferred, unchanged
    assert cells.validate_cell("EUR", "1d") == ("EUR", "1d")                   # fx inferred
    assert cells.validate_cell("EUR", "1d", asset_class="fx") == ("EUR", "1d")
    import pytest
    with pytest.raises(ValueError):
        cells.validate_cell("EUR", "1h")            # fx has no 1h
    with pytest.raises(ValueError):
        cells.validate_cell("EUR", "1d", asset_class="crypto")
    with pytest.raises(ValueError):
        cells.validate_cell("DOGEUSDT", "1d")


def test_class_of_asset():
    assert cells.class_of_asset("EUR") == "fx" and cells.class_of_asset("BTCUSDT") == "crypto"
    # SPY is now a declared equity_etf asset (Track 2a) -- no longer usable as
    # the "undeclared" probe below; QQQ covers the equity_etf membership case.
    assert cells.class_of_asset("SPY") == "equity_etf"
    import pytest
    with pytest.raises(ValueError):
        cells.class_of_asset("TLT")   # bond ETF lane, not declared until Track 2b


def test_unknown_class_and_session_sync():
    import pytest
    with pytest.raises(ValueError, match="not a declared class"):
        cells.class_cells("bond_etf")
    with pytest.raises(ValueError, match="not a declared class"):
        cells.validate_cell("EUR", "1d", asset_class="bond_etf")
    for cls, spec in cells.CLASSES.items():
        assert cells.SESSION_PERIODS[spec["session"]] == spec["periods_per_year"], cls


# ---------------- Track 2a: equity_etf class (declared, not active) ----------------

def test_equity_etf_class_declared():
    c = cells.CLASSES["equity_etf"]
    assert c["assets"] == ("SPY", "QQQ", "IWM", "DIA", "MDY", "EFA", "EEM", "EWJ",
                           "EWG", "EWU", "EWA", "EWC", "EWH", "FXI", "EWZ", "EWY")
    assert len(c["assets"]) == 16
    assert c["timeframes"] == ("1d",) and c["session"] == "us_equity_5d"
    assert c["periods_per_year"] == 252 and c["bar_kind"] == "ohlcv"
    assert c["cost_model"] == {"commission_per_side": 0.00010, "slippage_ticks": 0.00010,
                               "short_financing_per_year": -0.005}
    assert [e[0] for e in c["eras"]] == ["dotcom_gfc", "qe_bull", "covid_cycle", "post_2022"]
    assert c["eras"] == (
        ("dotcom_gfc", "1993-01-01", "2008-12-31"),
        ("qe_bull", "2009-01-01", "2019-12-31"),
        ("covid_cycle", "2020-01-01", "2021-12-31"),
        ("post_2022", "2022-01-01", "9999-12-31"))
    assert c["max_end_lag_days"] == 4
    assert isinstance(c["max_end_lag_days"], int) and c["max_end_lag_days"] >= 0
    assert cells.class_cells("equity_etf") == [(a, "1d") for a in c["assets"]]


def test_equity_etf_declared_and_active():
    # Track 2a ships the class declaration; activation (LIVE_CLASSES gaining
    # "equity_etf") is Coen's call after the dry-run ship-bar step (spec s8),
    # exactly as it was for fx before it.
    assert "equity_etf" in cells.CLASSES
    # activation 2026-08-25 (Coen): the declared-vs-active split is now pinned
    # by test_live_classes_gates_activation; this test keeps the declaration.
    assert "equity_etf" in cells.LIVE_CLASSES


def test_excluded_block_types_declared_per_class():
    # T4-rider-3 (addendum): every class carries its own excluded_block_types
    # rather than composer inferring "any non-crypto class" from asset_class.
    assert cells.CLASSES["crypto"]["excluded_block_types"] == frozenset()
    assert cells.CLASSES["fx"]["excluded_block_types"] == frozenset(
        {"channel_breakout", "channel_breakout_dense", "atr_stop", "atr_stop_dense"})
    # equity_etf carries REAL OHLC bars (bar_kind "ohlcv"), unlike fx's
    # single-fix bars, so no block type is excluded on range grounds.
    assert cells.CLASSES["equity_etf"]["excluded_block_types"] == frozenset()
    for cls, spec in cells.CLASSES.items():
        assert isinstance(spec["excluded_block_types"], frozenset), cls


def test_benchmark_declared_per_class():
    # B1 (SP4 Track 2a addendum, pre-registered 2026-08-26): equity_etf is
    # single-name, long-only-honest, and declares "self"; crypto and fx
    # declare None (no key is ever written on their verdicts). bond/metal
    # declare their own value at 2b, never inherited from equity_etf's here.
    assert cells.CLASSES["crypto"]["benchmark"] is None
    assert cells.CLASSES["fx"]["benchmark"] is None
    assert cells.CLASSES["equity_etf"]["benchmark"] == "self"
    for cls, spec in cells.CLASSES.items():
        assert spec.get("benchmark") in (None, "self"), cls
