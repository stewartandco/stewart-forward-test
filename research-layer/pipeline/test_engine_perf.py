"""Engine performance, bit-identical (Task 2, docs/2026-10-17-engine-perf-design.md
section 3.1): the trend t-stat is computed once per (cell, window, bar) and
shared by the trend_scan* entries, the tstat_decay exit and the gauntlet's
perturbation variants.

The rule under test is the project's absolute one: every value is EXACTLY what
the direct `trend_tstat(closes[i-w+1:i+1])` path produces. Floats are compared
as IEEE-754 bits (struct.pack), so -0.0 / inf are not hidden by `==`. The
direct path is kept in the engine as the reference (`tstat=None`, and the
test-only `simulate_asset(..., _reference=True)`).

Task 3 (section 3.2): `gate_mask`'s vol_percentile counts `v <= vol[i]` over
the trailing window with a sorted window and bisect instead of a rescan, and
the realized vol plus the percentile series are cached per cell. The reference
is `percentile_rank` per bar on today's `realized_ann_vol(closes, lookback)`
(`gate_mask(..., _reference=True)`).

Run: python -m pytest pipeline/test_engine_perf.py -q -p no:cacheprovider
"""
from __future__ import annotations

import inspect
import math
import random
import struct
import time

import pytest

from . import engine
from .engine import (entry_signals, gate_mask, percentile_rank, realized_ann_vol, run_spec,
                     simulate_asset, trend_tstat, trend_tstat_series, vol_percentile_series)
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


# ================================================================ Task 3
# vol_percentile: an exact incremental percentile and a cached realized vol.

def _bits_list(xs):
    return [_bits(x) for x in xs]


def _reference_pctile(vol, window):
    return [percentile_rank(vol, i, window) for i in range(len(vol))]


def tied_vols(n: int, none_prefix: int, seed: int) -> list:
    """Random vols rounded to 2 dp over a narrow range, so exact ties are
    everywhere, behind a leading None warm-up (realized_ann_vol's shape)."""
    rng = random.Random(seed)
    return [None] * none_prefix + [round(rng.uniform(0.2, 1.2), 2)
                                   for _ in range(n - none_prefix)]


@pytest.mark.parametrize("window", [365, 365 * 24])
def test_vol_percentile_series_equals_percentile_rank_bitwise(window):
    n = 9000
    vol = tied_vols(n, 90, seed=window)
    ref = _reference_pctile(vol, window)
    got = vol_percentile_series(vol, window)
    assert len(got) == n
    assert _bits_list(got) == _bits_list(ref)
    assert sum(r is not None for r in ref) == n - 90 - window + 1   # not vacuous
    assert len({r for r in ref if r is not None}) > 50              # ties, many ranks
    # any [start, stop) slice computes the same values as the full pass
    for start, stop in [(0, 1), (window - 2, window + 120), (n - 400, n), (n - 1, n)]:
        part = vol_percentile_series(vol, window, start=start, stop=stop)
        assert _bits_list(part) == _bits_list(ref[start:stop]), (start, stop)


def test_vol_percentile_none_anywhere_follows_percentile_rank():
    # realized_ann_vol only makes a None prefix, but the rule is percentile_rank's:
    # ANY None in the window -> None
    vol = tied_vols(400, 5, seed=3)
    vol[150] = None
    vol[151] = None
    for window in (1, 2, 20, 100, 399, 400, 401):
        assert _bits_list(vol_percentile_series(vol, window)) == \
            _bits_list(_reference_pctile(vol, window)), window
    assert vol_percentile_series([], 20) == []


def test_vol_percentile_non_positive_window_behaves_as_percentile_rank():
    vol = tied_vols(50, 0, seed=4)
    for window in (0, -3):
        with pytest.raises(ZeroDivisionError):
            percentile_rank(vol, 10, window)
        with pytest.raises(ZeroDivisionError):
            vol_percentile_series(vol, window)


# ---------------------------------------------------------------- Review Focus 2

