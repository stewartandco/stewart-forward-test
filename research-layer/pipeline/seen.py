"""Append-only seen-item event store: dedup, funnel state, crash-safe resume.

One JSONL event per status change; on load, an item's latest status wins.
Statuses: seen, screen_keep, screen_kill, screen_keep_low, deferred_screen,
deferred_budget, deferred_lock, deferred_parked, paywalled, fetch_failed,
thin_content, extracted, extract_failed.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SeenStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._latest: dict[str, dict] = {}
        self._counts: dict[str, dict[str, int]] = {}
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        self._latest[event["item_id"]] = event
                        counts = self._counts.setdefault(event["item_id"], {})
                        counts[event["status"]] = counts.get(event["status"], 0) + 1

    def events_for(self, item_id: str) -> list[dict]:
        """Status history for one item (counts only, cheap): one synthetic
        entry per recorded occurrence, used for retry-cap decisions."""
        return [{"status": status} for status, n
                in self._counts.get(item_id, {}).items() for _ in range(n)]

    def record(self, item_id: str, source_id: str, status: str, *,
               title: str | None = None, link: str | None = None,
               reason: str | None = None, ts_utc: str | None = None) -> dict:
        prior = self._latest.get(item_id, {})
        event = {
            "item_id": item_id,
            "source_id": source_id,
            "status": status,
            "title": title if title is not None else prior.get("title"),
            "link": link if link is not None else prior.get("link"),
            "reason": reason,
            "ts_utc": ts_utc or _now_utc(),
            "first_seen_utc": prior.get("first_seen_utc") or ts_utc or _now_utc(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._latest[item_id] = event
        counts = self._counts.setdefault(item_id, {})
        counts[status] = counts.get(status, 0) + 1
        return event

    def is_seen(self, item_id: str) -> bool:
        return item_id in self._latest

    def status(self, item_id: str) -> str | None:
        event = self._latest.get(item_id)
        return event["status"] if event else None

    def items_with_status(self, status: str) -> dict[str, dict]:
        return {iid: e for iid, e in self._latest.items() if e["status"] == status}

    def count_since(self, hours: int = 24, status: str | None = None) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        n = 0
        for event in self._latest.values():
            if status is not None and event["status"] != status:
                continue
            first = datetime.strptime(event["first_seen_utc"],
                                      "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if first >= cutoff:
                n += 1
        return n
