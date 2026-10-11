"""2b-2 design s3.5: fetch only the intraday cells quarantined strategies owe;
a failed cell is a degraded item that persists until a fetch succeeds."""
from __future__ import annotations

import json
from pathlib import Path

from . import degraded as dg
from .quarantine import run as quarantine_run, fetch_owed, FETCH_SOURCE
from .test_gen3b import argv_for
from .test_intraday_statistics import _twin_spec
from .test_quarantine_isolation import ledger
from .test_quarantine_record_intraday import _twins, _register_quarantined


def _specs(*specs):
    return {s["strategy_id"]: s for s in specs}


def test_no_intraday_cell_owed_means_no_fetch(tmp_path):
    calls = []
    sd = _twin_spec("1d")
    items = fetch_owed([sd["strategy_id"]], _specs(sd), tmp_path,
                       fetch=lambda *a, **k: calls.append(a))
    assert calls == [] and items == []


def test_each_owed_cell_is_fetched_once(tmp_path):
    a = _twin_spec("1h")
    b = json.loads(json.dumps(a))
    b["strategy_id"] = "b" * 16
    calls = []
    items = fetch_owed([a["strategy_id"], b["strategy_id"]], _specs(a, b), tmp_path,
                       fetch=lambda sym, tf, d: calls.append((sym, tf)) or {})
    assert calls == [("BTCUSD", "1h")] and items == []


def test_a_failed_fetch_is_an_item_and_the_other_cells_still_fetch(tmp_path):
    a = _twin_spec("1h")
    b = json.loads(json.dumps(a))
    b["strategy_id"] = "b" * 16
    b["universe"]["assets"] = ["ETHUSD"]
    calls = []

    def fetch(sym, tf, d):
        calls.append(sym)
        if sym == "BTCUSD":
            raise RuntimeError("HTTP 451")
        return {}
    items = fetch_owed([a["strategy_id"], b["strategy_id"]], _specs(a, b),
                       tmp_path, fetch=fetch)
    assert sorted(calls) == ["BTCUSD", "ETHUSD"]
    assert [(i["source"], i["key"]) for i in items] == [(FETCH_SOURCE, "BTCUSD@1h")]
    assert "HTTP 451" in items[0]["reason"]


def test_the_cli_exits_zero_and_records_the_item(tmp_path, monkeypatch):
    reg, data, daily, sd, sh = _twins(tmp_path)
    from . import crypto_data

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(crypto_data, "fetch_symbol", boom)
    assert quarantine_run(argv_for(reg, data, "--fetch-owed")) == 0
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [
        (FETCH_SOURCE, "BTCUSD@1h")]


def test_catch_up_never_drops_a_fetch_item(tmp_path, monkeypatch):
    """The catch-up must REACH every owed date (few owed days), so its write
    is the authoritative remove_unseen=True one; only keep_sources saves the
    fetch item."""
    from .test_intraday_statistics import _twin_bars
    reg, data, daily, sd, sh = _twins(tmp_path, end=_twin_bars()[0][153]["date"])
    p = reg.log_path.parent / "logs" / dg.QUARANTINE_LEDGER
    dg.record(p, "quarantine", [{"source": FETCH_SOURCE, "key": "BTCUSD@1h",
                                 "reason": "down"}], remove_unseen=False)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert any(i["source"] == FETCH_SOURCE for i in ledger(reg)["items"])


def test_a_recovered_fetch_clears_its_item_only(tmp_path, monkeypatch):
    reg, data, daily, sd, sh = _twins(tmp_path)
    p = reg.log_path.parent / "logs" / dg.QUARANTINE_LEDGER
    dg.record(p, "quarantine", [
        {"source": FETCH_SOURCE, "key": "BTCUSD@1h", "reason": "down"},
        {"source": "restated", "key": "EFA", "reason": "x"}], remove_unseen=False)
    from . import crypto_data
    monkeypatch.setattr(crypto_data, "fetch_symbol", lambda *a, **k: {})
    assert quarantine_run(argv_for(reg, data, "--fetch-owed")) == 0
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [("restated", "EFA")]


def test_fetch_owed_is_exclusive_with_the_other_modes(tmp_path):
    reg, data, daily, sd, sh = _twins(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--fetch-owed", "--catch-up")) == 1


def test_the_wrapper_runs_fetch_owed_before_the_date():
    """The fetch step precedes the daily --date record (so the record sees the
    fresh bars) and its crash reaches the task's exit code through FETCH_RC."""
    bat = (Path(__file__).resolve().parent.parent / "tasks"
           / "run_quarantine.bat").read_text(encoding="utf-8").splitlines()
    cmds = [ln for ln in bat if not ln.lstrip().upper().startswith("REM")]
    fetch = [i for i, ln in enumerate(cmds) if "--fetch-owed" in ln]
    date = [i for i, ln in enumerate(cmds) if "--date" in ln]
    assert len(fetch) == 1 and len(date) == 1 and fetch[0] < date[0]
    assert 'if "%FETCH_RC%"=="1" goto :fail' in [ln.strip() for ln in cmds]


def test_a_binance_ban_stops_the_fetch_and_owes_every_remaining_cell(tmp_path):
    """HTTP 418 is an IP-wide ban shared by every Binance consumer on this
    box: one more call can extend it, so the loop must stop at once."""
    from .crypto_data import BinanceBanned
    a = _twin_spec("1h")
    b = json.loads(json.dumps(a))
    b["strategy_id"] = "b" * 16
    b["universe"]["assets"] = ["ETHUSD"]
    calls = []

    def fetch(sym, tf, d):
        calls.append(sym)
        raise BinanceBanned("418 from Binance: IP-wide ban")
    items = fetch_owed([a["strategy_id"], b["strategy_id"]], _specs(a, b),
                       tmp_path, fetch=fetch)
    assert calls == ["BTCUSD"]
    assert [(i["source"], i["key"]) for i in items] == [
        (FETCH_SOURCE, "BTCUSD@1h"), (FETCH_SOURCE, "ETHUSD@1h")]
    assert "ban" in items[1]["reason"].lower() and "418" in items[1]["reason"]


def test_nothing_owed_never_imports_crypto_data(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pipeline.crypto_data", None)
    sd = _twin_spec("1d")
    assert fetch_owed([sd["strategy_id"]], _specs(sd), tmp_path) == []


def test_a_crashed_fetch_step_still_lets_the_record_run_and_fails_the_task(
        tmp_path, monkeypatch):
    """Behavioural: a --fetch-owed that exits 1 must not skip --date or
    --catch-up or the commit, and the task's final exit is 1."""
    import os
    import pytest
    if os.name != "nt":
        pytest.skip("runs the .bat under cmd.exe")
    from . import test_chain_mirror_wrappers as w
    stub = (
        "import sys, pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "with open(L / 'logs' / 'calls.txt', 'a') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "with open(L / 'registry_log.jsonl', 'a') as f: f.write('{\"q\": 1}\\n')\n"
        "if '--fetch-owed' in sys.argv:\n"
        "    sys.exit(1)\n")
    repo, bat = w._scratch(tmp_path, "run_quarantine.bat")
    (repo / "research-layer/pipeline/quarantine.py").write_text(stub)
    code = w._run(bat)
    calls = (repo / "research-layer/logs/calls.txt").read_text().splitlines()
    assert calls[0].startswith("--fetch-owed")
    assert any(c.startswith("--date") for c in calls) and "--catch-up" in calls
    assert code == 1
    assert w._git(repo, "rev-list", "--count", "HEAD").strip() == "2"
