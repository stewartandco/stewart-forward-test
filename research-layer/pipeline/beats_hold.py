"""Beats-hold (design 2026-11-05, section 3): buy and hold the strategy's own
asset over the OOS window, and the gauntlet comparison of the strategy's
Sharpe with the hold's. Pure: no I/O.

gauntlet_core imports this module (Task 3), so every gauntlet_core name is
imported INSIDE the functions to avoid a cycle.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

HOLD_BLOCK_LEN = 5       # circular block length (days) of the paired test
HOLD_TEST_PATHS = 2000   # bootstrap resamples of the paired test


def _class_and_spec(spec: dict) -> tuple[str, dict]:
    from . import cells
    asset_class = spec["universe"].get("asset_class", "crypto")
    return asset_class, cells.CLASSES.get(asset_class, {})


def hold_oos_returns(spec: dict, spec_bars: dict,
                     cutoff: str) -> list[tuple[str, float]]:
    """Daily (date, return) pairs of buy-and-hold over the OOS window.

    Per asset: daily marks (last bar of each UTC date) with date after
    `cutoff`; level_t = close_t / first_open times (1 - per_side) at entry,
    where per_side = commission_per_side + slippage_ticks of the class cost
    model. Assets are combined by the mean of levels on the dates EVERY asset
    has. The exit cost (1 - per_side) is applied on the last combined date.
    The first return is level_0 / 1.0 - 1.
    """
    from .bars import daily_bars
    from .gauntlet_core import _date_le

    _, class_spec = _class_and_spec(spec)
    cost = class_spec["cost_model"]
    per_side = cost["commission_per_side"] + cost["slippage_ticks"]

    per_asset: list[dict[str, float]] = []
    for a in spec["universe"]["assets"]:
        raw = [b for b in spec_bars[a] if not _date_le(b["date"], cutoff)]
        oos = daily_bars(raw)
        if not oos:
            return []
        # entry at the first OOS BAR's open (on an intraday cell a daily mark's
        # own open is the day's last bar's open, not the day's open)
        first_open = raw[0]["open"]
        per_asset.append({b["date"][:10]: (b["close"] / first_open) * (1 - per_side)
                          for b in oos})
    if not per_asset:
        return []

    dates = sorted(set.intersection(*(set(m) for m in per_asset)))
    if not dates:
        return []
    levels = [sum(m[d] for m in per_asset) / len(per_asset) for d in dates]
    levels[-1] *= (1 - per_side)

    out: list[tuple[str, float]] = []
    prev = 1.0
    for d, lv in zip(dates, levels):
        out.append((d, lv / prev - 1))
        prev = lv
    return out


def gauntlet_beats_hold(spec: dict, spec_bars: dict,
                        strat_dated: list[tuple[str, float]],
                        cutoff: str) -> tuple[bool, dict | None]:
    """The beats_hold gate. (True, None) when the class does not declare
    benchmark "self" (not applicable, nothing recorded). Otherwise pairs the
    strategy's OOS returns with the hold on the date intersection and passes
    iff both Sharpes exist and strategy > hold (strict; None fails)."""
    from .gauntlet_core import (BENCHMARK_BASIS, _DEFAULT_BASIS, _date_le,
                                _annualized_sharpe_from_returns)

    asset_class, class_spec = _class_and_spec(spec)
    if class_spec.get("benchmark") != "self":
        return True, None

    hold = dict(hold_oos_returns(spec, spec_bars, cutoff))
    strat = {d[:10]: r for d, r in strat_dated if not _date_le(d, cutoff)}
    common = sorted(set(hold) & set(strat))
    s = _annualized_sharpe_from_returns([strat[d] for d in common])
    h = _annualized_sharpe_from_returns([hold[d] for d in common])
    passed = s is not None and h is not None and s > h
    return passed, {"strategy_sharpe": s, "hold_sharpe": h,
                    "n_days": len(common), "window": "oos",
                    "basis": BENCHMARK_BASIS.get(asset_class, _DEFAULT_BASIS)}


def forward_hold_series(rows: list[dict]) -> list[tuple[str, float]]:
    """Forward buy-and-hold curve from quarantine rows: per asset, `price`
    rebased to 1.0 at that asset's FIRST row (earliest date), combined by the
    mean across assets on the dates where EVERY asset reports -- exactly
    livegate.forward_series' date rule. No cost is charged."""
    first: dict[str, tuple[str, float]] = {}
    for r in rows:
        a = r["asset"]
        if a not in first or r["date"] < first[a][0]:
            first[a] = (r["date"], r["price"])
    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        by_date[r["date"]][r["asset"]] = r["price"] / first[r["asset"]][1]
    out = []
    for d in sorted(by_date):
        got = by_date[d]
        if set(got) != set(first):
            continue
        out.append((d, sum(got.values()) / len(got)))
    return out


