"""Tests for the exact indicator-series cache (engine perf, Task 1,
docs/2026-10-17-engine-perf-design.md section 3.3 and "Calls made by the
designer").

The rule under test is the project's absolute one: results are BIT-IDENTICAL.
A cache here may only skip work. Every test compares what the cache serves
against a cold `compute(0, n)` on the same inputs, bit for bit (struct.pack,
so -0.0 / inf / NaN payloads are compared as bits, not as `==` would).

Run: python -m pytest pipeline/test_series_cache.py -q -p no:cacheprovider
"""
from __future__ import annotations

import math
import os
import struct
from array import array
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

import numpy as np
import pytest

from . import series_cache
from .series_cache import CACHE_DIRNAME, SeriesCache


# ---------------------------------------------------------------- fixtures

def make_inputs(n: int) -> array:
    # Deterministic and PREFIX-STABLE: the first 100 of 150 equal the 100.
    return array("d", [100.0 + 10.0 * math.sin(i * 0.37) + i * 0.01
                       for i in range(n)])


def series(inputs, start: int, stop: int) -> list:
    """A causal stand-in indicator: bar i depends only on bars <= i. Leading
    None (warm-up), plus +inf, -inf and -0.0 values so the exactness checks
    have something to bite on."""
    out: list = []
    for i in range(start, stop):
        if i < 5:
            out.append(None)
        elif i == 50:
            out.append(math.inf)
        elif i == 51:
            out.append(-math.inf)
        elif i == 52:
            out.append(-0.0)
        else:
            out.append(inputs[i] - inputs[i - 5] + inputs[i - 1] * 1e-3)
    return out


class Spy:
    """A compute() that records every (start, stop) it is asked for."""

    def __init__(self, inputs):
        self.inputs = inputs
        self.calls: list = []

    def __call__(self, start: int, stop: int) -> list:
        self.calls.append((start, stop))
        return series(self.inputs, start, stop)


def bits(x) -> bytes:
    return b"none" if x is None else struct.pack("<d", x)


def same(a: list, b: list) -> bool:
    return len(a) == len(b) and all(bits(x) == bits(y) for x, y in zip(a, b))


def cold(inputs) -> list:
    return series(inputs, 0, len(inputs))


KIND, PARAMS, CELL = "tstat", (20,), "BTCUSDT@1h"


def get(cache, inputs, *, kind=KIND, params=PARAMS, cell=CELL):
    spy = Spy(inputs)
    return cache.get(kind, params, cell, inputs, spy), spy


# ------------------------------------------------------------------- tests

def test_a_miss_computes_and_a_hit_serves_identical_values(tmp_path):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    inputs = make_inputs(100)
    first, spy1 = get(cache, inputs)
    assert spy1.calls == [(0, 100)]
    assert cache.stats["misses"] == 1 and cache.stats["hits"] == 0
    second, spy2 = get(cache, inputs)
    assert spy2.calls == []                      # not computed again
    assert cache.stats["hits"] == 1
    assert same(first, cold(inputs)) and same(second, cold(inputs))
    assert math.copysign(1.0, second[50]) == 1.0 and math.isinf(second[50])
    assert math.copysign(1.0, second[51]) == -1.0 and math.isinf(second[51])
    assert math.copysign(1.0, second[52]) == -1.0 and second[52] == 0.0
    assert second[:5] == [None] * 5
    assert cache.stats["extends"] == 0 and cache.stats["write_errors"] == 0


def test_a_shorter_request_is_served_from_the_prefix_and_never_shrinks_the_entry(tmp_path):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    full = make_inputs(100)
    get(cache, full)
    short, spy = get(cache, full[:60])
    assert spy.calls == []
    assert len(short) == 60 and same(short, cold(full)[:60])
    assert cache.stats["hits"] == 1
    # The 100-bar entry survived the 60-bar request: asking for 100 is a hit.
    again, spy = get(cache, full)
    assert spy.calls == [] and cache.stats["hits"] == 2
    assert same(again, cold(full))
    # ... and a fresh process-like instance (disk only) agrees: nothing was
    # rewritten shorter on the 60-bar request.
    other = SeriesCache(tmp_path / CACHE_DIRNAME)
    again2, spy = get(other, full)
    assert spy.calls == [] and other.stats["hits"] == 1
    assert same(again2, cold(full))


