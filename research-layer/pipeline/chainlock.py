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
  (the two-strike bookkeeping lives in loop_state.py, consumed by loop.py).
- Read paths never take this lock.
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A full gauntlet pass is now well under 1 h; 3 h marks a crashed holder.
STALE_AFTER_S = 3 * 3600


def process_start_time(pid: int) -> str | None:
    """Creation time of `pid` as ISO-8601 UTC, None when it cannot be read.
    A pid alone is not an identity on Windows: pids are reused within
    minutes. (pid, start time) is."""
    if os.name != "nt":
        try:
            import psutil                        # optional on POSIX
            return datetime.fromtimestamp(psutil.Process(pid).create_time(),
                                          timezone.utc).isoformat()
        except Exception:
            return None
    try:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # win64: HANDLE is pointer-sized; the default int conversion would
        # truncate it, so both calls declare their types.
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        ft_ptr = ctypes.POINTER(ctypes.c_ulonglong)
        kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p, ft_ptr, ft_ptr,
                                             ft_ptr, ft_ptr]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = wintypes.BOOL
        h = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            ft = [ctypes.c_ulonglong() for _ in range(4)]
            if not kernel32.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
                return None
            ticks = ft[0].value                  # 100 ns since 1601-01-01 UTC
            # Integer microseconds: the same process always yields the same
            # string (no float round-off between two reads).
            epoch_us = ticks // 10 - 11_644_473_600 * 1_000_000
            return (datetime(1970, 1, 1, tzinfo=timezone.utc)
                    + timedelta(microseconds=epoch_us)).isoformat()
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return None


def _same_process(info: dict, pid: int) -> bool:
    """A lock written before 2026-09-30 carries no start time: keep the pid
    rule for it (conservative). One that does must match the live process."""
    recorded = info.get("pid_start_utc")
    if not recorded:
        return True
    live = process_start_time(pid)
    return live is None or live == recorded


class ChainLockHeld(RuntimeError):
    """The lock is held (or fresh) and the requested action is refused."""


class ChainLock:
    def __init__(self, logs_dir: str | Path, holder: str, purpose: str,
                 stale_after_s: float = STALE_AFTER_S,
                 name: str = "chain.lock") -> None:
        """`name` generalises the lockfile's basename (default "chain.lock",
        every existing caller's exact prior behaviour). loop.py uses a
        second, distinctly-named instance ("loop.lock") to guard a whole
        `loop.run()` cycle against a concurrent second instance -- a
        separate concern from chain-write coordination, so it needs its own
        file rather than contending with chain.lock's holders."""
        self.path = Path(logs_dir) / name
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

    def holder_alive(self) -> bool:
        """Whether the recorded holder pid is still a running process.

        Used by loop.py's instance guard to break an orphaned loop.lock (a
        hard kill, reboot, or acquire-then-crash) without waiting on a
        two-strike sighting -- a dead pid is decisive, not merely
        suspicious. Conservative wherever liveness cannot be determined:
        unknown means ALIVE, so a live holder is never mistaken for dead.

        On Windows, NEVER use os.kill(pid, 0) for this: os.kill there, with
        any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT, unconditionally
        TERMINATES the target process via TerminateProcess -- it is not a
        liveness probe on that platform, unlike POSIX's signal 0 (which IS
        the correct probe there, and is what the POSIX branch below uses).
        """
        info = self.info()
        if info is None:
            return False
        pid = info.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return _same_process(info, pid)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True             # exists, owned by someone else -- alive
            except Exception:
                return True             # platform check failed -- conservative
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = ctypes.c_void_p   # win64 HANDLE hygiene
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                          False, pid)
            if not handle:
                # NULL is ALSO returned for ERROR_ACCESS_DENIED (5) -- the
                # scheduled-task-vs-supervised-shell cross-account case this
                # guard exists for, where the process very much EXISTS. Only
                # ERROR_INVALID_PARAMETER (87) means the pid is truly gone;
                # anything else (access denied included) means alive.
                return ctypes.get_last_error() != 87
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True         # query itself failed -- conservative
                if exit_code.value != STILL_ACTIVE:
                    return False
                return _same_process(info, pid)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True                 # platform check failed -- conservative

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "holder": self.holder,
            "pid": os.getpid(),
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "purpose": self.purpose,
            "pid_start_utc": process_start_time(os.getpid()),
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
        try:
            self.path.unlink()
        except OSError:
            pass

    def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        try:
            self.path.unlink()
        except OSError:
            pass  # already gone, or transiently held open by a reader (Windows)

    def __enter__(self) -> "ChainLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
