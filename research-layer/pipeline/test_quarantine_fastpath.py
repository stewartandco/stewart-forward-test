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


# ---- Task 5: the write phase (design s3.5-3.6) ----

from .test_gen3b import (quarantined_two_asset, race_before_lock, run_verifier,
                         snapshots, take_back)
from .test_quarantine_isolation import (btc_and_eth_solo, ledger,
                                        quarantined_split_calendar_full,
                                        restate)


def _count_full_reads(monkeypatch):
    """snapshot() calls, and a hard failure on any other full-chain read
    (entries / strategy_states / the head-hash walk)."""
    calls = []
    real = Registry.snapshot

    def counted(self, *a, **k):
        calls.append(1)
        return real(self, *a, **k)

    def forbidden(self, *a, **k):
        raise AssertionError("a full chain read outside snapshot()")

    monkeypatch.setattr(Registry, "snapshot", counted)
    for name in ("entries", "strategy_states", "_head_hash"):
        monkeypatch.setattr(Registry, name, forbidden)
    return calls


def test_a_date_reads_the_chain_once(tmp_path, monkeypatch):
    reg, spec, two, data = quarantined_split_calendar_full(tmp_path)
    calls = _count_full_reads(monkeypatch)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    monkeypatch.undo()
    assert len(calls) == 1
    assert len(decisions(reg)) == 3


def test_catch_up_over_four_dates_reads_the_chain_once(tmp_path, monkeypatch):
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    calls = _count_full_reads(monkeypatch)
    assert _catch_up(reg, data) == 0
    monkeypatch.undo()
    assert len(calls) == 1
    assert len(decisions(reg)) == 4


def test_a_chain_moved_date_costs_one_more_read(tmp_path, monkeypatch):
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    real = quarantine_mod.run_one_date_for_catch_up

    def rewrite_before_second(base, date, report, view=None, registry=None):
        if date == "2023-01-23":
            _rewrite_last_line(reg)
        return real(base, date, report, view=view, registry=registry)

    monkeypatch.setattr(quarantine_mod, "run_one_date_for_catch_up",
                        rewrite_before_second)
    calls = _count_full_reads(monkeypatch)
    assert _catch_up(reg, data) == 1
    monkeypatch.undo()
    assert len(calls) == 2


def test_simulation_runs_before_the_lock_and_the_append_inside_it(
        tmp_path, monkeypatch):
    reg, spec, two, data = quarantined_split_calendar_full(tmp_path)
    lock_path = reg.log_path.parent / "logs" / "chain.lock"
    during_sim, during_append = [], []
    real_obs, real_app = quarantine_mod.observe_day, Registry._append_at

    def obs(*a, **k):
        during_sim.append(lock_path.exists())
        return real_obs(*a, **k)

    def app(self, snap, items):
        during_append.append((lock_path.exists(), [t for t, _ in items]))
        return real_app(self, snap, items)

    monkeypatch.setattr(quarantine_mod, "observe_day", obs)
    monkeypatch.setattr(Registry, "_append_at", app)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert during_sim == [False, False]
    assert during_append == [(True, ["quarantine_data_snapshot"]
                              + ["quarantine_decision"] * 3)]


def _sim_calls(monkeypatch, fail_on, exc_factory):
    """Wrap quarantine.observe_day: record every strategy it is asked to
    simulate (in order) and raise `exc_factory()` for those where
    `fail_on(spec, call_no)` holds. Returns the calls list."""
    real = quarantine_mod.observe_day
    calls = []

    def wrapped(spec, *x):
        calls.append(spec["strategy_id"])
        if fail_on(spec, len(calls)):
            raise exc_factory()
        return real(spec, *x)

    monkeypatch.setattr(quarantine_mod, "observe_day", wrapped)
    return calls


