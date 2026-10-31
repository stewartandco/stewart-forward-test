"""SP5 Phase 2 FREEZE PROOF, rewritten at the activation it was guarding.

Phase 2 did two things and only two things. It DECLARED a 100-asset crypto
grid (600 cells, cells.ASSETS x cells.TIMEFRAMES), and it built a cell-level
ACTIVATION GATE (cells.ACTIVE_CELLS / cells.active_cells) so that an
already-live class's grid can grow without anything sweeping the growth.
Neither is a decision to search. Sweeping a cell moves the trial denominator
(design docs/2026-08-28-market-data-universe-design.md s2/s3), and moving the
denominator is Coen's own reviewed commit -- Phase 3's activation commit --
never a side effect of widening a tuple in cells.py.

REWRITTEN DELIBERATELY by that commit (Switch 1, SP5 Phase 3, build 2b-3
Task 5), as this docstring always said it would be -- not relaxed to make a
red suite green. What it pinned for crypto ("still pooled, zero cells
active, rotation inert") was the state BEFORE the denominator event, and its
subject (the pooled path, the dormant grid, a half-landed Phase 3) no longer
exists. What still holds, and is pinned here:

* the four tradfi classes still sweep their whole declared grids, through the
  per-cell expander, untouched by D6 rotation (their expansion is also pinned
  byte for byte against the pre-switch code in test_switch1.py);
* crypto's DECLARATION is still 600 cells, all import-safe, of which exactly
  76 (at 1d) are ACTIVE;
* crypto rotates now, against the real gate and the real dispatch.

The activated state itself (the 76-asset literal, the routing + benchmark
pair, the deleted legacy constants, the brief) is pinned in test_switch1.py.

Run: python -m pytest pipeline/test_phase2_freeze.py -q
"""
from __future__ import annotations

from . import cells
from . import composer
from . import loop

# Family fixtures, reused rather than reinvented: each class's own composer
# test module already carries the minimal valid family for that class, and a
# freeze proof that invented its own would be proving a shape nothing else
# uses.
from .test_composer_fx import fx_family
from .test_composer_equity import equity_family
from .test_composer_2b import bond_family, metal_family

# The four classes whose gate reads "all"/"all" -- everything except crypto.
TRADFI_CLASSES = ("fx", "equity_etf", "bond_etf", "metal_etf")

TRADFI_FAMILIES = {"fx": fx_family, "equity_etf": equity_family,
                   "bond_etf": bond_family, "metal_etf": metal_family}

_RUN_ID = "p2freeze"
_MODEL = "claude-opus-5"
_CREATED = "2026-08-29T00:00:00Z"

N_ACTIVE_CRYPTO = 76


def _expand(asset_class: str) -> list[dict]:
    """Expand that class's real fixture family through its REAL router.

    expander_for is the routing decision itself, so going through it (rather
    than calling the expander the test expects) means a freeze break in the
    routing shows up here as well as in the routing test.
    """
    fam = TRADFI_FAMILIES[asset_class]()
    expander = composer.expander_for(asset_class)
    assert expander is composer.expand_family_for_class
    return expander(fam, _RUN_ID, _MODEL, _CREATED, asset_class)


# ---------------- 1. tradfi expansion is the declared grid, unchanged ----------------

def test_tradfi_active_set_is_still_the_whole_declared_grid():
    """The gate is a no-op for the four classes that were already sweeping.

    ACTIVE_CELLS gates each tradfi class at "all"/"all", so active_cells must
    return class_cells EXACTLY -- same cells, same ORDER. Order is part of
    the declaration in cells.py (all_cells' fixed order so manifests diff
    cleanly, and D6's rotation cursor reads this list), so list equality
    is the right assertion, not set equality: a reordering is a behaviour
    change even when the membership matches.
    """
    for cls in TRADFI_CLASSES:
        assert cells.active_cells(cls) == cells.class_cells(cls), (
            f"{cls}: the activation gate changed what this class may sweep; "
            f"pre-SP5 it swept its whole declared grid, in declared order")


def test_tradfi_still_routes_to_the_per_cell_expander():
    """...and the gate did not re-route them either."""
    for cls in TRADFI_CLASSES:
        assert composer.expander_for(cls) is composer.expand_family_for_class, (
            f"{cls} left the per-cell expansion path")


# ---------------- 2. nothing sweeps a cell outside its active set ----------------

def test_no_tradfi_class_expands_a_cell_outside_its_active_set():
    """Expand each class's real family and check every spec it produced.

    This is the behavioural half of test 1: it is not enough that
    active_cells() returns the right list, the expander has to actually
    follow it. Every produced spec is one cell, so its (asset, timeframe)
    must be a member of active_cells(cls) -- and the class must produce at
    least one spec, or an expander that silently returned [] would pass a
    membership check vacuously.
    """
    for cls in TRADFI_CLASSES:
        active = set(cells.active_cells(cls))
        specs = _expand(cls)
        assert specs, f"{cls}: expanded to zero specs -- vacuous freeze proof"
        for spec in specs:
            uni = spec["universe"]
            assert len(uni["assets"]) == 1, (
                f"{cls}: per-cell expansion produced a multi-asset spec "
                f"{uni['assets']!r}")
            cell = (uni["assets"][0], uni["timeframe"])
            assert cell in active, (
                f"{cls}: swept {cell!r}, which is not in its active set")


# ---------------- 3. the crypto declaration vs the crypto activation ----------------

