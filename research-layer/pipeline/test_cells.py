"""The declared search space. Search-Space-First: this is enumerated once, in
code, and everything else reads it - nothing derives a grid from whatever
happens to be on disk."""
import pytest

from pipeline import cells


def test_the_grid_is_one_hundred_assets_by_six_timeframes():
    """SP5 P2-T3: the declared crypto grid is the pinned 100-asset universe.
    DECLARED, not active - ACTIVE_CELLS["crypto"] is still empty, so none of
    these 600 cells is in any trial denominator yet."""
    assert len(cells.ASSETS) == 100
    assert len(cells.TIMEFRAMES) == 6
    assert len(cells.all_cells()) == 600


def test_the_grid_is_declared_in_a_fixed_order():
    """Order is part of the declaration: a run manifest that lists cells in a
    different order every time cannot be diffed. The order is the universe
    manifest's own (market-cap rank at selection), so the first cell is still
    BTC's cheapest timeframe and the last is the 100th admitted asset's 1d."""
    assert cells.all_cells()[0] == ("BTCUSDT", "15m")
    assert cells.all_cells()[-1] == ("GASUSDT", "1d")


def test_phase_one_excludes_the_two_most_expensive_timeframes():
    """15m is 74.4% of all bars in the grid; 30m is not cached yet."""
    p1 = cells.phase_cells(1)
    assert len(p1) == 400
    assert all(tf in ("1h", "4h", "12h", "1d") for _, tf in p1)


def test_phase_two_is_the_whole_declared_grid():
    assert set(cells.phase_cells(2)) == set(cells.all_cells())


def test_a_cell_names_itself_unambiguously():
    assert cells.cell_id("ETHUSDT", "15m") == "ETHUSDT_15m"


def test_unknown_cells_are_rejected_not_silently_accepted():
    """A typo'd cell must not become a 601st trial nobody declared.

    The undeclared-asset probe is BTTUSDT, not DOGEUSDT: DOGE is admitted by
    the pinned universe manifest (P2-T3), so it is now a declared asset. BTT
    is the sharper probe anyway - the manifest EXCLUDED it ("Binance pair
    inactive (delisted)", last 1d bar 2022-01-17, caught by the amended
    active-trading rule) and `data/BTTUSDT_1d.csv` is nonetheless sitting on
    disk. Search-Space-First: on disk is not declared.
    """
    with pytest.raises(ValueError):
        cells.validate_cell("BTTUSDT", "1h")
    with pytest.raises(ValueError):
        cells.validate_cell("BTCUSDT", "3m")


def test_classes_registry_crypto_mirrors_the_declared_grid():
    """crypto's CLASSES entry is the read-only mirror of ASSETS/TIMEFRAMES.
    (Was `test_classes_registry_crypto_unchanged` until P2-T3 - the entry is
    no longer unchanged: assets, cost_model and benchmark all moved. What is
    still true, and what this test is for, is the mirror relationship and the
    back-compat aliases.)"""
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
    # activation is the denominator event for a class's cells. bond_etf/
    # metal_etf are declared at Track 2b but NOT activated -- Coen's call is
    # still pending the dry-run ship-bar step.
    assert cells.LIVE_CLASSES == ("crypto", "fx", "equity_etf", "bond_etf", "metal_etf")
    assert "bond_etf" in cells.LIVE_CLASSES
    assert "metal_etf" in cells.LIVE_CLASSES


# ---------------- SP5 P2-T1: ACTIVE_CELLS (cell-level activation gate) ----------------

def test_active_cells_declares_every_live_class():
    for cls in cells.LIVE_CLASSES:
        assert cls in cells.ACTIVE_CELLS


def test_tradfi_classes_are_fully_active_so_behavior_is_unchanged():
    for cls in ("fx", "equity_etf", "bond_etf", "metal_etf"):
        assert cells.active_cells(cls) == cells.class_cells(cls)


# ---------------- SP5 P2-T3: the 100-asset crypto grid (declared, NOT active) ----------------

