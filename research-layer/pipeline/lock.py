"""Cross-process file lock for chain writers.

The registry chain is append-only with each entry hashing its predecessor, so
two processes that read the same head and both append will FORK the chain
(hit twice on 2026-08-14: scanner vs gauntlet, scanner vs triage). Every
chain append must hold this lock across the head-read + write critical
section.

Implementation: portable O_CREAT|O_EXCL lockfile (<target>.lock) with polling,
timeout, and stale-lock breaking (a crashed holder leaves a lockfile; appends
take milliseconds, so anything older than stale_after is dead).
"""
from __future__ import annotations

import os
import time
from pathlib import Path


class FileLockTimeout(TimeoutError):
    pass


class FileLock:
    def __init__(self, target: str | Path, timeout: float = 10.0,
                 stale_after: float = 60.0, poll: float = 0.05):
        self.lock_path = Path(str(target) + ".lock")
        self.timeout = timeout
        self.stale_after = stale_after
        self.poll = poll
        self._held = False

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        last_error: OSError | None = None
        while True:
            try:
                fd = os.open(self.lock_path,
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
                os.close(fd)
                self._held = True
                return
            except FileExistsError as e:
                last_error = e
            except PermissionError as e:
                # Windows: a lockfile another process is mid-unlink sits in
                # delete-pending state, and CreateFile on it (or on a file an
                # AV/indexer briefly holds) fails ERROR_ACCESS_DENIED ->
                # PermissionError, not FileExistsError. Same meaning here:
                # someone else has the lock right now.
                last_error = e
            try:
                age = time.time() - os.path.getmtime(self.lock_path)
                if age > self.stale_after:
                    os.unlink(self.lock_path)  # break dead holder's lock
                    continue
            except OSError:
                pass  # lock vanished/unstatable between checks; poll again
            if time.monotonic() >= deadline:
                raise FileLockTimeout(
                    f"could not acquire {self.lock_path} within "
                    f"{self.timeout}s (held by another writer?) "
                    f"[last error: {last_error!r}]")
            time.sleep(self.poll)

    def release(self) -> None:
        if self._held:
            self._held = False
            try:
                os.unlink(self.lock_path)
            except OSError:
                pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
