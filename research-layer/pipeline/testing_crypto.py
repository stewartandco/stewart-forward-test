"""Test helper: run the post-switch (per-cell) crypto path today.

Build 2b-3 / crypto grid Switch 1. Until the activation commit, crypto is
served by the legacy pooled expander (composer.expand_family) and
cells.ACTIVE_CELLS["crypto"] is empty. A test that wants to exercise crypto
the way it will run AFTER the switch -- one single-asset spec per active cell,
rotation switched on -- wraps its body in `active_crypto()`:

    with active_crypto():                       # BTCUSDT + ETHUSDT at 1d
        specs = composer.expand_family_for_class(fam, run, model, ts, "crypto")

It does two things, and puts both back on exit (also when the body raises):

  1. sets cells.ACTIVE_CELLS["crypto"] to the given assets x timeframes;
  2. points composer.expander_for AND loop.expander_for (loop imports the name
     at module level, so patching composer alone would leave loop on the old
     dispatch) at the per-cell expander for every class.

When the activation commit lands, half 2 is a no-op (expander_for already
returns expand_family_for_class for crypto) and is removed there; half 1 stays.

This is a plain module, not a conftest fixture, so a test can import it by
name (`from .testing_crypto import active_crypto`). It is not named test_*.py,
so pytest does not collect it.
"""
from __future__ import annotations

from contextlib import contextmanager

from . import cells, composer, loop


@contextmanager
def active_crypto(assets=("BTCUSDT", "ETHUSDT"), timeframes=("1d",)):
    """Activate crypto per cell for the duration of a `with` block.

    `assets` / `timeframes` must satisfy the same rules the import-time gate
    check enforces on ACTIVE_CELLS (tuple, subset of the declaration, no
    repeats, declaration order) -- a test that hand-writes an out-of-order
    gate would otherwise pass here and fail on the real commit.
    """
    gate = {"assets": tuple(assets), "timeframes": tuple(timeframes)}
    cells._assert_gate_axes("crypto", gate)
    cells._assert_gate_both_or_neither("crypto", gate)

    old_gate = cells.ACTIVE_CELLS["crypto"]
    old_composer_expander = composer.expander_for
    old_loop_expander = loop.expander_for
    cells.ACTIVE_CELLS["crypto"] = gate
    composer.expander_for = lambda asset_class: composer.expand_family_for_class
    loop.expander_for = composer.expander_for
    try:
        yield gate
    finally:
        cells.ACTIVE_CELLS["crypto"] = old_gate
        composer.expander_for = old_composer_expander
        loop.expander_for = old_loop_expander
