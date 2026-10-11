"""2b-2 design s3.3 / s3.6: owed days for an intraday cell are its COMPLETE
UTC days; catch-up and review work unchanged on cell keys."""
from __future__ import annotations

from .quarantine import run as quarantine_run
from .test_gen3b import argv_for, decisions
from .test_intraday_statistics import _twin_bars, _twin_spec
from .test_quarantine_isolation import ledger
from .test_quarantine_record_intraday import _twins, _write, _register_quarantined
from .test_gauntlet import gauntlet_registry


def _days_of(reg, sid):
    return sorted({r["date"] for r in decisions(reg) if r["strategy_id"] == sid})


def test_catch_up_records_every_complete_owed_day_of_an_hourly_strategy(tmp_path):
    daily, hourly, _ = _twin_bars()
    end = daily[158]["date"]
    reg, data, daily_, sd, sh = _twins(tmp_path, end=end)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    want = [d["date"] for d in daily[151:159]]        # 8 days, under the 10 slots
    assert _days_of(reg, sh["strategy_id"]) == want
    assert _days_of(reg, sd["strategy_id"]) == want   # the daily twin, same days


def test_catch_up_never_records_an_incomplete_last_day(tmp_path):
    daily, hourly, _ = _twin_bars()
    last = daily[155]["date"]
    rows = [b for b in hourly if b["date"] < f"{last} 23:00:00"]
    reg, data, daily_, sd, sh = _twins(tmp_path, hourly_rows=rows, end=last)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert last not in _days_of(reg, sh["strategy_id"])
    assert daily[154]["date"] in _days_of(reg, sh["strategy_id"])
    assert last in _days_of(reg, sd["strategy_id"])   # the daily cell IS complete


def test_an_incomplete_last_day_is_not_owed_yet(tmp_path, capsys):
    """s3.3: an incomplete day is not owed, so once every complete day is
    recorded a second catch-up spends no slot retrying it."""
    daily, hourly, _ = _twin_bars()
    last = daily[155]["date"]
    rows = [b for b in hourly if b["date"] < f"{last} 23:00:00"]
    reg, data, daily_, sd, sh = _twins(tmp_path, hourly_rows=rows, end=last)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert _days_of(reg, sh["strategy_id"]) == [d["date"] for d in daily[151:155]]
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    out = capsys.readouterr().out
    assert "catch-up: nothing owed" in out, out


def test_review_reports_no_gap_for_a_fully_recorded_hourly_strategy(tmp_path, capsys):
    daily, hourly, _ = _twin_bars()
    end = daily[156]["date"]
    reg, data, daily_, sd, sh = _twins(tmp_path, end=end)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--review")) == 0
    out = capsys.readouterr().out
    block = out.split(sh["strategy_id"], 1)[1].split("\n\n")[0]
    assert "unrecorded bar dates in window: 0" in block
    assert "partially recorded dates: 0" in block


# ---- Task 4 review findings (a) and (c) ----

def test_a_second_catch_up_owes_nothing_for_a_recorded_hourly_strategy(tmp_path, capsys):
    """(a) The rows name the cell `BTCUSD@1h`; the universe the owed set is
    compared against must too, or every recorded 1h day is owed again."""
    daily, hourly, _ = _twin_bars()
    end = daily[158]["date"]
    reg, data, daily_, sd, sh = _twins(tmp_path, end=end)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    n = len(decisions(reg))
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    out = capsys.readouterr().out
    assert "catch-up: nothing owed" in out, out
    assert "--date" not in out, out
    assert len(decisions(reg)) == n


def test_recorded_hourly_days_never_starve_newer_owed_days(tmp_path):
    """(a) With more owed days than slots, the second run must spend its slots
    on the days the first did not reach, not on the 1h days it recorded."""
    daily, hourly, _ = _twin_bars()
    end = daily[170]["date"]                          # 20 owed days, 10 slots
    reg, data, daily_, sd, sh = _twins(tmp_path, end=end)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    first = [d["date"] for d in daily[151:161]]
    assert _days_of(reg, sh["strategy_id"]) == first
    assert _days_of(reg, sd["strategy_id"]) == first
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    want = [d["date"] for d in daily[151:171]]
    assert _days_of(reg, sh["strategy_id"]) == want
    assert _days_of(reg, sd["strategy_id"]) == want


