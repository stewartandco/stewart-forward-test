"""Quarantine fast path (2026-10-16 design): one chain read per run,
simulation before chain.lock, one append per date."""
from __future__ import annotations

import json

import pytest

from . import quarantine as quarantine_mod
from .quarantine import run as quarantine_run
from .quarantine_view import QuarantineView
from .registry import Registry
from .test_gen3b import (argv_for, extend_ethusd, quarantined_split_calendar,
                         snap_payload)


def _old_readers(reg):
    """What the module-level readers (kept as the oracle, ruling R4) say."""
    return {
        "states": reg.strategy_states(),
        "entered": quarantine_mod.quarantine_entry_dates(reg),
        "specs": {e["payload"]["strategy_id"]: e["payload"]
                  for e in reg.entries()
                  if e["entry_type"] == "strategy_registered"},
        "rows": quarantine_mod.decision_entries(reg),
        "keys": quarantine_mod.existing_decisions(reg),
        "snapshots": quarantine_mod.data_snapshots(reg),
        "supplements": quarantine_mod.snapshot_supplements(reg),
    }


def _view_says(view):
    return {"states": view.states(), "entered": view.entry_dates(),
            "specs": view.specs(), "rows": view.decision_rows(),
            "keys": view.decision_keys(), "snapshots": view.snapshots(),
            "supplements": view.supplements()}


def _busy_chain(tmp_path):
    """Two strategies, a base snapshot, a supplement, rows on two dates, plus
    the defective shapes the readers' first/last-wins rules exist for."""
    reg, spec, two, data = quarantined_split_calendar(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    extend_ethusd(data)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-23")) == 0
    # a defective second base snapshot for a date: FIRST wins
    reg.append("quarantine_data_snapshot",
               dict(snap_payload(data, ["BTCUSD"]),
                    bars_sha256={"BTCUSD": "c" * 64}))
    # a re-registration of a strategy id: LAST wins (old dict comprehension)
    reg.append("strategy_registered", dict(spec, note="re-registered"))
    return reg, spec, two, data


def test_the_view_equals_the_old_readers(tmp_path):
    reg, spec, two, data = _busy_chain(tmp_path)
    assert _view_says(QuarantineView.build(reg)) == _old_readers(reg)


def test_an_advanced_view_equals_a_fresh_one(tmp_path):
    reg, spec, two, data = quarantined_split_calendar(tmp_path)
    view = QuarantineView.build(reg)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    extend_ethusd(data)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    view.advance(reg)
    assert _view_says(view) == _view_says(QuarantineView.build(reg))
    assert _view_says(view) == _old_readers(reg)
    assert view.snap == reg.snapshot()


def test_decision_payload_is_the_first_chained_for_its_key(tmp_path):
    """The old writer's guard found the FIRST row for a key; a defective
    duplicate must not replace it."""
    reg, spec, two, data = quarantined_split_calendar(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    first = next(e["payload"] for e in reg.entries()
                 if e["entry_type"] == "quarantine_decision")
    reg.append("quarantine_decision", dict(first, equity=9.5))
    key = (first["strategy_id"], first["date"], first["asset"])
    assert QuarantineView.build(reg).decision_payload(key) == first
    assert QuarantineView.build(reg).decision_payload(("x", "y", "z")) is None


def test_the_view_hands_out_copies(tmp_path):
    reg, spec, two, data = quarantined_split_calendar(tmp_path)
    view = QuarantineView.build(reg)
    view.states()["x"] = "live"
    view.decision_keys().add(("x", "y", "z"))
    view.supplements().setdefault("2099-01-01", []).append({})
    assert "x" not in view.states()
    assert ("x", "y", "z") not in view.decision_keys()
    assert "2099-01-01" not in view.supplements()


def test_rebuild_replaces_everything_with_a_fresh_read(tmp_path):
    reg, spec, two, data = quarantined_split_calendar(tmp_path)
    view = QuarantineView.build(reg)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    view.rebuild(reg)
    assert _view_says(view) == _old_readers(reg)
