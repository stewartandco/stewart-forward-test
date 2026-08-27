"""Per-class watermark state for the pipeline loop (logs/loop_state.json).

The trigger rule (spec 2026-08-27-pipeline-loop-design): a class fires when
routable_accepted_now - watermark >= threshold (default 25). The watermark
is the routable-accepted count recorded at that class's last completed
generation. pick_class returns ONE class per fire: the over-threshold class
whose last generation is oldest; a never-run class counts as oldest;
ties break by cells.LIVE_CLASSES order. Also holds the two-strike stale
chain.lock bookkeeping (the lock itself is pipeline/chainlock.py).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import cells

DEFAULT_THRESHOLD = 25


def load(path: str | Path) -> dict:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"classes": {}, "stale_lock": None}


def save(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def record_generation(state: dict, asset_class: str, *, run_id: str,
                      routable_count: int, ts_utc: str) -> None:
    entry = state["classes"].setdefault(asset_class, {"threshold": DEFAULT_THRESHOLD})
    entry["watermark"] = routable_count
    entry["last_run_id"] = run_id
    entry["last_gen_ts_utc"] = ts_utc


def pick_class(state: dict, routable_counts: dict[str, int]) -> str | None:
    over: list[str] = []
    for cls in cells.LIVE_CLASSES:
        if cls not in routable_counts:
            continue
        entry = state["classes"].get(cls, {})
        threshold = entry.get("threshold", DEFAULT_THRESHOLD)
        watermark = entry.get("watermark", 0)
        if routable_counts[cls] - watermark >= threshold:
            over.append(cls)
    if not over:
        return None
    order = {c: i for i, c in enumerate(cells.LIVE_CLASSES)}
    # never-run sorts before any timestamp; then oldest timestamp; then declared order
    return min(over, key=lambda c: (
        state["classes"].get(c, {}).get("last_gen_ts_utc") is not None,
        state["classes"].get(c, {}).get("last_gen_ts_utc") or "",
        order[c],
    ))


def _lock_key(info: dict) -> str:
    return f"{info.get('holder')}|{info.get('pid')}|{info.get('ts_utc')}"


def record_stale_lock(state: dict, info: dict) -> bool:
    """Record a stale-lock sighting. True when the SAME lock was already
    recorded on a previous fire (second strike: the caller may break it)."""
    key = _lock_key(info)
    if state.get("stale_lock") == key:
        return True
    state["stale_lock"] = key
    return False


def clear_stale_lock(state: dict) -> None:
    state["stale_lock"] = None