def test_a_simulation_valueerror_defers_that_strategy_and_the_others_record(
        tmp_path, monkeypatch, capsys):
    """2026-10-16 (Coen, follow-up to design s3.6): the second strategy's
    data-shaped raise defers ONLY that strategy. The first one's row IS
    chained (and its asset is in the date's snapshot), the failed one has no
    row and stays owed, stdout names it, and one `simulation_failed` item
    keyed by it reaches the ledger. Exit 0: a deferral is not a failure."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    asset_of = {a["strategy_id"]: "BTCUSD", b["strategy_id"]: "ETHUSD"}
    calls = _sim_calls(monkeypatch, lambda spec, n: n == 2,
                       lambda: ValueError("equity baseline is 0.0"))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert len(calls) == 2
    ok_sid, bad_sid = calls
    out = capsys.readouterr()
    assert {r["strategy_id"] for r in decisions(reg)} == {ok_sid}
    assert (f"{bad_sid}  deferred: simulation failed: equity baseline is 0.0"
            in out.out)
    assert "PARTIAL WRITE" not in out.err
    snaps = snapshots(reg)
    assert len(snaps) == 1
    assert set(snaps[0]["bars_sha256"]) == {asset_of[ok_sid]}   # not the failed one's
    items = ledger(reg)["items"]
    assert [(i["source"], i["key"]) for i in items] == [
        ("simulation_failed", bad_sid)]
    assert items[0]["reason"] == "2023-01-22: equity baseline is 0.0"
    monkeypatch.undo()
    assert run_verifier(reg.log_path).returncode == 0
    view = QuarantineView.build(reg)
    owed = quarantine_mod._owed_dates_for_catch_up(
        view, sorted([a["strategy_id"], b["strategy_id"]]), view.entry_dates(),
        view.specs(), data)
    assert "2023-01-22" in owed
    # ...and the failed strategy is the one that still owes it
    assert (bad_sid, "2023-01-22", asset_of[bad_sid]) not in view.decision_keys()


def test_a_non_valueerror_in_a_simulation_still_fails_the_date(
        tmp_path, monkeypatch, capsys):
    """Only observe_day's data-shaped ValueErrors defer a strategy. A
    RuntimeError is a code bug: it propagates, nothing is written, and no
    strategy is reported as deferred."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    before = reg.log_path.read_bytes()
    calls = _sim_calls(monkeypatch, lambda spec, n: n == 2,
                       lambda: RuntimeError("engine bug"))
    with pytest.raises(RuntimeError, match="engine bug"):
        quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    assert len(calls) == 2
    assert reg.log_path.read_bytes() == before
    out = capsys.readouterr()
    assert "simulation failed" not in out.out
    assert "PARTIAL WRITE" not in out.err
    monkeypatch.undo()
    assert not (ledger(reg) or {"items": []})["items"]