def test_vol_percentile_with_nan_and_inf_matches_the_reference():
    nan, inf = float("nan"), float("inf")
    vol = tied_vols(600, 10, seed=5)
    for i in (40, 41, 300):
        vol[i] = nan
    for i in (60, 61, 200, 420):
        vol[i] = inf
    for i in (80, 250):
        vol[i] = -inf
    vol[90] = 0.0
    vol[91] = -0.0
    for window in (1, 7, 30, 120, 365):
        ref = _reference_pctile(vol, window)
        got = vol_percentile_series(vol, window)
        assert _bits_list(got) == _bits_list(ref), window
    # bisect orders inf exactly as `<=` does: inf counts every value, -inf only
    # itself; a NaN window falls back to percentile_rank (NaN <= x is False)
    ref = _reference_pctile(vol, 30)
    assert ref[420] == 1.0 and ref[250] == 1 / 30      # windows free of NaN
    assert ref[61] == 28 / 30                          # NaN at 40, 41 counts as not <=
    assert ref[41] is not None and ref[41] == vol_percentile_series(vol, 30)[41]
    # the gate end to end: a NaN close makes NaN vols; a zero close raises as today
    closes = walk_closes(700, seed=11)
    closes[500] = nan
    bars = [{"close": c} for c in closes]
    gates = [{"type": "vol_percentile", "params": {"lookback": 20, "max_pctile": 0.6}}]
    ref_mask = gate_mask(gates, bars, 1, _reference=True)
    assert gate_mask(gates, bars, 1) == ref_mask
    assert gate_mask(gates, bars, 1, series_cache=SeriesCache(), cell="BTCUSDT") == ref_mask
    assert any(ref_mask) and not all(ref_mask[400:])
    closes[500] = 0.0
    bars = [{"close": c} for c in closes]
    ref_exc = _raised(lambda: gate_mask(gates, bars, 1, _reference=True))
    assert _raised(lambda: gate_mask(gates, bars, 1)) == ref_exc
    assert ref_exc[0] is ValueError             # log(0 / c) at bar 500, as today


# ---------------------------------------------------------------- the gate

GATE_TYPES = [
    {"type": "regime_ma", "params": {"ma_len": 50}},
    {"type": "regime_ma_short", "params": {"ma_len": 50}},
    {"type": "regime_ma_short_dense", "params": {"ma_len": 40}},
    {"type": "vol_percentile", "params": {"lookback": 90, "max_pctile": 0.8}},
    {"type": "vol_percentile_dense", "params": {"lookback": 120, "max_pctile": 0.6}},
]


@pytest.mark.parametrize("bars_per_day,n", [(1, 1500), (24, 365 * 24 + 900)])
@pytest.mark.parametrize("gate", GATE_TYPES, ids=lambda g: g["type"])
def test_gate_mask_identical_with_and_without_cache(gate, bars_per_day, n, tmp_path):
    bars = walk_bars(n, seed=13)
    gates = [gate]
    ref = gate_mask(gates, bars, bars_per_day, _reference=True)
    assert any(ref) and not all(ref)                       # not vacuous
    assert gate_mask(gates, bars, bars_per_day) == ref     # private memory-only cache
    shared = SeriesCache()
    assert gate_mask(gates, bars, bars_per_day, series_cache=shared, cell="BTCUSDT") == ref
    assert gate_mask(gates, bars, bars_per_day, series_cache=shared, cell="BTCUSDT") == ref
    # disk: a prefix first (screen's cutoff), then the full history extends it,
    # then a fresh process-like cache reads both back
    disk = SeriesCache(tmp_path)
    k = n - 37
    assert gate_mask(gates, bars[:k], bars_per_day, series_cache=disk, cell="BTCUSDT") == \
        gate_mask(gates, bars[:k], bars_per_day, _reference=True)
    assert gate_mask(gates, bars, bars_per_day, series_cache=disk, cell="BTCUSDT") == ref
    again = SeriesCache(tmp_path)
    assert gate_mask(gates, bars, bars_per_day, series_cache=again, cell="BTCUSDT") == ref
    combo = [gate, GATE_TYPES[0], GATE_TYPES[3]]
    assert gate_mask(combo, bars, bars_per_day, series_cache=again, cell="BTCUSDT") == \
        gate_mask(combo, bars, bars_per_day, _reference=True)