def test_crypto_assets_are_the_pinned_universe_manifest():
    """The tuple in cells.py is a LITERAL written from the pinned manifest
    (design s2); the manifest is the PROVENANCE of the declaration, never a
    runtime input. This test is the only thing keeping the two in step."""
    import json
    import pathlib
    man = json.loads((pathlib.Path(__file__).resolve().parent.parent
                      / "data" / "crypto_universe_manifest.json").read_text(encoding="utf-8"))
    assert list(cells.CLASSES["crypto"]["assets"]) == [
        a["binance_symbol"] for a in man["admitted"]]


def test_crypto_declares_a_cost_model_and_defers_its_benchmark():
    """The cost_model half of the design's s4 migration lands here; the
    benchmark half does NOT. Flipping benchmark to "self" while the legacy
    pooled path still builds 2-asset BTCUSD+ETHUSD specs makes every crypto
    verdict raise in gauntlet._benchmark_relative, so it rides with the
    Phase 3 routing change instead (design s4/s7 corrected accordingly).
    The both-or-neither coupling is pinned in test_gauntlet_classes.py."""
    spec = cells.CLASSES["crypto"]
    assert spec["cost_model"] == {"commission_per_side": 0.001,
                                  "slippage_ticks": 0.0005}
    assert spec["benchmark"] is None


def test_crypto_declared_grid_is_100_assets_by_6_timeframes():
    assert len(cells.CLASSES["crypto"]["assets"]) == 100
    assert len(cells.CLASSES["crypto"]["timeframes"]) == 6
    assert len(cells.class_cells("crypto")) == 600


def test_crypto_active_set_is_empty_until_activation():
    # Phase 2 declares; Phase 3 (Coen's own commit) activates. The empty
    # active set is what keeps the legacy pooled path serving crypto.
    assert cells.active_cells("crypto") == []


def test_active_cells_is_a_subset_of_the_declared_grid():
    for cls in cells.LIVE_CLASSES:
        assert set(cells.active_cells(cls)) <= set(cells.class_cells(cls))


def test_a_partial_gate_restricts_BOTH_axes(monkeypatch):
    # P2-T1 rider R1: every other test on this gate uses "all"/"all" or
    # crypto's empty tuple, so a timeframes-BLIND active_cells() passed the
    # whole file. Phase 3 activates 95 assets x 1d out of a 6-timeframe
    # declared grid: a blind gate would sweep 570 cells, a 6x denominator
    # blowout shipped green. This is the only test that would catch it.
    monkeypatch.setitem(cells.ACTIVE_CELLS, "crypto",
                        {"assets": ("BTCUSDT", "ETHUSDT"), "timeframes": ("1h",)})
    assert cells.active_cells("crypto") == [("BTCUSDT", "1h"), ("ETHUSDT", "1h")]


def test_validate_cell_still_accepts_the_whole_declared_grid():
    # declaration admits data/import work; activation admits sweeping
    for asset, tf in cells.class_cells("crypto"):
        cells.validate_cell(asset, tf)


def test_active_cells_refuses_a_declared_class_with_no_active_entry(monkeypatch):
    # Declare-then-activate is a legal intermediate state for a FUTURE class
    # (only LIVE_CLASSES members are import-time required to have an entry).
    # Reaching it must fail loudly like every other unknown-class lookup in
    # this module, not with a bare KeyError.
    import pytest
    monkeypatch.setitem(cells.CLASSES, "commodity_future",
                        dict(cells.CLASSES["equity_etf"], assets=("USO",)))
    assert cells.class_cells("commodity_future") == [("USO", "1d")]   # declared
    with pytest.raises(ValueError, match="ACTIVE_CELLS"):
        cells.active_cells("commodity_future")                        # not active


# The gate's import-time shape rules are exercised through the two private
# checkers the assertion block calls (cells._assert_gate_axes /
# _assert_gate_both_or_neither) rather than re-implemented here: a test that
# restates the rule cannot catch a rule that was never wired in.

