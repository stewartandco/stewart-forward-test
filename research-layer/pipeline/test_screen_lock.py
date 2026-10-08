"""Screen writes batch by batch under short chain.lock holds (2026-10-08 design)."""
from __future__ import annotations

import shutil
import subprocess
import types
from pathlib import Path

import pytest

from . import screen
from .chainlock import ChainLock
from .common import content_id
from .registry import Registry
from .test_screen import (screening_registry, write_data_dir, dated_target_hit_bars,
                          chain_protocol_note, run_verifier)

LAYER = Path(__file__).resolve().parent.parent

# The plan's base commit: the last screen.py BEFORE this change (Ruling 6, I3).
# Pinned, never derived from a branch: once this lands on the live branch a
# merge-base with it is HEAD itself, and the identity test would compare the
# new screen with itself.
PRE_CHANGE_BASE = "6dc03f501f28da7b8180b256ade18fff35174684"


def _registry_with(tmp_path, n):
    """n proposed specs (distinct lookbacks) on BTCUSD, protocol note chained."""
    reg, spec = screening_registry(tmp_path)
    sids = [spec["strategy_id"]]
    for i in range(1, n):
        s = dict(spec, name=f"b{i}", strategy_id=None)
        s["blocks"] = [dict(b) for b in spec["blocks"]]
        s["blocks"][0] = {"role": "entry", "type": "channel_breakout",
                          "params": {"lookback": 20 + i, "direction": "long"}}
        s["strategy_id"] = content_id(s, "strategy_id")
        reg.register_strategy(s)
        sids.append(s["strategy_id"])
    chain_protocol_note(reg)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    return reg, sids, data


def _run(reg, data, tmp_path, *extra):
    return screen.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art"),
                       "--logs-dir", str(tmp_path / "logs"), "--workers", "1", *extra])


def _result(reg):
    from .deadline import read_result
    return read_result(reg.log_path, "screen")


def _fake_clock(monkeypatch, chunk_s):
    """One fake clock for the whole run: the budget's clock, screen's
    time.time / time.monotonic, and _sleep (which advances it). Each run_all
    call (one chunk) advances it by `chunk_s`, so the budget measures exactly
    chunk_s per spec with one spec per chunk. Returns (now, budgets, calls)."""
    now = {"t": 1000.0}
    budgets: list = []
    calls = {"n": 0}
    real_budget = screen._deadline.DeadlineBudget

    def make_budget(d, **k):
        b = real_budget(d, clock=lambda: now["t"], **k)
        budgets.append(b)
        return b
    monkeypatch.setattr(screen._deadline, "DeadlineBudget", make_budget)
    monkeypatch.setattr(screen, "time", types.SimpleNamespace(
        time=lambda: now["t"], monotonic=lambda: now["t"]))
    monkeypatch.setattr(screen, "_sleep", lambda s: now.__setitem__("t", now["t"] + s))
    real_run_all = screen.run_all

    def run_all_on_the_clock(*a, **k):
        calls["n"] += 1
        out = real_run_all(*a, **k)
        now["t"] += chunk_s
        return out
    monkeypatch.setattr(screen, "run_all", run_all_on_the_clock)
    return now, budgets, calls


