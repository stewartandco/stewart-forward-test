"""Automating the quarantine -> live gate (2026-09-03, Coen: "build the
chained protocol"). livegate.run() already judges and, when not --dry-run,
writes verdicts and state changes. To run unattended it must (1) honour the
chain lock the way the quarantine daily does -- defer politely, exit 0, write
nothing -- and (2) leave a dated report for Coen's quarterly review.

The statistics are unit-tested in test_livegate.py; here `assess` and
`train_contributions` are monkeypatched so the fixture stays small.
"""
import json
from pathlib import Path

from . import livegate
from .chainlock import ChainLock
from .registry import Registry
from .test_screen import screening_registry, write_data_dir, dated_target_hit_bars


def _quarantined(tmp_path):
    reg, spec = screening_registry(tmp_path)
    sid = spec["strategy_id"]
    for to in ("screened", "gauntlet", "quarantine"):        # legal walk
        reg.record_state_change(sid, to, "test")
    reg.append("note", {"text": f"{livegate.PROTOCOL}: test anchor"})
    write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    return reg, sid


def _chain(reg):
    return [json.dumps(e, sort_keys=True) for e in reg.entries()]


def _patch_verdict(monkeypatch, sid, verdict):
    monkeypatch.setattr(livegate, "train_contributions", lambda spec, d, c: [0.01] * 10)
    monkeypatch.setattr(livegate, "assess", lambda cases, min_days, q=livegate.BH_Q: {
        sid: {"eligible": True, "forward_days": 90, "forward_trades": 7,
              "terminal": 0.5, "cone_p01": 0.8, "psr": 0.1,
              "verdict": verdict, "cohort_size": 1}})


def test_a_held_chain_lock_defers_the_gate_and_writes_nothing(tmp_path, monkeypatch, capsys):
    reg, sid = _quarantined(tmp_path)
    _patch_verdict(monkeypatch, sid, "graveyard")            # something TO write
    before = _chain(reg)
    other = ChainLock(tmp_path / "logs", holder="session", purpose="manual")
    other.acquire()
    try:
        rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data")])
    finally:
        other.release()
    assert rc == 0
    assert "deferred_lock" in capsys.readouterr().out
    assert _chain(Registry(reg.log_path)) == before
    assert Registry(reg.log_path).strategy_states()[sid] == "quarantine"


def test_the_apply_path_takes_and_releases_the_lock_and_still_writes(tmp_path, monkeypatch):
    reg, sid = _quarantined(tmp_path)
    _patch_verdict(monkeypatch, sid, "graveyard")
    rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data")])
    assert rc == 0
    reg2 = Registry(reg.log_path)
    assert reg2.strategy_states()[sid] == "graveyard"
    verdicts = [e for e in reg2.entries() if e["entry_type"] == "verdict"
                and e["payload"]["stage"] == "live_gate"]
    assert len(verdicts) == 1
    assert not (tmp_path / "logs" / "chain.lock").exists()   # released


def test_report_is_written_dated_and_works_with_dry_run(tmp_path, monkeypatch):
    """--report DIR writes DIR/<date>-livegate-assessment.md: one row per
    quarantined strategy with its verdict and the cohort size -- Coen's
    quarterly read. A dry run writes the report and nothing else."""
    reg, sid = _quarantined(tmp_path)
    _patch_verdict(monkeypatch, sid, "hold")
    out_dir = tmp_path / "docs" / "runs"
    before = _chain(reg)
    rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data"),
                       "--dry-run", "--report", str(out_dir)])
    assert rc == 0
    files = sorted(out_dir.glob("*-livegate-assessment.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert sid in body and "HOLD" in body.upper() and "cohort" in body.lower()
    assert livegate.PROTOCOL in body
    assert _chain(Registry(reg.log_path)) == before           # dry run: chain untouched
