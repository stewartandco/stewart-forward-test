"""Quarantine per-strategy isolation (2026-10-08 addendum; design s6)."""
import json

from . import quarantine as quarantine_mod
from .quarantine import run as quarantine_run
from .common import content_id
from .test_gauntlet import gauntlet_registry
from .test_gen3b import (ENTERED, argv_for, decisions, dated_target_hit_bars,
                         extend_ethusd, flat_dated_bars, read_csv_lines,
                         quarantined_split_calendar, snap_payload, snapshots,
                         supplements, write_csv_lines, write_data_dir)


def _quarantine(reg, spec):
    reg.record_state_change(spec["strategy_id"], "screened", "test")
    reg.record_state_change(spec["strategy_id"], "gauntlet", None)
    reg.record_state_change(spec["strategy_id"], "quarantine", "test",
                            ts_utc=f"{ENTERED}T00:00:00Z")


def _clone(spec, assets):
    two = json.loads(json.dumps(spec))
    two["universe"] = dict(two["universe"], assets=list(assets))
    two["strategy_id"] = None
    two["strategy_id"] = content_id(two, "strategy_id")
    return two


def btc_and_eth_solo(tmp_path, eth_bars=None):
    """A = BTCUSD only, B = ETHUSD only, both quarantined at ENTERED. By default
    ETHUSD ends 2023-01-21, so on 2023-01-22 B defers for a missing bar while A
    records -- the EFA shape's starting point."""
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "pass",
                       {"deflated_sharpe": 0.5}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "quarantine", "test",
                            ts_utc=f"{ENTERED}T00:00:00Z")
    b = _clone(spec, ["ETHUSD"])
    reg.register_strategy(b)
    _quarantine(reg, b)
    data = write_data_dir(tmp_path, {
        "BTCUSD": dated_target_hit_bars(),
        "ETHUSD": flat_dated_bars(n=21) if eth_bars is None else eth_bars})
    return reg, spec, b, data


def restate(data, asset, day, row):
    lines = read_csv_lines(data, asset)
    i = next(i for i, l in enumerate(lines) if l.startswith(f"{day},"))
    lines[i] = row
    write_csv_lines(data, lines, asset)


