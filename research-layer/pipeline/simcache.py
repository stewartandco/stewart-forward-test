"""Content-addressed on-disk cache for the gauntlet's registry-wide
clustering re-simulations (SP4 Task P1; arrays since 2026-09-03).

Every gauntlet pass re-simulates EVERY registered strategy (including every
graveyarded and quarantined sibling, not just this pass's candidates) so
`gauntlet.py` can cluster on the whole registry's return series. That
registry only grows, so this cost grows with it forever -- verified as the
single largest identified hot spot short of the engine itself (docs/plans/
2026-08-26-sp4-perf-and-benchmark.md, "Verified hot spots").

This module caches the ONE thing those registry-wide re-simulations exist to
produce: the dated daily-returns series (`gauntlet.daily_returns_with_dates`
output). It deliberately does NOT cache full trades or the raw equity curve
-- the returns series is everything clustering, train_sharpe and the PBO
family matrix need, and it is small.

REPRESENTATION (2026-09-03, docs/plans/2026-09-03-simcache-arrays.md): a
series is a `Series` -- an int32 array of day ordinals plus a float64 array
of returns -- on disk as `<key>.npz`. It used to be a JSON list of
`[date, ret]` pairs materialised as Python objects: ~6,000 registered
strategies x up to 11,450 points (1981->2026) x ~150 bytes a pair put the
gauntlet parent at 9.6 GB of commit, which is what killed the 10:30 cycle
that day (a worker's OpenBLAS malloc failed on a box at its commit limit).
The values are the SAME float64s, so every verdict is byte-identical; only
the container changed. Legacy `.json` entries are migrated on read (parsed,
self-checked exactly as before, rewritten as `.npz`, unlinked) and in bulk
by `python -m pipeline.simcache migrate DIR`.

Cache identity: a strategy's simulated output is pure and deterministic given
(1) its own spec content -- represented here by its content-addressed
`strategy_id`, since two different specs never share one, (2) the exact bytes
of every asset's price data it reads, (3) the engine's own numeric behaviour,
and (4) the RESOLVED `periods_per_year` the engine annualizes with. `engine.
ENGINE_REV` is the hand-bumped contract for (3): bumped on ANY engine change
that can alter a simulated number, so a cache entry built under a prior
revision is a guaranteed miss under a new one rather than a silently stale
hit -- see engine.py's own comment on the constant. (2) is the per-asset data
sha256 the caller already has from `screen.load_cell_data`; this module does
not read data files itself. (4) is needed because `periods_per_year` is NOT
implied by (1)-(3): it comes from `cells.SESSION_PERIODS[universe.session]`
(engine.py's `run_spec`), a mapping that lives outside the spec, outside the
per-asset data, and outside ENGINE_REV, yet feeds vol_target's realized-vol
sizing (engine.py's `realized_ann_vol`) and therefore the simulated return
series itself. Without it, a SESSION_PERIODS edit (e.g. correcting fx's
trading-day count) would silently keep serving every cached fx series computed
under the OLD mapping instead of missing and re-simulating. (1) plus (2) plus
(3) plus (4), hashed together, is the cache key.

CANDIDATES (this pass's own evaluations) are never served from here -- see
gauntlet.py's own comment at the call site. Only a spec NOT being evaluated
this pass, re-simulated purely so its return series can feed clustering, ever
consults this cache.

Every entry self-checks on read: the series is stored together with its own
sha256 (over the raw array bytes), verified before being trusted. A mismatch
(truncated write, disk corruption, a hand-edited file) is treated as a miss
AND the file is deleted, so a poisoned entry is auto-healed by the very next
write rather than serving bad data forever. This is deliberately not silent
-- the caller counts it.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date as _date
from pathlib import Path

import numpy as np

# Two-space-safe compact JSON. Sort keys so byte-identical inputs always
# produce byte-identical serialisations (the sha256 below depends on it).
_DUMPS_KW = dict(sort_keys=True, separators=(",", ":"))


def cache_key(sid: str, data_shas: dict[str, str], engine_rev: str,
              periods_per_year: int) -> str:
    """sha256 hex digest identifying one strategy's registry-wide
    re-simulation, over (sid, {asset: data_sha256}, engine_rev,
    periods_per_year).

    `data_shas` should be the per-asset data_sha256 values the caller already
    computed for THIS spec's own universe (`screen.load_cell_data`'s
    `data_hashes`, looked up per (asset, timeframe) cell) -- not recomputed
    here. Any change to the spec's own content changes `sid` itself (it is
    content-addressed), so the key needs nothing else about the spec.

    `periods_per_year` must be the RESOLVED value run_spec itself would use
    (`cells.SESSION_PERIODS.get(spec["universe"]["session"], 365)`) -- not
    recomputed here, same discipline as `data_shas`. See this module's
    docstring for why it is a fourth, independent key component rather than
    implied by the other three.
    """
    canonical = json.dumps(
        {"sid": sid, "data": data_shas, "engine": engine_rev,
         "periods_per_year": periods_per_year}, **_DUMPS_KW)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Series: the one container every registry-wide series lives in
# ---------------------------------------------------------------------------

def date_ordinal(d) -> int:
    """`YYYY-MM-DD` (any time suffix ignored -- gauntlet._date_le's rule) ->
    proleptic Gregorian ordinal. Accepts a str or a datetime.date."""
    if isinstance(d, _date):
        return d.toordinal()
    return _date.fromisoformat(str(d)[:10]).toordinal()


def _iso(ordinal: int) -> str:
    return _date.fromordinal(int(ordinal)).isoformat()


class Series:
    """Dated daily returns as two parallel arrays.

    `dates`: int32 day ordinals, in the order the equity curve produced them
    (chronological); `rets`: float64 returns. `len()` and iteration behave
    exactly like the list of `(date_str, float)` pairs this replaced, so a
    diagnostic that writes `for d, r in series` still works; the hot paths
    (intersection, the train slice) read the arrays directly.
    """
    __slots__ = ("dates", "rets")

    def __init__(self, dates, rets):
        self.dates = np.ascontiguousarray(dates, dtype=np.int32)
        self.rets = np.ascontiguousarray(rets, dtype=np.float64)
        if self.dates.shape != self.rets.shape or self.dates.ndim != 1:
            raise ValueError("Series needs two equal-length 1-d arrays, got "
                             f"{self.dates.shape} and {self.rets.shape}")

    @classmethod
    def from_pairs(cls, pairs) -> "Series":
        """From `[(date_str, ret), ...]` (daily_returns_with_dates' output or a
        legacy cache list). An empty input is an empty Series."""
        rows = list(pairs)
        if not rows:
            return cls(np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.float64))
        return cls(np.fromiter((date_ordinal(d) for d, _ in rows), dtype=np.int32,
                               count=len(rows)),
                   np.fromiter((float(r) for _, r in rows), dtype=np.float64,
                               count=len(rows)))

    def __len__(self) -> int:
        return int(self.dates.shape[0])

    def __iter__(self):
        rets = self.rets.tolist()
        for o, r in zip(self.dates.tolist(), rets):
            yield (_iso(o), r)

    def pairs(self) -> list[tuple[str, float]]:
        return list(iter(self))

    def train(self, cutoff: str) -> list[float]:
        """Returns dated `<= cutoff` (date-only compare, gauntlet._date_le's
        rule), as the Python floats the pure-Python consumers (train Sharpe,
        PBO's block sums) expect. Same floats, same order as slicing the
        pairs."""
        return self.rets[self.dates <= date_ordinal(cutoff)].tolist()

    def sha256(self) -> str:
        h = hashlib.sha256()
        h.update(self.dates.tobytes())
        h.update(self.rets.tobytes())
        return h.hexdigest()

    def __eq__(self, other) -> bool:
        return (isinstance(other, Series)
                and np.array_equal(self.dates, other.dates)
                and np.array_equal(self.rets, other.rets))


def _series_sha256_json(series: list) -> str:
    """The legacy entry's self-check: sha over the compact JSON of the list
    form -- kept verbatim so a legacy file is trusted or poisoned by exactly
    the rule it was written under."""
    return hashlib.sha256(
        json.dumps(series, **_DUMPS_KW).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# SimCache
# ---------------------------------------------------------------------------

class SimCache:
    """A directory of one `.npz` file per cache key (legacy `.json` entries
    are migrated on read).

    `get` returns `None` on a miss (file absent) OR a poisoned entry (self
    check fails -- the file is deleted in that case too, so the next `put`
    starts clean). `put` writes atomically (tmp file + `os.replace`) so a
    crash or a concurrent reader never observes a partially-written entry.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.npz"

    def _legacy_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    # -- read ---------------------------------------------------------------

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if path.exists():
            return self._get_npz(path)
        legacy = self._legacy_path(key)
        if legacy.exists():
            return self._migrate_legacy(legacy, path)
        return None

    def _get_npz(self, path: Path) -> dict | None:
        try:
            with np.load(path) as z:
                dates, rets = z["dates"], z["rets"]
                equity_len = int(z["equity_len"])
                expected = str(z["sha256"])
        except (OSError, KeyError, ValueError, TypeError):
            self._poison(path)
            return None
        try:
            series = Series(dates, rets)
        except ValueError:
            self._poison(path)
            return None
        if series.sha256() != expected:
            self._poison(path)
            return None
        return {"series": series, "equity_len": equity_len}

    def _migrate_legacy(self, legacy: Path, path: Path) -> dict | None:
        """Read a pre-2026-09-03 JSON entry under ITS rules, rewrite it as
        `.npz`, remove the JSON. A legacy file that fails its own self-check
        is poisoned exactly as it always was."""
        try:
            raw = legacy.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(raw)
            rows = payload["series"]
            expected = payload["series_sha256"]
            equity_len = int(payload["equity_len"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._poison(legacy)
            return None
        if _series_sha256_json(rows) != expected:
            self._poison(legacy)
            return None
        try:
            series = Series.from_pairs(rows)
        except (ValueError, TypeError):
            self._poison(legacy)
            return None
        self._write(path, series, equity_len)
        self._poison(legacy)          # the .npz is the entry now
        return {"series": series, "equity_len": equity_len}

    # -- write --------------------------------------------------------------

    def put(self, key: str, series, equity_len: int) -> None:
        if not isinstance(series, Series):
            series = Series.from_pairs(series)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._write(self._path(key), series, int(equity_len))
        # A legacy entry for the same key is superseded, never read again.
        self._poison(self._legacy_path(key))

    def _write(self, final: Path, series: Series, equity_len: int) -> None:
        tmp = final.with_name(final.name + ".tmp.npz")
        with open(tmp, "wb") as f:
            np.savez(f, dates=series.dates, rets=series.rets,
                     equity_len=np.int64(equity_len),
                     sha256=np.str_(series.sha256()))
        os.replace(tmp, final)   # atomic on POSIX and Windows alike

    def _poison(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    # -- bulk migration -----------------------------------------------------

    def migrate(self, verbose: bool = True) -> dict:
        """Convert every legacy `.json` entry in the directory. Returns
        {"migrated", "poisoned", "already"} counts."""
        counts = {"migrated": 0, "poisoned": 0, "already": 0}
        legacy_files = sorted(self.cache_dir.glob("*.json")) if self.cache_dir.exists() else []
        counts["already"] = len(list(self.cache_dir.glob("*.npz"))) if self.cache_dir.exists() else 0
        for i, legacy in enumerate(legacy_files, start=1):
            key = legacy.stem
            target = self._path(key)
            if target.exists():
                self._poison(legacy)
                counts["already"] += 1
            elif self._migrate_legacy(legacy, target) is None:
                counts["poisoned"] += 1
            else:
                counts["migrated"] += 1
            if verbose and (i % 500 == 0 or i == len(legacy_files)):
                print(f"[simcache] migrated {i}/{len(legacy_files)} legacy entries "
                      f"({counts['poisoned']} poisoned)", flush=True)
        return counts


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "migrate":
        print("usage: python -m pipeline.simcache migrate <cache dir>", file=sys.stderr)
        return 2
    counts = SimCache(Path(argv[1])).migrate()
    print(f"[simcache] done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