def _deadline_in(seconds):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def test_identical_entries_to_the_pre_change_screen(tmp_path):
    """The same chain as screen at the plan's base commit, apart from ts_utc."""
    old_src = subprocess.run(["git", "-C", str(LAYER), "show",
                              f"{PRE_CHANGE_BASE}:research-layer/pipeline/screen.py"],
                             capture_output=True, text=True, check=True).stdout
    assert "record_screen_outcomes_batch" not in old_src   # really the old screen
    old_mod = tmp_path / "old_screen_pkg"
    shutil.copytree(LAYER / "pipeline", old_mod / "pipeline")
    (old_mod / "pipeline" / "screen.py").write_text(old_src, encoding="utf-8")
    a_reg, a_sids, a_data = _registry_with(tmp_path / "a", 5)
    # b is a byte copy of a (chain and data), so both screens start from the
    # same input: the fixture card stamps created_utc with the wall clock, so
    # two separately built registries differ whenever a second ticks over.
    (tmp_path / "b").mkdir()
    shutil.copyfile(a_reg.log_path, tmp_path / "b" / "reg.jsonl")
    b_data = Path(shutil.copytree(a_data, tmp_path / "b" / "data"))
    b_reg = Registry(tmp_path / "b" / "reg.jsonl")
    assert b_reg.log_path.read_bytes() == a_reg.log_path.read_bytes()
    rc_old = subprocess.run(
        ["python", "-m", "pipeline.screen", "--registry", str(a_reg.log_path),
         "--data-dir", str(a_data), "--artifacts-dir", str(tmp_path / "a" / "art"),
         "--workers", "1"], cwd=old_mod, capture_output=True, text=True).returncode
    assert rc_old == 0
    assert _run(b_reg, b_data, tmp_path / "b") == 0
    strip = lambda r: [(e["entry_type"], e["payload"]) for e in r.entries()]
    assert strip(a_reg) == strip(b_reg)
    assert any(e["entry_type"] == "verdict" for e in b_reg.entries())
    assert run_verifier(a_reg.log_path).returncode == 0
    assert run_verifier(b_reg.log_path).returncode == 0


def test_every_spec_is_written_and_the_lock_is_released(tmp_path):
    reg, sids, data = _registry_with(tmp_path, 3)
    assert _run(reg, data, tmp_path) == 0
    states = reg.strategy_states()
    assert all(states[s] in ("gauntlet", "graveyard") for s in sids)
    assert not (tmp_path / "logs" / "chain.lock").exists()
    r = _result(reg)
    assert r["evaluated"] == 3 and r["deferred"] == 0 and r["deferred_lock"] == 0
    assert run_verifier(reg.log_path).returncode == 0


