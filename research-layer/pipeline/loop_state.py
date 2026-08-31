"""Per-class loop state for the pipeline loop (logs/loop_state.json).

Four things live in this file, all keyed by asset class: the trigger
WATERMARK (below), the D6 sweep-rotation CURSOR (`rotation_cursor`), the D10
sibling QUEUE (`sibling_queue`), and the two ordering stamps
(`last_gen_ts_utc` / `last_park_ts_utc`). The queue is the only one of them
the COMPOSER writes -- see refresh_queues.

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
                      watermark_count: int, ts_utc: str,
                      routable_at_generation: int | None = None) -> None:
    """Record a completed generation's watermark.

    routable_at_generation is the ACCEPTED-only routable count as of a cycle
    in which the composer actually swept -- the baseline the
    no_new_accepted_cards guard compares against. Passed only on the
    cycle_complete path; None leaves any prior value untouched, which is what
    the guard's own early-exit path wants (it did not sweep, so it must not
    move the "last swept" mark).

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
    if routable_at_generation is not None:
        entry["routable_at_last_generation"] = routable_at_generation


def record_park(state: dict, asset_class: str, *, ts_utc: str) -> None:
    """Record that a cycle for this class was PARKED (budget) without doing
    the work.

    Deliberately does NOT touch `watermark`: no cards were triaged, no
    generation ran, and banking a watermark for undone work would silently
    skip that backlog forever. Only the ordering hint moves.

    Why it must move something: a park leaves the class exactly as
    pick_class found it -- still over threshold, still the oldest
    last_gen_ts_utc -- so the very next fire re-selects the SAME class, parks
    again, and every other over-threshold class starves behind it until the
    budget frees up. The park stamp rotates the class to the back of the
    queue without claiming its work is done.
    """
    entry = state["classes"].setdefault(asset_class, {"threshold": DEFAULT_THRESHOLD})
    entry["last_park_ts_utc"] = ts_utc


def _entry(state: dict, asset_class: str) -> dict:
    return state.setdefault("classes", {}).setdefault(
        asset_class, {"threshold": DEFAULT_THRESHOLD})


# ---------------- D6: sweep rotation ----------------
#
# A generation sweeps a ROTATING WINDOW of a class's active assets rather
# than all of them (design docs/2026-08-28-market-data-universe-design.md s5).
# The cursor lives here, per class, as `rotation_cursor`.
#
# THE THING THIS IS NOT: rotation is a cost SCHEDULE, never a selection
# mechanism. Every active cell is swept with equal frequency (pinned by
# test_rotation_sweeps_every_asset_with_equal_frequency) -- the window only
# decides WHEN, never WHETHER. N accounting is untouched by it: a cell not in
# this generation's window is not excluded, it is next.

def rotation_window(state: dict, asset_class: str, assets, size: int) -> list:
    """The next `size` assets this class should sweep, wrapping at the end.

    THE SMALL-SET RULE, pinned and load-bearing: when the active set has
    <= `size` assets the window is the WHOLE set, in declared order, and the
    cursor is neither created nor consulted. Rotating a set that already fits
    inside one window would change nothing about coverage and everything about
    which cells a given generation touches, so it is a no-op by construction.
    That is what keeps a class whose active set already fits byte-identical to
    its pre-rotation behaviour -- the loop compares this window against the
    full active list and passes no `--assets` subset at all when they match.

    Pure with respect to `state` on the small-set and empty paths (it does not
    even create the class entry); on the rotating path it only READS
    `rotation_cursor`. Advancing is advance_rotation's job, and only a
    COMPLETED generation may advance -- a parked or failed cycle must leave the
    window where it was, or the assets it never swept are skipped silently.
    """
    items = list(assets)
    if size <= 0 or len(items) <= size:
        return items
    cursor = state.get("classes", {}).get(asset_class, {}).get("rotation_cursor", 0)
    cursor %= len(items)
    return [items[(cursor + i) % len(items)] for i in range(size)]


def advance_rotation(state: dict, asset_class: str, n_assets: int, size: int) -> None:
    """Move the cursor one window on, after a COMPLETED generation.

    Same small-set rule as rotation_window, and for the same reason: if the
    window is the whole set the cursor has nothing to advance past, and
    writing one would put a `rotation_cursor` key into loop_state.json for a
    class that does not rotate.
    """
    if size <= 0 or n_assets <= size:
        return
    entry = _entry(state, asset_class)
    entry["rotation_cursor"] = (entry.get("rotation_cursor", 0) + size) % n_assets


