"""Degraded ledgers (2026-10-08 per-asset isolation design s3.1).

A degraded-but-ran run exits 0: 25_PipelineLoop retries any non-zero exit
three times, and each retry is a metered cycle. What is degraded is recorded
here instead, one file per writer, and the Ops Sentinel's `research_degraded`
check turns it into WARN, then FAIL once an item is 3 days old. `since_utc` is
the clock: an item seen again keeps its original value, so the Sentinel can
tell a new problem from one nobody has fixed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOOP_LEDGER = "degraded_loop.json"
QUARANTINE_LEDGER = "degraded_quarantine.json"
_REQUIRED = {"source", "key", "since_utc"}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_items(path) -> tuple[list[dict], str | None]:
    """(items, note). A missing file is a first run: ([], None). An unreadable
    or malformed one is ([], note): every clock restarts, and the note says so
    in the ledger rather than letting a reset pass silently."""
    path = Path(path)
    if not path.exists():
        return [], None
    try:
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
        if not isinstance(items, list) or not all(
                isinstance(i, dict) and _REQUIRED <= set(i) for i in items):
            raise ValueError("items malformed")
        return items, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [], (f"previous ledger unreadable ({type(exc).__name__}: {exc}); "
                    f"every since_utc restarts now")


def merge(previous: list[dict], seen: list[dict], now: str, *,
          remove_unseen: bool) -> list[dict]:
    """New ledger items. An item seen again keeps its ORIGINAL since_utc and
    gets the new reason and last_seen_utc; a new one starts at `now`. An item
    not seen this run is dropped when `remove_unseen` (it recovered), kept
    untouched otherwise (the run could not re-check it). Sorted by (source,
    key) so a diff of two ledgers reads cleanly."""
    prev = {(i["source"], i["key"]): i for i in previous}
    out: dict[tuple[str, str], dict] = {}
    for s in seen:
        k = (s["source"], s["key"])
        since = prev[k]["since_utc"] if k in prev else now
        out[k] = {"source": s["source"], "key": s["key"], "reason": s["reason"],
                  "since_utc": since, "last_seen_utc": now}
    if not remove_unseen:
        for k, i in prev.items():
            out.setdefault(k, dict(i))
    return [out[k] for k in sorted(out)]


def write(path, writer: str, items: list[dict], now: str,
          note: str | None = None) -> None:
    """Atomic replace: the Sentinel must never read a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"writer": writer, "ts_utc": now, "items": items}
    if note:
        body["note"] = note
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record(path, writer: str, seen: list[dict], *,
           remove_unseen: bool) -> list[dict]:
    now = now_utc()
    previous, note = read_items(path)
    items = merge(previous, seen, now, remove_unseen=remove_unseen)
    write(path, writer, items, now, note)
    return items
