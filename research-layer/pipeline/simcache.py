"""Content-addressed on-disk cache for the gauntlet's registry-wide
clustering re-simulations (SP4 Task P1).

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

Cache identity: a strategy's simulated output is pure and deterministic given
(1) its own spec content -- represented here by its content-addressed
`strategy_id`, since two different specs never share one, (2) the exact bytes
of every asset's price data it reads, and (3) the engine's own numeric
behaviour. `engine.ENGINE_REV` is the hand-bumped contract for (3): bumped on
ANY engine change that can alter a simulated number, so a cache entry built
under a prior revision is a guaranteed miss under a new one rather than a
silently stale hit -- see engine.py's own comment on the constant. (2) is the
per-asset data sha256 the caller already has from `screen.load_cell_data`;
this module does not read data files itself. (1) plus (2) plus (3), hashed
together, is the cache key.

CANDIDATES (this pass's own evaluations) are never served from here -- see
gauntlet.py's own comment at the call site. Only a spec NOT being evaluated
this pass, re-simulated purely so its return series can feed clustering, ever
consults this cache.

Every entry self-checks on read: the series is stored together with its own
sha256, verified before being trusted. A mismatch (truncated write, disk
corruption, a hand-edited file) is treated as a miss AND the file is deleted,
so a poisoned entry is auto-healed by the very next write rather than serving
bad data forever. This is deliberately not silent -- the caller counts it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# Two-space-safe compact JSON. Sort keys so byte-identical inputs always
# produce byte-identical serialisations (the sha256 below depends on it).
_DUMPS_KW = dict(sort_keys=True, separators=(",", ":"))


def cache_key(sid: str, data_shas: dict[str, str], engine_rev: str) -> str:
    """sha256 hex digest identifying one strategy's registry-wide
    re-simulation, over (sid, {asset: data_sha256}, engine_rev).

    `data_shas` should be the per-asset data_sha256 values the caller already
    computed for THIS spec's own universe (`screen.load_cell_data`'s
    `data_hashes`, looked up per (asset, timeframe) cell) -- not recomputed
    here. Any change to the spec's own content changes `sid` itself (it is
    content-addressed), so the key needs nothing else about the spec.
    """
    canonical = json.dumps(
        {"sid": sid, "data": data_shas, "engine": engine_rev}, **_DUMPS_KW)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _series_sha256(series: list) -> str:
    return hashlib.sha256(
        json.dumps(series, **_DUMPS_KW).encode("utf-8")).hexdigest()


class SimCache:
    """A directory of one JSON file per cache key.

    `get` returns `None` on a miss (file absent) OR a poisoned entry (self
    check fails -- the file is deleted in that case too, so the next `put`
    starts clean). `put` writes atomically (tmp file + `os.replace`) so a
    crash or a concurrent reader never observes a partially-written entry.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(raw)
            series = payload["series"]
            expected = payload["series_sha256"]
            equity_len = payload["equity_len"]
        except (json.JSONDecodeError, KeyError, TypeError):
            self._poison(path)
            return None
        if _series_sha256(series) != expected:
            self._poison(path)
            return None
        return {"series": [tuple(row) for row in series],
                "equity_len": equity_len}

    def put(self, key: str, series: list, equity_len: int) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # series may hold tuples (daily_returns_with_dates' native return
        # type); json.dumps already renders them as lists, so the sha is
        # taken over the list form both here and on the read-back check.
        list_series = [list(row) for row in series]
        payload = {"series": list_series,
                  "series_sha256": _series_sha256(list_series),
                  "equity_len": equity_len}
        final = self._path(key)
        tmp = final.with_name(final.name + ".tmp")
        tmp.write_text(json.dumps(payload, **_DUMPS_KW), encoding="utf-8")
        os.replace(tmp, final)   # atomic on POSIX and Windows alike

    def _poison(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass
