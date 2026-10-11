"""2b-2 design s3.1, s3.3, s3.6: the --date path records an intraday
strategy on cell keys; daily behaviour is unchanged."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from .common import content_id
from .quarantine import run as quarantine_run
from .test_gen3b import argv_for, decisions
from .test_intraday_statistics import _twin_bars, _twin_spec
from .test_quarantine_isolation import ledger
from .test_gauntlet import gauntlet_registry

LAYER = Path(__file__).resolve().parent.parent


def _write(data, name, rows):
    with (data / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low",
                                          "close", "volume"])
        w.writeheader()
        w.writerows(rows)


def _register_quarantined(reg, spec, entered):
    spec = json.loads(json.dumps(spec))
    spec["strategy_id"] = None
    spec["strategy_id"] = content_id(spec, "strategy_id")
    try:
        reg.register_strategy(spec)
    except ValueError:
        reg.append("strategy_registered", spec)   # cell admission is not the point
    reg.record_state_change(spec["strategy_id"], "screened", "test")
    reg.record_state_change(spec["strategy_id"], "gauntlet", None)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "pass",
                       {"deflated_sharpe": 0.5}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "quarantine", "test",
                            ts_utc=f"{entered}T00:00:00Z")
    return spec


def _twins(tmp_path, hourly_rows=None, end=None):
    """A 1d and a 1h twin on BTCUSD, both quarantined on day 150. `end`
    truncates BOTH price files at that day (so a catch-up owes few enough
    days to reach every one); `hourly_rows` overrides the hourly file."""
    reg, _ = gauntlet_registry(tmp_path)
    daily, hourly, _ = _twin_bars()
    if end is not None:
        daily = [b for b in daily if b["date"] <= end]
        hourly = [b for b in hourly if b["date"][:10] <= end]
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    _write(data, "BTCUSD_1d.csv", daily)
    _write(data, "BTCUSD_1h.csv", hourly if hourly_rows is None else hourly_rows)
    entered = daily[150]["date"]
    sd = _register_quarantined(reg, _twin_spec("1d"), entered)
    sh = _register_quarantined(reg, _twin_spec("1h"), entered)
    return reg, data, daily, sd, sh


def _verify(reg):
    r = subprocess.run([sys.executable, str(LAYER / "verify_registry.py"),
                        str(reg.log_path)], capture_output=True, text=True,
                       cwd=str(LAYER))
    return r.returncode, r.stdout + r.stderr


def test_daily_and_hourly_on_one_asset_use_disjoint_keys(tmp_path):
    reg, data, daily, sd, sh = _twins(tmp_path)
    day = daily[151]["date"]
    assert quarantine_run(argv_for(reg, data, "--date", day)) == 0
    rows = {r["strategy_id"]: r for r in decisions(reg)}
    assert rows[sd["strategy_id"]]["asset"] == "BTCUSD"
    assert rows[sh["strategy_id"]]["asset"] == "BTCUSD@1h"
    assert {"entries", "exits"} <= set(rows[sh["strategy_id"]])
    snap = [e["payload"] for e in reg.entries()
            if e["entry_type"] == "quarantine_data_snapshot"][0]
    assert set(snap["bars_sha256"]) == {"BTCUSD", "BTCUSD@1h"}
    assert set(snap["data_sha256"]) == {"BTCUSD", "BTCUSD@1h"}
    rc, out = _verify(reg)
    # The verifier flags the fixture's own hand-registered twins (their
    # provenance carries no card_ids), so rc is 1 for a reason this test does
    # not own. What it owns: no failure line names a quarantine entry, i.e.
    # every row is covered by an earlier snapshot under its cell key.
    fails = [ln for ln in out.splitlines() if ln.lstrip().startswith("line ")]
    assert not [ln for ln in fails if "quarantine_decision" in ln
                or "quarantine_data_snapshot" in ln], out


def test_an_incomplete_intraday_day_defers_only_its_strategy(tmp_path, capsys):
    daily, hourly, _ = _twin_bars()
    day = daily[151]["date"]
    cut = [b for b in hourly if b["date"] < f"{day} 23:00:00"]   # day without its last slot
    reg, data, daily, sd, sh = _twins(tmp_path, hourly_rows=cut)
    assert quarantine_run(argv_for(reg, data, "--date", day)) == 0
    sids = {r["strategy_id"] for r in decisions(reg)}
    assert sd["strategy_id"] in sids and sh["strategy_id"] not in sids
    assert f"{sh['strategy_id']}  deferred: no BTCUSD@1h bar for {day}" in capsys.readouterr().out


def test_a_restated_intraday_cell_defers_only_its_owing_strategy(tmp_path, capsys):
    """Rule 2 on a cell key: `day` is chained (base snapshot covers BTCUSD
    and BTCUSD@1h); a second 1h strategy then owes `day`, but one hourly bar
    of `day` was restated, so it is deferred and reported; nothing else moves."""
    reg, data, daily, sd, sh = _twins(tmp_path)
    day = daily[151]["date"]
    assert quarantine_run(argv_for(reg, data, "--date", day)) == 0
    sh2 = json.loads(json.dumps(_twin_spec("1h")))
    sh2["provenance"] = {"sibling_group_id": "twin-1h-b"}
    sh2 = _register_quarantined(reg, sh2, daily[150]["date"])
    p = data / "BTCUSD_1h.csv"
    lines = p.read_text(encoding="utf-8").splitlines()
    i = next(i for i, ln in enumerate(lines) if ln.startswith(f"{day} 05:00:00,"))
    parts = lines[i].split(",")
    parts[4] = str(float(parts[4]) + 1.0)
    lines[i] = ",".join(parts)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", day)) == 0
    out = capsys.readouterr()
    assert "RESTATED" in out.err and "BTCUSD@1h" in out.err
    assert sh2["strategy_id"] not in {r["strategy_id"] for r in decisions(reg)}
    assert ("restated", "BTCUSD@1h") in {(i["source"], i["key"])
                                         for i in ledger(reg)["items"]}


def test_a_non_24x7_intraday_spec_is_deferred_not_a_crash(tmp_path, capsys):
    reg, data, daily, sd, sh = _twins(tmp_path)
    bad = json.loads(json.dumps(_twin_spec("1h")))
    bad["universe"]["session"] = "us_equity"
    bad = _register_quarantined(reg, bad, daily[150]["date"])
    day = daily[151]["date"]
    assert quarantine_run(argv_for(reg, data, "--date", day)) == 0
    assert bad["strategy_id"] not in {r["strategy_id"] for r in decisions(reg)}
    items = [i for i in ledger(reg)["items"] if i["source"] == "intraday_unsupported"]
    assert [i["key"] for i in items] == [bad["strategy_id"]]
