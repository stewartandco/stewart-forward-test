"""2b-2: quarantine records supported intraday specs; only a NON-24x7
intraday spec is refused (degraded intraday_unsupported). The supported path
is tested in test_quarantine_record_intraday.py."""
from __future__ import annotations

import json

from .common import content_id
from .quarantine import run as quarantine_run
from .test_gen3b import argv_for, decisions
from .test_quarantine_isolation import _quarantine, btc_and_eth_solo, ledger


def _unsupported_clone(spec):
    one = json.loads(json.dumps(spec))
    one["universe"] = dict(one["universe"], timeframe="1h", session="us_equity")
    one["strategy_id"] = None
    one["strategy_id"] = content_id(one, "strategy_id")
    return one


def test_a_non_24x7_intraday_spec_is_skipped_and_reported(tmp_path, capsys):
    reg, a, b, data = btc_and_eth_solo(tmp_path)
    h = _unsupported_clone(a)
    try:
        reg.register_strategy(h)
    except ValueError:
        reg.append("strategy_registered", h)
    _quarantine(reg, h)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    sids = {r["strategy_id"] for r in decisions(reg)}
    assert h["strategy_id"] not in sids and a["strategy_id"] in sids
    out = capsys.readouterr().out
    assert f"{h['strategy_id']}  skipped: intraday timeframe '1h' in session 'us_equity'" in out
    assert any(i["source"] == "intraday_unsupported" and i["key"] == h["strategy_id"]
               for i in ledger(reg)["items"])
