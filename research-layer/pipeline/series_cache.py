"""Exact indicator-series cache (engine perf, 2026-10-17; design
docs/2026-10-17-engine-perf-design.md section 3.3 and "Calls made by the
designer").

WHY: the quarantine catch-up re-simulates every owing strategy from bar 0 on
every date, over bars that did not change since yesterday. Indicators are
causal (bar i depends only on bars <= i), so the series computed yesterday is
already the right answer for yesterday's bars; only the new tail needs
computing. This module carries those series across runs.

THE RULE (design section 2): a cache here may only SKIP work, never change a
result. Every value served is the very float the engine's own arithmetic
produced, round-tripped as raw IEEE-754 bits; on any doubt the series is
recomputed from bar 0. Nothing is approximated, rolled or vectorised.

HOW AN ENTRY IS TRUSTED: each entry stores, beside the series, the exact input
values (closes, or high/low/close for ATR, flattened by the caller into one
`array('d')`) it was computed from. A request for n bars compares the stored
inputs with the request's first m = min(stored_n, n) inputs BYTEWISE
(`tobytes`), not by float `==`: `-0.0 == 0.0` is True yet the two behave
differently downstream, and a NaN is never equal to anything, so both must
count as a mismatch. This is checked bar by bar rather than by a hash on
purpose (design doc): "the stored series was computed from exactly these
bars" is then a fact about the bars, not about a digest. Consequences:
  - equal prefix and n <= stored_n -> serve series[:n]; the entry is NOT
    rewritten (screen's cutoff bars, the gauntlet's full history and
    quarantine's per-date prefixes all read the one entry without ever
    shrinking it);
  - equal prefix and n > stored_n -> `compute(stored_n, n)` for the tail only,
    and the longer entry is written;
  - any difference (a restated bar, -0.0 vs 0.0) -> `compute(0, n)` and the
    entry is replaced.
A NaN anywhere in the compared prefix is also a mismatch even when the bytes
are identical (the design's "NaN is never equal", and "when in doubt,
recompute"): a NaN-bearing input is never trusted, only recomputed.

KEY: (kind, params, ENGINE_REV, cell). A bumped revision is a different key,
never a stale hit (the simcache.py contract). `series_cache.ENGINE_REV` is a
COPY taken from the engine at import (`from .engine import ENGINE_REV`); the
functions here look the name up in THIS module's globals at call time. In
production that is exact, because ENGINE_REV only ever changes in code, i.e.
between processes. A test that wants another revision must patch
`series_cache.ENGINE_REV` (as test_series_cache does); patching
`engine.ENGINE_REV` does not reach the cache. `cell=None` means memory-only for
that call.

MEMORY LAYOUT: a series is `(none_prefix, values)`. Indicators have a warm-up
of leading None and never a None after the first value; a `compute` result
that breaks that raises ValueError (a contract breach: the engine must not
paper over it). Values live in an `array('d')`, so reading them back as Python
floats gives exactly the stored bits (inf included), and 8 bytes a bar instead
of a boxed float.

IN-PROCESS BOUND (2b-3 D-i, Switch 2 gate 3): `_mem` is an OrderedDict used as
an LRU, bounded in bytes per process (`MEM_MAX_BYTES`, 1 GiB; an entry weighs
`len(inputs_b) + 8 * len(values)`). Every `get` moves its key to the end; after
an insert the oldest entries are evicted until the total fits, never the one
just inserted. Eviction changes only what is resident: an evicted key is
adopted back from disk (counted a hit, as any disk adopt) or recomputed, and
the values served are the same bits either way. `stats["evictions"]` counts
them.

DISK FORMAT (builder's choice, 2026-10-17): one `<sha256(repr(key))>.npz` per
key under `directory`, the simcache.py precedent. Fields: `inputs` (float64),
`values` (float64), `none_prefix` (int64), `key` (the key's repr, checked on
read so a hash collision or a hand-copied file is a miss) and `sha256` (over
inputs + values + none_prefix, simcache.py's self-check; a bit flip is a
miss). Written to a unique temp file and published by `os.replace`, so a
concurrent reader sees the old whole file or the new whole file, never a torn
one. Any parse error, missing field, wrong dtype/shape, length inconsistency
(`none_prefix + len(values) != len(inputs)`), checksum or key mismatch is
treated as a MISS: the caller recomputes and the write heals the file.

WINDOWS: `os.replace` onto a file a reader has open raises PermissionError
(signal_service.py's publish retries the same way). Reads therefore slurp the
file in one call and close it at once, and the replace is retried briefly; if
it still fails that is an ordinary write failure (counted, uncached), never a
wrong value.

FAILURES NEVER RAISE: an OSError on write increments `write_errors`, prints
ONE line per process (`seriescache: write failed (<err>); continuing uncached`)
and the run goes on with the in-memory entry (the PBO cache precedent in
gauntlet_stats.py). `read_only=True` never writes (the signal service's
contract) but still extends in memory.

PRUNE (`prune`, ledger ruling, design s3.3 "size bounded and prunable"): the
quarantine recorder calls it ONCE per process run, on the cache it builds,
before the first date is simulated. It removes `*.tmp` files older than an hour
(a writer killed between write and `os.replace`), entries stored under another
ENGINE_REV (unreachable, never served) and entries whose `key` member cannot be
read as a 4-tuple literal. Since 2b-3 D-i (Switch 2 gates 1-2) it reads the
`key` member ONLY (`_read_key`), never an entry's payload or checksum: at 1h
scale a payload read per entry per run is GBs of I/O. A bit flip in the values
therefore survives prune; `_load` still verifies the checksum, treats it as a
MISS and the next write heals the file. After that it caps the disk: if the
surviving entries exceed `max_bytes` (`MAX_DISK_BYTES`, 20 GiB) it deletes the
oldest by mtime until at or under. It does nothing on a read-only cache or an
absent directory and never raises (a file that vanishes or whose stat or unlink
fails is left alone; an entry whose key cannot be read for any reason is treated
as corrupt and unlinked if possible).

IMPORT DIRECTION: this module takes ENGINE_REV from the engine; `engine.py`
imports SeriesCache only lazily, inside `simulate_asset` (and under
TYPE_CHECKING for annotations), so there is no import cycle (Task 2).
"""
from __future__ import annotations

