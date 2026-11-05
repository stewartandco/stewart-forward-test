"""Beats-hold (design 2026-11-05, section 3): buy and hold the strategy's own
asset over the OOS window, and the gauntlet comparison of the strategy's
Sharpe with the hold's. Pure: no I/O.

gauntlet_core imports this module (Task 3), so every gauntlet_core name is
imported INSIDE the functions to avoid a cycle.
"""
from __future__ import annotations


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
