"""Gauntlet battery: five-gate validation of gauntlet-state strategies on
the 2024+ holdout, with pre-declared sibling selection.

Usage:
    python -m pipeline.gauntlet [--registry registry_log.jsonl]
        [--data-dir data] [--artifacts-dir artifacts]
        [--cutoff 2023-12-31] [--dry-run]

Real runs HARD-REFUSE unless a note starting with PROTOCOL is chained.
Gates and amendments per docs/2026-08-14-gauntlet-design.md.
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path

from .registry import Registry
from .engine import run_spec
from .stats import (moments, sharpe, percentile, psr, expected_max_sharpe,
                    bootstrap_paths)

PROTOCOL = "gauntlet-protocol-v1"
DECAY_MIN_PCT = -25.0
MC_PATHS = 2000
MC_P05_MIN = 1.0
RUIN_LEVEL = 0.5
P_RUIN_MAX = 0.05
DSR_MIN = 0.95
DEFAULT_CUTOFF = "2023-12-31"


def split_trades(trades: list[dict], cutoff: str) -> tuple[list, list]:
    is_t = [t for t in trades if t["entry_date"] <= cutoff]
    oos_t = [t for t in trades if t["entry_date"] > cutoff]
    return is_t, oos_t


def contributions(trades: list[dict]) -> list[float]:
    """Per-trade portfolio contribution: return_net x notional_frac."""
    return [t["return_net"] * t["notional_frac"] for t in trades]


def compound(contribs: list[float]) -> float:
    eq = 1.0
    for c in contribs:
        eq *= 1 + c
    return eq - 1


def evaluate_spec(is_trades: list[dict], oos_trades: list[dict],
                  stress_oos_trades: list[dict], daily_returns: list[float],
                  group_n: int, group_sr_var: float, seed: int):
    """Run the six checks in fixed order. Returns
    (passed, fail_reason|None, metrics, mc_summary)."""
    is_c = contributions(is_trades)
    oos_c = contributions(oos_trades)
    is_edge = sum(is_c) / len(is_c) if is_c else 0.0
    oos_edge = sum(oos_c) / len(oos_c) if oos_c else 0.0
    oos_net = compound(oos_c)
    decay = ((oos_edge - is_edge) / abs(is_edge) * 100
             if is_edge > 0 else None)

    mc = bootstrap_paths(is_c + oos_c, MC_PATHS, seed, RUIN_LEVEL)
    mc_p05 = percentile(mc["terminals"], 0.05)

    sr_hat = sharpe(daily_returns)
    _, _, skew, kurt = moments(daily_returns)
    sr_star = expected_max_sharpe(group_n, group_sr_var)
    dsr = psr(sr_hat, sr_star, len(daily_returns), skew, kurt)

    stress_net = compound(contributions(stress_oos_trades))

    metrics = {
        "is_edge_per_trade": is_edge,
        "oos_edge_per_trade": oos_edge,
        "edge_decay_pct": decay,
        "mc_p05_equity": mc_p05,
        "p_ruin": mc["p_ruin"],
        "deflated_sharpe": dsr,
        "sibling_group_n": group_n,
        "cost_stress_net_pnl": stress_net,
    }
    mc_summary = {"seed": seed, "paths": MC_PATHS,
                  "p05": mc_p05,
                  "p25": percentile(mc["terminals"], 0.25),
                  "p50": percentile(mc["terminals"], 0.50),
                  "p_ruin": mc["p_ruin"], "ruin_level": RUIN_LEVEL}

    if not oos_net > 0:
        return False, "oos_negative", metrics, mc_summary
    if decay is None or not decay > DECAY_MIN_PCT:
        return False, "edge_decay", metrics, mc_summary
    if not mc_p05 > MC_P05_MIN:
        return False, "mc_p05", metrics, mc_summary
    if not mc["p_ruin"] < P_RUIN_MAX:
        return False, "p_ruin", metrics, mc_summary
    if not dsr >= DSR_MIN:
        return False, "dsr", metrics, mc_summary
    if not stress_net > 0:
        return False, "cost_stress", metrics, mc_summary
    return True, None, metrics, mc_summary