import ast
import hashlib
import io
import os
import sys
import time
import uuid
from array import array
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import numpy as np

from .engine import ENGINE_REV  # noqa: F401  (a copy at import; see the KEY paragraph above)

CACHE_DIRNAME = "seriescache"

# Disk cap applied by prune (Switch 2 gate 2). E: has 1.3 TB free; the final
# value is proposed by the sign-off 2 report after the 1h measurement.
MAX_DISK_BYTES = 20 * 2**30

# In-process store budget per process (Switch 2 gate 3): the worker's 6
# processes may hold up to 6 GiB between them; the sign-off 2 report states it.
MEM_MAX_BYTES = 2**30

# One warning line per process, however many caches or writes fail.
_write_warned = False

_REPLACE_TRIES = 8          # os.replace onto a file a reader holds open (Windows)
_REPLACE_PAUSE_S = 0.02
_READ_TRIES = 3
_LE = sys.byteorder == "little"   # the on-disk float64 layout is little-endian


class _Entry:
    __slots__ = ("n", "inputs_b", "none_prefix", "values")

    def __init__(self, n: int, inputs_b: bytes, none_prefix: int, values: array):
        self.n = n                      # bars stored == len(inputs_b) // 8
        self.inputs_b = inputs_b        # the exact input bytes (a copy)
        self.none_prefix = none_prefix  # leading None count
        self.values = values            # array('d'), n - none_prefix floats


def _split(seq, expected_len: int, had_values: bool) -> tuple[int, array]:
    """compute()'s list -> (leading None count, array('d') of the rest).
    Raises ValueError on a wrong length or a None after a value."""
    if len(seq) != expected_len:
        raise ValueError(f"series cache: compute returned {len(seq)} values, "
                         f"expected {expected_len}")
    k = 0
    while k < expected_len and seq[k] is None:
        k += 1
    if k and had_values:
        raise ValueError("series cache: compute returned a None after a value "
                         "(None may only be a leading warm-up prefix)")
    try:
        return k, array("d", seq[k:])
    except TypeError as exc:            # a None (or non-number) after a value
        raise ValueError("series cache: compute returned a None after a value "
                         f"(None may only be a leading warm-up prefix): {exc}") from None