def test_an_hourly_only_strategy_needs_only_its_hourly_file(tmp_path, capsys):
    """(c) With no `_1d.csv` on disk, the 1h strategy's owed days come from
    its own `_1h.csv`; nothing is skipped and no price file is reported
    missing."""
    reg, _ = gauntlet_registry(tmp_path)
    daily, hourly, _ = _twin_bars()
    end = daily[158]["date"]
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    _write(data, "BTCUSD_1h.csv", [b for b in hourly if b["date"][:10] <= end])
    sh = _register_quarantined(reg, _twin_spec("1h"), daily[150]["date"])
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    out = capsys.readouterr().out
    assert f"catch-up: {sh['strategy_id']} skipped" not in out, out
    assert _days_of(reg, sh["strategy_id"]) == [d["date"] for d in daily[151:159]]
    led = ledger(reg)
    assert not [i for i in (led or {}).get("items", [])
                if i["source"] == "price_file_missing"], led


def test_a_missing_hourly_file_is_reported_under_its_cell_key(tmp_path, capsys):
    """(c) The degraded item names the cell whose file is missing, and the
    daily twin on the same asset still records every owed day."""
    daily, hourly, _ = _twin_bars()
    end = daily[158]["date"]
    reg, data, daily_, sd, sh = _twins(tmp_path, end=end)
    (data / "BTCUSD_1h.csv").unlink()
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    out = capsys.readouterr().out
    assert f"catch-up: {sh['strategy_id']} skipped" in out, out
    items = ledger(reg)["items"]
    keys = [(i["source"], i["key"]) for i in items]
    assert ("price_file_missing", "BTCUSD@1h") in keys, keys
    reason = [i["reason"] for i in items if i["key"] == "BTCUSD@1h"
              and i["source"] == "price_file_missing"][0]
    assert reason.startswith("no price file for BTCUSD@1h at "), reason
    assert reason.endswith("BTCUSD_1h.csv"), reason
    assert ("price_file_missing", "BTCUSD") not in keys, keys
    assert _days_of(reg, sd["strategy_id"]) == [d["date"] for d in daily[151:159]]
    assert _days_of(reg, sh["strategy_id"]) == []


def test_cell_days_reads_each_cell_from_its_own_file(tmp_path):
    """`_cell_days` directly: a daily cell's bar dates and an hourly cell's
    COMPLETE days, each bounded by `through`; None for a missing file."""
    from .quarantine import _cell_days
    daily, hourly, _ = _twin_bars()
    last = daily[155]["date"]
    data = tmp_path / "data"
    data.mkdir()
    _write(data, "BTCUSD_1d.csv", [b for b in daily if b["date"] <= last])
    _write(data, "BTCUSD_1h.csv",
           [b for b in hourly if b["date"] < f"{last} 23:00:00"])
    assert _cell_days(data, "BTCUSD", "9999-12-31") == [b["date"] for b in daily[:156]]
    assert _cell_days(data, "BTCUSD@1h", "9999-12-31") == [b["date"] for b in daily[:155]]
    mid = daily[100]["date"]
    assert _cell_days(data, "BTCUSD", mid) == [b["date"] for b in daily[:101]]
    assert _cell_days(data, "BTCUSD@1h", mid) == [b["date"] for b in daily[:101]]
    assert _cell_days(data, "ETHUSD", "9999-12-31") is None
    assert _cell_days(data, "BTCUSD@4h", "9999-12-31") is None


def test_catch_up_names_a_missing_or_empty_hourly_file_by_its_cell(tmp_path, capsys):
    """The catch-up's own degraded items (before any --date run refreshes
    them): the path and the 'no bars' test are the CELL's, not `_1d.csv`'s."""
    from .quarantine import DateReport, _owed_dates_for_catch_up, quarantine_entry_dates
    daily, hourly, _ = _twin_bars()
    reg, data, daily_, sd, sh = _twins(tmp_path, end=daily[158]["date"])
    specs = {s["strategy_id"]: s for s in (sd, sh)}
    entered = quarantine_entry_dates(reg)
    sids = sorted(specs)

    (data / "BTCUSD_1h.csv").unlink()
    rep = DateReport()
    _owed_dates_for_catch_up(reg, sids, entered, specs, data, report=rep)
    assert [(i["source"], i["key"], i["reason"]) for i in rep.degraded] == [
        ("price_file_missing", "BTCUSD@1h",
         f"no price file for BTCUSD@1h at {data / 'BTCUSD_1h.csv'}")]

    _write(data, "BTCUSD_1h.csv", [])
    rep = DateReport()
    _owed_dates_for_catch_up(reg, sids, entered, specs, data, report=rep)
    assert [(i["source"], i["key"], i["reason"]) for i in rep.degraded] == [
        ("price_file_missing", "BTCUSD@1h", "price file for BTCUSD@1h has no bars")]
