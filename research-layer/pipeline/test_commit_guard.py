"""Registry commit guard (2026-10-09 stopgap). GitHub blocks any file over
100 MiB anywhere in pushed history, so registry_log.jsonl must never be
COMMITTED at or above GUARD_BYTES; the chain keeps growing on disk, only its
commit pauses, and the pause is a degraded-ledger item the Sentinel reads."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from . import commit_guard as cg

LAYER = Path(__file__).resolve().parent.parent


def _setup(tmp_path, size):
    reg = tmp_path / "registry_log.jsonl"
    reg.write_bytes(b"x" * size)
    logs = tmp_path / "logs"
    logs.mkdir()
    return reg, logs


def _ledger(logs):
    return json.loads((logs / cg.LEDGER).read_text(encoding="utf-8"))


def test_guard_sits_five_mib_under_githubs_hard_limit():
    assert cg.GITHUB_LIMIT_BYTES == 100 * 1024 * 1024
    assert cg.GUARD_BYTES == 95 * 1024 * 1024
    assert cg.GUARD_BYTES < cg.GITHUB_LIMIT_BYTES


def test_under_the_guard_commits_and_writes_an_empty_fresh_ledger(tmp_path):
    reg, logs = _setup(tmp_path, 100)
    assert cg.registry_committable(reg, logs, guard_bytes=101) is True
    led = _ledger(logs)
    assert led["writer"] == "commit"
    assert led["items"] == []
    assert led["ts_utc"]


def test_at_the_guard_the_registry_is_held_back_and_recorded(tmp_path, capsys):
    reg, logs = _setup(tmp_path, 101)
    assert cg.registry_committable(reg, logs, guard_bytes=101) is False
    (item,) = _ledger(logs)["items"]
    assert item["source"] == "registry_commit"
    assert item["key"] == "registry_log.jsonl"
    assert "101 bytes" in item["reason"]
    assert "WARNING" in capsys.readouterr().out


def test_a_paused_commit_keeps_its_original_since_and_clears_on_recovery(tmp_path):
    reg, logs = _setup(tmp_path, 200)
    cg.registry_committable(reg, logs, guard_bytes=101)
    since = _ledger(logs)["items"][0]["since_utc"]
    # A later run: a later clock, still over. The clock must not restart.
    led = json.loads((logs / cg.LEDGER).read_text(encoding="utf-8"))
    led["items"][0]["since_utc"] = "2026-10-01T00:00:00+00:00"
    (logs / cg.LEDGER).write_text(json.dumps(led), encoding="utf-8")
    cg.registry_committable(reg, logs, guard_bytes=101)
    assert _ledger(logs)["items"][0]["since_utc"] == "2026-10-01T00:00:00+00:00"
    assert since                                     # first run stamped one
    reg.write_bytes(b"x" * 10)                       # segmented: small again
    assert cg.registry_committable(reg, logs, guard_bytes=101) is True
    assert _ledger(logs)["items"] == []


def test_a_registry_that_cannot_be_read_is_never_committed(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    assert cg.registry_committable(tmp_path / "missing.jsonl", logs) is False
    (item,) = _ledger(logs)["items"]
    assert "cannot stat" in item["reason"]


def test_a_ledger_write_failure_never_changes_the_decision(tmp_path, capsys):
    reg = tmp_path / "registry_log.jsonl"
    reg.write_bytes(b"x" * 200)
    logs = tmp_path / "logs"
    logs.write_text("a file where the logs dir should be", encoding="utf-8")
    assert cg.registry_committable(reg, logs, guard_bytes=101) is False
    reg.write_bytes(b"x" * 10)
    assert cg.registry_committable(reg, logs, guard_bytes=101) is True
    assert "ledger not written" in capsys.readouterr().out


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "pipeline.commit_guard", *args],
                          cwd=str(LAYER), capture_output=True, text=True)


def test_cli_exit_codes_are_0_commit_and_3_paused(tmp_path):
    reg, logs = _setup(tmp_path, 200)
    base = ["--registry", str(reg), "--logs-dir", str(logs)]
    assert _cli(*base, "--guard-bytes", "201").returncode == 0
    r = _cli(*base, "--guard-bytes", "200")
    assert r.returncode == cg.EXIT_PAUSED == 3
    assert "NOT committed" in r.stdout


def test_cli_defaults_to_the_layers_own_registry_and_logs():
    """The wrappers call it with no arguments from the layer directory."""
    ap = cg.build_parser()
    ns = ap.parse_args([])
    assert ns.registry == LAYER / "registry_log.jsonl"
    assert ns.logs_dir == LAYER / "logs"
    assert ns.guard_bytes == cg.GUARD_BYTES


@pytest.mark.parametrize("bad", ["--guard-bytes=0", "--guard-bytes=-5"])
def test_cli_refuses_a_nonsense_guard(bad, tmp_path):
    reg, logs = _setup(tmp_path, 1)
    r = _cli("--registry", str(reg), "--logs-dir", str(logs), bad)
    assert r.returncode not in (0, cg.EXIT_PAUSED)