def test_the_shipped_gate_obeys_its_own_import_time_rules():
    for cls, gate in cells.ACTIVE_CELLS.items():
        cells._assert_gate_axes(cls, gate)
    for cls in cells.LIVE_CLASSES:
        cells._assert_gate_both_or_neither(cls, cells.ACTIVE_CELLS[cls])


def test_a_gate_axis_may_not_repeat_a_value():
    # R2: set(_sub) <= set(declared) accepts ("BTCUSDT", "BTCUSDT"), which
    # yields a DUPLICATED cell - swept twice, counted twice in the trial
    # denominator. Hand-writing ~95 tickers is exactly where this happens.
    with pytest.raises(AssertionError, match="repeats"):
        cells._assert_gate_axes("crypto", {"assets": ("BTCUSDT", "BTCUSDT"),
                                           "timeframes": ("1h",)})


def test_a_gate_axis_must_be_in_declaration_order():
    # R2: a transposed tuple also passes the set-subset check, and then
    # active_cells() returns gate order while class_cells() returns declared
    # order. Order is part of the declaration (test_the_grid_is_declared_in_
    # a_fixed_order above) and D6's rotation cursor reads this list.
    with pytest.raises(AssertionError, match="declaration order"):
        cells._assert_gate_axes("crypto", {"assets": ("ETHUSDT", "BTCUSDT"),
                                           "timeframes": ("1h",)})
    # the same values in declared order are fine
    cells._assert_gate_axes("crypto", {"assets": ("BTCUSDT", "ETHUSDT"),
                                       "timeframes": ("1h",)})


def test_a_gate_missing_an_axis_key_names_the_class_not_a_bare_KeyError():
    # R4: _gate[_key] used to raise KeyError: 'timeframes' at IMPORT, with
    # nothing saying which class's entry was malformed.
    with pytest.raises(AssertionError, match=r"ACTIVE_CELLS\['crypto'\]\['timeframes'\]"):
        cells._assert_gate_axes("crypto", {"assets": ("BTCUSDT",)})


def test_a_half_filled_gate_is_refused_at_import():
    # R3: {"assets": (95 tickers), "timeframes": ()} imports clean and sweeps
    # ZERO cells - an activation commit that fills one axis and forgets the
    # other is a silent no-op, and "activation shipped" gets claimed against
    # a gate that activated nothing. Both-or-neither costs nothing because
    # crypto's legitimate resting state is ((), ()).
    with pytest.raises(AssertionError, match="both-or-neither"):
        cells._assert_gate_both_or_neither(
            "crypto", {"assets": cells.ASSETS, "timeframes": ()})
    with pytest.raises(AssertionError, match="both-or-neither"):
        cells._assert_gate_both_or_neither(
            "crypto", {"assets": (), "timeframes": ("1d",)})
    # both legal resting/active states pass
    cells._assert_gate_both_or_neither("crypto", {"assets": (), "timeframes": ()})
    cells._assert_gate_both_or_neither("fx", {"assets": "all", "timeframes": "all"})


def test_a_half_filled_gate_really_would_sweep_nothing(monkeypatch):
    # the defect R3 guards against, demonstrated: one axis filled, no cells.
    monkeypatch.setitem(cells.ACTIVE_CELLS, "crypto",
                        {"assets": cells.ASSETS, "timeframes": ()})
    assert cells.active_cells("crypto") == []


def test_validate_cell_class_aware():
    assert cells.validate_cell("BTCUSDT", "1h") == ("BTCUSDT", "1h")          # crypto inferred, unchanged
    assert cells.validate_cell("EUR", "1d") == ("EUR", "1d")                   # fx inferred
    assert cells.validate_cell("EUR", "1d", asset_class="fx") == ("EUR", "1d")
    import pytest
    with pytest.raises(ValueError):
        cells.validate_cell("EUR", "1h")            # fx has no 1h
    with pytest.raises(ValueError):
        cells.validate_cell("EUR", "1d", asset_class="crypto")
    # DOGEUSDT is now a DECLARED crypto asset (P2-T3's pinned universe), so it
    # no longer works as the undeclared probe -- BTTUSDT, which the manifest
    # excluded as a delisted pair, does.
    with pytest.raises(ValueError):
        cells.validate_cell("BTTUSDT", "1d")