def test_crypto_declares_600_cells_and_activates_76():
    """The whole point of the gate, stated as one number pair.

    600 cells DECLARED (100 assets x 6 timeframes) -- which is what admits
    data/import/snapshot work against them. 76 cells ACTIVE (Switch 1: the
    assets with a first daily bar on or before 2022-12-31, at 1d) -- which
    is what a generation is permitted to sweep. A change to the second number
    is Coen's own reviewed commit (Switch 2 grows the timeframes).
    """
    assert len(cells.class_cells("crypto")) == 600, (
        "the declared crypto grid is no longer 100 assets x 6 timeframes")
    active = cells.active_cells("crypto")
    assert len(active) == N_ACTIVE_CRYPTO, (
        "crypto's ACTIVE cell count moved outside a reviewed activation commit "
        "-- the trial denominator has moved")
    assert {tf for _, tf in active} == {"1d"}
    assert set(active) <= set(cells.class_cells("crypto"))


def test_declared_crypto_cells_validate_and_only_the_gated_ones_are_active():
    """The distinction the entire gate exists for, in one test.

    validate_cell is the DECLARATION check: it must accept all 600 cells,
    because declaring them is exactly what makes fetching, snapshotting and
    importing their data legal. active_cells is the ACTIVATION check: 76 of
    them, because sweeping is what moves the denominator. The other 524
    validate and are never swept.
    """
    declared = cells.class_cells("crypto")
    assert len(declared) == 600
    for asset, timeframe in declared:
        assert cells.validate_cell(asset, timeframe) == (asset, timeframe)
        assert cells.validate_cell(asset, timeframe, "crypto") == (asset, timeframe)
    active = cells.active_cells("crypto")
    assert len(active) == N_ACTIVE_CRYPTO < len(declared), (
        "the gate has collapsed into the declaration and Phase 2's whole "
        "distinction is gone")


# ---------------- 4. D6 sweep rotation ----------------

def test_crypto_rotates_over_its_active_set():
    """crypto: the one rotating class, now with 76 active assets.

    The window is ROTATION_SIZE of the active assets, in declared order, and
    `rotates` is True, which is what makes the loop pass --assets."""
    state = {"classes": {}}
    window, rotates = loop._sweep_window(state, "crypto")
    active_assets = [a for a, _ in cells.active_cells("crypto")]
    assert rotates is True
    assert window == active_assets[:loop.ROTATION_SIZE]
    assert len(active_assets) > loop.ROTATION_SIZE


def test_rotation_is_inert_for_every_tradfi_class():
    """The four already-sweeping classes are untouched by D6.

    Each is checked TWICE, because the freeze needs both halves:

    * the window equals the whole active asset list, so `rotates` is False and
      the loop's composer argv carries no --assets -- byte-identical to the
      pre-D6 invocation;
    * asking for that same window through the composer's own subset gate
      (sweep_cells) returns exactly active_cells(), same cells, same ORDER.

    equity_etf is the one that matters most here and is the reason
    ROTATION_CLASSES is a DECLARATION and not "any class bigger than the
    window": its active set is 16 assets, ABOVE ROTATION_SIZE 12. An inferred
    rule would have windowed it 12-of-16 and broken this freeze silently.
    """
    for cls in TRADFI_CLASSES:
        active = [a for a, _ in cells.active_cells(cls)]
        state = {"classes": {}}
        window, rotates = loop._sweep_window(state, cls)
        assert window == active, f"{cls}: the rotation window is not the whole active set"
        assert rotates is False, (
            f"{cls}: D6 started restricting a class that swept its whole "
            f"active set before Phase 2 -- the sweep freeze is broken")
        assert state == {"classes": {}}, (
            f"{cls}: a non-rotating class wrote rotation state")
        assert composer.sweep_cells(cls, window) == cells.active_cells(cls), (
            f"{cls}: routing the window through the composer's subset gate "
            f"changed which cells (or their order) get swept")
    assert len(cells.CLASSES["equity_etf"]["assets"]) > loop.ROTATION_SIZE, (
        "equity_etf no longer exceeds ROTATION_SIZE; the declaration-not-"
        "inference argument above needs re-checking against the new numbers")


def test_the_landed_phase3_rotates_and_the_composer_accepts_the_window():
    """BOTH halves moved, as Phase 3 required in one commit -- now against
    the REAL gate and the REAL dispatch (no monkeypatch: what this test used
    to simulate is what runs).

    Rotation is on, and the window is exactly ROTATION_SIZE of the activated
    assets, in declared order. `composer.sweep_cells` resolves the window to
    CELLS without consulting `expander_for`, so the third assertion proves the
    emitted window addresses real active cells and nothing outside them; that
    the composer accepts the flag end to end is
    test_switch1.py::test_the_crypto_cap_counts_cells (a real composer.run
    with a 12-asset --assets window, rc 0).
    """
    assert composer.expander_for("crypto") is composer.expand_family_for_class
    window, rotates = loop._sweep_window({"classes": {}}, "crypto")
    assert rotates is True, (
        "the landed Phase 3 did not switch rotation on -- the 76-asset "
        "universe would be swept whole every generation, which is the cost "
        "problem D6 exists to solve")
    active_assets = [a for a, _ in cells.active_cells("crypto")]
    assert window == active_assets[:12]
    # the window resolves to real ACTIVE cells and nothing outside them
    assert composer.sweep_cells("crypto", window) == [(a, "1d") for a in window]
