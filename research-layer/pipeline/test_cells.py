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
