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


from .common import canonical_json
from .registry import ChainMoved
from .test_gen3b import decisions, flat_dated_bars, quarantined


def _rewrite_last_line(reg):
    """Rewrite the chain's head entry in place: advance() must see a
    rewritten head and raise ChainMoved."""
    raw = reg.log_path.read_bytes().splitlines(keepends=True)
    last = json.loads(raw[-1])
    last["ts_utc"] = "2000-01-01T00:00:00Z"
    ending = b"\r\n" if raw[-1].endswith(b"\r\n") else b"\n"
    raw[-1] = canonical_json(last).encode("utf-8") + ending
    reg.log_path.write_bytes(b"".join(raw))


def _catch_up(reg, data):
    return quarantine_run(["--catch-up", "--registry", str(reg.log_path),
                           "--data-dir", str(data),
                           "--artifacts-dir", str(reg.log_path.parent / "artifacts")])


def test_catch_up_hands_one_view_to_every_date(tmp_path, monkeypatch):
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    seen = []
    real = quarantine_mod.run_one_date_for_catch_up

    def spy(base, date, report, view=None, registry=None):
        seen.append(id(view))
        return real(base, date, report, view=view, registry=registry)

    monkeypatch.setattr(quarantine_mod, "run_one_date_for_catch_up", spy)
    assert _catch_up(reg, data) == 0
    assert len(seen) == 4 and len(set(seen)) == 1 and seen[0] != id(None)
    assert {r["date"] for r in decisions(reg)} == {
        "2023-01-22", "2023-01-23", "2023-01-24", "2023-01-25"}


def test_catch_up_rebuilds_the_view_after_chain_moved(tmp_path, monkeypatch):
    """Spec s3.4: a date that ends in ChainMoved fails; the view is rebuilt
    with a fresh read before the next date, so later dates still record."""
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    real = quarantine_mod.run_one_date_for_catch_up

    def rewrite_before_second(base, date, report, view=None, registry=None):
        if date == "2023-01-23":
            _rewrite_last_line(reg)
        return real(base, date, report, view=view, registry=registry)

    monkeypatch.setattr(quarantine_mod, "run_one_date_for_catch_up",
                        rewrite_before_second)
    assert _catch_up(reg, data) == 1
    assert {r["date"] for r in decisions(reg)} == {
        "2023-01-22", "2023-01-24", "2023-01-25"}


def test_a_chain_moved_date_marks_the_report_unchecked(tmp_path, monkeypatch):
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    view = QuarantineView.build(reg)
    _rewrite_last_line(reg)
    rep = quarantine_mod.DateReport()
    base = ["--registry", str(reg.log_path), "--data-dir", str(data)]
    with pytest.raises(ChainMoved):
        quarantine_mod.run_one_date_for_catch_up(base, "2023-01-22", rep,
                                                 view=view, registry=reg)
    assert rep.unchecked is True
    assert view.snap == reg.snapshot()        # rebuilt on the rewritten chain


def test_catch_up_sees_a_state_change_between_dates(tmp_path, monkeypatch):
    """Review Focus 2: each date advances the shared view, so a strategy
    that left quarantine after date 1 is not recorded on date 2."""
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    real = quarantine_mod.run_one_date_for_catch_up

    def bury_after_first(base, date, report, view=None, registry=None):
        if date == "2023-01-23":
            reg.record_state_change(spec["strategy_id"], "graveyard", "test")
        return real(base, date, report, view=view, registry=registry)

    monkeypatch.setattr(quarantine_mod, "run_one_date_for_catch_up",
                        bury_after_first)
    assert _catch_up(reg, data) == 0
    assert {r["date"] for r in decisions(reg)} == {"2023-01-22"}