def test_every_ready_strategy_failing_simulation_is_a_total_stall(
        tmp_path, monkeypatch, capsys):
    """The total-stall rule extended: nothing recorded, nothing already
    present, every ready strategy's simulation failed -> REFUSED, exit 1,
    chain byte-identical, and chain.lock is never taken (the refusal falls
    before the lock, as the missing-bar refusal does)."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    before = reg.log_path.read_bytes()
    lock_path = reg.log_path.parent / "logs" / "chain.lock"
    acquired = []
    real_acquire = quarantine_mod.ChainLock.acquire

    def spy(self):
        acquired.append(1)
        return real_acquire(self)

    monkeypatch.setattr(quarantine_mod.ChainLock, "acquire", spy)
    seen_lock = []
    calls = _sim_calls(monkeypatch,
                       lambda spec, n: seen_lock.append(lock_path.exists()) or True,
                       lambda: ValueError("no zero point"))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 1
    assert len(calls) == 2 and seen_lock == [False, False]
    out = capsys.readouterr()
    assert "REFUSED" in out.err and "simulation" in out.err
    for sid in calls:
        assert sid in out.err
    assert acquired == [] and not lock_path.exists()
    assert reg.log_path.read_bytes() == before
    monkeypatch.undo()
    assert sorted(i["key"] for i in ledger(reg)["items"]) == sorted(calls)
    assert {i["source"] for i in ledger(reg)["items"]} == {"simulation_failed"}


def test_failed_simulations_beside_an_already_chained_strategy_are_not_a_stall(
        tmp_path, monkeypatch, capsys):
    """n_present > 0: another strategy's rows for the date are already on the
    chain, so a failing simulation is a deferral (exit 0), not a dead
    pipeline -- the same exemption the missing-bar refusal has."""
    reg, a, b, data = btc_and_eth_solo(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    assert quarantine_run(argv) == 0                     # A records, B lacks a bar
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"]}
    extend_ethusd(data)                                  # B is ready now
    _sim_calls(monkeypatch, lambda spec, n: True,
               lambda: ValueError("no zero point"))
    capsys.readouterr()
    assert quarantine_run(argv) == 0
    out = capsys.readouterr()
    assert f"{b['strategy_id']}  deferred: simulation failed: no zero point" in out.out
    assert "REFUSED" not in out.err
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"]}


def test_a_strategy_that_failed_simulation_is_retried_by_the_next_catch_up(
        tmp_path, monkeypatch, capsys):
    """The failed strategy stays owed (no row was chained), so once its
    simulation succeeds the catch-up records it -- once -- and the recovered
    ledger item goes away."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    _sim_calls(monkeypatch, lambda spec, n: spec["strategy_id"] == b["strategy_id"],
               lambda: ValueError("equity baseline is 0.0"))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"]}
    monkeypatch.undo()
    capsys.readouterr()
    assert _catch_up(reg, data) == 0
    keys = [(r["strategy_id"], r["date"], r["asset"]) for r in decisions(reg)]
    assert len(keys) == len(set(keys))
    assert (b["strategy_id"], "2023-01-22", "ETHUSD") in keys
    assert keys.count((a["strategy_id"], "2023-01-22", "BTCUSD")) == 1
    assert not [i for i in (ledger(reg) or {"items": []})["items"]
                if i["source"] == "simulation_failed"]
    assert run_verifier(reg.log_path).returncode == 0


def test_a_strategy_leaving_quarantine_mid_run_is_skipped_and_the_others_record(
        tmp_path, monkeypatch, capsys):
    """2026-10-16 (Coen): a live-gate move while the run simulated is routine.
    The strategy that left has its rows dropped (none queued, no degraded
    item), the others record, exit 0, and the chain verifies."""
    reg, spec, two, data = quarantined_split_calendar_full(tmp_path)
    fired = race_before_lock(monkeypatch, lambda: reg.record_state_change(
        two["strategy_id"], "graveyard", "test"))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert fired == [1]
    monkeypatch.undo()
    out = capsys.readouterr()
    sids = {r["strategy_id"] for r in decisions(reg)}
    assert sids == {spec["strategy_id"]} and decisions(reg)
    skip = (f"{two['strategy_id']}  skipped: left quarantine (graveyard) "
            f"while this run simulated")
    assert out.out.count(skip) == 1                 # printed once, not twice
    # provenance vouches only for the strategy that recorded: `two` (BTC+ETH)
    # left, so ETHUSD is not in the date's snapshot
    assert set(snapshots(reg)[0]["bars_sha256"]) == {"BTCUSD"}
    assert set(snapshots(reg)[0]["data_sha256"]) == {"BTCUSD"}
    assert not (ledger(reg) or {"items": []})["items"]
    assert run_verifier(reg.log_path).returncode == 0


