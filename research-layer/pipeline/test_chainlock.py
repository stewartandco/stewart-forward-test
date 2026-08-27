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
