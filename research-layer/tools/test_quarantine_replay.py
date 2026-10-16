from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pipeline.quarantine import run as quarantine_run
from pipeline.registry import Registry
from pipeline.test_gen3b import argv_for, flat_dated_bars, quarantined

LAYER = Path(__file__).resolve().parent.parent
TOOL = LAYER / "tools" / "quarantine_replay.py"


def _tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True)


def _chain_with_two_days(tmp_path):
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    n_before = len(reg.log_path.read_bytes().splitlines())
    # stamp the next day's entries late enough to cut on
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-23")) == 0
    return reg, data, n_before


def test_prefix_cuts_before_the_first_quarantine_entry_at_or_after_the_stamp(tmp_path):
    reg, data, n_before = _chain_with_two_days(tmp_path)
    lines = reg.log_path.read_bytes().splitlines(keepends=True)
    cut_ts = json.loads(lines[n_before])["ts_utc"]
    out = tmp_path / "prefix.jsonl"
    r = _tool("prefix", "--registry", str(reg.log_path), "--cut-utc", cut_ts,
              "--out", str(out))
    assert r.returncode == 0, r.stderr
    kept = out.read_bytes()
    # the cut is the FIRST quarantine entry at/after the stamp, which may be
    # earlier than n_before when both days share a second; it is a prefix
    assert reg.log_path.read_bytes().startswith(kept)
    assert f"kept {len(kept.splitlines())} lines" in r.stdout
    assert all(json.loads(l)["ts_utc"] < cut_ts
               or not json.loads(l)["entry_type"].startswith("quarantine_")
               for l in kept.splitlines())


def test_prefix_with_nothing_to_cut_exits_2(tmp_path):
    reg, data, _ = _chain_with_two_days(tmp_path)
    r = _tool("prefix", "--registry", str(reg.log_path), "--cut-utc",
              "2999-01-01T00:00:00Z", "--out", str(tmp_path / "x.jsonl"))
    assert r.returncode == 2


def _copy(src, dst):
    dst.write_bytes(src.read_bytes())
    return dst


def test_compare_ignores_timestamps_and_hashes(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = len(reg.log_path.read_bytes().splitlines())
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    b = _copy(reg.log_path, tmp_path / "b.jsonl")
    for p in (a, b):
        assert quarantine_run(argv_for(Registry(p), data, "--date",
                                       "2023-01-22")) == 0
    r = _tool("compare", str(a), str(b), "--skip", str(base))
    assert r.returncode == 0 and "MATCH" in r.stdout


def test_compare_reports_a_payload_difference(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = len(reg.log_path.read_bytes().splitlines())
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    b = _copy(reg.log_path, tmp_path / "b.jsonl")
    Registry(a).append("note", {"text": "x"})
    Registry(b).append("note", {"text": "y"})
    r = _tool("compare", str(a), str(b), "--skip", str(base))
    assert r.returncode == 1 and "MISMATCH" in r.stdout and "index 0" in r.stdout


def test_compare_reports_a_length_difference(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = len(reg.log_path.read_bytes().splitlines())
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    b = _copy(reg.log_path, tmp_path / "b.jsonl")
    Registry(a).append("note", {"text": "x"})
    r = _tool("compare", str(a), str(b), "--skip", str(base))
    assert r.returncode == 1 and "MISMATCH" in r.stdout


def test_compare_checks_the_ledgers(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    la, lb = tmp_path / "la.json", tmp_path / "lb.json"
    la.write_text(json.dumps({"items": [{"source": "restated", "key": "X",
                                         "reason": "r", "since_utc": "1"}]}))
    lb.write_text(json.dumps({"items": [{"source": "restated", "key": "X",
                                         "reason": "r", "since_utc": "2"}]}))
    assert _tool("compare", str(a), str(a), "--skip", "0", "--ledger-a",
                 str(la), "--ledger-b", str(lb)).returncode == 0
    lb.write_text(json.dumps({"items": []}))
    assert _tool("compare", str(a), str(a), "--skip", "0", "--ledger-a",
                 str(la), "--ledger-b", str(lb)).returncode == 1


def test_run_times_the_lock_and_returns_the_code(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(LAYER), "--timing", str(timing), "--",
              *argv_for(reg, data, "--date", "2023-01-22"))
    assert r.returncode == 0, r.stderr
    t = json.loads(timing.read_text())
    assert t["rc"] == 0 and t["error"] is None
    assert len(t["holds_s"]) == 1 and t["max_hold_s"] == t["holds_s"][0]
    assert t["wall_s"] >= t["max_hold_s"] >= 0


def test_run_passes_a_refusal_through_as_its_rc(tmp_path):
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(LAYER), "--timing", str(timing), "--",
              "--date", "2023-01-22", "--registry", str(tmp_path / "missing.jsonl"),
              "--data-dir", str(tmp_path))
    t = json.loads(timing.read_text())
    assert r.returncode == t["rc"] == 1
