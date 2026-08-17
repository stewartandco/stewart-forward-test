"""Block grammar v1 for the Composer.

BLOCK_TYPES maps (role, type) -> params schema. Each param declares its type
and `grid`: the closed set of values the Composer may use or sweep over.
Grammar changes happen ONLY by editing this file; every type is chained as a
block_type_registered entry on the Composer's first non-dry run, so the
grammar's growth is auditable. All types are computable from daily OHLCV.
"""
from __future__ import annotations

import copy

BLOCK_TYPES: dict[tuple[str, str], dict] = {
    ("entry", "ma_cross"): {
        "fast": {"type": "int", "grid": [5, 10, 20]},
        "slow": {"type": "int", "grid": [50, 100, 200]},
    },
    ("entry", "channel_breakout"): {
        "lookback": {"type": "int", "grid": [20, 55, 100]},
        "direction": {"type": "str", "grid": ["long", "both"]},
    },
    ("entry", "zscore_reversion"): {
        "lookback": {"type": "int", "grid": [20, 60, 90]},
        "z_entry": {"type": "float", "grid": [1.5, 2.0, 2.5]},
        "direction": {"type": "str", "grid": ["long", "both"]},
    },
    ("entry", "trend_scan"): {
        "max_lookback": {"type": "int", "grid": [60, 90, 120]},
        "t_min": {"type": "float", "grid": [2.0, 3.0]},
    },
    ("regime", "regime_ma"): {
        "ma_len": {"type": "int", "grid": [100, 200]},
    },
    ("filter", "vol_percentile"): {
        "lookback": {"type": "int", "grid": [90, 180]},
        "max_pctile": {"type": "float", "grid": [0.8, 0.9, 1.0]},
    },
    ("stop", "atr_stop"): {
        "atr_len": {"type": "int", "grid": [14]},
        "mult": {"type": "float", "grid": [1.5, 2.0, 3.0]},
    },
    ("stop", "pct_stop"): {
        "pct": {"type": "float", "grid": [0.05, 0.10, 0.15]},
    },
    ("target", "r_multiple"): {
        "r": {"type": "float", "grid": [1.0, 1.5, 2.0, 3.0]},
    },
    ("exit", "time_stop"): {
        "max_bars": {"type": "int", "grid": [10, 20, 40]},
    },
    ("risk", "fixed_fraction"): {
        "f": {"type": "float", "grid": [0.01, 0.02]},
    },
    ("risk", "vol_target"): {
        "ann_vol": {"type": "float", "grid": [0.20, 0.40]},
        "lookback": {"type": "int", "grid": [30]},
    },
    ("entry", "trend_scan_ds"): {
        "max_lookback": {"type": "int", "grid": [60, 90, 120]},
        "t_min": {"type": "float", "grid": [2.0, 3.0]},
        "direction": {"type": "str", "grid": ["long", "short", "both"]},
    },
    ("entry", "ma_cross_ds"): {
        "fast": {"type": "int", "grid": [5, 10, 20]},
        "slow": {"type": "int", "grid": [50, 100, 200]},
        "direction": {"type": "str", "grid": ["long", "short", "both"]},
    },
    ("regime", "regime_ma_short"): {
        "ma_len": {"type": "int", "grid": [100, 200]},
    },

    # --- protocol-v4 dense twins -------------------------------------------
    # Chained schemas are immutable (composer.preflight_block_types), so
    # plateau selection gets density through NEW types rather than by widening
    # the coarse ones. Only these may be swept; see composer.validate_family.
    ("entry", "channel_breakout_d"): {
        "lookback": {"type": "int", "grid": [20, 35, 55, 75, 100]},
        "direction": {"type": "str", "grid": ["long", "both"]},
    },
    ("entry", "ma_cross_d"): {
        "fast": {"type": "int", "grid": [5, 8, 13, 20, 34]},
        "slow": {"type": "int", "grid": [50, 80, 130, 200]},
        "direction": {"type": "str", "grid": ["long", "short", "both"]},
    },
    ("entry", "trend_scan_d"): {
        "max_lookback": {"type": "int", "grid": [60, 75, 90, 105, 120]},
        "t_min": {"type": "float", "grid": [2.0, 2.5, 3.0]},
        "direction": {"type": "str", "grid": ["long", "short", "both"]},
    },
    ("stop", "atr_stop_d"): {
        "atr_len": {"type": "int", "grid": [14]},
        "mult": {"type": "float", "grid": [1.5, 2.0, 2.5, 3.0, 3.5]},
    },
}

# Cross-param constraints, keyed like BLOCK_TYPES. Return list of error strings.
CONSTRAINTS = {
    ("entry", "ma_cross"):
        lambda p: ["ma_cross: fast must be < slow"] if p["fast"] >= p["slow"] else [],
    ("entry", "ma_cross_ds"):
        lambda p: ["ma_cross_ds: fast must be < slow"] if p["fast"] >= p["slow"] else [],
    ("entry", "ma_cross_d"):
        lambda p: ["ma_cross_d: fast must be < slow"] if p["fast"] >= p["slow"] else [],
}


def validate_block(role: str, btype: str, params: dict) -> list[str]:
    """Return error strings; empty list = valid."""
    key = (role, btype)
    if key not in BLOCK_TYPES:
        return [f"unknown block type {role}/{btype}"]
    schema = BLOCK_TYPES[key]
    errors = []
    for p in params:
        if p not in schema:
            errors.append(f"{role}/{btype}: unknown param {p!r}")
    for p, spec in schema.items():
        if p not in params:
            errors.append(f"{role}/{btype}: missing param {p!r}")
        elif params[p] not in spec["grid"]:
            errors.append(f"{role}/{btype}: param {p}={params[p]!r} not on grid {spec['grid']}")
    if not errors and key in CONSTRAINTS:
        errors.extend(CONSTRAINTS[key](params))
    return errors


def block_type_payload(role: str, btype: str) -> dict:
    """Payload for a block_type_registered registry entry."""
    return {"role": role, "type": btype,
            "params_schema": copy.deepcopy(BLOCK_TYPES[(role, btype)])}
