"""Advisory chain lock for big writers on registry_log.jsonl.

Registry.append() already serialises individual appends (FileLock on
registry_log.jsonl.lock, pipeline/lock.py). This lock is the coordination
layer ABOVE that: a writer takes logs/chain.lock for a WRITE WINDOW (a
batch of chain appends), so the loop can defer instead of interleaving a
generation with another writer's batch, and manual sessions can hold it
while they work on the chain. Rules (spec 2026-08-27-pipeline-loop-design):

- Held for append windows, not whole runs; the scanner's cycle must never
  block on a gauntlet.
- The loop DEFERS when the lock is held; it never breaks a fresh lock.
- A stale lock is surfaced as WARN and only broken on a second sighting
  (the two-strike rule lives in loop.py, not here).
- Read paths never take this lock.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# A full gauntlet pass is now well under 1 h; 3 h marks a crashed holder.
STALE_AFTER_S = 3 * 3600


class ChainLockHeld(RuntimeError):
    """The lock is held (or fresh) and the requested action is refused."""


class ChainLock:
    def __init__(self, logs_dir: str | Path, holder: str, purpose: str,
                 stale_after_s: float = STALE_AFTER_S) -> None:
        self.path = Path(logs_dir) / "chain.lock"
        self.holder = holder
        self.purpose = purpose
        self.stale_after_s = stale_after_s
        self._acquired = False

    def info(self) -> dict | None:
        """Lock metadata, None when absent, holder='unreadable' on corrupt."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return {"holder": "unreadable", "pid": None, "ts_utc": None,
                    "purpose": None}

    def age_s(self) -> float | None:
        try:
            return time.time() - self.path.stat().st_mtime
        except OSError:
            return None

    def is_stale(self) -> bool:
        age = self.age_s()
        return age is not None and age > self.stale_after_s

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "holder": self.holder,
            "pid": os.getpid(),
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "purpose": self.purpose,
        })
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ChainLockHeld(f"chain.lock held: {self.info()}") from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        self._acquired = True

    def break_stale(self) -> None:
        """Remove a STALE lock. Refuses a fresh one. Two-strike rule is the
        caller's responsibility."""
        if not self.is_stale():
            raise ChainLockHeld("refusing to break a fresh chain.lock")
        self.path.unlink(missing_ok=True)

    def release(self) -> None:
        if self._acquired:
            self.path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self) -> "ChainLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
