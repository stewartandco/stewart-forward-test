"""Quarantine per-strategy isolation (2026-10-08 addendum; design s6)."""
import json

from . import quarantine as quarantine_mod
from .quarantine import run as quarantine_run
from .common import content_id
from .test_gauntlet import gauntlet_registry
from .test_gen3b import (ENTERED, argv_for, decisions, dated_target_hit_bars,
                         extend_ethusd, flat_dated_bars, read_csv_lines,
                         snap_payload, write_csv_lines, write_data_dir)


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