def test_a_longer_request_extends_only_the_tail(tmp_path):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    get(cache, make_inputs(100))
    longer = make_inputs(150)
    out, spy = get(cache, longer)
    assert spy.calls == [(100, 150)]
    assert cache.stats["extends"] == 1
    assert same(out, cold(longer))
    # The longer entry was written: a fresh instance hits all 150.
    other = SeriesCache(tmp_path / CACHE_DIRNAME)
    out2, spy2 = get(other, longer)
    assert spy2.calls == [] and other.stats["hits"] == 1
    assert same(out2, cold(longer))


def test_a_changed_input_inside_the_prefix_recomputes_from_zero(tmp_path):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    inputs = make_inputs(100)
    get(cache, inputs)
    restated = array("d", inputs)
    restated[37] = restated[37] + 0.5
    out, spy = get(cache, restated)
    assert spy.calls == [(0, 100)]
    assert same(out, cold(restated))
    assert not same(out, cold(inputs))           # the restatement mattered
    # The restated entry replaced the old one.
    out2, spy2 = get(cache, restated)
    assert spy2.calls == [] and same(out2, cold(restated))
    # A request SHORTER than the changed bar is still a valid prefix of the
    # restated entry (bars 0..29 are equal either way): a hit.
    short, spy3 = get(cache, inputs[:30])
    assert spy3.calls == [] and same(short, cold(inputs)[:30])


def test_negative_zero_and_nan_inputs_never_match(tmp_path):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    # -0.0 vs 0.0: float == says equal, the bytes differ -> must recompute.
    pos = make_inputs(60)
    pos[10] = 0.0
    neg = array("d", pos)
    neg[10] = -0.0
    get(cache, pos)
    out, spy = get(cache, neg)
    assert spy.calls == [(0, 60)]
    assert same(out, cold(neg))
    # NaN: never equal, not even to itself -> a repeat is a recompute, not a hit.
    nan_in = make_inputs(60)
    nan_in[10] = float("nan")
    cache2 = SeriesCache(tmp_path / "other")
    get(cache2, nan_in)
    out2, spy2 = get(cache2, nan_in)
    assert spy2.calls == [(0, 60)]
    assert cache2.stats["hits"] == 0 and cache2.stats["misses"] == 2
    assert same(out2, cold(nan_in))
    # A NaN BEYOND the compared prefix does not poison a shorter request.
    cache3 = SeriesCache(tmp_path / "third")
    tail_nan = make_inputs(60)
    tail_nan[55] = float("nan")
    get(cache3, tail_nan)
    short, spy3 = get(cache3, tail_nan[:40])
    assert spy3.calls == [] and cache3.stats["hits"] == 1
    assert same(short, cold(tail_nan)[:40])


def test_an_engine_rev_change_is_a_different_key(tmp_path, monkeypatch):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    inputs = make_inputs(80)
    get(cache, inputs)
    monkeypatch.setattr(series_cache, "ENGINE_REV", "e2-test-bumped")
    out, spy = get(cache, inputs)
    assert spy.calls == [(0, 80)]                # new rev: a miss, not a stale hit
    assert same(out, cold(inputs))
    assert cache.stats["misses"] == 2 and cache.stats["hits"] == 0
    # And on disk too: a fresh instance under the bumped rev hits ITS entry,
    # and the original rev's entry is still there, separately.
    other = SeriesCache(tmp_path / CACHE_DIRNAME)
    _, spy = get(other, inputs)
    assert spy.calls == []
    monkeypatch.undo()
    _, spy = get(SeriesCache(tmp_path / CACHE_DIRNAME), inputs)
    assert spy.calls == []


