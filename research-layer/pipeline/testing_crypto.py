"""Test helper: narrow crypto's active cell set for the length of a test.

Build 2b-3 / crypto grid Switch 1. Crypto expands per cell over its ACTIVE
cells (76 assets at 1d since the switch). A test that wants a smaller, known
grid -- one asset, so a family of 9 combos registers 9 specs rather than
9 x 76 -- wraps its body in `active_crypto()`:

    with active_crypto(("BTCUSDT",)):
        specs = composer.expand_family_for_class(fam, run, model, ts, "crypto")

It sets cells.ACTIVE_CELLS["crypto"] to the given assets x timeframes and
puts the real gate back on exit (also when the body raises).

Before the switch this helper had a second half that pointed
composer.expander_for and loop.expander_for at the per-cell expander; Switch
1 made that the real dispatch for every class and it was removed there.

This is a plain module, not a conftest fixture, so a test can import it by
name (`from .testing_crypto import active_crypto`). It is not named test_*.py,
so pytest does not collect it.
"""
from __future__ import annotations

from contextlib import contextmanager

from . import cells


@contextmanager
def active_crypto(assets=("BTCUSDT", "ETHUSDT"), timeframes=("1d",)):
    """Narrow (or reshape) crypto's active cells for the duration of a
    `with` block.

    `assets` / `timeframes` must satisfy the same rules the import-time gate
    check enforces on ACTIVE_CELLS (tuple, subset of the declaration, no
    repeats, declaration order) -- a test that hand-writes an out-of-order
    gate would otherwise pass here and describe a gate the real module could
    never hold.
    """
    gate = {"assets": tuple(assets), "timeframes": tuple(timeframes)}
    cells._assert_gate_axes("crypto", gate)
    cells._assert_gate_both_or_neither("crypto", gate)

    old_gate = cells.ACTIVE_CELLS["crypto"]
    cells.ACTIVE_CELLS["crypto"] = gate
    try:
        yield gate
    finally:
        cells.ACTIVE_CELLS["crypto"] = old_gate
