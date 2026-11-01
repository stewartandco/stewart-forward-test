"""Switch 2 guard (2b-3 plan 5, Task 8; cold review M2): the composer never
sweeps an intraday crypto cell until BOTH the intraday protocol note and the
phase 2 note are on the chain. Design s8.4 says "chained verbatim before the
first intraday verdict"; this is the code that enforces it, so a merge whose
note chaining failed still yields only 1d specs at 20:00.

The gate is monkeypatched open for the duration of a test; the model is never
called (propose_fn is canned) and the chain is a tmp one.
"""
from __future__ import annotations

import json

import pytest

from . import cells, composer
from .registry import Registry
from .test_composer import seeded_registry
from .test_switch1 import ADMITTED, _crypto_family

SWITCH2_TIMEFRAMES = ("1h", "4h", "12h", "1d")
PROTOCOL_PREFIX, PHASE2_PREFIX = composer.INTRADAY_NOTE_PREFIXES
N_COMBOS = 9                                      # good_family: 3 x 3 sweep
SIBLING_CAP = 25
HELD_LINE = "HELD: intraday cells not swept until both notes are chained"


@pytest.fixture
def switch2(monkeypatch):
    monkeypatch.setitem(cells.ACTIVE_CELLS["crypto"], "timeframes", SWITCH2_TIMEFRAMES)


def _chain_notes(reg_path, *prefixes):
    reg = Registry(reg_path)
    for p in prefixes:
        reg.append("note", {"text": p + " the verbatim body of the note"})


def _run(tmp_path, reg_path, cid, monkeypatch):
    seen = []
    real_split = composer.split_for_cycle

    def _spy(specs, cap):
        seen.append((len(specs), cap))
        return real_split(specs, cap)

    monkeypatch.setattr(composer, "split_for_cycle", _spy)
    window = ",".join(ADMITTED[:12])
    rc = composer.run(["--registry", str(reg_path), "--run-id", "guard", "--dry-run",
                       "--sibling-cap", str(SIBLING_CAP), "--assets", window,
                       "--loop-state", str(tmp_path / "loop_state.json")],
                      propose_fn=lambda cards: [_crypto_family(card_ids=[cid])])
    assert rc == 0
    return seen


def _drift(reg_path):
    lines = (reg_path.resolve().parent / "logs" / "batch_drift.jsonl"
             ).read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


def test_no_intraday_cell_is_swept_without_the_notes(switch2, tmp_path, monkeypatch, capsys):
    reg_path, cid = seeded_registry(tmp_path)
    seen = _run(tmp_path, reg_path, cid, monkeypatch)
    out = capsys.readouterr().out
    assert HELD_LINE in out
    assert PROTOCOL_PREFIX in out and PHASE2_PREFIX in out
    rec = _drift(reg_path)
    assert rec["held_intraday"] is True
    assert rec["n_specs"] == N_COMBOS * 12          # 12 1d cells, no 1h/4h/12h
    assert seen == [(N_COMBOS * 12, SIBLING_CAP * 12)]


@pytest.mark.parametrize("prefix", composer.INTRADAY_NOTE_PREFIXES)
def test_one_note_is_not_enough(switch2, tmp_path, monkeypatch, capsys, prefix):
    reg_path, cid = seeded_registry(tmp_path)
    _chain_notes(reg_path, prefix)
    assert composer.intraday_notes_chained(Registry(reg_path)) is False
    _run(tmp_path, reg_path, cid, monkeypatch)
    assert HELD_LINE in capsys.readouterr().out
    rec = _drift(reg_path)
    assert rec["held_intraday"] is True
    assert rec["n_specs"] == N_COMBOS * 12


def test_both_notes_open_the_intraday_cells(switch2, tmp_path, monkeypatch, capsys):
    reg_path, cid = seeded_registry(tmp_path)
    _chain_notes(reg_path, PROTOCOL_PREFIX, PHASE2_PREFIX)
    assert composer.intraday_notes_chained(Registry(reg_path)) is True
    seen = _run(tmp_path, reg_path, cid, monkeypatch)
    assert HELD_LINE not in capsys.readouterr().out
    rec = _drift(reg_path)
    assert "held_intraday" not in rec
    assert rec["n_specs"] == N_COMBOS * 48
    assert seen == [(N_COMBOS * 48, SIBLING_CAP * 48)]


def test_the_cap_and_the_expansion_agree_when_held(switch2, tmp_path, monkeypatch):
    reg_path, cid = seeded_registry(tmp_path)
    seen = _run(tmp_path, reg_path, cid, monkeypatch)
    (n_specs, cap), = seen
    assert cap == SIBLING_CAP * 12                  # the 1d cells only
    assert n_specs == N_COMBOS * 12 <= cap          # the expansion used the same set
    rec = _drift(reg_path)
    assert all(sid for sid in rec["strategy_ids"])


def test_a_note_must_start_with_the_prefix_not_merely_contain_it(switch2, tmp_path):
    reg_path, _ = seeded_registry(tmp_path)
    reg = Registry(reg_path)
    reg.append("note", {"text": "see " + PROTOCOL_PREFIX + " and " + PHASE2_PREFIX})
    assert composer.intraday_notes_chained(Registry(reg_path)) is False


def test_timeframes_none_is_todays_behaviour(switch2):
    window = list(ADMITTED[:12])
    assert (composer.sweep_cells("crypto", assets=window, timeframes=None)
            == composer.sweep_cells("crypto", assets=window))
    only_1d = composer.sweep_cells("crypto", assets=window, timeframes=("1d",))
    assert only_1d == [(a, "1d") for a in window]


def test_a_1d_only_gate_never_reads_the_notes(tmp_path, monkeypatch):
    # the committed gate is 1h/4h/12h/1d since the Switch 2 commit: this test
    # models the pre-Switch-2 (and every tradfi) 1d-only gate explicitly
    monkeypatch.setitem(cells.ACTIVE_CELLS["crypto"], "timeframes", ("1d",))

    def _boom(registry):
        raise AssertionError("the notes must not be read with a 1d-only gate")

    monkeypatch.setattr(composer, "intraday_notes_chained", _boom)
    reg_path, cid = seeded_registry(tmp_path)
    seen = _run(tmp_path, reg_path, cid, monkeypatch)
    assert seen == [(N_COMBOS * 12, SIBLING_CAP * 12)]
    assert "held_intraday" not in _drift(reg_path)