def _checksum(inputs_b: bytes, values: array, none_prefix: int) -> str:
    h = hashlib.sha256()
    h.update(inputs_b)
    h.update(values.tobytes())
    h.update(int(none_prefix).to_bytes(8, "little", signed=True))
    return h.hexdigest()


def _parse_blob(blob: bytes) -> tuple[str, _Entry] | None:
    """(stored key repr, entry) when `blob` is a well-formed entry, else None
    for ANY doubt: a parse error, a missing field, a wrong dtype/shape, a
    length inconsistency or a checksum mismatch. `_load`'s parse (a None is a
    miss); `prune` no longer calls it (it reads an entry's key only, via
    `_read_key`)."""
    try:
        with np.load(io.BytesIO(blob), allow_pickle=False) as z:
            inputs = z["inputs"]
            values = z["values"]
            none_prefix = int(z["none_prefix"])
            stored_key = str(z["key"])
            expected = str(z["sha256"])
        f8 = np.dtype("<f8")
        if inputs.dtype != f8 or values.dtype != f8 \
                or inputs.ndim != 1 or values.ndim != 1:
            return None
        n = int(inputs.shape[0])
        if n == 0 or none_prefix < 0 or none_prefix + int(values.shape[0]) != n:
            return None
        inputs_b = inputs.tobytes()
        vals = array("d")
        vals.frombytes(values.tobytes())
        if _checksum(inputs_b, vals, none_prefix) != expected:
            return None
        return stored_key, _Entry(n, inputs_b, none_prefix, vals)
    except Exception:                   # any parse error is a miss, never a raise
        return None


def _read_key(path: Path) -> str | None:
    """The stored key repr of the entry at `path`, reading ONLY the `key`
    member of the zip (the zip directory plus that one small member; never
    `inputs`, `values` or the checksum). None on any error. The file is closed
    before this returns: a handle left open would make a writer's `os.replace`
    onto it fail on Windows."""
    try:
        with open(path, "rb") as fh:
            with np.load(fh, allow_pickle=False) as z:
                return str(z["key"])
    except Exception:
        return None


def _stored_rev(key_repr: str):
    """ENGINE_REV out of a stored key's repr `(kind, params, rev, cell)`, or
    None when the repr is not a 4-tuple literal (then the file is corrupt)."""
    try:
        key = ast.literal_eval(key_repr)
    except Exception:
        return None
    if not isinstance(key, tuple) or len(key) != 4:
        return None
    return key[2]


