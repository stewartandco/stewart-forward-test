"""Offline tests for the advisory chain lock (no network).

Run: python -m pytest pipeline/test_chainlock.py -q
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from .chainlock import ChainLock, ChainLockHeld


def test_acquire_writes_holder_metadata(tmp_path):
    lk = ChainLock(tmp_path, holder="loop", purpose="cycle 2026-08-27-loop-fx")
    lk.acquire()
    try:
        info = json.loads((tmp_path / "chain.lock").read_text(encoding="utf-8"))
        assert info["holder"] == "loop"
        assert info["pid"] == os.getpid()
        assert info["purpose"] == "cycle 2026-08-27-loop-fx"
        assert info["ts_utc"].endswith("+00:00") or info["ts_utc"].endswith("Z")
    finally:
        lk.release()
    assert not (tmp_path / "chain.lock").exists()


def test_second_acquire_raises_held(tmp_path):
    a = ChainLock(tmp_path, holder="scanner", purpose="card batch")
    a.acquire()
    b = ChainLock(tmp_path, holder="loop", purpose="cycle")
    with pytest.raises(ChainLockHeld):
        b.acquire()
    a.release()
    b.acquire()
    b.release()


def test_info_none_when_absent_and_unreadable_when_corrupt(tmp_path):
    lk = ChainLock(tmp_path, holder="loop", purpose="x")
    assert lk.info() is None
    (tmp_path / "chain.lock").write_text("not json", encoding="utf-8")
    assert lk.info()["holder"] == "unreadable"


def test_stale_detection_and_break(tmp_path):
    lk = ChainLock(tmp_path, holder="loop", purpose="x", stale_after_s=1)
    other = ChainLock(tmp_path, holder="session", purpose="manual")
    other.acquire()
    assert not lk.is_stale()
    with pytest.raises(ChainLockHeld):
        lk.break_stale()          # refuses to break a fresh lock
    old = time.time() - 10
    os.utime(tmp_path / "chain.lock", (old, old))
    assert lk.is_stale()
    lk.break_stale()
    assert not (tmp_path / "chain.lock").exists()


def test_context_manager_releases_on_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with ChainLock(tmp_path, holder="loop", purpose="x"):
            raise RuntimeError("boom")
    assert not (tmp_path / "chain.lock").exists()


def test_release_swallows_oserror_and_clears_flag(tmp_path, monkeypatch):
    """A transient Windows PermissionError on unlink must not mask the
    caller's real exception, and the acquired flag must clear first."""
    lk = ChainLock(tmp_path, holder="loop", purpose="x")
    lk.acquire()
    original_unlink = Path.unlink
    def failing_unlink(self, *a, **kw):
        raise PermissionError("WinError 32: held open by a reader")
    monkeypatch.setattr(Path, "unlink", failing_unlink)
    lk.release()                      # must not raise
    assert lk._acquired is False
    monkeypatch.setattr(Path, "unlink", original_unlink)
    (tmp_path / "chain.lock").unlink()
    lk2 = ChainLock(tmp_path, holder="loop", purpose="y")
    fh = None
    try:
        with pytest.raises(ValueError):   # real error propagates, not PermissionError
            with lk2:
                fh = (tmp_path / "chain.lock").open("r")
                raise ValueError("real stage failure")
    finally:
        if fh:
            fh.close()
        (tmp_path / "chain.lock").unlink(missing_ok=True)


def test_acquire_creates_missing_logs_dir(tmp_path):
    lk = ChainLock(tmp_path / "logs", holder="loop", purpose="x")
    lk.acquire()
    try:
        assert (tmp_path / "logs" / "chain.lock").exists()
    finally:
        lk.release()


def test_custom_lock_name_is_independent_of_chain_lock(tmp_path):
    """loop.py's instance guard uses name="loop.lock" -- a second, distinct
    lockfile that must neither collide with nor block on chain.lock."""
    chain = ChainLock(tmp_path, holder="loop", purpose="chain write")
    instance = ChainLock(tmp_path, holder="loop-instance", purpose="run start",
                         name="loop.lock")
    chain.acquire()
    try:
        instance.acquire()          # must NOT raise ChainLockHeld
        try:
            assert (tmp_path / "chain.lock").exists()
            assert (tmp_path / "loop.lock").exists()
            info = json.loads((tmp_path / "loop.lock").read_text(encoding="utf-8"))
            assert info["holder"] == "loop-instance"
        finally:
            instance.release()
        assert not (tmp_path / "loop.lock").exists()
        assert (tmp_path / "chain.lock").exists()   # untouched by instance.release()
    finally:
        chain.release()


def test_holder_alive_true_for_own_pid(tmp_path):
    lk = ChainLock(tmp_path, holder="loop-instance", purpose="x")
    lk.acquire()
    try:
        assert lk.holder_alive() is True
    finally:
        lk.release()


def test_holder_alive_false_for_bogus_pid(tmp_path):
    (tmp_path / "chain.lock").write_text(
        json.dumps({"holder": "loop-instance", "pid": 4_000_000,
                   "ts_utc": "2020-01-01T00:00:00+00:00", "purpose": "x"}),
        encoding="utf-8")
    lk = ChainLock(tmp_path, holder="loop-instance", purpose="y")
    assert lk.holder_alive() is False