def test_class_of_asset():
    assert cells.class_of_asset("EUR") == "fx" and cells.class_of_asset("BTCUSDT") == "crypto"
    # SPY is now a declared equity_etf asset (Track 2a) -- no longer usable as
    # the "undeclared" probe below; QQQ covers the equity_etf membership case.
    assert cells.class_of_asset("SPY") == "equity_etf"
    # TLT is now a declared bond_etf asset (Track 2b) -- no longer usable as
    # the "undeclared" probe either; GLD covers the metal_etf membership case.
    assert cells.class_of_asset("TLT") == "bond_etf"
    assert cells.class_of_asset("GLD") == "metal_etf"
    import pytest
    with pytest.raises(ValueError):
        cells.class_of_asset("USO")   # commodity futures ETF lane, not declared by any class


def test_unknown_class_and_session_sync():
    import pytest
    # "commodity_future" is not declared by any class -- bond_etf/metal_etf
    # (Track 2b) are now declared, so they no longer serve as the "unknown
    # class" probe here.
    with pytest.raises(ValueError, match="not a declared class"):
        cells.class_cells("commodity_future")
    with pytest.raises(ValueError, match="not a declared class"):
        cells.validate_cell("EUR", "1d", asset_class="commodity_future")
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
    # D15 exit rules v7 (2026-09-03): swing_stop / channel_stop / channel_exit
    # read highs/lows, so they join the four range-requiring types.
    assert cells.CLASSES["fx"]["excluded_block_types"] == frozenset(
        {"channel_breakout", "channel_breakout_dense", "atr_stop", "atr_stop_dense",
         "swing_stop", "channel_stop", "channel_exit"})
    # equity_etf carries REAL OHLC bars (bar_kind "ohlcv"), unlike fx's
    # single-fix bars, so no block type is excluded on range grounds.
    assert cells.CLASSES["equity_etf"]["excluded_block_types"] == frozenset()
    for cls, spec in cells.CLASSES.items():
        assert isinstance(spec["excluded_block_types"], frozenset), cls


def test_benchmark_declared_per_class():
    # B1 (SP4 Track 2a addendum, pre-registered 2026-08-26) + SP5 D3
    # (docs/2026-08-28-market-data-universe-design.md s7, Coen 2026-08-28):
    # every single-name class declares "self" -- fx flipped under SP5 because
    # a recorded-not-gated control is strictly more information than none.
    # crypto alone still declares None (no key is ever written on its
    # verdicts). P2-T3 declared its 100-asset grid and its cost_model but
    # DEFERRED this flip: the legacy pooled path still serves crypto, and a
    # "self" control needs exactly one asset per cell. It flips at Phase 3,
    # coupled to the routing change -- see cells.py's crypto comment block
    # and test_gauntlet_classes.py's coupling pin.
    assert cells.CLASSES["crypto"]["benchmark"] is None
    assert cells.CLASSES["fx"]["benchmark"] == "self"
    assert cells.CLASSES["equity_etf"]["benchmark"] == "self"
    assert cells.CLASSES["bond_etf"]["benchmark"] == "self"
    assert cells.CLASSES["metal_etf"]["benchmark"] == "self"
    for cls, spec in cells.CLASSES.items():
        assert spec.get("benchmark") in (None, "self"), cls


# ---------------- Track 2b: bond_etf + metal_etf classes (declared, not active) ----------------