def test_a_corrupt_disk_entry_is_a_miss(tmp_path):
    d = tmp_path / CACHE_DIRNAME
    inputs = make_inputs(100)
    get(SeriesCache(d), inputs)
    files = list(d.iterdir())
    assert len(files) == 1
    blob = files[0].read_bytes()
    files[0].write_bytes(blob[: len(blob) // 2])  # truncated
    fresh = SeriesCache(d)
    out, spy = get(fresh, inputs)
    assert spy.calls == [(0, 100)]
    assert fresh.stats["misses"] == 1 and fresh.stats["hits"] == 0
    assert same(out, cold(inputs))
    # The recompute healed it: the next fresh instance hits.
    healed = SeriesCache(d)
    _, spy = get(healed, inputs)
    assert spy.calls == [] and healed.stats["hits"] == 1


def _entry_file(d):
    (f,) = list(d.iterdir())
    return f


@pytest.mark.parametrize("mutation", [
    "missing_field", "length_inconsistent", "bad_sha", "wrong_key", "garbage",
])
def test_other_unreadable_entries_are_misses(tmp_path, mutation):
    d = tmp_path / CACHE_DIRNAME
    inputs = make_inputs(100)
    get(SeriesCache(d), inputs)
    f = _entry_file(d)
    with np.load(f) as z:
        fields = {k: z[k] for k in z.files}
    if mutation == "missing_field":
        del fields["values"]
    elif mutation == "length_inconsistent":
        fields["none_prefix"] = np.int64(int(fields["none_prefix"]) + 3)
    elif mutation == "bad_sha":
        v = fields["values"].copy()
        v[7] = v[7] + 1.0
        fields["values"] = v
    elif mutation == "wrong_key":
        fields["key"] = np.str_("some other key")
    if mutation == "garbage":
        f.write_bytes(b"not an npz at all")
    else:
        with open(f, "wb") as fh:
            np.savez(fh, **fields)
    fresh = SeriesCache(d)
    out, spy = get(fresh, inputs)
    assert spy.calls == [(0, 100)] and fresh.stats["hits"] == 0
    assert same(out, cold(inputs))


def test_disk_round_trip_is_bit_exact(tmp_path):
    d = tmp_path / CACHE_DIRNAME
    inputs = make_inputs(100)
    first, _ = get(SeriesCache(d), inputs)
    other = SeriesCache(d)
    second, spy = get(other, inputs)
    assert spy.calls == [] and other.stats["hits"] == 1
    assert len(second) == 100
    for a, b, c in zip(first, second, cold(inputs)):
        assert bits(a) == bits(b) == bits(c)
    assert bits(second[50]) == struct.pack("<d", math.inf)
    assert bits(second[51]) == struct.pack("<d", -math.inf)
    assert bits(second[52]) == struct.pack("<d", -0.0)
    assert second[:5] == [None] * 5


def test_an_unwritable_dir_continues_uncached(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(series_cache, "_write_warned", False)
    blocker = tmp_path / "iamafile"
    blocker.write_text("x")
    cache = SeriesCache(blocker)                 # a FILE where a dir should be
    inputs = make_inputs(90)
    out, spy = get(cache, inputs)
    out_b, _ = get(cache, make_inputs(70), params=(30,))   # a second failing write
    assert same(out, cold(inputs))
    assert same(out_b, cold(make_inputs(70)))
    assert cache.stats["write_errors"] >= 2
    printed = [ln for ln in capsys.readouterr().out.splitlines()
               if ln.startswith("seriescache: write failed (")
               and ln.endswith("); continuing uncached")]
    assert len(printed) == 1                     # one line per process
    # Still serves from memory.
    again, spy = get(cache, inputs)
    assert spy.calls == [] and same(again, cold(inputs))


def test_read_only_never_writes(tmp_path):
    d = tmp_path / CACHE_DIRNAME
    d.mkdir()
    before = sorted(os.listdir(d))
    ro = SeriesCache(d, read_only=True)
    inputs = make_inputs(100)
    out, spy = get(ro, inputs)
    assert spy.calls == [(0, 100)] and same(out, cold(inputs))
    longer = make_inputs(150)
    out2, spy2 = get(ro, longer)                 # still extends in memory
    assert spy2.calls == [(100, 150)] and ro.stats["extends"] == 1
    assert same(out2, cold(longer))
    assert sorted(os.listdir(d)) == before == []
    assert ro.stats["write_errors"] == 0
    # A read-only cache over a missing directory neither creates it nor fails.
    ghost = tmp_path / "ghost"
    ro2 = SeriesCache(ghost, read_only=True)
    get(ro2, inputs)
    assert not ghost.exists()
    # It DOES read what a writer put there.
    get(SeriesCache(d), inputs)
    ro3 = SeriesCache(d, read_only=True)
    _, spy3 = get(ro3, inputs)
    assert spy3.calls == [] and ro3.stats["hits"] == 1


def test_a_none_after_a_value_is_refused(tmp_path):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    inputs = make_inputs(10)
    with pytest.raises(ValueError):
        cache.get(KIND, PARAMS, CELL, inputs,
                  lambda s, e: [None, 1.0, None] + [2.0] * (e - s - 3))
    assert list((tmp_path / CACHE_DIRNAME).glob("*")) == [] \
        if (tmp_path / CACHE_DIRNAME).exists() else True
    # Nothing was stored by the refused call: the next good call is a miss.
    out, spy = get(cache, inputs)
    assert spy.calls == [(0, 10)] and cache.stats["hits"] == 0
    # A breach inside an EXTEND is refused too, and the stored entry survives.
    longer = make_inputs(20)
    with pytest.raises(ValueError):
        cache.get(KIND, PARAMS, CELL, longer,
                  lambda s, e: [1.0, None] + [2.0] * (e - s - 2))
    # ... including a tail that STARTS with a None right after stored values.
    with pytest.raises(ValueError):
        cache.get(KIND, PARAMS, CELL, longer,
                  lambda s, e: [None] + [2.0] * (e - s - 1))
    out2, spy2 = get(cache, inputs)
    assert spy2.calls == [] and same(out2, out)
    # compute returning the wrong number of values is a contract breach too.
    with pytest.raises(ValueError):
        cache.get("other", PARAMS, CELL, inputs, lambda s, e: [1.0] * (e - s - 1))


def test_an_all_none_entry_extends_its_prefix(tmp_path):
    d = tmp_path / CACHE_DIRNAME
    cache = SeriesCache(d)
    short = make_inputs(3)                       # shorter than the warm-up
    out, _ = get(cache, short)
    assert out == [None, None, None]
    longer = make_inputs(40)
    out2, spy = get(cache, longer)
    assert spy.calls == [(3, 40)] and same(out2, cold(longer))
    out3, spy3 = get(SeriesCache(d), longer)
    assert spy3.calls == [] and same(out3, cold(longer))


def test_cell_none_is_memory_only(tmp_path):
    d = tmp_path / CACHE_DIRNAME
    cache = SeriesCache(d)
    inputs = make_inputs(100)
    out, spy = get(cache, inputs, cell=None)
    assert spy.calls == [(0, 100)]
    out2, spy2 = get(cache, inputs, cell=None)   # memory hit within the process
    assert spy2.calls == [] and cache.stats["hits"] == 1
    assert same(out2, cold(inputs))
    assert not d.exists() or list(d.iterdir()) == []
    # A different instance never sees it.
    _, spy3 = get(SeriesCache(d), inputs, cell=None)
    assert spy3.calls == [(0, 100)]


def test_a_memory_only_cache_works_without_a_directory():
    cache = SeriesCache()
    inputs = make_inputs(50)
    get(cache, inputs)
    _, spy = get(cache, inputs)
    assert spy.calls == [] and cache.stats["write_errors"] == 0


def test_different_kinds_params_and_cells_are_different_keys(tmp_path):
    cache = SeriesCache(tmp_path / CACHE_DIRNAME)
    inputs = make_inputs(60)
    for kw in ({}, {"kind": "atr"}, {"params": (30,)}, {"cell": "ETHUSDT@1h"}):
        _, spy = get(cache, inputs, **kw)
        assert spy.calls == [(0, 60)], kw
    assert cache.stats["misses"] == 4 and cache.stats["hits"] == 0


def test_the_inputs_are_copied_not_aliased(tmp_path):
    cache = SeriesCache()
    inputs = make_inputs(60)
    get(cache, inputs)
    inputs[3] = 12345.0                          # caller mutates its own array
    out, spy = get(cache, inputs)
    assert spy.calls == [(0, 60)]                # the stored bars were NOT the caller's
    assert same(out, cold(inputs))


# ----------------------------------------------- concurrency (Review Focus 3)

def _hammer(directory: str, lengths: tuple, rounds: int) -> tuple:
    """One process: `rounds` times, a FRESH cache (so it goes through the disk
    entry every time) asks for each length; returns (mismatches, write_errors)."""
    from .series_cache import SeriesCache as SC
    full = make_inputs(max(lengths))
    bad = 0
    werr = 0
    for _ in range(rounds):
        for n in lengths:
            c = SC(directory)
            inputs = full[:n]
            out = c.get(KIND, PARAMS, CELL, inputs, lambda s, e: series(inputs, s, e))
            if not same(out, cold(inputs)):
                bad += 1
            werr += c.stats["write_errors"]
    return bad, werr


def _reader(directory: str, lengths: tuple, rounds: int) -> tuple:
    from .series_cache import SeriesCache as SC
    full = make_inputs(max(lengths))
    bad = 0
    for i in range(rounds * len(lengths)):
        n = lengths[i % len(lengths)]
        c = SC(directory, read_only=True)
        inputs = full[:n]
        out = c.get(KIND, PARAMS, CELL, inputs, lambda s, e: series(inputs, s, e))
        if not same(out, cold(inputs)):
            bad += 1
    return bad, 0


def test_concurrent_writers_never_tear_a_read(tmp_path):
    d = str(tmp_path / CACHE_DIRNAME)
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=3, mp_context=ctx) as pool:
        futs = [pool.submit(_hammer, d, (60, 100), 20),
                pool.submit(_hammer, d, (80, 140), 20),
                pool.submit(_reader, d, (60, 80, 100, 140), 20)]
        results = [f.result(timeout=300) for f in futs]
    assert [r[0] for r in results] == [0, 0, 0]  # every read equalled a cold compute
    # No temp file left behind by a clean run.
    leftovers = [p.name for p in (tmp_path / CACHE_DIRNAME).iterdir()
                 if ".tmp" in p.name]
    assert leftovers == []


# ------------------------------------------------------------------ prune
# Ledger ruling (spec s3.3 "size bounded and prunable"): SeriesCache.prune.

def _prune_dir(tmp_path):
    d = tmp_path / CACHE_DIRNAME
    d.mkdir()
    return d


def _age(path, seconds_ago, now):
    os.utime(path, (now - seconds_ago, now - seconds_ago))


def test_prune_removes_an_old_tmp_and_keeps_a_young_one(tmp_path):
    d = _prune_dir(tmp_path)
    now = 1_000_000.0
    old, young = d / "aa.npz.1.deadbeef.tmp", d / "bb.npz.2.cafef00d.tmp"
    old.write_bytes(b"x")
    young.write_bytes(b"y")
    _age(old, 3601.0, now)
    _age(young, 3599.0, now)
    out = SeriesCache(d).prune(now=now)
    assert out == {"tmp_removed": 1, "stale_rev_removed": 0,
                   "corrupt_removed": 0, "kept": 0}
    assert not old.exists() and young.exists()     # a live writer's, untouched
    # the age limit is a parameter
    out2 = SeriesCache(d).prune(now=now, tmp_max_age_s=10.0)
    assert out2["tmp_removed"] == 1 and not young.exists()


def test_prune_removes_a_stale_rev_entry_and_keeps_the_current_one(
        tmp_path, monkeypatch):
    d = tmp_path / CACHE_DIRNAME
    inputs = make_inputs(100)
    monkeypatch.setattr(series_cache, "ENGINE_REV", "e2-old")
    get(SeriesCache(d), inputs, params=(10,))
    (old_file,) = list(d.iterdir())
    monkeypatch.undo()
    get(SeriesCache(d), inputs, params=(20,))
    assert len(list(d.iterdir())) == 2

    out = SeriesCache(d).prune()
    assert out == {"tmp_removed": 0, "stale_rev_removed": 1,
                   "corrupt_removed": 0, "kept": 1}
    assert not old_file.exists() and len(list(d.iterdir())) == 1
    # the survivor is still served, from disk, without a recompute
    fresh = SeriesCache(d)
    served, spy = get(fresh, inputs, params=(20,))
    assert spy.calls == [] and fresh.stats["hits"] == 1
    assert same(served, cold(inputs))


def test_prune_removes_a_corrupt_file_like_load_treats_it_as_a_miss(tmp_path):
    d = tmp_path / CACHE_DIRNAME
    inputs = make_inputs(100)
    get(SeriesCache(d), inputs, params=(10,))
    get(SeriesCache(d), inputs, params=(20,))
    files = sorted(d.iterdir())
    files[0].write_bytes(b"not an npz at all")
    blob = files[1].read_bytes()
    out = SeriesCache(d).prune()
    assert out == {"tmp_removed": 0, "stale_rev_removed": 0,
                   "corrupt_removed": 1, "kept": 1}
    assert not files[0].exists() and files[1].read_bytes() == blob
    # A truncated npz and a bit-flipped one (bad checksum) are corrupt too.
    keep = files[1].name
    get(SeriesCache(d), inputs, params=(30,))
    get(SeriesCache(d), inputs, params=(40,))
    trunc, flipped = [p for p in sorted(d.iterdir()) if p.name != keep]
    trunc.write_bytes(trunc.read_bytes()[:40])
    with np.load(flipped) as z:
        fields = {k: z[k] for k in z.files}
    v = fields["values"].copy()
    v[3] = v[3] + 1.0
    fields["values"] = v
    with open(flipped, "wb") as fh:
        np.savez(fh, **fields)
    out = SeriesCache(d).prune()
    assert out["corrupt_removed"] == 2 and out["kept"] == 1
    assert [p.name for p in d.iterdir()] == [keep]


def test_prune_on_a_read_only_cache_touches_nothing(tmp_path, monkeypatch):
    d = tmp_path / CACHE_DIRNAME
    inputs = make_inputs(100)
    monkeypatch.setattr(series_cache, "ENGINE_REV", "e2-old")
    get(SeriesCache(d), inputs)                  # stale once the rev is back
    monkeypatch.undo()
    old_tmp = d / "zz.npz.1.deadbeef.tmp"
    old_tmp.write_bytes(b"x")
    (d / "junk.npz").write_bytes(b"junk")
    _age(old_tmp, 10_000.0, 1_000_000.0)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime) for p in d.iterdir()}
    out = SeriesCache(d, read_only=True).prune(now=1_000_000.0)
    assert out == {"tmp_removed": 0, "stale_rev_removed": 0,
                   "corrupt_removed": 0, "kept": 0}
    assert {p.name: (p.read_bytes(), p.stat().st_mtime)
            for p in d.iterdir()} == before