def _delta_and_grad(m: np.ndarray) -> tuple[np.ndarray, np.ndarray,
                                            np.ndarray, np.ndarray]:
    """Sharpe difference f(m) and its gradient, for m of shape (..., 4) =
    (E a, E b, E a^2, E b^2). Also returns both variances."""
    m1, m2, m3, m4 = m[..., 0], m[..., 1], m[..., 2], m[..., 3]
    va, vb = m3 - m1 ** 2, m4 - m2 ** 2
    f = m1 / np.sqrt(va) - m2 / np.sqrt(vb)
    grad = np.stack([m3 / va ** 1.5, -m4 / vb ** 1.5,
                     -m1 / (2 * va ** 1.5), m2 / (2 * vb ** 1.5)], axis=-1)
    return f, grad, va, vb


def _block_se(y: np.ndarray, m: np.ndarray, grad: np.ndarray,
              block: int) -> np.ndarray:
    """Ledoit-Wolf block HAC standard error of f, for y of shape (..., T, 4)."""
    T = y.shape[-2]
    nb = T // block
    blocks = y[..., :nb * block, :].reshape(*y.shape[:-2], nb, block, 4)
    psi = (blocks - m[..., None, None, :]).sum(axis=-2) / math.sqrt(block)
    proj = (psi * grad[..., None, :]).sum(axis=-1)     # grad . psi_j
    return np.sqrt((proj ** 2).mean(axis=-1) / T)


def paired_sharpe_pvalue(strat: list[float], hold: list[float], seed: int,
                         block: int = HOLD_BLOCK_LEN,
                         paths: int = HOLD_TEST_PATHS) -> float:
    """One-sided p for H0: SR(strat) <= SR(hold), per-period Sharpes on the
    PAIRED series (Ledoit-Wolf 2008, studentized circular block bootstrap).

    Returns 1.0 (cannot reject) when the record is shorter than 2*block,
    either sample variance is <= 0, se(y) <= 0, or t_obs is non-finite. A
    resample whose variance or se* is <= 0, or whose t* is non-finite,
    contributes t* = +inf. Mismatched lengths are a caller bug: ValueError.
    """
    if len(strat) != len(hold):
        raise ValueError(f"paired series differ in length: "
                         f"{len(strat)} != {len(hold)}")
    T = len(strat)
    if T < 2 * block:
        return 1.0
    a = np.asarray(strat, dtype=float)
    b = np.asarray(hold, dtype=float)
    with np.errstate(all="ignore"):
        # np.ptp catches an exactly constant series, whose float np.var can
        # come out a tiny positive number instead of 0
        for x in (a, b):
            if np.ptp(x) == 0 or np.var(x, ddof=1) <= 0:
                return 1.0
        y = np.column_stack([a, b, a * a, b * b])
        m = y.mean(axis=0)
        f, grad, va, vb = _delta_and_grad(m)
        if va <= 0 or vb <= 0:
            return 1.0
        se = _block_se(y, m, grad, block)
        if se <= 0:
            return 1.0
        t_obs = f / se
        if not np.isfinite(t_obs):
            return 1.0

        rng = np.random.default_rng(seed)
        n_starts = -(-T // block)
        starts = rng.integers(0, T, size=(paths, n_starts))
        idx = ((starts[:, :, None] + np.arange(block)) % T)
        idx = idx.reshape(paths, n_starts * block)[:, :T]
        ys = y[idx]                                     # (paths, T, 4)
        ms = ys.mean(axis=1)
        fs, grads, vas, vbs = _delta_and_grad(ms)
        ses = _block_se(ys, ms, grads, block)
        ts = (fs - f) / ses
        flat = (np.ptp(ys[:, :, 0], axis=1) == 0) | (np.ptp(ys[:, :, 1], axis=1) == 0)
        bad = flat | (vas <= 0) | (vbs <= 0) | ~(ses > 0) | ~np.isfinite(ts)
        ts = np.where(bad, np.inf, ts)
    return float((1 + np.count_nonzero(ts >= t_obs)) / (paths + 1))