def test_gate_vol_series_is_realized_ann_vol_bitwise_at_every_extension():
    # the cached vol keeps today's default periods_per_year (365) in every
    # session and timeframe, and extends to exactly the cold values
    assert inspect.signature(realized_ann_vol).parameters["periods_per_year"].default == 365
    assert engine.GATE_VOL_PERIODS_PER_YEAR == 365
    closes = walk_closes(600, seed=17)
    for lookback in (2, 5, 90):
        full = realized_ann_vol(closes, lookback)
        for first in (1, lookback - 1, lookback, lookback + 1, 300):
            cache = SeriesCache()
            head = engine.gate_vol_series(closes[:first], lookback, cache, "X")
            assert _bits_list(head) == _bits_list(full[:first]), (lookback, first)
            tail = engine.gate_vol_series(closes, lookback, cache, "X")
            assert _bits_list(tail) == _bits_list(full), (lookback, first)
            assert cache.stats["extends"] == 1
    for lookback in (0, 1):             # no window of > 1 return: all None, as today
        assert engine.gate_vol_series(closes, lookback, SeriesCache(), None) == \
            realized_ann_vol(closes, lookback)


def test_gate_percentile_series_extends_exactly():
    closes = walk_closes(1400, seed=19)
    vol = realized_ann_vol(closes, 90)
    ref = _reference_pctile(vol, 365)
    cache = SeriesCache()
    for stop in (100, 454, 455, 456, 900, 901, 1400):
        got = engine.vol_percentile_gate_series(closes[:stop], 90, 365, cache, "X")
        assert _bits_list(got) == _bits_list(ref[:stop]), stop
    assert cache.stats["extends"] >= 6


def test_vol_series_cached_across_variants(monkeypatch):
    bars = walk_bars(1200, seed=23)
    calls = []
    real = engine.realized_ann_vol

    def counted(*a, **k):
        calls.append((len(a[0]),) + a[1:] + tuple(sorted(k.items())))
        return real(*a, **k)
    monkeypatch.setattr(engine, "realized_ann_vol", counted)
    cache = SeriesCache()
    gates = [{"type": "vol_percentile", "params": {"lookback": 90, "max_pctile": 0.8}}]
    variant = [{"type": "vol_percentile", "params": {"lookback": 90, "max_pctile": 0.6}}]
    gate_mask(gates, bars, 1, series_cache=cache, cell="BTCUSDT")
    assert calls == [(1200, 90)]                # positional args only: ppy is the default
    calls.clear()
    gate_mask(variant, bars, 1, series_cache=cache, cell="BTCUSDT")
    assert calls == []                          # the percentile series was a hit


def test_simulate_asset_with_vol_percentile_identical(monkeypatch):
    bars = walk_bars(1200, seed=29)
    calls = []
    real = engine.percentile_rank

    def counted(*a):
        calls.append(a[1])
        return real(*a)
    monkeypatch.setattr(engine, "percentile_rank", counted)
    blocks = [{"role": "entry", "type": "channel_breakout",
               "params": {"lookback": 10, "direction": "both"}},
              {"role": "filter", "type": "vol_percentile_dense",
               "params": {"lookback": 90, "max_pctile": 0.7}},
              {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 1.5}},
              {"role": "target", "type": "r_multiple", "params": {"r": 2.0}},
              {"role": "risk", "type": "vol_target",
               "params": {"ann_vol": 0.4, "lookback": 30}}]
    golden = simulate_asset(blocks, bars, COST, version=2, _reference=True)
    assert len(golden["trades"]) > 3
    assert len(calls) == len(bars)              # the golden really took the direct path
    calls.clear()
    assert _canon(simulate_asset(blocks, bars, COST, version=2)) == _canon(golden)
    assert calls == []                          # ... and the default path did not
    shared = SeriesCache()
    for _ in range(2):
        got = simulate_asset(blocks, bars, COST, version=2, series_cache=shared, cell="BTCUSDT")
        assert _canon(got) == _canon(golden)


def test_gate_mask_percentile_is_not_quadratic():
    # a guard against a regression to O(n x window), not a benchmark
    rng = random.Random(31)
    c, closes = 100.0, []
    for _ in range(20000):
        c *= math.exp(rng.gauss(0.0, 0.004))
        closes.append(c)
    bars = [{"close": x} for x in closes]
    gates = [{"type": "vol_percentile", "params": {"lookback": 90, "max_pctile": 0.8}}]
    t0 = time.perf_counter()
    mask = gate_mask(gates, bars, 24)           # window 365 * 24 = 8760
    elapsed = time.perf_counter() - t0
    print(f"gate_mask n=20000 window=8760: {elapsed:.3f} s")
    assert any(mask)
    assert elapsed < 5.0