def ledger(reg):
    p = reg.log_path.parent / "logs" / "degraded_quarantine.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_the_efa_shape_a_restated_asset_with_nothing_owed_blocks_nobody(tmp_path, capsys):
    """2026-10-07: every EFA strategy already had its rows; the rows being
    written belonged to others. A restated asset whose strategies owe nothing
    must not be hashed, so the owing strategy records."""
    reg, a, b, data = btc_and_eth_solo(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"]}
    restate(data, "BTCUSD", "2023-01-22", "2023-01-22,111,111,111,112,1.0")
    extend_ethusd(data)
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"], b["strategy_id"]}
    assert "RESTATED" not in capsys.readouterr().err


def test_a_fully_recorded_strategy_is_not_resimulated(tmp_path, capsys, monkeypatch):
    reg, a, b, data = btc_and_eth_solo(tmp_path)
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    calls = []
    real = quarantine_mod.observe_day
    monkeypatch.setattr(quarantine_mod, "observe_day",
                        lambda spec, *x: calls.append(spec["strategy_id"]) or real(spec, *x))
    extend_ethusd(data)
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert calls == [b["strategy_id"]]
    assert "1 decision(s) chained, 1 already present" in capsys.readouterr().out


def test_a_missing_file_behind_a_fully_recorded_strategy_changes_nothing(tmp_path, capsys):
    """Review Focus 3: rule 1 runs BEFORE bars are loaded."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    (data / "ETHUSD_1d.csv").unlink()
    before = sum(1 for _ in reg.entries())
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert sum(1 for _ in reg.entries()) == before
    assert "2 already present" in capsys.readouterr().out


def test_a_rerun_where_only_a_lagging_strategy_owes_is_not_a_stall(tmp_path, capsys):
    """Rule 1 filters out the fully recorded strategies, so a same-day re-run
    sees only the lagging one, deferred for a missing bar. That is not a dead
    pipeline (rows for the date are already chained): rc 0, not the stall
    guard's REFUSED. The guard itself stays pinned by
    test_gen3b.test_every_eligible_spec_deferred_is_refused."""
    reg, spec, two, data = quarantined_split_calendar(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    cap = capsys.readouterr()
    assert "already present" in cap.out
    assert "REFUSED" not in cap.err


def quarantined_split_calendar_full(tmp_path):
    """quarantined_split_calendar with ETHUSD published through 2023-01-23,
    so the two-asset strategy is ready on 2023-01-22."""
    reg, spec, two, data = quarantined_split_calendar(tmp_path)
    extend_ethusd(data)
    return reg, spec, two, data


def test_a_restated_asset_defers_only_its_owing_strategy(tmp_path, capsys):
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD", "ETHUSD"]))
    restate(data, "ETHUSD", "2023-01-21", "2023-01-21,100.0,100.0,100.0,101.0,1.0")
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"]}
    cap = capsys.readouterr()
    assert f"{b['strategy_id']}  deferred:" in cap.out and "RESTATED" in cap.err
    led = ledger(reg)
    assert [(i["source"], i["key"]) for i in led["items"]] == [("restated", "ETHUSD")]


def test_a_two_asset_strategy_with_one_restated_asset_writes_no_row_at_all(tmp_path, capsys):
    """Review Focus 2: deferral is per STRATEGY."""
    reg, spec, two, data = quarantined_split_calendar_full(tmp_path)
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD", "ETHUSD"]))
    restate(data, "ETHUSD", "2023-01-21", "2023-01-21,100.0,100.0,100.0,101.0,1.0")
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert all(r["strategy_id"] != two["strategy_id"] for r in decisions(reg))


def test_only_restated_strategies_owing_is_deferred_only_and_exits_zero(tmp_path, monkeypatch):
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD", "ETHUSD"]))
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))     # both record
    restate(data, "ETHUSD", "2023-01-21", "2023-01-21,100.0,100.0,100.0,101.0,1.0")
    # B owes again only if its row is absent: hide it from the pre-filter view
    real = quarantine_mod.existing_decisions
    monkeypatch.setattr(quarantine_mod, "existing_decisions",
                        lambda r: {k for k in real(r) if k[0] != b["strategy_id"]})
    rep = quarantine_mod.DateReport()
    rc = quarantine_run(argv_for(reg, data, "--date", "2023-01-22"), report=rep)
    assert rc == 0 and rep.deferred_only is True


def test_a_missing_price_file_defers_only_its_strategy(tmp_path, capsys):
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    (data / "ETHUSD_1d.csv").unlink()
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"]}
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [("price_file_missing", "ETHUSD")]


def test_every_owing_strategy_missing_its_file_is_still_a_total_stall(tmp_path, capsys):
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    (data / "ETHUSD_1d.csv").unlink()
    (data / "BTCUSD_1d.csv").unlink()
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 1
    assert "REFUSED: nothing recorded" in capsys.readouterr().err


def test_a_date_run_never_removes_a_ledger_item(tmp_path):
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    from . import degraded as dg
    p = reg.log_path.parent / "logs" / dg.QUARANTINE_LEDGER
    dg.record(p, "quarantine", [{"source": "restated", "key": "EFA", "reason": "x"}],
              remove_unseen=True)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert [i["key"] for i in ledger(reg)["items"]] == ["EFA"]


def test_a_failing_ledger_write_never_changes_the_exit_code(tmp_path, capsys, monkeypatch):
    """Controller ruling: the ledger is observability; a PermissionError on it
    must not turn a recorded day into a non-zero exit (the loop would retry)."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    from . import degraded as dg

    def boom(*args, **kwargs):
        raise PermissionError("ledger is locked")
    monkeypatch.setattr(dg, "record", boom)
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert "degraded_ledger_error" in capsys.readouterr().err
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"], b["strategy_id"]}


def test_a_deferred_strategy_s_new_asset_is_never_supplemented(tmp_path):
    """Rule 2 narrows the provenance map to the strategies that still record.
    X (ETHUSD + SOLUSD) is deferred for the restated ETHUSD, so SOLUSD -- not
    yet covered by the chained base -- must NOT be supplemented: that would
    chain provenance for bars no row was computed from."""
    reg, a = gauntlet_registry(tmp_path)
    reg.record_verdict(a["strategy_id"], "gauntlet", "pass",
                       {"deflated_sharpe": 0.5}, "0" * 64)
    reg.record_state_change(a["strategy_id"], "quarantine", "test",
                            ts_utc=f"{ENTERED}T00:00:00Z")
    x = _clone(a, ["ETHUSD", "SOLUSD"])
    reg.register_strategy(x)
    _quarantine(reg, x)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars(),
                                     "ETHUSD": flat_dated_bars(),
                                     "SOLUSD": flat_dated_bars()})
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD", "ETHUSD"]))
    restate(data, "ETHUSD", "2023-01-21", "2023-01-21,100.0,100.0,100.0,101.0,1.0")
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert {r["strategy_id"] for r in decisions(reg)} == {a["strategy_id"]}
    assert supplements(reg) == []
    assert len(snapshots(reg)) == 1