def test_bond_etf_class_declared():
    c = cells.CLASSES["bond_etf"]
    assert c["assets"] == ("SHY", "IEF", "TLT", "TIP", "LQD", "HYG", "EMB", "BND")
    assert len(c["assets"]) == 8
    assert c["timeframes"] == ("1d",) and c["session"] == "us_equity_5d"
    assert c["periods_per_year"] == 252 and c["bar_kind"] == "ohlcv"
    assert c["cost_model"] == {"commission_per_side": 0.00010, "slippage_ticks": 0.00010,
                               "short_financing_per_year": -0.005}
    assert [e[0] for e in c["eras"]] == ["pre_gfc", "zirp", "hike_cut_cycle", "post_2022"]
    assert c["eras"] == (
        ("pre_gfc", "2002-07-26", "2008-12-31"),
        ("zirp", "2009-01-01", "2015-12-31"),
        ("hike_cut_cycle", "2016-01-01", "2021-12-31"),
        ("post_2022", "2022-01-01", "9999-12-31"))
    assert c["max_end_lag_days"] == 4
    assert isinstance(c["max_end_lag_days"], int) and c["max_end_lag_days"] >= 0
    assert c["excluded_block_types"] == frozenset()
    assert c["benchmark"] == "self"
    assert cells.class_cells("bond_etf") == [(a, "1d") for a in c["assets"]]


def test_metal_etf_class_declared():
    c = cells.CLASSES["metal_etf"]
    assert c["assets"] == ("GLD", "SLV")
    assert len(c["assets"]) == 2
    assert c["timeframes"] == ("1d",) and c["session"] == "us_equity_5d"
    assert c["periods_per_year"] == 252 and c["bar_kind"] == "ohlcv"
    # Only the short_financing_per_year differs from bond_etf (Phase C table).
    assert c["cost_model"] == {"commission_per_side": 0.00010, "slippage_ticks": 0.00010,
                               "short_financing_per_year": -0.0075}
    assert [e[0] for e in c["eras"]] == ["pre_gfc", "zirp", "hike_cut_cycle", "post_2022"]
    assert c["eras"] == (
        ("pre_gfc", "2004-11-18", "2008-12-31"),
        ("zirp", "2009-01-01", "2015-12-31"),
        ("hike_cut_cycle", "2016-01-01", "2021-12-31"),
        ("post_2022", "2022-01-01", "9999-12-31"))
    # The same three post-pre_gfc cuts as bond_etf, verbatim (addendum: "then
    # the same three cuts").
    assert c["eras"][1:] == cells.CLASSES["bond_etf"]["eras"][1:]
    assert c["max_end_lag_days"] == 4
    assert isinstance(c["max_end_lag_days"], int) and c["max_end_lag_days"] >= 0
    assert c["excluded_block_types"] == frozenset()
    assert c["benchmark"] == "self"
    assert cells.class_cells("metal_etf") == [(a, "1d") for a in c["assets"]]


def test_bond_and_metal_etf_declared_not_active():
    # Track 2b ships the two class declarations; activation (LIVE_CLASSES
    # gaining them) is Coen's call after the dry-run ship-bar step (spec s8),
    # exactly as it was for fx and equity_etf before them.
    assert "bond_etf" in cells.CLASSES and "metal_etf" in cells.CLASSES
    assert "bond_etf" in cells.LIVE_CLASSES
    assert "metal_etf" in cells.LIVE_CLASSES


def test_bond_and_metal_etf_disjoint_from_every_other_class():
    # The module-level disjointness assertion already ran at import time (it
    # would have raised AssertionError on import if TLT/GLD/etc. collided
    # with an existing class's assets); this test pins the same invariant
    # from the test side so a future edit that reintroduces a collision is
    # caught here too, not only by a hard-to-diagnose import-time crash.
    seen: dict[str, str] = {}
    for cls, spec in cells.CLASSES.items():
        for asset in spec["assets"]:
            assert asset not in seen, f"{asset} declared in both {seen.get(asset)} and {cls}"
            seen[asset] = cls
    for a in cells.CLASSES["bond_etf"]["assets"]:
        assert cells.class_of_asset(a) == "bond_etf"
    for a in cells.CLASSES["metal_etf"]["assets"]:
        assert cells.class_of_asset(a) == "metal_etf"
