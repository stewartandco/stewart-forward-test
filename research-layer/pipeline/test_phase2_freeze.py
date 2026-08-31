"""SP5 Phase 2 FREEZE PROOF: the declaration changed nothing that runs.

Phase 2 did two things and only two things. It DECLARED a 100-asset crypto
grid (600 cells, cells.ASSETS x cells.TIMEFRAMES), and it built a cell-level
ACTIVATION GATE (cells.ACTIVE_CELLS / cells.active_cells) so that an
already-live class's grid can grow without anything sweeping the growth.
Neither is a decision to search. Sweeping a cell moves the trial denominator
(design docs/2026-08-28-market-data-universe-design.md s2/s3), and moving the
denominator is Coen's own reviewed commit -- Phase 3's activation commit --
never a side effect of widening a tuple in cells.py.

This module is the pin on that claim. Every test here asserts that what runs
TODAY is byte-for-byte what ran before Phase 2: crypto still on the legacy
pooled path, the four tradfi classes still sweeping their whole declared
grids, and not one of the 600 declared crypto cells reaching a generation.

A FAILURE HERE MEANS THE FREEZE BROKE. These tests are not a description of
current behaviour to be updated when behaviour changes; they are the guard
that says behaviour must not change until the activation commit. When Phase 3
lands, these tests are REWRITTEN DELIBERATELY as part of that commit, with
Coen's review on the denominator move -- not quietly relaxed to make a red
suite green.

Run: python -m pytest pipeline/test_phase2_freeze.py -q
"""
# GAP CLOSED (P2-T4, 2026-08-31): sweep rotation (design D6) is built, and
# section 7 below is the freeze proof the gap notice promised -- the rotation
# window changes NOTHING for any live class today, both where the active set
# is empty (crypto) and where it is smaller than the window (the four tradfi
# classes).
from __future__ import annotations

from . import cells
from . import composer
from . import loop
from . import loop_state

# Family fixtures, reused rather than reinvented: each class's own composer
# test module already carries the minimal valid family for that class, and a
# freeze proof that invented its own would be proving a shape nothing else
# uses.
from .test_composer import good_family                    # crypto (pooled)
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


# ---------------- 1. crypto is still on the legacy pooled path ----------------

def test_crypto_still_routes_to_the_legacy_pooled_expander():
    """The declaration did not move crypto off expand_family.

    Phase 2 widened CLASSES["crypto"]["assets"] from 5 tickers to 100 and
    declared its cost_model where every other class declares one. It did NOT
    touch the router: crypto still gets expand_family (one multi-asset
    BTCUSD+ETHUSD pooled spec per sweep combo), not expand_family_for_class
    (one single-asset spec per active cell). Phase 3's activation commit is
    what deletes that branch.

    The paired invariant is asserted with it because the two are one fact,
    not two: benchmark:"self" needs exactly one asset per cell, which only
    the per-cell path provides, so `benchmark` MUST still be None for as
    long as the pooled route holds.
    """
    assert composer.expander_for("crypto") is composer.expand_family, (
        "SP5 freeze broken: crypto has left the legacy pooled path outside "
        "Coen's Phase 3 activation commit")
    assert cells.CLASSES["crypto"]["benchmark"] is None, (
        "crypto is still pooled (multi-asset specs) but its benchmark left "
        "None -- every crypto verdict now raises in _benchmark_relative")


# ---------------- 2. tradfi expansion is the declared grid, unchanged ----------------

def test_tradfi_active_set_is_still_the_whole_declared_grid():
    """The gate is a no-op for the four classes that were already sweeping.

    ACTIVE_CELLS gates each tradfi class at "all"/"all", so active_cells must
    return class_cells EXACTLY -- same cells, same ORDER. Order is part of
    the declaration in cells.py (all_cells' fixed order so manifests diff
    cleanly, and D6's rotation cursor will read this list), so list equality
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


# ---------------- 3. nothing sweeps a cell outside its active set ----------------

def test_no_tradfi_class_expands_a_cell_outside_its_active_set():
    """Expand each class's real family and check every spec it produced.

    This is the behavioural half of test 2: it is not enough that
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