class SeriesCache:
    def __init__(self, directory: Path | None = None, *, read_only: bool = False,
                 mem_max_bytes: int | None = MEM_MAX_BYTES):
        self.directory = Path(directory) if directory is not None else None
        self.read_only = read_only
        self._mem_max_bytes = mem_max_bytes     # None = unbounded
        self._mem: OrderedDict = OrderedDict()  # LRU: oldest use first
        self._mem_bytes = 0                     # sum of _entry_size over _mem
        self._stats = {"hits": 0, "extends": 0, "misses": 0, "write_errors": 0,
                       "evictions": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ------------------------------------------------------- in-process LRU

    @staticmethod
    def _entry_size(entry: _Entry) -> int:
        return len(entry.inputs_b) + 8 * len(entry.values)

    def _mem_put(self, key: tuple, entry: _Entry) -> None:
        """Store `entry` as the most recently used under `key`, REPLACING any
        entry already there (its size leaves the total first), then evict
        least-recently-used entries until the total is within the budget. The
        entry just stored is never evicted, even alone over budget. Eviction
        only decides what stays in memory: get() returns the same values on
        every path (an evicted key is re-adopted from disk or recomputed,
        both exact)."""
        old = self._mem.pop(key, None)
        if old is not None:
            self._mem_bytes -= self._entry_size(old)
        self._mem[key] = entry
        self._mem_bytes += self._entry_size(entry)
        if self._mem_max_bytes is None:
            return
        while self._mem_bytes > self._mem_max_bytes and len(self._mem) > 1:
            _, victim = self._mem.popitem(last=False)
            self._mem_bytes -= self._entry_size(victim)
            self._stats["evictions"] += 1

    # ------------------------------------------------------------------ get

    def get(self, kind: str, params: tuple, cell: str | None, inputs: array,
            compute: Callable[[int, int], list]) -> list:
        if getattr(inputs, "typecode", None) != "d":
            raise TypeError("series cache: inputs must be an array('d')")
        n = len(inputs)
        if n == 0:
            return []
        key = (kind, tuple(params), ENGINE_REV, cell)
        persist = cell is not None and self.directory is not None and _LE
        req = memoryview(inputs).cast("B")

        best = self._mem.get(key)
        if best is not None:
            self._mem.move_to_end(key)          # every get is a use
        if best is not None and not self._valid(best, n, req):
            best = None
        if (best is None or best.n < n) and persist:
            disk = self._load(key)
            if disk is not None and self._valid(disk, n, req) \
                    and (best is None or disk.n > best.n):
                best = disk
                self._mem_put(key, disk)   # adopt what another process wrote

        if best is not None and n <= best.n:
            self._stats["hits"] += 1
            return self._slice(best, n)

        if best is not None:            # extend: only the new tail is computed
            k, tail = _split(compute(best.n, n), n - best.n,
                             had_values=len(best.values) > 0)
            entry = _Entry(n, inputs.tobytes(), best.none_prefix + k,
                           best.values + tail)
            self._stats["extends"] += 1
        else:                           # miss, or an invalid entry replaced
            k, vals = _split(compute(0, n), n, had_values=False)
            entry = _Entry(n, inputs.tobytes(), k, vals)
            self._stats["misses"] += 1
        self._mem_put(key, entry)
        if persist and not self.read_only:
            self._store(key, entry)
        return self._slice(entry, n)

    @staticmethod
    def _valid(entry: _Entry, n: int, req: memoryview) -> bool:
        m = min(entry.n, n)
        k = 8 * m
        if memoryview(entry.inputs_b)[:k] != req[:k]:
            return False
        # Bytewise-equal NaNs would pass the compare above; a NaN is never
        # trusted (design: "NaN is never equal"; when in doubt, recompute).
        return not np.isnan(np.frombuffer(entry.inputs_b, dtype=np.float64,
                                          count=m)).any()

    @staticmethod
    def _slice(entry: _Entry, n: int) -> list:
        p = min(n, entry.none_prefix)
        out: list = [None] * p
        if n > p:
            out.extend(entry.values[: n - p].tolist())
        return out

    # ---------------------------------------------------------------- prune

    def prune(self, *, now: float | None = None,
              tmp_max_age_s: float = 3600.0,
              max_bytes: int | None = MAX_DISK_BYTES) -> dict:
        """Housekeeping (ledger ruling, design s3.3 "size bounded and
        prunable"). Under the cache directory it deletes
          (a) `*.tmp` files older than `tmp_max_age_s` by mtime: leftovers of
              a writer killed between its write and `os.replace`. A younger
              one may belong to a live writer and is never touched;
          (b) entry files (`*.npz`) stored under a different ENGINE_REV than
              the current one (read at call time): a bumped revision makes
              them unreachable, never served;
          (c) entry files whose `key` member cannot be read, or is not a
              4-tuple literal. Only the key is read (`_read_key`): never an
              entry's payload, never its checksum (2b-3 D-i, Switch 2 gate 1).
              A bit flip in the values survives prune; `_load` still verifies
              the checksum, treats the entry as a MISS and the next write
              heals it;
          (d) after (a)-(c), if the kept entries total more than `max_bytes`
              (None disables the cap), the oldest by `st_mtime_ns` until the
              total is at or under it (2b-3 D-i, Switch 2 gate 2).
        Returns {"tmp_removed", "stale_rev_removed", "corrupt_removed",
        "capped_removed", "kept", "bytes_kept"}; `kept` and `bytes_kept`
        describe the entries left on disk AFTER the cap. A no-op returning
        zeros on a read-only cache or an absent directory. A file that
        vanishes, or whose stat or unlink fails (a sharing violation on a
        locked file), is swallowed and left alone; prune never raises. An
        entry whose key cannot be read for ANY reason, an open error included,
        is treated as corrupt and unlinked if possible (`_read_key` returns
        None on any error): a file locked on Windows fails the unlink and
        survives, anywhere else it goes. The cost is a recompute, never a
        wrong value. An entry whose
        (mtime_ns, size) changed between the scan and the unlink was replaced
        by a writer and is left alone too (for the cap it then counts at its
        new size, and is not a candidate again). A key whose repr is not a
        Python literal (e.g. nan/inf in params) cannot be read back by
        `_stored_rev`, so it is treated as corrupt (counted under
        stale_rev_removed) and pruned on every run; all current params are
        ints and strings."""
        out = {"tmp_removed": 0, "stale_rev_removed": 0, "corrupt_removed": 0,
               "capped_removed": 0, "kept": 0, "bytes_kept": 0}
        if self.read_only or self.directory is None:
            return out
        try:
            names = sorted(os.listdir(self.directory))
        except Exception:
            return out
        t_now = time.time() if now is None else now
        survivors: list = []            # (mtime_ns, name, path, size)
        for name in names:
            path = self.directory / name
            try:
                if name.endswith(".tmp"):
                    if t_now - path.stat().st_mtime > tmp_max_age_s:
                        path.unlink()
                        out["tmp_removed"] += 1
                elif name.endswith(".npz"):
                    st0 = path.stat()
                    key_repr = _read_key(path)
                    rev = _stored_rev(key_repr) if key_repr is not None else None
                    if key_repr is None or rev is None:
                        reason = "corrupt_removed"
                    elif rev != ENGINE_REV:
                        reason = "stale_rev_removed"
                    else:
                        survivors.append((st0.st_mtime_ns, name, path, st0.st_size))
                        continue
                    # a writer may have replaced the file since it was read;
                    # the verdict is about the old bytes, so a changed file
                    # is left alone
                    st1 = path.stat()
                    if (st1.st_mtime_ns, st1.st_size) != (st0.st_mtime_ns, st0.st_size):
                        continue
                    path.unlink()
                    out[reason] += 1
            except Exception:           # vanished, sharing violation, ...
                continue
        total = sum(sv[3] for sv in survivors)
        kept = len(survivors)
        if max_bytes is not None and total > max_bytes:
            for mtime_ns, name, path, size in sorted(survivors):
                if total <= max_bytes:
                    break
                try:
                    st1 = path.stat()
                    if (st1.st_mtime_ns, st1.st_size) != (mtime_ns, size):
                        total += st1.st_size - size   # replaced: new size, kept
                        continue
                    path.unlink()
                except Exception:       # vanished (gone: no longer counted)
                    if not path.exists():
                        total -= size
                        kept -= 1
                    continue
                total -= size
                kept -= 1
                out["capped_removed"] += 1
        out["kept"] = kept
        out["bytes_kept"] = total
        return out

    # ----------------------------------------------------------------- disk

    def _path(self, key: tuple) -> Path:
        name = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()
        return self.directory / f"{name}.npz"

    def _load(self, key: tuple) -> _Entry | None:
        """The stored entry, or None for ANY doubt (absent, unreadable,
        inconsistent): the caller then recomputes."""
        blob = self._read_blob(self._path(key))
        if blob is None:
            return None
        parsed = _parse_blob(blob)
        if parsed is None or parsed[0] != repr(key):
            return None
        return parsed[1]

    @staticmethod
    def _read_blob(path: Path) -> bytes | None:
        for attempt in range(_READ_TRIES):
            try:
                return path.read_bytes()
            except PermissionError:     # a writer is mid-replace (Windows)
                time.sleep(_REPLACE_PAUSE_S)
            except OSError:
                return None
        return None

    def _store(self, key: tuple, entry: _Entry) -> None:
        global _write_warned
        final = self._path(key)
        tmp = final.with_name(f"{final.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as fh:
                np.savez(fh,
                         inputs=np.frombuffer(entry.inputs_b, dtype="<f8"),
                         values=np.frombuffer(entry.values.tobytes(), dtype="<f8"),
                         none_prefix=np.int64(entry.none_prefix),
                         key=np.str_(repr(key)),
                         sha256=np.str_(_checksum(entry.inputs_b, entry.values,
                                                  entry.none_prefix)))
            for attempt in range(_REPLACE_TRIES):
                try:
                    os.replace(tmp, final)   # atomic on POSIX and Windows alike
                    return
                except PermissionError:
                    if attempt == _REPLACE_TRIES - 1:
                        raise
                    time.sleep(_REPLACE_PAUSE_S)
        except OSError as exc:
            self._stats["write_errors"] += 1
            if not _write_warned:
                _write_warned = True
                print(f"seriescache: write failed ({exc}); continuing uncached",
                      flush=True)
            try:
                tmp.unlink()
            except OSError:
                pass