def test_prune_on_a_missing_or_absent_directory_returns_zeros(tmp_path):
    zeros = {"tmp_removed": 0, "stale_rev_removed": 0, "corrupt_removed": 0,
             "kept": 0}
    ghost = tmp_path / "ghost"
    assert SeriesCache(ghost).prune() == zeros
    assert not ghost.exists()                    # prune never creates it
    assert SeriesCache().prune() == zeros        # memory-only cache


def test_prune_swallows_a_per_file_error_and_keeps_going(tmp_path, monkeypatch):
    d = _prune_dir(tmp_path)
    now = 1_000_000.0
    stuck, gone = d / "a.npz.1.aaaaaaaa.tmp", d / "b.npz.2.bbbbbbbb.tmp"
    for p in (stuck, gone):
        p.write_bytes(b"x")
        _age(p, 10_000.0, now)
    real_unlink = type(stuck).unlink

    def unlink(self, *a, **k):
        if self.name == stuck.name:
            raise PermissionError("sharing violation")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(type(stuck), "unlink", unlink)
    out = SeriesCache(d).prune(now=now)          # must not raise
    assert out["tmp_removed"] == 1               # only the one that went
    assert stuck.exists() and not gone.exists()
    # A file that vanishes between the listing and the stat is not an error.
    monkeypatch.undo()
    real_stat = type(stuck).stat

    def stat(self, *a, **k):
        if self.name == stuck.name:
            raise FileNotFoundError(str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(type(stuck), "stat", stat)
    assert SeriesCache(d).prune(now=now)["tmp_removed"] == 0


# ------------------------------------------------- prune: races and errors
# Review minors (engine perf Task 9b): prune must not delete a file a writer
# replaced between prune's read and its unlink, and an unreadable entry is left
# alone. Neither touches a served value.

def _stale_entry(tmp_path, monkeypatch, params):
    """A readable entry stored under another ENGINE_REV (prune would unlink it)."""
    d = tmp_path / CACHE_DIRNAME
    monkeypatch.setattr(series_cache, "ENGINE_REV", "e2-old")
    get(SeriesCache(d), make_inputs(100), params=params)
    monkeypatch.undo()
    (path,) = [p for p in d.iterdir() if p.suffix == ".npz"]
    return d, path


@pytest.mark.parametrize("how", ["mtime_only", "size_only"])
def test_prune_does_not_unlink_an_entry_rewritten_after_it_was_read(
        tmp_path, monkeypatch, how):
    """Delete-after-read race: a writer replaces the entry between prune's read
    and its unlink. prune judged the OLD bytes; the file now there is a new
    entry nobody judged, so it must survive. Each leg changes only ONE of the
    two things prune re-checks (mtime_ns, size) and restores the other."""
    d, path = _stale_entry(tmp_path, monkeypatch, (10,))
    st0 = path.stat()
    old_bytes = path.read_bytes()
    new_bytes = (b"Z" * len(old_bytes) if how == "mtime_only"
                 else old_bytes + b"appended-by-the-writer")
    real_read = type(path).read_bytes
    fired = []

    def read_then_rewrite(self):
        data = real_read(self)
        if self.name == path.name and not fired:
            fired.append(1)
            self.write_bytes(new_bytes)
            if how == "mtime_only":     # same size, a strictly later mtime
                os.utime(self, ns=(st0.st_atime_ns, st0.st_mtime_ns + 5_000_000_000))
            else:                       # a different size, the old mtime back
                os.utime(self, ns=(st0.st_atime_ns, st0.st_mtime_ns))
        return data

    monkeypatch.setattr(type(path), "read_bytes", read_then_rewrite)
    out = SeriesCache(d).prune()                 # must not raise
    assert fired == [1]
    assert out["stale_rev_removed"] == 0 and out["corrupt_removed"] == 0
    assert path.exists() and path.read_bytes() == new_bytes


def test_prune_leaves_an_entry_it_cannot_read_and_goes_on(tmp_path, monkeypatch):
    d, blocked = _stale_entry(tmp_path, monkeypatch, (10,))
    monkeypatch.setattr(series_cache, "ENGINE_REV", "e2-old")
    get(SeriesCache(d), make_inputs(100), params=(20,))
    monkeypatch.undo()
    (other,) = [p for p in d.iterdir() if p.suffix == ".npz" and p != blocked]
    real_read = type(blocked).read_bytes

    def read(self):
        if self.name == blocked.name:
            raise PermissionError("sharing violation")
        return real_read(self)

    monkeypatch.setattr(type(blocked), "read_bytes", read)
    out = SeriesCache(d).prune()                 # must not raise
    assert blocked.exists()                      # left alone
    assert not other.exists()                    # the walk went on
    assert out == {"tmp_removed": 0, "stale_rev_removed": 1,
                   "corrupt_removed": 0, "kept": 0}
