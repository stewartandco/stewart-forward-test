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


from .quarantine import _plan_provenance

H = {c: c * 64 for c in "abcdef"}          # six distinct valid digests


class _V:
    def __init__(self, snaps=None, sups=None):
        self._s, self._u = snaps or {}, sups or {}

    def snapshots(self):
        return dict(self._s)

    def supplements(self):
        return {d: list(v) for d, v in self._u.items()}


def _sp(*assets):
    return {"universe": {"assets": list(assets)}}


SPECS = {"A": _sp("BTCUSD"), "B": _sp("ETHUSD"), "AB": _sp("BTCUSD", "ETHUSD")}
D = "2023-01-22"


def test_plan_writes_a_base_snapshot_when_the_date_has_none():
    p = _plan_provenance(_V(), D, ["A", "B"], SPECS,
                         {"BTCUSD": H["a"], "ETHUSD": H["b"]},
                         {"BTCUSD": H["c"], "ETHUSD": H["d"]})
    assert p.refusal is None and p.ready == ["A", "B"] and p.conflicted == []
    assert p.write == ("quarantine_data_snapshot", {
        "date": D, "data_sha256": {"BTCUSD": H["c"], "ETHUSD": H["d"]},
        "bars_sha256": {"BTCUSD": H["a"], "ETHUSD": H["b"]}})


def test_plan_refuses_a_supplement_without_a_base():
    p = _plan_provenance(_V(sups={D: [{"bars_sha256": {"ETHUSD": H["b"]}}]}),
                         D, ["A"], SPECS, {"BTCUSD": H["a"]}, {"BTCUSD": H["c"]})
    assert p.refusal == (
        f"REFUSED: {D} has supplement provenance but no base "
        f"quarantine_data_snapshot -- a defective chain needs human eyes, "
        f"not more rows.")
    assert p.write is None


def test_plan_refuses_a_base_with_no_usable_bars_map():
    p = _plan_provenance(_V(snaps={D: {"bars_sha256": {}}}), D, ["A"], SPECS,
                         {"BTCUSD": H["a"]}, {"BTCUSD": H["c"]})
    assert p.refusal == (
        f"REFUSED: the quarantine_data_snapshot chained for {D} carries no "
        f"usable bars_sha256 map, so it covers nothing -- BTCUSD included -- "
        f"and cannot be supplemented honestly.")


def test_plan_writes_nothing_when_everything_is_covered_and_matches():
    p = _plan_provenance(_V(snaps={D: {"bars_sha256": {"BTCUSD": H["a"]}}}),
                         D, ["A"], SPECS, {"BTCUSD": H["a"]}, {"BTCUSD": H["c"]})
    assert (p.refusal, p.write, p.ready, p.conflicted) == (None, None, ["A"], [])


def test_plan_supplements_only_the_uncovered_assets():
    p = _plan_provenance(_V(snaps={D: {"bars_sha256": {"BTCUSD": H["a"]}}}),
                         D, ["A", "AB"], SPECS,
                         {"BTCUSD": H["a"], "ETHUSD": H["b"]},
                         {"BTCUSD": H["c"], "ETHUSD": H["d"]})
    assert p.write == ("quarantine_data_snapshot_supplement", {
        "date": D, "data_sha256": {"ETHUSD": H["d"]},
        "bars_sha256": {"ETHUSD": H["b"]}})
    assert p.ready == ["A", "AB"]


def test_plan_defers_the_strategies_trading_a_restated_asset():
    base = {"bars_sha256": {"BTCUSD": H["a"], "ETHUSD": H["e"]}}
    p = _plan_provenance(_V(snaps={D: base}), D, ["A", "B", "AB"], SPECS,
                         {"BTCUSD": H["a"], "ETHUSD": H["b"]},
                         {"BTCUSD": H["c"], "ETHUSD": H["d"]})
    assert p.conflicted == ["ETHUSD"]
    assert p.ready == ["A"]
    assert [sid for sid, _ in p.deferred] == ["B", "AB"]
    assert p.reasons["ETHUSD"] == p.deferred[0][1]
    assert "ETHUSD: the bars up to this date have changed" in p.reasons["ETHUSD"]
    assert p.write is None


def test_plan_counts_supplement_coverage_first_writer_wins():
    base = {"bars_sha256": {"BTCUSD": H["a"]}}
    sups = [{"bars_sha256": {"ETHUSD": H["b"]}},
            {"bars_sha256": {"ETHUSD": H["e"]}}]      # defective overlap
    p = _plan_provenance(_V(snaps={D: base}, sups={D: sups}), D, ["B"], SPECS,
                         {"ETHUSD": H["b"]}, {"ETHUSD": H["d"]})
    assert (p.conflicted, p.ready, p.write) == ([], ["B"], None)


def test_plan_does_not_supplement_assets_of_deferred_strategies():
    specs = {**SPECS, "X": _sp("BTCUSD", "SOLUSD")}
    base = {"bars_sha256": {"BTCUSD": H["e"]}}
    p = _plan_provenance(_V(snaps={D: base}), D, ["B", "X"], specs,
                         {"BTCUSD": H["a"], "ETHUSD": H["b"], "SOLUSD": H["c"]},
                         {"BTCUSD": H["d"], "ETHUSD": H["e"], "SOLUSD": H["f"]})
    assert p.ready == ["B"]
    assert [s for s, _ in p.deferred] == ["X"]
    assert p.conflicted == ["BTCUSD"]
    assert p.write == ("quarantine_data_snapshot_supplement", {
        "date": D, "data_sha256": {"ETHUSD": H["e"]},
        "bars_sha256": {"ETHUSD": H["b"]}})


def test_plan_with_nothing_ready_plans_nothing():
    p = _plan_provenance(_V(), D, [], {}, {}, {})
    assert (p.refusal, p.write, p.ready, p.conflicted, p.deferred) == (
        None, None, [], [], [])
