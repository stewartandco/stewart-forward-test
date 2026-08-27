"""Offline tests for loop watermark state.

Run: python -m pytest pipeline/test_loop_state.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

from .loop_state import (DEFAULT_THRESHOLD, load, save, pick_class,
                         record_generation, record_stale_lock, clear_stale_lock)


def test_load_missing_file_returns_empty_state(tmp_path):
    st = load(tmp_path / "loop_state.json")
    assert st == {"classes": {}, "stale_lock": None}


def test_save_load_roundtrip_atomic(tmp_path):
    p = tmp_path / "loop_state.json"
    st = load(p)
    record_generation(st, "fx", run_id="2026-08-27-loop-fx",
                      routable_count=40, ts_utc="2026-08-27T10:30:00+00:00")
    save(p, st)
    assert not p.with_suffix(".json.tmp").exists()
    st2 = load(p)
    assert st2["classes"]["fx"]["watermark"] == 40
    assert st2["classes"]["fx"]["last_run_id"] == "2026-08-27-loop-fx"


def test_pick_class_requires_threshold_delta():
    st = {"classes": {
        "fx": {"watermark": 40, "threshold": 25,
               "last_gen_ts_utc": "2026-08-20T00:00:00+00:00", "last_run_id": "x"},
    }, "stale_lock": None}
    assert pick_class(st, {"fx": 60}) is None          # delta 20 < 25
    assert pick_class(st, {"fx": 66}) == "fx"          # delta 26 >= 25


def test_pick_class_prefers_oldest_and_never_run_counts_as_oldest():
    st = {"classes": {
        "crypto": {"watermark": 0, "threshold": 25,
                   "last_gen_ts_utc": "2026-08-26T00:00:00+00:00", "last_run_id": "a"},
        "fx": {"watermark": 0, "threshold": 25,
               "last_gen_ts_utc": "2026-08-20T00:00:00+00:00", "last_run_id": "b"},
    }, "stale_lock": None}
    assert pick_class(st, {"crypto": 30, "fx": 30}) == "fx"       # older gen wins
    assert pick_class(st, {"crypto": 30, "fx": 30, "equity_etf": 30}) == "equity_etf"  # never-run is oldest


def test_pick_class_unknown_class_uses_default_threshold():
    st = {"classes": {}, "stale_lock": None}
    assert DEFAULT_THRESHOLD == 25
    assert pick_class(st, {"fx": 24}) is None
    assert pick_class(st, {"fx": 25}) == "fx"


def test_stale_lock_two_strike_bookkeeping(tmp_path):
    st = load(tmp_path / "s.json")
    first = record_stale_lock(st, {"holder": "loop", "pid": 1,
                                   "ts_utc": "2026-08-27T01:00:00+00:00"})
    assert first is False                              # first sighting: not yet breakable
    again = record_stale_lock(st, {"holder": "loop", "pid": 1,
                                   "ts_utc": "2026-08-27T01:00:00+00:00"})
    assert again is True                               # same lock seen twice: breakable
    other = record_stale_lock(st, {"holder": "loop", "pid": 2,
                                   "ts_utc": "2026-08-27T02:00:00+00:00"})
    assert other is False                              # different lock: strike count resets
    clear_stale_lock(st)
    assert st["stale_lock"] is None
