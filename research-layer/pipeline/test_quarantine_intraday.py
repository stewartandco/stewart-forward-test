"""Quarantine is daily-only until 2b-2: an intraday spec is skipped and
reported as degraded, never simulated on daily bars (2b-1 Ruling R1)."""
from __future__ import annotations

import json

from .common import content_id
from .quarantine import run as quarantine_run
from .test_gen3b import argv_for, decisions
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
