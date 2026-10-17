"""active_crypto() sets and restores what it claims to.

Run: python -m pytest pipeline/test_testing_crypto.py -q
"""
from __future__ import annotations

import pytest

from . import cells, composer, loop
from .testing_crypto import active_crypto


def test_activates_cells_and_routes_per_cell_then_restores():
    gate, comp_exp, loop_exp = (cells.ACTIVE_CELLS["crypto"], composer.expander_for,
                                loop.expander_for)
    with active_crypto():
        assert cells.active_cells("crypto") == [("BTCUSDT", "1d"), ("ETHUSDT", "1d")]
        assert composer.expander_for("crypto") is composer.expand_family_for_class
        assert loop.expander_for("crypto") is composer.expand_family_for_class
    assert cells.ACTIVE_CELLS["crypto"] is gate
    assert composer.expander_for is comp_exp and loop.expander_for is loop_exp


def test_restores_on_exception():
    gate, comp_exp, loop_exp = (cells.ACTIVE_CELLS["crypto"], composer.expander_for,
                                loop.expander_for)
    with pytest.raises(RuntimeError):
        with active_crypto(("BTCUSDT",)):
            raise RuntimeError("boom")
    assert cells.ACTIVE_CELLS["crypto"] is gate
    assert composer.expander_for is comp_exp and loop.expander_for is loop_exp


@pytest.mark.parametrize("assets", [("ETHUSDT", "BTCUSDT"),       # out of order
                                    ("BTCUSDT", "BTCUSDT"),       # repeat
                                    ("BTCUSD",)])                 # not declared
def test_refuses_a_gate_the_import_check_would_refuse(assets):
    gate, comp_exp = cells.ACTIVE_CELLS["crypto"], composer.expander_for
    with pytest.raises(AssertionError):
        with active_crypto(assets):
            pass
    assert cells.ACTIVE_CELLS["crypto"] is gate and composer.expander_for is comp_exp