# ---------------- D10: sweep queues ----------------
#
# family-openness-v1 (chained 2026-08-29): validate_family's "exceeds cap,
# rejected, not clipped" refusal is replaced by split-and-carry. The
# remainder of an over-cap family is QUEUED here, per class, and drained by
# the composer on subsequent cycles until empty. The invariant that makes
# that safe: no proposed variation is ever dropped without either a gauntlet
# verdict or a queue entry.
#
# WHO WRITES THIS: the composer, which runs as a SUBPROCESS of the loop and
# holds no shared memory with it. Both processes read and write the same
# logs/loop_state.json, so the loop must call refresh_queues() after a
# composer stage before its own end-of-cycle save(), or that save silently
# clobbers the queue the composer just wrote -- which would drop exactly the
# work the queue exists to preserve.

def queue_depth(state: dict, asset_class: str) -> int:
    return len(state.get("classes", {}).get(asset_class, {}).get("sibling_queue", []))


def queue_depths(state: dict) -> dict[str, int]:
    """Per-class depth for every class that has an entry -- status reporting."""
    return {cls: len(e.get("sibling_queue", []))
            for cls, e in state.get("classes", {}).items()}


def enqueue_specs(state: dict, asset_class: str, specs: list[dict]) -> None:
    """Append carried-over specs to the BACK of this class's queue (FIFO).

    FIFO, not LIFO: the queue is a fairness device, and a stack would let a
    steady trickle of new over-cap families starve the first family that ever
    overflowed."""
    if not specs:
        return
    entry = _entry(state, asset_class)
    entry.setdefault("sibling_queue", []).extend(specs)


def dequeue_specs(state: dict, asset_class: str, n: int) -> list[dict]:
    """Take up to n specs off the FRONT of the queue, removing them.

    A queue that drains to empty loses its key entirely rather than leaving
    `"sibling_queue": []` behind -- loop_state.json is Coen-editable and a
    dangling empty list reads as "something is parked here" when nothing is.
    """
    entry = state.get("classes", {}).get(asset_class)
    if not entry or not entry.get("sibling_queue"):
        return []
    queue = entry["sibling_queue"]
    taken, rest = queue[:n], queue[n:]
    if rest:
        entry["sibling_queue"] = rest
    else:
        entry.pop("sibling_queue", None)
    return taken


def refresh_queues(state: dict, path: str | Path) -> None:
    """Re-read every class's sibling_queue from disk into `state`.

    The composer subprocess owns the queue while it runs; the loop holds an
    older in-memory copy of the same file. Without this the loop's own save()
    at the end of a cycle would write back a state whose queues predate the
    composer -- resurrecting drained entries AND losing newly-queued ones.
    Only the queue key is taken: watermarks, park stamps and the rotation
    cursor stay the loop's own (the composer never writes them).

    A queue absent on disk REMOVES the in-memory one; that is the drained
    case, and treating absence as "no news" is how a fully-drained queue comes
    back from the dead.
    """
    disk = load(path)
    for cls in set(disk.get("classes", {})) | set(state.get("classes", {})):
        queued = disk.get("classes", {}).get(cls, {}).get("sibling_queue")
        entry = _entry(state, cls)
        if queued:
            entry["sibling_queue"] = queued
        else:
            entry.pop("sibling_queue", None)


def pick_class(state: dict, counts: dict[str, int]) -> str | None:
    """`counts` is the caller's TRIGGERABLE per-class count (accepted+pending)
    -- see the module BASIS WARNING. The threshold arithmetic below is
    unchanged from the original spec; only what the caller measures changed.

    Ordering among over-threshold classes: least-recently-ATTENDED first,
    where "attended" is the later of a completed generation and a budget
    park. A never-attended class still sorts first; ties break by
    cells.LIVE_CLASSES order.
    """
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

    def _attended(cls: str) -> str | None:
        """Latest time this class occupied a fire, generation or park. Both
        stamps are the loop's _now_utc isoformat, so max() on the strings is
        a real recency compare (see record_generation's format warning)."""
        entry = state["classes"].get(cls, {})
        stamps = [s for s in (entry.get("last_gen_ts_utc"),
                              entry.get("last_park_ts_utc")) if s]
        return max(stamps) if stamps else None

    # never-attended sorts before any timestamp; then oldest; then declared order
    return min(over, key=lambda c: (
        _attended(c) is not None,
        _attended(c) or "",
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
