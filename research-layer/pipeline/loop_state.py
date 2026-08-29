"""Per-class watermark state for the pipeline loop (logs/loop_state.json).

The trigger rule (spec 2026-08-27-pipeline-loop-design, amended by Coen
2026-08-29): a class fires when triggerable_now - watermark >= threshold
(default 25), where "triggerable" is the count of class-routable cards a
cycle COULD act on -- accepted AND pending, never rejected. The watermark is
that same triggerable count recorded at the class's last completed
generation. pick_class returns ONE class per fire: the over-threshold class
whose last generation is oldest; a never-run class counts as oldest;
ties break by cells.LIVE_CLASSES order. Also holds the two-strike stale
chain.lock bookkeeping (the lock itself is pipeline/chainlock.py).

BASIS WARNING (the 2026-08-29 deadlock fix -- read before touching either
side of the comparison): this module does not compute the counts, it only
compares them, so it CANNOT enforce that the two sides share a basis. The
caller (pipeline/loop.py) must feed pick_class and record_generation the
SAME measure. Originally both were the accepted-only count, which deadlocked:
cards are only ever accepted by the D31 triage panel, which runs INSIDE a
cycle, after the trigger decision -- so the accepted count could not move
between fires and the loop could never start a generation on its own.
Feeding one side accepted+pending and the other accepted-only would be the
mirror defect: the loop would re-fire on every run against an unchanged
corpus.
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
        # Only a MISSING file gets the fresh-state default. Corrupt JSON
        # (json.JSONDecodeError) must raise loudly -- a silent reset here
        # would zero all watermarks and trigger a spurious generation on
        # every live class, spending gauntlet trials the loop never meant
        # to spend.
        return {"classes": {}, "stale_lock": None}


def save(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def record_generation(state: dict, asset_class: str, *, run_id: str,
                      watermark_count: int, ts_utc: str) -> None:
    """Record a completed generation's watermark.

    watermark_count MUST be measured on the SAME basis pick_class's counts
    are (the triggerable accepted+pending count -- see the module BASIS
    WARNING). Named for the slot it fills, not for a measure, precisely
    because that basis changed once already on 2026-08-29 and a name like
    "routable_count" outlived its meaning.

    ts_utc MUST be datetime.now(timezone.utc).isoformat() (the loop's
    _now_utc) -- pick_class orders entries by a lexical string compare of
    last_gen_ts_utc, not by parsing. The codebase has two live timestamp
    formats (strftime "...Z" in registry.py/scanstatus.py vs isoformat
    "+00:00" in chainlock.py); mixing them within one state file would let
    format, not age, decide which class fires next. Never mix stamp
    formats in loop_state.json.
    """
    entry = state["classes"].setdefault(asset_class, {"threshold": DEFAULT_THRESHOLD})
    entry["watermark"] = watermark_count
    entry["last_run_id"] = run_id
    entry["last_gen_ts_utc"] = ts_utc


def pick_class(state: dict, counts: dict[str, int]) -> str | None:
    """`counts` is the caller's TRIGGERABLE per-class count (accepted+pending)
    -- see the module BASIS WARNING. The comparison arithmetic below is
    unchanged from the original spec; only what the caller measures changed."""
    over: list[str] = []
    for cls in cells.LIVE_CLASSES:
        if cls not in counts:
            continue
        entry = state["classes"].get(cls, {})
        threshold = entry.get("threshold", DEFAULT_THRESHOLD)
        watermark = entry.get("watermark", 0)
        if counts[cls] - watermark >= threshold:
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
    recorded on a previous fire (second strike: the caller may break it).

    The sighting is recorded only in the in-memory dict passed in -- the
    caller MUST save() this state before exiting, or the strike is lost and
    the next fire starts back at strike one, deferring behind the stale
    lock forever instead of ever reaching the second strike that permits
    breaking it.
    """
    key = _lock_key(info)
    if state.get("stale_lock") == key:
        return True
    state["stale_lock"] = key
    return False


def clear_stale_lock(state: dict) -> None:
    state["stale_lock"] = None
