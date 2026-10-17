"""Engine performance, bit-identical (Task 2, docs/2026-10-17-engine-perf-design.md
section 3.1): the trend t-stat is computed once per (cell, window, bar) and
shared by the trend_scan* entries, the tstat_decay exit and the gauntlet's
perturbation variants.

The rule under test is the project's absolute one: every value is EXACTLY what
the direct `trend_tstat(closes[i-w+1:i+1])` path produces. Floats are compared
as IEEE-754 bits (struct.pack), so -0.0 / inf are not hidden by `==`. The
direct path is kept in the engine as the reference (`tstat=None`, and the
test-only `simulate_asset(..., _reference=True)`).

Run: python -m pytest pipeline/test_engine_perf.py -q -p no:cacheprovider
"""
from __future__ import annotations

import random
import struct

import pytest

from . import engine
from .engine import entry_signals, run_spec, simulate_asset, trend_tstat, trend_tstat_series
from .series_cache import SeriesCache

COST = {"commission_per_side": 0.001, "slippage_ticks": 0.0005}


# ---------------------------------------------------------------- fixtures

def walk_closes(n: int = 400, seed: int = 7) -> list[float]:
    """A random walk with regime-switching drift, so trend entries fire and
    t-stat decay exits follow."""
    rng = random.Random(seed)
    c, out = 100.0, []
    for i in range(n):
        drift = 0.004 if (i // 60) % 2 == 0 else -0.003
        c *= 1.0 + drift + rng.gauss(0.0, 0.012)
        out.append(c)
    return out


def walk_bars(n: int = 400, seed: int = 7) -> list[dict]:
    rng = random.Random(seed + 1)
    closes = walk_closes(n, seed)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, 0.004)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, 0.004)))
        out.append({"date": f"2024-{i:04d}", "open": o, "high": hi, "low": lo,
                    "close": c, "volume": 1.0})
        prev = c
    return out


def _bits(x):
    return struct.pack("<d", x) if isinstance(x, float) else x


def _canon(obj):
    """A structure with every float replaced by its IEEE-754 bytes."""
    if isinstance(obj, float):
        return ("f", struct.pack("<d", obj))
    if isinstance(obj, dict):
        return {k: _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_canon(v) for v in obj)
    return obj


def v2_blocks(entry_lookback=60, t_min=2.0, exit_lookback=60, t_exit=0.5,
              entry_type="trend_scan_dense", direction="both"):
    entry = {"role": "entry", "type": entry_type,
             "params": {"max_lookback": entry_lookback, "t_min": t_min}}
    if entry_type != "trend_scan":
        entry["params"]["direction"] = direction
    return [entry,
            {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 3.0}},
            {"role": "exit", "type": "tstat_decay",
             "params": {"max_lookback": exit_lookback, "t_exit": t_exit}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}]


def v2_spec(blocks, asset="BTCUSDT"):
    return {"strategy_id": "perf-test", "version": 2,
            "universe": {"assets": [asset], "asset_class": "crypto",
                         "timeframe": "1d", "session": "24x7"},
            "blocks": blocks, "cost_model": COST}


class _Counter:
    def __init__(self, monkeypatch):
        self.n = 0
        real = engine.trend_tstat

        def counted(window):
            self.n += 1
            return real(window)
        monkeypatch.setattr(engine, "trend_tstat", counted)


# ---------------------------------------------------------------- the series

def test_trend_series_equals_direct_calls():
    closes = walk_closes(400)
    cache = SeriesCache()
    for w in (20, 30, 120):
        series = trend_tstat_series(closes, w, cache, None)
        assert len(series) == len(closes)
        assert series[:w - 1] == [None] * (w - 1)
        for i in range(w - 1, len(closes)):
            assert _bits(series[i]) == _bits(trend_tstat(closes[i - w + 1:i + 1])), (w, i)
        # a second read is served by the cache and is still the same bits
        again = trend_tstat_series(closes, w, cache, None)
        assert [_bits(x) for x in again] == [_bits(x) for x in series]


# ---------------------------------------------------------------- entries

ENTRY_CASES = [("trend_scan", None)] + [
    (t, d) for t in ("trend_scan_ds", "trend_scan_dense") for d in ("long", "short", "both")]