def test_the_date_provenance_names_only_the_strategies_that_record(
        tmp_path, monkeypatch, capsys):
    """Coen 2026-10-16, fix round 1: B (ETHUSD only) fails simulation, so the
    chained snapshot covers BTCUSD only. The retry then records B with a
    SUPPLEMENT for ETHUSD (not a `restated` pin), chain VALID."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    _sim_calls(monkeypatch, lambda spec, n: spec["strategy_id"] == b["strategy_id"],
               lambda: ValueError("equity baseline is 0.0"))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    snaps = snapshots(reg)
    assert len(snaps) == 1
    assert set(snaps[0]["bars_sha256"]) == {"BTCUSD"}
    assert set(snaps[0]["data_sha256"]) == {"BTCUSD"}
    monkeypatch.undo()
    capsys.readouterr()
    assert _catch_up(reg, data) == 0
    sups = [e["payload"] for e in reg.entries()
            if e["entry_type"] == "quarantine_data_snapshot_supplement"]
    assert [set(p["bars_sha256"]) for p in sups
            if p["date"] == "2023-01-22"] == [{"ETHUSD"}]
    assert (b["strategy_id"], "2023-01-22", "ETHUSD") in {
        (r["strategy_id"], r["date"], r["asset"]) for r in decisions(reg)}
    assert run_verifier(reg.log_path).returncode == 0


def test_a_date_where_the_only_owing_strategy_left_quarantine_appends_nothing(
        tmp_path, monkeypatch, capsys):
    """Coen 2026-10-16, fix round 1: provenance vouches for rows. With no row
    queued (the only strategy left quarantine mid-run) not even the date's
    snapshot is appended: rc 0, the skip line, the chain byte-identical."""
    reg, spec, data = quarantined(tmp_path)
    before = reg.log_path.read_bytes()
    fired = race_before_lock(monkeypatch, lambda: reg.record_state_change(
        spec["strategy_id"], "graveyard", "test"))
    after_race = []
    real_append = Registry._append_at
    monkeypatch.setattr(Registry, "_append_at",
                        lambda self, snap, items: after_race.append(items)
                        or real_append(self, snap, items))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert fired == [1]
    monkeypatch.undo()
    out = capsys.readouterr().out
    assert (f"{spec['strategy_id']}  skipped: left quarantine (graveyard) "
            f"while this run simulated") in out
    assert after_race == []
    assert snapshots(reg) == [] and decisions(reg) == []
    assert "0 decision(s) chained" in out
    assert run_verifier(reg.log_path).returncode == 0


def test_an_unlocked_append_inside_the_lock_window_is_chain_moved(
        tmp_path, monkeypatch, capsys):
    """The worker's alarm (CLAUDE.md Chain lock): a hand-run writer that
    appends WITHOUT chain.lock between our tail read and our append makes
    the date write nothing and fail."""
    reg, spec, data = quarantined(tmp_path)
    real = Registry.advance

    def advance_then_foreign_append(self, snap, on_entry=None):
        out = real(self, snap, on_entry=on_entry)
        Registry.append(self, "note", {"text": "unlocked hand write"})
        return out

    monkeypatch.setattr(Registry, "advance", advance_then_foreign_append)
    with pytest.raises(ChainMoved):
        quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    monkeypatch.undo()
    assert "CHAIN MOVED" in capsys.readouterr().err
    assert decisions(reg) == [] and snapshots(reg) == []
    assert [e["entry_type"] for e in reg.entries()][-1] == "note"


def test_a_row_chained_before_the_run_is_skipped_uncompared(tmp_path, capsys):
    """Ruling R3 / Review Focus 3: a row already on the chain at the run's
    one read is skipped exactly as today's pre-filter skipped it -- even if
    today's recomputation differs -- and only the missing cell records."""
    reg, spec, data = quarantined_two_asset(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    assert quarantine_run(argv) == 0
    gone = {p["asset"]: p for _, p in take_back(reg, 2)}
    btc, eth = gone["BTCUSD"], gone["ETHUSD"]
    reg.append("quarantine_decision", dict(btc, equity=7.25))
    capsys.readouterr()
    assert quarantine_run(argv) == 0
    rows = {r["asset"]: r for r in decisions(reg)}
    assert rows["BTCUSD"]["equity"] == 7.25 and rows["ETHUSD"] == eth
    assert "1 decision(s) chained, 1 already present" in capsys.readouterr().out


def test_a_restated_strategy_is_not_simulated(tmp_path, monkeypatch, capsys):
    """Review Focus 5: the pre-lock plan drops it before simulation; the
    under-lock plan still defers it with today's lines."""
    reg, spec, two, data = quarantined_split_calendar_full(tmp_path)
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD", "ETHUSD"]))
    restate(data, "ETHUSD", "2023-01-21", "2023-01-21,100.0,100.0,100.0,101.0,1.0")
    simulated = []
    real = quarantine_mod.observe_day
    monkeypatch.setattr(quarantine_mod, "observe_day",
                        lambda s, *x: simulated.append(s["strategy_id"]) or real(s, *x))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert simulated == [spec["strategy_id"]]
    cap = capsys.readouterr()
    assert "RESTATED" in cap.err
    assert f"{two['strategy_id']}  deferred:" in cap.out


def test_a_fast_path_day_verifies(tmp_path):
    reg, spec, two, data = quarantined_split_calendar_full(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-23")) == 0
    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout


# ---- Task 5 fix round 1 ----

def test_a_chain_moved_after_the_append_reports_what_landed(
        tmp_path, monkeypatch, capsys):
    """The post-append advance is the only ChainMoved that can strike AFTER
    the date's entries are on the chain: the message must say they WERE
    chained, never 'nothing written', and the run still fails loudly. On a
    plain --date run the view is built by snapshot(), so advance() call 1 is
    the under-lock one and call 2 the post-append one."""
    reg, spec, data = quarantined(tmp_path)
    real = Registry.advance
    calls = []

    def second_call_moves(self, snap, on_entry=None):
        calls.append(1)
        if len(calls) == 2:
            raise ChainMoved("forced")
        return real(self, snap, on_entry=on_entry)

    monkeypatch.setattr(Registry, "advance", second_call_moves)
    report = quarantine_mod.DateReport()
    with pytest.raises(ChainMoved):
        quarantine_run(argv_for(reg, data, "--date", "2023-01-22"),
                       report=report)
    monkeypatch.undo()
    assert len(calls) == 2
    assert len(decisions(reg)) == 1
    assert len(snapshots(reg)) == 1
    cap = capsys.readouterr()
    assert "WERE chained" in cap.err
    assert ("the 1 decision row(s) above plus the date's provenance "
            "snapshot/supplement WERE chained") in cap.err
    assert "nothing written" not in cap.err
    assert "1 decision(s) chained" in cap.out
    assert report.unchecked is True


def test_the_post_append_alarm_counts_rows_only_when_no_provenance_landed(
        tmp_path, monkeypatch, capsys):
    """Provenance already chained for the date: the batch is rows alone, so
    the alarm must not claim a snapshot/supplement landed with it."""
    reg, spec, data = quarantined(tmp_path)
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD"]))
    real = Registry.advance
    calls = []

    def second_call_moves(self, snap, on_entry=None):
        calls.append(1)
        if len(calls) == 2:
            raise ChainMoved("forced")
        return real(self, snap, on_entry=on_entry)

    monkeypatch.setattr(Registry, "advance", second_call_moves)
    with pytest.raises(ChainMoved):
        quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    monkeypatch.undo()
    assert len(calls) == 2
    assert len(decisions(reg)) == 1
    assert len(snapshots(reg)) == 1
    err = capsys.readouterr().err
    assert "the 1 decision row(s) above WERE chained" in err
    assert "provenance" not in err.split("WERE chained")[0]


def test_a_plan_that_changes_under_the_lock_refuses_and_writes_nothing(
        tmp_path, monkeypatch, capsys):
    reg, spec, two, data = quarantined_split_calendar_full(tmp_path)
    before = reg.log_path.read_bytes()
    real = quarantine_mod._plan_provenance
    calls = []

    def plan(view, date, ready, specs, bars_digests, data_digests):
        out = real(view, date, ready, specs, bars_digests, data_digests)
        calls.append(1)
        if len(calls) == 2:
            out.ready = list(out.ready) + ["ghost"]
        return out

    monkeypatch.setattr(quarantine_mod, "_plan_provenance", plan)
    report = quarantine_mod.DateReport()
    rc = quarantine_run(argv_for(reg, data, "--date", "2023-01-22"),
                        report=report)
    monkeypatch.undo()
    assert rc == 1
    assert len(calls) == 2
    assert "without being simulated" in capsys.readouterr().err
    assert reg.log_path.read_bytes() == before
    assert report.unchecked is True


def test_a_pre_lock_refusal_simulates_nothing(tmp_path, monkeypatch, capsys):
    """Ruling R7: a refusal the pre-lock plan already knows simulates nothing;
    the plan under the lock refuses again."""
    reg, spec, data = quarantined(tmp_path)
    reg.append("quarantine_data_snapshot_supplement",
               snap_payload(data, ["BTCUSD"]))
    simulated = []
    real = quarantine_mod.observe_day
    monkeypatch.setattr(quarantine_mod, "observe_day",
                        lambda *a, **k: simulated.append(1) or real(*a, **k))
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 1
    assert "has supplement provenance but no base" in capsys.readouterr().err
    assert simulated == []


def test_a_held_lock_defers_before_simulating(tmp_path, monkeypatch, capsys):
    """A held chain.lock no longer costs a whole date's simulation: the
    polite deferral (exit 0) comes first, and a simulation that would raise
    does not pre-empt it."""
    from .chainlock import ChainLock
    reg, spec, data = quarantined(tmp_path)
    simulated = []
    real = quarantine_mod.observe_day
    monkeypatch.setattr(quarantine_mod, "observe_day",
                        lambda *a, **k: simulated.append(1) or real(*a, **k))
    before = reg.log_path.read_bytes()
    other = ChainLock(reg.log_path.parent / "logs", holder="session",
                      purpose="manual work")
    other.acquire()
    try:
        report = quarantine_mod.DateReport()
        rc = quarantine_run(argv_for(reg, data, "--date", "2023-01-22"),
                            report=report)
    finally:
        other.release()
    assert rc == 0
    cap = capsys.readouterr()
    assert "deferred_lock" in cap.out and "--date 2023-01-22" in cap.out
    assert simulated == []
    assert report.unchecked is True
    assert reg.log_path.read_bytes() == before


# ---- final whole-branch review fixes ----

def test_a_repeated_cell_in_one_universe_chains_one_row(tmp_path):
    """A universe that repeats an asset yields two identical rows in one
    date's batch. The old per-row `seen` guard skipped the second; the batch
    append must too, or the chain verifies INVALID (duplicate decision)."""
    from .common import content_id
    from .test_gen3b import ENTERED
    from .test_gauntlet import gauntlet_registry
    from .test_screen import dated_target_hit_bars, write_data_dir
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_state_change(spec["strategy_id"], "graveyard",
                            "replaced by the repeated-asset fixture")
    twin = json.loads(json.dumps(spec))
    twin["universe"] = dict(twin["universe"], assets=["BTCUSD", "BTCUSD"])
    twin["strategy_id"] = None
    twin["strategy_id"] = content_id(twin, "strategy_id")
    reg.register_strategy(twin)
    reg.record_state_change(twin["strategy_id"], "screened", "test")
    reg.record_state_change(twin["strategy_id"], "gauntlet", None)
    reg.record_state_change(twin["strategy_id"], "quarantine", "test",
                            ts_utc=f"{ENTERED}T00:00:00Z")
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    rows = [e["payload"] for e in reg.entries()
            if e["entry_type"] == "quarantine_decision"
            and e["payload"]["strategy_id"] == twin["strategy_id"]
            and e["payload"]["date"] == "2023-01-22"]
    assert len(rows) == 1 and rows[0]["asset"] == "BTCUSD"
    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