def test_lock_held_at_first_attempt_then_released_is_written_in_the_same_run(
        tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 3)
    monkeypatch.setattr(screen, "SCREEN_CHUNK_PER_WORKER", 1)   # one spec per chunk
    holder = ChainLock(tmp_path / "logs", holder="session", purpose="test")
    holder.acquire()
    calls = {"n": 0}
    real_run_all = screen.run_all

    def run_all_then_release(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            holder.release()       # free after the first chunk's flush was refused
        return real_run_all(*a, **k)
    monkeypatch.setattr(screen, "run_all", run_all_then_release)
    assert _run(reg, data, tmp_path) == 0
    r = _result(reg)
    assert r["evaluated"] == 3 and r["retried_written"] >= 1 and r["deferred_lock"] == 0
    bundles = sorted(p.name for p in (tmp_path / "art").iterdir())
    assert bundles == sorted(sids)
    assert run_verifier(reg.log_path).returncode == 0


def test_lock_held_for_the_whole_run_writes_nothing_and_exits_0(tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 3)
    monkeypatch.setattr(screen, "_sleep", lambda s: None)
    monkeypatch.setattr(screen, "DRAIN_RESERVE_S", 0.0)
    holder = ChainLock(tmp_path / "logs", holder="session", purpose="test")
    holder.acquire()
    before = reg.log_path.read_bytes()
    try:
        assert _run(reg, data, tmp_path) == 0
    finally:
        holder.release()
    assert reg.log_path.read_bytes() == before
    assert not (tmp_path / "art").exists() or not any((tmp_path / "art").iterdir())
    assert all(reg.strategy_states()[s] == "proposed" for s in sids)
    r = _result(reg)
    assert r["evaluated"] == 0 and r["deferred_lock"] == 3 and r["deferred"] == 3


def test_a_spec_moved_out_of_proposed_before_the_flush_is_dropped(tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 3)
    real_run_all = screen.run_all

    def run_all_then_bury(*a, **k):
        out = real_run_all(*a, **k)
        if reg.strategy_states()[sids[1]] == "proposed":
            reg.record_state_change(sids[1], "graveyard", "hand")   # a hand-run writer
        return out
    monkeypatch.setattr(screen, "run_all", run_all_then_bury)
    assert _run(reg, data, tmp_path) == 0
    r = _result(reg)
    assert r["dropped_stale"] == 1 and r["evaluated"] == 2
    assert not (tmp_path / "art" / sids[1]).exists()
    gy = [e for e in reg.entries() if e["entry_type"] == "state_change"
          and e["payload"]["strategy_id"] == sids[1]]
    assert [g["payload"]["to"] for g in gy] == ["graveyard"]       # only the hand change
    assert run_verifier(reg.log_path).returncode == 0


def test_a_foreign_unlocked_append_refuses_the_batch_and_exits_1(tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 2)
    monkeypatch.setattr(screen, "SCREEN_CHUNK_PER_WORKER", 1)
    real_advance = Registry.advance
    calls = {"n": 0}

    def advance_then_foreign_append(self, snap):
        out = real_advance(self, snap)
        calls["n"] += 1
        if calls["n"] == 2:                      # second batch: an unlocked writer
            Registry(self.log_path).append("note", {"text": "foreign"})
        return out
    monkeypatch.setattr(Registry, "advance", advance_then_foreign_append)
    assert _run(reg, data, tmp_path) == 1
    states = reg.strategy_states()
    assert states[sids[0]] in ("gauntlet", "graveyard")     # first batch stays
    assert states[sids[1]] == "proposed"                     # refused batch: nothing
    assert run_verifier(reg.log_path).returncode == 0


def test_a_cell_error_keeps_earlier_batches_and_leaves_the_rest_proposed(
        tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 3)
    monkeypatch.setattr(screen, "SCREEN_CHUNK_PER_WORKER", 1)
    real_run_all = screen.run_all
    calls = {"n": 0}

    def run_all_failing_second(job, chunk, workers=1):
        calls["n"] += 1
        if calls["n"] == 2:
            return [screen.CellError("boom")]
        return real_run_all(job, chunk, workers=workers)
    monkeypatch.setattr(screen, "run_all", run_all_failing_second)
    with pytest.raises(RuntimeError, match="boom"):
        _run(reg, data, tmp_path)
    states = reg.strategy_states()
    order = [e["payload"]["strategy_id"] for e in reg.entries()
             if e["entry_type"] == "strategy_registered"]
    assert states[order[0]] in ("gauntlet", "graveyard")
    assert states[order[1]] == "proposed" and states[order[2]] == "proposed"
    assert not [s for s in states.values() if s == "screened"]
    assert run_verifier(reg.log_path).returncode == 0


def test_a_second_hold_that_finds_the_lock_held_keeps_the_remainder(
        tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 3)
    monkeypatch.setattr(screen, "SCREEN_BATCH_MAX", 2)          # 3 pending -> 2 holds
    monkeypatch.setattr(screen, "_sleep", lambda s: None)
    monkeypatch.setattr(screen, "DRAIN_RESERVE_S", 0.0)
    monkeypatch.setattr(screen, "_TEST_GRAB_AFTER_RELEASE", False, raising=False)
    real = screen.Registry.record_screen_outcomes_batch

    def write_then_grab(self, snap, items):
        out = real(self, snap, items)
        # another writer queues for the lock right after this hold is released
        screen._TEST_GRAB_AFTER_RELEASE = True
        return out
    monkeypatch.setattr(screen.Registry, "record_screen_outcomes_batch", write_then_grab)
    real_release = screen.ChainLock.release

    def release_and_grab(self):
        real_release(self)
        if getattr(screen, "_TEST_GRAB_AFTER_RELEASE", False):
            screen._TEST_GRAB_AFTER_RELEASE = False
            ChainLock(tmp_path / "logs", holder="session", purpose="grab").acquire()
    monkeypatch.setattr(screen.ChainLock, "release", release_and_grab)
    assert _run(reg, data, tmp_path) == 0
    r = _result(reg)
    assert r["evaluated"] == 2 and r["deferred_lock"] == 1
    written = [e["payload"]["strategy_id"] for e in reg.entries()
               if e["entry_type"] == "verdict"]
    assert len(written) == len(set(written)) == 2                  # nothing twice
    assert run_verifier(reg.log_path).returncode == 0


def test_no_flush_starts_after_the_deadline(tmp_path, monkeypatch):
    """A chunk whose evaluation overruns the deadline: its results are kept,
    NO chain.lock acquire is attempted once the deadline has passed (not at
    the chunk boundary, not in the final drain), and the leftovers are
    reported as a DEADLINE stop, not a lock deferral (Ruling 6, I1). No lock
    is held anywhere: the deadline is the only reason nothing is written.
    Fake clock (Ruling 1): the chunk moves it 60 s past a deadline 30 s
    ahead; the test reads "past the deadline" from its own clock."""
    reg, sids, data = _registry_with(tmp_path, 2)
    now, budgets, calls = _fake_clock(monkeypatch, chunk_s=60.0)
    attempts = {"after": 0, "before": 0}
    real_acquire = screen.ChainLock.acquire

    def counting_acquire(self):
        if budgets and budgets[0].remaining_s() <= 0:
            attempts["after"] += 1
        else:
            attempts["before"] += 1
        return real_acquire(self)
    monkeypatch.setattr(screen.ChainLock, "acquire", counting_acquire)
    assert _run(reg, data, tmp_path, "--deadline-utc", _deadline_in(30)) == 0
    assert calls["n"] == 1 and budgets[0].remaining_s() < 0
    assert attempts["after"] == 0
    assert all(reg.strategy_states()[s] == "proposed" for s in sids)
    r = _result(reg)
    assert r["evaluated"] == 0 and r["deferred"] == 2
    assert r["deferred_lock"] == 0 and r["stopped_at_deadline"] is True


def test_with_a_batch_pending_a_chunk_that_eats_the_drain_reserve_is_not_started(
        tmp_path, monkeypatch):
    """Design 3.4 / Ruling 6 (I2): while a batch waits, DRAIN_RESERVE_S is
    SUBTRACTED from the budget. Deadline 200 s ahead, 90 s per spec, one
    spec per chunk, chain.lock held: after chunk 1, 110 s are left; chunk 2
    fits on its own (90 <= 110) and is above the reserve floor (110 > 75),
    but chunk + reserve (165) does not fit, so it is not started."""
    reg, sids, data = _registry_with(tmp_path, 2)
    monkeypatch.setattr(screen, "SCREEN_CHUNK_PER_WORKER", 1)
    now, budgets, calls = _fake_clock(monkeypatch, chunk_s=90.0)
    holder = ChainLock(tmp_path / "logs", holder="session", purpose="test")
    holder.acquire()
    try:
        assert _run(reg, data, tmp_path, "--deadline-utc", _deadline_in(200)) == 0
    finally:
        holder.release()
    assert calls["n"] == 1                       # chunk 2 never started
    assert all(reg.strategy_states()[s] == "proposed" for s in sids)
    r = _result(reg)
    assert r["evaluated"] == 0 and r["deferred"] == 2
    assert r["deferred_lock"] == 1 and r["stopped_at_deadline"] is True


def test_with_nothing_pending_the_same_chunk_is_started(tmp_path, monkeypatch):
    """The converse: the same clock, lock free, so chunk 1 is written and
    nothing is pending; no reserve is held back and chunk 2 (90 s of the 110
    left) is started and written."""
    reg, sids, data = _registry_with(tmp_path, 2)
    monkeypatch.setattr(screen, "SCREEN_CHUNK_PER_WORKER", 1)
    now, budgets, calls = _fake_clock(monkeypatch, chunk_s=90.0)
    assert _run(reg, data, tmp_path, "--deadline-utc", _deadline_in(200)) == 0
    assert calls["n"] == 2
    assert all(reg.strategy_states()[s] in ("gauntlet", "graveyard") for s in sids)
    r = _result(reg)
    assert r["evaluated"] == 2 and r["deferred"] == 0
    assert r["deferred_lock"] == 0 and r["stopped_at_deadline"] is False
    assert run_verifier(reg.log_path).returncode == 0


def test_dry_run_takes_no_lock_and_writes_nothing(tmp_path):
    reg, sids, data = _registry_with(tmp_path, 2)
    holder = ChainLock(tmp_path / "logs", holder="session", purpose="test")
    holder.acquire()
    before = reg.log_path.read_bytes()
    try:
        assert _run(reg, data, tmp_path, "--dry-run") == 0
    finally:
        holder.release()
    assert reg.log_path.read_bytes() == before