# ---- end to end through --catch-up, the REAL seams (no monkeypatching) ----

def test_catch_up_reports_a_restated_asset_and_charges_no_slot_for_its_date(tmp_path, capsys):
    """B owes 2023-01-22 and its ETHUSD bars were restated after that date's
    provenance was chained, so the date records nothing and costs no slot.
    Built honestly: A records 01-22 BEFORE B is quarantined, so B's row is
    genuinely absent from the chain."""
    reg, a = gauntlet_registry(tmp_path)
    reg.record_verdict(a["strategy_id"], "gauntlet", "pass",
                       {"deflated_sharpe": 0.5}, "0" * 64)
    reg.record_state_change(a["strategy_id"], "quarantine", "test",
                            ts_utc=f"{ENTERED}T00:00:00Z")
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars(),
                                     "ETHUSD": flat_dated_bars()})
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD", "ETHUSD"]))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    b = _clone(a, ["ETHUSD"])
    reg.register_strategy(b)
    _quarantine(reg, b)
    restate(data, "ETHUSD", "2023-01-21", "2023-01-21,100.0,100.0,100.0,101.0,1.0")
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    out = capsys.readouterr().out
    assert "2023-01-22 recorded nothing" in out and "no slot used" in out
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [("restated", "ETHUSD")]
    # the date after it still records both strategies
    assert {(r["strategy_id"], r["date"]) for r in decisions(reg)} >= {
        (a["strategy_id"], "2023-01-23"), (b["strategy_id"], "2023-01-23")}


def test_catch_up_reports_a_missing_price_file_even_when_no_date_is_owed(tmp_path, capsys):
    """Nothing runs through --date here (A and B are fully recorded and then B's
    file disappears), so only the catch-up's own pre-filter can report it."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    for d in ("2023-01-22", "2023-01-23"):
        assert quarantine_run(argv_for(reg, data, "--date", d)) == 0
    (data / "ETHUSD_1d.csv").unlink()
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert "nothing owed" in capsys.readouterr().out
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [("price_file_missing", "ETHUSD")]


def test_catch_up_skipped_on_chain_lock_keeps_existing_ledger_items(tmp_path, capsys):
    """The nested --date returns 0 on a held chain.lock before checking
    anything; the authoritative write must not read that silence as recovery."""
    from .chainlock import ChainLock
    from . import degraded as dg
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    p = reg.log_path.parent / "logs" / dg.QUARANTINE_LEDGER
    dg.record(p, "quarantine", [{"source": "restated", "key": "EFA", "reason": "x"}],
              remove_unseen=True)
    other = ChainLock(reg.log_path.parent / "logs", holder="session", purpose="manual work")
    other.acquire()
    try:
        capsys.readouterr()
        assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    finally:
        other.release()
    assert "deferred_lock" in capsys.readouterr().out
    assert [i["key"] for i in ledger(reg)["items"]] == ["EFA"]
