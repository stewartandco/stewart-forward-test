"""Composer agent: compose candidate strategy specs from accepted research
cards and pre-register them into the chained registry, before any results.

Usage:
    python -m pipeline.composer --max-families 8 --sibling-cap 25 \
        [--dry-run] [--model claude-opus-5] [--registry registry_log.jsonl] \
        [--run-id 2026-08-06-manual]

Two-stage: one structured-output model call proposes idea families (blocks,
cited cards, sweep axes); deterministic code validates each family against
the block grammar, expands sweeps into sibling specs sharing a
sibling_group_id, and chains block_type_registered + strategy_registered
entries. Invalid families are dropped loudly, never silently.
"""
from __future__ import annotations

import re
import sys
import copy
import json
import hashlib
import argparse
import itertools
from math import prod
from pathlib import Path
from datetime import datetime, timezone

from .common import content_id
from .registry import Registry
from .blocks import BLOCK_TYPES, validate_block, block_type_payload

PIPELINE_VERSION = "g1.0.0"
DEFAULT_MODEL = "claude-opus-5"
SIBLING_CAP_DEFAULT = 25
ALLOWED_ASSETS = ("BTCUSD", "ETHUSD")
UNIVERSE_BASE = {"asset_class": "crypto", "timeframe": "1d", "session": "24x7"}
COST_MODEL = {"commission_per_side": 0.001, "slippage_ticks": 0.0005}
# ^ both are FRACTIONS of notional (10 bps + 5 bps); the field name
#   slippage_ticks is inherited from spec schema v1 — see design doc.


def validate_family(fam: dict, accepted_ids: set[str], sibling_cap: int) -> list[str]:
    """Return error strings; empty = family is expandable."""
    errors = []
    if not re.fullmatch(r"[a-z0-9_]+", fam.get("family", "")):
        errors.append(f"family name {fam.get('family')!r} must match [a-z0-9_]+")
    if not fam.get("card_ids"):
        errors.append("no cards cited")
    for cid in fam.get("card_ids", []):
        if cid not in accepted_ids:
            errors.append(f"card {cid} not accepted (or unknown)")
    if not fam.get("assets") or not set(fam["assets"]) <= set(ALLOWED_ASSETS):
        errors.append(f"assets {fam.get('assets')} must be a non-empty subset of {list(ALLOWED_ASSETS)}")

    blocks = fam.get("blocks", [])
    roles = [b.get("role") for b in blocks]
    if roles.count("entry") != 1:
        errors.append("exactly one entry block required")
    if "stop" not in roles:
        errors.append("at least one stop block required")
    if "risk" not in roles:
        errors.append("at least one risk block required")
    for b in blocks:
        errors.extend(validate_block(b.get("role"), b.get("type"), b.get("params", {})))

    seen_axes = set()
    for ax in fam.get("sweep", []):
        i = ax.get("block")
        if not isinstance(i, int) or not 0 <= i < len(blocks):
            errors.append(f"sweep axis references bad block index {i!r}")
            continue
        key = (blocks[i].get("role"), blocks[i].get("type"))
        schema = BLOCK_TYPES.get(key, {})
        p = ax.get("param")
        if p not in schema:
            errors.append(f"sweep axis param {p!r} not in {key[0]}/{key[1]}")
            continue
        if (i, p) in seen_axes:
            errors.append(f"duplicate sweep axis {p!r} on block {i}")
        seen_axes.add((i, p))
        values = ax.get("values", [])
        if not values or not set(values) <= set(schema[p]["grid"]):
            errors.append(f"sweep values for {p!r} not a subset of grid {schema[p]['grid']}")
        if len(set(values)) != len(values):
            errors.append(f"duplicate values in sweep axis {p!r}")

    if not errors:
        n = prod(len(ax["values"]) for ax in fam.get("sweep", [])) if fam.get("sweep") else 1
        if n > sibling_cap:
            errors.append(f"{n} siblings exceeds cap {sibling_cap} — rejected, not clipped")
    return errors


def _build_name(assets: list[str], family: str, blocks: list[dict]) -> str:
    bits = ["+".join(a.replace("USD", "") for a in assets),
            UNIVERSE_BASE["timeframe"], family]
    for b in blocks:
        for p, v in sorted(b["params"].items()):
            short = "".join(w[0] for w in p.split("_"))
            bits.append(f"{short}{v}")
    full = " ".join(bits)
    if len(full) <= 120:
        return full
    # keep names unique after truncation: suffix = hash of the full name
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    return full[:111] + "~" + digest


def _snap_to_grid(blocks: list[dict]) -> None:
    """Replace each param value with the equal grid element (in place), so
    2 and 2.0 hash identically. Membership was already validated."""
    for b in blocks:
        schema = BLOCK_TYPES.get((b["role"], b["type"]), {})
        for p, v in b["params"].items():
            grid = schema.get(p, {}).get("grid")
            if grid:
                b["params"][p] = next(g for g in grid if g == v)


def expand_family(fam: dict, run_id: str, model: str, created_utc: str) -> list[dict]:
    """Cartesian expansion of sweep axes, in declaration order. Deterministic:
    same family + run_id + timestamp -> same strategy_ids."""
    assets = sorted(fam["assets"])
    axes = fam.get("sweep", [])
    combos = itertools.product(*[ax["values"] for ax in axes]) if axes else [()]
    specs = []
    for combo in combos:
        blocks = copy.deepcopy(fam["blocks"])
        for ax, val in zip(axes, combo):
            blocks[ax["block"]]["params"][ax["param"]] = val
        _snap_to_grid(blocks)
        spec = {
            "strategy_id": None,
            "version": 1,
            "created_utc": created_utc,
            "name": _build_name(assets, fam["family"], blocks),
            "family": fam["family"],
            "universe": {"assets": assets, **UNIVERSE_BASE},
            "blocks": blocks,
            "provenance": {
                "card_ids": sorted(fam["card_ids"]),
                "parent_strategy_id": None,
                "sibling_group_id": f"{fam['family']}-{run_id}",
                "generation": 0,
            },
            "generator": {
                "agent": "composer",
                "model": model,
                "pipeline_version": PIPELINE_VERSION,
                "run_id": run_id,
            },
            "cost_model": dict(COST_MODEL),
        }
        spec["strategy_id"] = content_id(spec, "strategy_id")
        specs.append(spec)
    return specs
