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
    monkeypatch.setattr(livegate, "assess", lambda cases, min_days, q=livegate.BH_Q, hold_arm=False: {
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


# ---------------- v2 switch: the quarantine-live-protocol-v2 note ----------------

V2_ROW = {"p_zero": 0.01, "p_hold": 0.02, "p": 0.02,
          "strategy_sr": 2.0, "hold_sr": 0.5}


def _patch_live(monkeypatch, sid, seen):
    """A graduating case. The fake records the hold_arm it was called with and,
    like assess, adds the v2 keys only under the hold arm."""
    monkeypatch.setattr(livegate, "train_contributions", lambda spec, d, c: [0.01] * 10)

    def fake(cases, min_days, q=livegate.BH_Q, hold_arm=False):
        seen.append(hold_arm)
        r = {"eligible": True, "forward_days": 90, "forward_trades": 7,
             "terminal": 1.5, "cone_p01": 0.8, "psr": 0.99,
             "verdict": "live", "cohort_size": 1}
        return {sid: r | V2_ROW if hold_arm else r}
    monkeypatch.setattr(livegate, "assess", fake)


def _gate_entries(reg, sid):
    reg2 = Registry(reg.log_path)
    v = [e["payload"] for e in reg2.entries() if e["entry_type"] == "verdict"
         and e["payload"]["stage"] == "live_gate"]
    s = [e["payload"] for e in reg2.entries() if e["entry_type"] == "state_change"
         and e["payload"]["strategy_id"] == sid and e["payload"]["to"] == "live"]
    return v, s


def test_v2_note_switches_and_labels(tmp_path, monkeypatch):
    reg, sid = _quarantined(tmp_path)
    reg.append("note", {"text": "quarantine-live-protocol-v2: test anchor"})
    seen = []
    _patch_live(monkeypatch, sid, seen)
    out_dir = tmp_path / "reports"
    rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data"),
                       "--report", str(out_dir)])
    assert rc == 0
    assert seen == [True]
    verdicts, changes = _gate_entries(reg, sid)
    assert len(verdicts) == 1
    m = verdicts[0]["metrics"]
    assert m["protocol"] == "quarantine-live-protocol-v2"
    assert (m["p_zero"], m["p_hold"], m["p"]) == (0.01, 0.02, 0.02)
    assert [c["reason"] for c in changes] == ["quarantine-live-protocol-v2 pass"]
    body = next(out_dir.glob("*-livegate-assessment.md")).read_text(encoding="utf-8")
    assert "`quarantine-live-protocol-v2`" in body
    assert "beats zero AND own-asset hold" in body


def test_v1_only_chain_unchanged(tmp_path, monkeypatch):
    reg, sid = _quarantined(tmp_path)
    seen = []
    _patch_live(monkeypatch, sid, seen)
    out_dir = tmp_path / "reports"
    rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data"),
                       "--report", str(out_dir)])
    assert rc == 0
    assert seen == [False]
    verdicts, changes = _gate_entries(reg, sid)
    m = verdicts[0]["metrics"]
    assert set(m) == {"forward_days", "forward_trades", "terminal", "cone_p01",
                      "psr", "eligible", "protocol", "cohort_size"}
    assert m["protocol"] == "quarantine-live-protocol-v1"
    assert [c["reason"] for c in changes] == ["quarantine-live-protocol-v1 pass"]
    body = next(out_dir.glob("*-livegate-assessment.md")).read_text(encoding="utf-8")
    assert "quarantine-live-protocol-v2" not in body
    assert "own-asset hold" not in body


def test_v2_mention_does_not_arm(tmp_path, monkeypatch):
    """Arming is a startswith on 'quarantine-live-protocol-v2: ' (with the
    colon). A note that merely mentions it, or lacks the colon, does not."""
    reg, sid = _quarantined(tmp_path)
    reg.append("note", {"text": "draft: quarantine-live-protocol-v2: not yet"})
    reg.append("note", {"text": "quarantine-live-protocol-v2 is under review"})
    seen = []
    _patch_live(monkeypatch, sid, seen)
    rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data")])
    assert rc == 0
    assert seen == [False]
    verdicts, _ = _gate_entries(reg, sid)
    assert verdicts[0]["metrics"]["protocol"] == "quarantine-live-protocol-v1"
    assert "p_hold" not in verdicts[0]["metrics"]


def test_v2_note_without_v1_still_refuses(tmp_path, capsys):
    """The v1 refusal is unchanged: v2 arms the hold test, it does not stand
    in for the chained v1 protocol."""
    reg, spec = screening_registry(tmp_path)
    sid = spec["strategy_id"]
    for to in ("screened", "gauntlet", "quarantine"):
        reg.record_state_change(sid, to, "test")
    reg.append("note", {"text": "quarantine-live-protocol-v2: test anchor"})
    before = _chain(reg)
    rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path)])
    assert rc == 1
    assert f"REFUSED: no '{livegate.PROTOCOL}'" in capsys.readouterr().out
    assert _chain(Registry(reg.log_path)) == before


def test_v2_graveyard_verdict_carries_null_p_fields(tmp_path, monkeypatch):
    """A buried strategy is never hold-tested (kill first): under v2 its
    verdict still names v2 and carries the p fields, as None."""
    reg, sid = _quarantined(tmp_path)
    reg.append("note", {"text": "quarantine-live-protocol-v2: test anchor"})
    _patch_verdict(monkeypatch, sid, "graveyard")
    rc = livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data")])
    assert rc == 0
    verdicts, _ = _gate_entries(reg, sid)
    m = verdicts[0]["metrics"]
    assert m["protocol"] == "quarantine-live-protocol-v2"
    assert (m["p_zero"], m["p_hold"], m["p"]) == (None, None, None)
    assert Registry(reg.log_path).strategy_states()[sid] == "graveyard"