@pytest.mark.parametrize("etype,direction", ENTRY_CASES)
@pytest.mark.parametrize("max_lookback", [60, 120])
def test_trend_scan_entries_identical_with_and_without_the_series(etype, direction, max_lookback):
    bars = walk_bars(400)
    closes = [b["close"] for b in bars]
    params = {"max_lookback": max_lookback, "t_min": 2.0}
    if direction is not None:
        params["direction"] = direction
    block = {"role": "entry", "type": etype, "params": params}
    cache = SeriesCache()
    ref = entry_signals(block, bars)
    new = entry_signals(block, bars,
                        tstat=lambda w: trend_tstat_series(closes, w, cache, None))
    assert new == ref
    assert any(s != 0 for s in ref[0])          # the fixture actually signals


# ---------------------------------------------------------------- exits

@pytest.mark.parametrize("entry_lookback,exit_lookback", [(60, 60), (120, 60), (60, 90)])
def test_tstat_decay_exit_identical(entry_lookback, exit_lookback):
    bars = walk_bars(400)
    blocks = v2_blocks(entry_lookback=entry_lookback, exit_lookback=exit_lookback)
    golden = simulate_asset(blocks, bars, COST, version=2, _reference=True)
    plain = simulate_asset(blocks, bars, COST, version=2)
    shared = SeriesCache()
    cached = simulate_asset(blocks, bars, COST, version=2, series_cache=shared, cell="BTCUSDT")
    cached_again = simulate_asset(blocks, bars, COST, version=2, series_cache=shared,
                                  cell="BTCUSDT")
    assert _canon(plain) == _canon(golden)
    assert _canon(cached) == _canon(golden)
    assert _canon(cached_again) == _canon(golden)
    # the fixture exercises the exit (not vacuous)
    assert any(t["exit_reason"] == "signal:tstat_decay" for t in golden["trades"])


# ---------------------------------------------------------------- counts

def test_each_tstat_value_is_computed_once_per_cell_and_window(monkeypatch):
    bars = walk_bars(400)
    n = len(bars)
    blocks = v2_blocks(entry_type="trend_scan", entry_lookback=60, exit_lookback=60)
    windows = list(range(20, 60 + 1, 10))
    counter = _Counter(monkeypatch)
    out = simulate_asset(blocks, bars, COST, version=2)
    assert counter.n == sum(n - w + 1 for w in windows)
    assert any(t["exit_reason"] == "signal:tstat_decay" for t in out["trades"])
    # the direct path really does recompute (the count above has teeth)
    counter.n = 0
    simulate_asset(blocks, bars, COST, version=2, _reference=True)
    assert counter.n > sum(n - w + 1 for w in windows)


def test_perturbation_variants_share_the_series(monkeypatch):
    bars = {"BTCUSDT": walk_bars(400)}
    cache = SeriesCache()
    spec_a = v2_spec(v2_blocks(t_min=2.0))
    spec_b = v2_spec(v2_blocks(t_min=2.5))
    ref_a, ref_b = run_spec(spec_a, bars), run_spec(spec_b, bars)
    counter = _Counter(monkeypatch)
    got_a = run_spec(spec_a, bars, series_cache=cache)
    first = counter.n
    assert first > 0
    counter.n = 0
    got_b = run_spec(spec_b, bars, series_cache=cache)
    assert counter.n == 0
    assert _canon(got_a) == _canon(ref_a)
    assert _canon(got_b) == _canon(ref_b)


# ---------------------------------------------------------------- Review Focus 1

def _raised(fn):
    with pytest.raises(Exception) as ei:
        fn()
    return type(ei.value), str(ei.value)


def test_empty_windows_raise_as_today():
    bars = walk_bars(200)
    closes = [b["close"] for b in bars]
    cache = SeriesCache()
    for etype, direction in ENTRY_CASES:
        params = {"max_lookback": 15, "t_min": 2.0}
        if direction is not None:
            params["direction"] = direction
        block = {"role": "entry", "type": etype, "params": params}
        ref = _raised(lambda: entry_signals(block, bars))
        new = _raised(lambda: entry_signals(
            block, bars, tstat=lambda w: trend_tstat_series(closes, w, cache, None)))
        assert new == ref
    # the exit block's own windows are empty: raises at the first bar in position
    blocks = v2_blocks(exit_lookback=15)
    ref = _raised(lambda: simulate_asset(blocks, bars, COST, version=2, _reference=True))
    new = _raised(lambda: simulate_asset(blocks, bars, COST, version=2))
    assert new == ref
    assert ref[0] is ValueError
