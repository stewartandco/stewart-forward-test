# research-layer/pipeline/test_chainlock_start_time.py
import json
import os

from . import chainlock
from .chainlock import ChainLock


def test_acquire_records_the_holder_start_time(tmp_path):
    lock = ChainLock(tmp_path, "t", "test")
    lock.acquire()
    try:
        info = json.loads((tmp_path / "chain.lock").read_text())
        assert info["pid"] == os.getpid()
        assert info["pid_start_utc"] == chainlock.process_start_time(os.getpid())
        assert info["pid_start_utc"] is not None
    finally:
        lock.release()


def test_a_reused_pid_with_a_different_start_time_reads_dead(tmp_path):
    """2026-09-28: a killed loop's pid was reused by a browser tab and both
    locks read ALIVE, wedging the loop and the quarantine daily."""
    (tmp_path / "chain.lock").write_text(json.dumps({
        "holder": "loop", "pid": os.getpid(), "ts_utc": "2026-09-27T15:13:04+00:00",
        "purpose": "x", "pid_start_utc": "2001-01-01T00:00:00+00:00"}))
    assert ChainLock(tmp_path, "probe", "probe").holder_alive() is False


def test_the_same_pid_and_start_time_reads_alive(tmp_path):
    (tmp_path / "chain.lock").write_text(json.dumps({
        "holder": "loop", "pid": os.getpid(), "ts_utc": "2026-09-27T15:13:04+00:00",
        "purpose": "x", "pid_start_utc": chainlock.process_start_time(os.getpid())}))
    assert ChainLock(tmp_path, "probe", "probe").holder_alive() is True


def test_a_legacy_lock_without_start_time_keeps_the_pid_rule(tmp_path):
    (tmp_path / "chain.lock").write_text(json.dumps({
        "holder": "loop", "pid": os.getpid(), "ts_utc": "x", "purpose": "x"}))
    assert ChainLock(tmp_path, "probe", "probe").holder_alive() is True
