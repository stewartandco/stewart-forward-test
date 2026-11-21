"""Process fan-out for cell sweeps.

The engine is pure and deterministic, so parallel results are identical to
serial ones - the reason this is safe under screen-protocol-v1, which pins
results to the engine rather than to how it was scheduled.

Results keep INPUT order. A run manifest and a trial denominator are indexed by
position, so out-of-order results would mis-attribute one cell's verdict to
another.

A failing cell yields a CellError in its slot rather than killing the run: one
bad cell must not lose 24 good ones.
"""
from __future__ import annotations

import concurrent.futures as cf


class CellError(Exception):
    """A cell that raised. Carried in the results list in the cell's slot."""


def run_all(fn, items: list, workers: int = 0) -> list:
    """Map fn over items across processes; return results in input order."""
    if workers == 1 or len(items) <= 1:
        return [_guard(fn, x) for x in items]

    if workers <= 0:
        import os
        workers = max(1, (os.cpu_count() or 2) - 1)

    with cf.ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_Guarded(fn), items))


def _guard(fn, x):
    try:
        return fn(x)
    except Exception as exc:  # noqa: BLE001 - one cell must not kill the run
        return CellError(f"{type(exc).__name__}: {exc}")


class _Guarded:
    """Picklable wrapper so the guard survives the process boundary."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, x):
        return _guard(self.fn, x)