def test_crypto_expands_the_legacy_universe_not_the_declared_grid():
    """Crypto's counterpart: it must sweep the OLD pooled universe.

    The declared grid is 100 USDT pairs on six timeframes. The pooled path
    knows nothing about it -- its specs come from composer.ALLOWED_ASSETS
    (BTCUSD/ETHUSD, tickers that are not declared crypto cell assets at all)
    on UNIVERSE_BASE's single "1d" timeframe. That is the pre-SP5 behaviour,
    and it is what must still be true.
    """
    fam = good_family(assets=["BTCUSD", "ETHUSD"])
    specs = composer.expander_for("crypto")(fam, _RUN_ID, _MODEL, _CREATED)
    assert specs, "crypto expanded to zero specs -- vacuous freeze proof"
    allowed = set(composer.ALLOWED_ASSETS)
    for spec in specs:
        uni = spec["universe"]
        assert set(uni["assets"]) <= allowed, (
            f"crypto spec names {uni['assets']!r}, outside the legacy pooled "
            f"universe {composer.ALLOWED_ASSETS!r}")
        assert uni["timeframe"] == "1d", (
            f"crypto spec carries timeframe {uni['timeframe']!r}; the pooled "
            f"path is 1d-only (UNIVERSE_BASE)")


# ---------------- 4. declared-but-inactive crypto cells never reach a generation ----------------

def test_crypto_declares_600_cells_and_activates_none():
    """The whole point of the gate, stated as one number pair.

    600 cells DECLARED (100 assets x 6 timeframes) -- which is what admits
    data/import/snapshot work against them. ZERO cells ACTIVE -- which is
    what a generation is permitted to sweep. Phase 3 is the commit that
    changes the second number, and it is Coen's.
    """
    assert len(cells.class_cells("crypto")) == 600, (
        "the declared crypto grid is no longer 100 assets x 6 timeframes")
    assert cells.active_cells("crypto") == [], (
        "SP5 freeze broken: crypto cells are ACTIVE outside Coen's Phase 3 "
        "activation commit -- the trial denominator has moved")


def test_no_declared_crypto_ticker_reaches_a_generated_spec():
    """No spec off the crypto path names any of the 100 declared tickers.

    Assertion 4a says the gate is empty; this says the empty gate is
    actually load-bearing on the path crypto really runs. The declared grid
    is all *USDT pairs and the pooled universe is *USD, so disjointness is
    checkable directly rather than by ticker-shape guessing.
    """
    declared = set(cells.ASSETS)
    assert all(t.endswith("USDT") for t in declared), (
        "the declared crypto universe is no longer all-USDT; this test's "
        "disjointness check assumed it was")
    fam = good_family(assets=["BTCUSD", "ETHUSD"])
    specs = composer.expander_for("crypto")(fam, _RUN_ID, _MODEL, _CREATED)
    assert specs
    for spec in specs:
        named = set(spec["universe"]["assets"])
        assert not (named & declared), (
            f"a crypto spec names declared-but-inactive cell assets "
            f"{sorted(named & declared)!r} -- the declaration is being swept")


# ---------------- 5. declared cells are import-safe but unswept ----------------

def test_declared_crypto_cells_validate_while_none_are_active():
    """The distinction the entire gate exists for, in one test.

    validate_cell is the DECLARATION check: it must accept all 600 cells,
    because declaring them is exactly what makes fetching, snapshotting and
    importing their data legal. active_cells is the ACTIVATION check: it is
    empty, because sweeping them is what moves the denominator. Both at once
    is the state Phase 2 is supposed to be in -- data work admitted, search
    not.
    """
    declared = cells.class_cells("crypto")
    assert len(declared) == 600
    for asset, timeframe in declared:
        assert cells.validate_cell(asset, timeframe) == (asset, timeframe)
        assert cells.validate_cell(asset, timeframe, "crypto") == (asset, timeframe)
    assert cells.active_cells("crypto") == [], (
        "declared cells validate AND are active -- the gate has collapsed "
        "into the declaration and Phase 2's whole distinction is gone")


# ---------------- 6. Phase 3 is a coupled change ----------------

def test_phase3_benchmark_and_routing_stay_coupled():
    """One assertion, referencing the existing pin rather than re-deriving it.

    test_gauntlet_classes.py carries the BEHAVIOURAL half of this coupling
    (test_crypto_benchmark_and_the_legacy_pooled_path_flip_together: it reads
    the same dispatch and then proves it on a real spec off that path, and
    both directions were mutation-tested at the P2-T3 rider). Importing that
    test here is itself part of the pin -- if the coupling test is deleted or
    renamed, this test raises ImportError and names it, so the behavioural
    half cannot quietly disappear behind a green freeze proof.

    What is asserted here is only the freeze-relevant statement: the two
    halves are in the SAME state today, so Phase 3 has to move both in one
    commit. Half-landing fails differently in each direction: benchmark
    alone makes every crypto verdict raise (loud); routing alone leaves
    benchmark None so no control is written at all, and absence is this
    codebase's declared signal for "not applicable" (silent).
    """
    from .test_gauntlet_classes import (
        test_crypto_benchmark_and_the_legacy_pooled_path_flip_together as _coupling_pin)
    assert callable(_coupling_pin)

    assert ((cells.CLASSES["crypto"]["benchmark"] is None)
            == (composer.expander_for("crypto") is composer.expand_family)), (
        "crypto's benchmark declaration and its composer routing have "
        "half-flipped: they are one commit (SP5 Phase 3), never two")


