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


def test_live_classes_gates_activation():
    assert cells.LIVE_CLASSES == ("crypto",)      # fx declared, NOT active until the real generation


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
    import pytest
    with pytest.raises(ValueError):
        cells.class_of_asset("SPY")


def test_unknown_class_and_session_sync():
    import pytest
    with pytest.raises(ValueError, match="not a declared class"):
        cells.class_cells("equities")
    with pytest.raises(ValueError, match="not a declared class"):
        cells.validate_cell("EUR", "1d", asset_class="equities")
    for cls, spec in cells.CLASSES.items():
        assert cells.SESSION_PERIODS[spec["session"]] == spec["periods_per_year"], cls
