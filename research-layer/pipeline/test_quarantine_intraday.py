"""Quarantine is daily-only until 2b-2: an intraday spec is skipped and
reported as degraded, never simulated on daily bars (2b-1 Ruling R1)."""
from __future__ import annotations

import json

from .common import content_id
from .quarantine import run as quarantine_run
from .test_gen3b import argv_for, decisions, flat_dated_bars
from .test_quarantine_isolation import _quarantine, btc_and_eth_solo, ledger


def _intraday_clone(spec):
    one = json.loads(json.dumps(spec))
    one["universe"] = dict(one["universe"], timeframe="1h", session="24x7")
    one["strategy_id"] = None
    one["strategy_id"] = content_id(one, "strategy_id")
    return one


def test_an_intraday_spec_in_quarantine_is_skipped_and_reported(tmp_path, capsys):
    reg, a, b, data = btc_and_eth_solo(tmp_path)
    h = _intraday_clone(a)
    try:
        reg.register_strategy(h)
    except ValueError:
        reg.append("strategy_registered", h)   # cell validation is not the point here
    _quarantine(reg, h)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    sids = {r["strategy_id"] for r in decisions(reg)}
    assert h["strategy_id"] not in sids and a["strategy_id"] in sids
    assert "intraday" in capsys.readouterr().out
    items = ledger(reg)["items"]
    assert any(i["source"] == "intraday_unsupported" and i["key"] == h["strategy_id"]
               for i in items)


def _with_intraday_clone(tmp_path):
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    h = _intraday_clone(a)
    try:
        reg.register_strategy(h)
    except ValueError:
        reg.append("strategy_registered", h)   # cell validation is not the point here
    _quarantine(reg, h)
    return reg, a, b, h, data


def test_catch_up_records_the_daily_specs_and_skips_the_intraday_one(tmp_path, capsys):
    """Task 9 (addendum C): --catch-up runs the same guard. Every owed date
    records the 1d specs; the intraday spec records nothing on any date and
    is in the degraded ledger as intraday_unsupported."""
    reg, a, b, h, data = _with_intraday_clone(tmp_path)
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    out = capsys.readouterr().out
    rows = decisions(reg)
    assert {r["strategy_id"] for r in rows} == {a["strategy_id"], b["strategy_id"]}
    assert {r["date"] for r in rows} >= {"2023-01-22", "2023-01-23"}
    assert f"{h['strategy_id']}  skipped: intraday timeframe '1h'" in out
    items = [i for i in ledger(reg)["items"] if i["source"] == "intraday_unsupported"]
    assert [i["key"] for i in items] == [h["strategy_id"]]


def test_review_skips_the_intraday_spec_and_writes_nothing(tmp_path, capsys):
    """Task 9 (addendum C): --review reports the 1d specs' progress, prints
    the intraday spec's skip line instead of a progress line, and writes
    nothing (not even the degraded ledger)."""
    reg, a, b, h, data = _with_intraday_clone(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    before = reg.log_path.read_bytes()
    ledger_before = ledger(reg)
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--review")) == 0
    out = capsys.readouterr().out
    h_lines = [ln for ln in out.splitlines() if ln.startswith(h["strategy_id"])]
    assert h_lines == [f"{h['strategy_id']}  skipped: intraday timeframe '1h'; "
                       f"the intraday paper stage is not built yet (2b-2)"]
    for sid in (a["strategy_id"], b["strategy_id"]):
        assert any(ln.startswith(sid) and "days 1/" in ln for ln in out.splitlines()), sid
    assert reg.log_path.read_bytes() == before
    assert ledger(reg) == ledger_before