# ---------------- 7. D6 sweep rotation is machinery, not behaviour ----------------

def test_rotation_is_inert_over_an_empty_active_set():
    """crypto: rotating over zero active cells rotates nothing.

    The window is empty, it EQUALS the (empty) active set -- which is what
    makes the loop pass no --assets at all -- and no rotation_cursor is
    written into loop_state.json for a class that swept nothing.
    """
    assert cells.active_cells("crypto") == [], (
        "this test's premise is gone: crypto has active cells")
    state = {"classes": {}}
    window, rotates = loop._sweep_window(state, "crypto")
    assert window == []
    assert rotates is False, (
        "the loop would pass --assets for crypto, which is still on the "
        "legacy pooled path where --assets has no meaning")
    loop_state.advance_rotation(state, "crypto", 0, loop.ROTATION_SIZE)
    assert state["classes"].get("crypto", {}).get("rotation_cursor") is None


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


def test_a_half_landed_phase3_does_not_emit_a_window_into_a_refusing_composer():
    """P2-T4 review F2: ACTIVE_CELLS filled, expander NOT switched.

    This is the dangerous half-landing for D6, and it is dangerous at RUNTIME
    rather than at import: the cells gate is what rotation reads, the routing
    dispatch is what makes `--assets` legal, and they live in different files.
    Emitting a window into the pooled composer is a guaranteed nonzero exit,
    which the loop maps to stage_failed -- a Sentinel FAIL on every crypto
    fire, three times a day, until a human notices.

    So `rotates` must stay False for as long as crypto is pooled, no matter
    what ACTIVE_CELLS says. The loud failure for a half-landed Phase 3 is the
    NEXT test, at test time, where it belongs.
    """
    gate = dict(cells.ACTIVE_CELLS["crypto"])
    cells.ACTIVE_CELLS["crypto"] = {"assets": cells.ASSETS[:20],
                                    "timeframes": ("1d",)}
    try:
        assert len(cells.active_cells("crypto")) == 20, "premise check"
        assert composer.expander_for("crypto") is composer.expand_family, (
            "premise check: this test simulates the HALF-landed case")
        window, rotates = loop._sweep_window({"classes": {}}, "crypto")
        assert rotates is False, (
            "a half-landed Phase 3 (cells activated, expander still pooled) "
            "emits --assets into a composer that refuses it: stage_failed on "
            "every crypto fire")
    finally:
        cells.ACTIVE_CELLS["crypto"] = gate


def test_a_fully_landed_phase3_rotates_and_the_composer_accepts_the_window(
        monkeypatch):
    """The other side: BOTH halves moved, as Phase 3 requires in one commit.

    Rotation must switch on, the window must be exactly ROTATION_SIZE of the
    activated assets, and the composer's own `--assets` gate must ACCEPT it.
    That last assertion is the one F2 was about: the loop's emit condition and
    the composer's accept condition now read the SAME fact (the routing
    dispatch), so they cannot disagree. If a future edit re-keys either of
    them on the literal string "crypto", this test fails loudly here instead
    of in production.
    """
    gate = dict(cells.ACTIVE_CELLS["crypto"])
    cells.ACTIVE_CELLS["crypto"] = {"assets": cells.ASSETS[:20],
                                    "timeframes": ("1d",)}
    monkeypatch.setattr(composer, "expander_for",
                        lambda cls: composer.expand_family_for_class)
    monkeypatch.setattr(loop, "expander_for", composer.expander_for)
    try:
        window, rotates = loop._sweep_window({"classes": {}}, "crypto")
        assert rotates is True, (
            "a fully-landed Phase 3 did not switch rotation on -- the 100-asset "
            "universe would be swept whole every generation, which is the cost "
            "problem D6 exists to solve")
        assert window == list(cells.ASSETS[:12])
        # the composer's gate agrees: a window off the per-cell path is legal,
        # and names only ACTIVE cells
        assert composer.sweep_cells("crypto", window) == [(a, "1d") for a in window]
    finally:
        cells.ACTIVE_CELLS["crypto"] = gate


def test_no_live_class_rotates_today():
    """One line for the whole claim: the only rotating class has no active
    cells, so not one live generation is scheduled differently today."""
    assert loop.ROTATION_CLASSES == ("crypto",)
    for cls in cells.LIVE_CLASSES:
        state = {"classes": {}}
        _, rotates = loop._sweep_window(state, cls)
        assert rotates is False, f"{cls} rotates -- Phase 2's sweep freeze is broken"
