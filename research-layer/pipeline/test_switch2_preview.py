"""Switch 2 preview (2b-3 plan 4, Task 7, design D-i2 item 1): what the gate
change to ("1h", "4h", "12h", "1d") does to a sweep window, proven BEFORE the
commit that flips it.

Written as a preview (the gate monkeypatched open); the Switch 2 commit made
the gate real, so these tests now run against the committed gate. No model is
called (expand_family_for_class runs on a canned family) and the chain is a tmp
one.
"""
from __future__ import annotations

import pytest

from . import cells, composer
from .registry import Registry
from .test_composer import good_family, seeded_registry
from .test_switch1 import ADMITTED

SWITCH2_TIMEFRAMES = ("1h", "4h", "12h", "1d")
_RUN, _MODEL, _CREATED = "switch2-preview", "claude-opus-5", "2026-10-29T00:00:00Z"
N_COMBOS = 9                                    # good_family: 3 x 3 sweep


@pytest.fixture
def switch2():
    """The preview's gate is the committed gate since the Switch 2 commit:
    nothing is monkeypatched any more, and the fixture asserts that."""
    assert cells.ACTIVE_CELLS["crypto"]["timeframes"] == SWITCH2_TIMEFRAMES


def test_a_window_of_12_is_48_cells(switch2):
    window = list(ADMITTED[:12])
    swept = composer.sweep_cells("crypto", assets=window)
    assert len(swept) == 48
    assert swept == [(a, tf) for a in window for tf in SWITCH2_TIMEFRAMES]
    assert len(set(swept)) == 48

    fam = good_family(assets=["BTCUSDT"])
    specs = composer.expand_family_for_class(fam, _RUN, _MODEL, _CREATED, "crypto",
                                             assets=window)
    # one single-asset spec per cell per combo, distinct ids
    assert len(specs) == N_COMBOS * 48
    assert len({s["strategy_id"] for s in specs}) == len(specs)
    for s in specs:
        uni = s["universe"]
        assert len(uni["assets"]) == 1
        assert uni["session"] == "24x7"
        assert uni["asset_class"] == "crypto"
        assert uni["timeframe"] in SWITCH2_TIMEFRAMES
    assert cells.CLASSES["crypto"]["benchmark"] == "self"
    per_cell: dict[tuple[str, str], int] = {}
    for s in specs:
        k = (s["universe"]["assets"][0], s["universe"]["timeframe"])
        per_cell[k] = per_cell.get(k, 0) + 1
    assert set(per_cell) == set(swept)
    assert set(per_cell.values()) == {N_COMBOS}
    # one sibling group per cell
    assert len({s["provenance"]["sibling_group_id"] for s in specs}) == 48


def test_one_spec_per_timeframe_registers_on_a_tmp_chain_and_passes_c3(switch2, tmp_path):
    reg_path, cid = seeded_registry(tmp_path)
    reg = Registry(reg_path)
    fam = good_family(card_ids=[cid], assets=["BTCUSDT"])
    specs = composer.expand_family_for_class(fam, _RUN, _MODEL, _CREATED, "crypto",
                                             assets=["BTCUSDT"])
    assert len(specs) == N_COMBOS * 4
    for key in composer.BLOCK_TYPES:
        if key not in reg.block_types():
            reg.register_block_type(composer.block_type_payload(*key))
    one_per_tf = {}
    for s in specs:
        one_per_tf.setdefault(s["universe"]["timeframe"], s)
    assert tuple(sorted(one_per_tf, key=SWITCH2_TIMEFRAMES.index)) == SWITCH2_TIMEFRAMES
    for spec in one_per_tf.values():
        reg.register_strategy(spec)             # C3: bars.bars_per_day(session, tf)
    registered = {e["payload"]["strategy_id"]: e["payload"]
                  for e in Registry(reg_path).entries()
                  if e["entry_type"] == "strategy_registered"}
    assert {p["universe"]["timeframe"] for p in registered.values()} == set(SWITCH2_TIMEFRAMES)
    assert all(p["universe"]["session"] == "24x7" for p in registered.values())


def test_c3_would_refuse_an_intraday_spec_on_a_non_24x7_session(switch2, tmp_path):
    """The check the registration above passes is a real one: the same spec
    with a daily-only session is refused before any write."""
    reg_path, cid = seeded_registry(tmp_path)
    reg = Registry(reg_path)
    for key in composer.BLOCK_TYPES:
        if key not in reg.block_types():
            reg.register_block_type(composer.block_type_payload(*key))
    fam = good_family(card_ids=[cid], assets=["BTCUSDT"])
    spec = next(s for s in composer.expand_family_for_class(
        fam, _RUN, _MODEL, _CREATED, "crypto", assets=["BTCUSDT"])
        if s["universe"]["timeframe"] == "1h")
    bad = {**spec, "universe": {**spec["universe"], "session": "fx_5d"}}
    before = reg_path.read_bytes()
    with pytest.raises(ValueError, match="C3"):
        reg.register_strategy(bad)
    assert reg_path.read_bytes() == before
