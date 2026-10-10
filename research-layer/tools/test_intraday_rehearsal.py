from __future__ import annotations

import json

from pipeline.common import content_id
from pipeline.registry import Registry
from pipeline.test_gauntlet import gauntlet_registry
from tools import intraday_rehearsal as ir


def _v2_original(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    two = json.loads(json.dumps(spec))
    two["version"] = 2
    two["strategy_id"] = None
    two["strategy_id"] = content_id(two, "strategy_id")
    reg.register_strategy(two)
    return reg, spec, two


def test_register_makes_a_1d_and_a_1h_twin_per_asset(tmp_path):
    reg, v1, orig = _v2_original(tmp_path)
    out = tmp_path / "twins.json"
    rc = ir.main(["register", "--registry", str(reg.log_path),
                  "--assets", "BTCUSDT,ETHUSDT", "--originals", "1",
                  "--seed", "1", "--out", str(out)])
    assert rc == 0
    twins = json.loads(out.read_text())
    assert len(twins) == 4                                  # 1 x 2 assets x 2 tfs
    assert {(t["asset"], t["tf"]) for t in twins.values()} == {
        ("BTCUSDT", "1d"), ("BTCUSDT", "1h"), ("ETHUSDT", "1d"), ("ETHUSDT", "1h")}
    assert {t["original"] for t in twins.values()} == {orig["strategy_id"]}
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in Registry(reg.log_path).entries()
             if e["entry_type"] == "strategy_registered"}
    for sid, t in twins.items():
        s = specs[sid]
        assert sid not in (v1["strategy_id"], orig["strategy_id"])
        assert s["universe"] == dict(orig["universe"], assets=[t["asset"]],
                                     timeframe=t["tf"], session="24x7",
                                     asset_class="crypto")
        assert s["cost_model"] == orig["cost_model"]
        assert s["provenance"]["run_id"] == "rehearsal-2b1"
        assert s["provenance"]["sibling_group_id"] == (
            f"rehearsal-2b1:{orig['strategy_id']}:{t['asset']}_{t['tf']}")
        chk = dict(s, strategy_id=None)
        assert content_id(chk, "strategy_id") == sid        # fresh and honest
    assert len(set(twins)) == 4


def test_register_skips_version_1_originals(tmp_path):
    reg, v1 = gauntlet_registry(tmp_path)                   # version 1 only
    try:
        ir.register(reg.log_path, ["BTCUSDT"], 1, 1, tmp_path / "t.json")
    except SystemExit as exc:
        assert "eligible" in str(exc)
    else:
        raise AssertionError("a version-1 original must not be picked")


def test_report_reads_verdicts_and_config(tmp_path):
    reg, _, orig = _v2_original(tmp_path)
    out = tmp_path / "twins.json"
    ir.main(["register", "--registry", str(reg.log_path), "--assets", "BTCUSDT",
             "--originals", "1", "--seed", "1", "--out", str(out)])
    twins = json.loads(out.read_text())
    sid = next(s for s, t in twins.items() if t["tf"] == "1h")
    reg.record_state_change(sid, "screened", "t")
    reg.record_verdict(sid, "screened", "pass", {"trades": 77}, "0" * 64)
    reg.record_state_change(sid, "gauntlet", None)
    reg.record_verdict(sid, "gauntlet", "fail",
                       {"fail_reason": "mc_p05", "train_sharpe": 1.5}, "0" * 64)
    art = tmp_path / "art" / sid / "gauntlet"
    art.mkdir(parents=True)
    (art / "config.json").write_text(json.dumps(
        {"metrics": {"is_edge_per_trade": 0.01, "oos_edge_per_trade": 0.02,
                     "mc_p05_equity": 0.9, "p_ruin": 0.25}}))
    rep = tmp_path / "report.md"
    assert ir.main(["report", "--registry", str(reg.log_path), "--twins", str(out),
                    "--artifacts-dir", str(tmp_path / "art"), "--out", str(rep)]) == 0
    text = rep.read_text()
    row = next(l for l in text.splitlines() if sid in l)
    assert "pass (77)" in row and "fail" in row and "mc_p05" in row
    assert "1.500" in row and "0.01000" in row and "0.9000"[:5] in row and "0.2500" in row
    assert "| 1d |" in text and "| 1h |" in text
