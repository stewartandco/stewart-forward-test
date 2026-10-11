"""Run a scheduled task's command so that nothing it starts can outlive it.

Why (incident 2026-10-10/11): at a task's ExecutionTimeLimit Task Scheduler
terminates only the process it launched, the wrapper's cmd.exe (event 329,
action result 0x8007042B). Windows never kills children with their parent,
so the wrapper's python kept running for up to 90 minutes, held its instance
lock, and held the run log open: cmd opens a `>>` target without write
sharing, so every later fire's `>> "%LOG%"` failed, python never started,
and cmd left ERRORLEVEL unchanged, so those runs exited 0 having done
nothing.

What it does: the wrapper re-runs itself under this guard (`RL_TASKGUARD=1`
marks the inner copy). The guard puts the inner wrapper in a job object
with KILL_ON_JOB_CLOSE, waits, and ends the whole tree when
  - the wall passes (`--wall-s`, counted from the start of the cmd.exe Task
    Scheduler launched, i.e. the task's start): exit WALL_EXIT, which the
    Sentinel FAILs;
  - that cmd.exe dies (Task Scheduler's stop): exit PARENT_EXIT;
  - the guard itself is killed: the job's last handle closes and Windows
    kills the jobbed processes (KILL_ON_JOB_CLOSE).
Otherwise it returns the child's exit code unchanged, so the wrapper's
load-bearing exit code is the python step's, as before.

The tree is the job's processes PLUS every descendant of the child by
parent pid (review 2026-10-11): a packaged app breaks out of a job on
launch, and on this box bare `python` is the WindowsApps PythonManager alias
(cmd -> packaged launcher -> runtime, neither in the job). The wrappers
therefore call the interpreter by absolute path, and the guard still freezes,
lock-checks and kills any escaped descendant, and names it in the log.

Never mid-write: before a kill the guard suspends the whole tree and checks
that no lock file names a process in it. `--lock` (chain.lock, the mirror's
lock) is waited for up to `--grace-s`; `--hard-lock` (the chain's append
FileLock) and an in-tree git.exe holding `<git-dir>/index.lock` up to
`--hard-grace-s`, because a kill there leaves a torn chain tail every later
writer refuses (ChainMoved) or an index.lock that blocks all git. Between
checks the tree is resumed, so a holder can finish. Past the grace it kills
anyway and says `killed while busy`. After a kill (once the tree is gone,
so nothing still holds the log) it writes its log line, then removes a lock
only when that lock names a pid it killed (its holder is then provably
dead, the loop's dead-pid fast path): a `--release-lock` instance lock
always; a `--lock` (chain.lock) only when no killed pid also held a
`--hard-lock`, since then the chain's tail may be torn and chain.lock must
stay to keep writers off it until a human looks (round-2 review: only the
20:00 loop breaks a dead chain.lock, so a kept one stalls chain writes for
up to a day). A `--hard-lock` is never removed.

The child's environment carries RL_TASK_STARTED_UTC (the task's start, as
above), so a job's own deadline covers its startup.

If the job object cannot be made, the command still runs (fail open) under
the wall, the parent watch and the descendant sweep, with a WARNING line: a
guard failure must never stop the pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WALL_EXIT = 124          # the wall ended the tree (GNU timeout's convention)
PARENT_EXIT = 125        # the task's cmd.exe died, so the tree was ended
GUARD_ENV = "RL_TASKGUARD"
STARTED_ENV = "RL_TASK_STARTED_UTC"
QUIESCE_POLL_S = 0.25
DEFAULT_GRACE_S = 60.0
DEFAULT_HARD_GRACE_S = 300.0
# A lock file that exists but names nobody is a writer mid-creation for a
# moment; one that has been unreadable for longer is not ours to wait on.
UNREADABLE_FRESH_S = 5.0
TREE_GONE_WAIT_S = 30.0
# The task's start is read from the cmd.exe that launched the guard, but
# never earlier than this before the guard itself: a wrapper hand-run from a
# long-lived console would otherwise inherit that console's age and hit its
# wall at once.
MAX_STARTUP_S = 600.0

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    CREATE_SUSPENDED = 0x00000004
    SYNCHRONIZE = 0x00100000
    PROCESS_TERMINATE = 0x0001
    PROCESS_SUSPEND_RESUME = 0x0800
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectBasicProcessIdList = 3
    JobObjectExtendedLimitInformation = 9
    WAIT_OBJECT_0 = 0x0
    WAIT_TIMEOUT = 0x102
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _EXT_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             ctypes.c_void_p, wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.c_void_p]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD,
                                            ctypes.POINTER(wintypes.HANDLE),
                                            wintypes.BOOL, wintypes.DWORD]
    _k32.WaitForMultipleObjects.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.Process32FirstW.restype = wintypes.BOOL
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.Process32NextW.restype = wintypes.BOOL
    _ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_started(now: datetime) -> datetime:
    """When the task's clock started: STARTED_ENV as the guard stamped it,
    so a job's deadline covers its own startup (on 2026-10-10 python's
    imports alone took 3 min 19 s under memory pressure); else `now` (a hand
    run, a test). A stamp that does not parse, lies in the future or is over
    a day old is ignored. A naive stamp is UTC."""
    raw = os.environ.get(STARTED_ENV)
    if raw:
        try:
            t = datetime.fromisoformat(raw)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if timedelta(0) <= now - t <= timedelta(days=1):
                return t
        except ValueError:
            pass
    return now


def note(log: Path | None, msg: str) -> None:
    """One line to stderr and, if given, to the run log. The log is opened
    only for this line, so the guard never holds it while the wrapper's
    `>>` redirections need it. A log another process holds is retried
    briefly, then skipped: a note must never fail the guard."""
    line = f"taskguard: {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    if log is None:
        return
    for _ in range(50):
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return
        except OSError:
            time.sleep(0.1)


# ---- processes -----------------------------------------------------------

def _with_process(pid: int, access: int, fn):
    h = _k32.OpenProcess(access, False, pid)
    if not h:
        return None
    try:
        return fn(h)
    finally:
        _k32.CloseHandle(h)


def _suspend(pid: int) -> None:
    _with_process(pid, PROCESS_SUSPEND_RESUME, _ntdll.NtSuspendProcess)


def _resume(pid: int) -> None:
    _with_process(pid, PROCESS_SUSPEND_RESUME, _ntdll.NtResumeProcess)


def _terminate(pid: int, code: int) -> None:
    _with_process(pid, PROCESS_TERMINATE, lambda h: _k32.TerminateProcess(h, code))


def _alive(pid: int) -> bool:
    return bool(_with_process(
        pid, SYNCHRONIZE, lambda h: _k32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT))


def created_at(pid: int) -> float | None:
    """A process's creation time, epoch seconds; None if it cannot be read."""
    def q(h):
        ft = [wintypes.FILETIME() for _ in range(4)]
        if not _k32.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
            return None
        t100ns = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
        return t100ns / 1e7 - 11644473600.0
    return _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, q)


def image_path(pid: int) -> str:
    """Full image path, lowercased; "" if it cannot be read."""
    def q(h):
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value.lower()
        return ""
    return _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, q) or ""


def is_launcher(path: str) -> bool:
    """An interpreter LAUNCHER, not an interpreter: the packaged WindowsApps
    alias (it breaks out of jobs) or py.exe/pyw.exe."""
    path = path.lower()
    return "\\windowsapps\\" in path or os.path.basename(path) in ("py.exe", "pyw.exe")


def process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, lowercased image name), one toolhelp snapshot."""
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return {}
    out: dict[int, tuple[int, str]] = {}
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = _k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile.lower())
            ok = _k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        _k32.CloseHandle(snap)
    return out


def descendants(root: int, table: dict[int, tuple[int, str]], created=None,
                root_born: float | None = None) -> list[int]:
    """Every process whose parent chain leads to `root` (alive or not; its
    orphans keep its pid as their parent). A child must have been created
    after its parent: that drops the false links a reused pid would make."""
    created = created or created_at
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        if pid != ppid:
            kids.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    born = {root: root_born if root_born is not None else created(root)}
    while todo:
        p = todo.pop()
        for c in kids.get(p, []):
            if c in born:
                continue
            tc = created(c)
            if tc is None or born[p] is None or tc >= born[p] - 1e-3:
                born[c] = tc
                out.append(c)
                todo.append(c)
    return out


def watch_target(table: dict[int, tuple[int, str]], me: int | None = None,
                 path_of=None) -> int | None:
    """The process whose death means the task was stopped: the guard's
    nearest ancestor that is not an interpreter launcher. With bare `python`
    on this box the guard's parent is the WindowsApps alias, which outlives
    the cmd.exe Task Scheduler kills (review 2026-10-11)."""
    path_of = path_of or image_path
    me = os.getpid() if me is None else me
    pid, seen = table.get(me, (None, ""))[0], set()
    while pid and pid not in seen:
        seen.add(pid)
        entry = table.get(pid)
        if entry is None or not is_launcher(path_of(pid)):
            return pid
        pid = entry[0]
    return pid


def task_start(target: int | None, table: dict[int, tuple[int, str]],
               now: float, created=None) -> float:
    """Epoch seconds the task started: the creation of the cmd.exe that
    launched the guard (Task Scheduler's own clock), clamped to at most
    MAX_STARTUP_S before now; now when the parent is not cmd.exe."""
    created = created or created_at
    if not target or table.get(target, (0, ""))[1] != "cmd.exe":
        return now
    t = created(target)
    if t is None or t > now:
        return now
    return max(t, now - MAX_STARTUP_S)


class Job:
    """A KILL_ON_JOB_CLOSE job object. Its handle lives as long as the
    guard: if the guard is killed, Windows closes it and ends the jobbed
    processes."""

    def __init__(self) -> None:
        h = _k32.CreateJobObjectW(None, None)
        if not h:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        info = _EXT_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(h, JobObjectExtendedLimitInformation,
                                            ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            _k32.CloseHandle(h)
            raise OSError(err, "SetInformationJobObject failed")
        self.handle = h

    def assign(self, process_handle: int) -> None:
        if not _k32.AssignProcessToJobObject(self.handle, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def pids(self) -> list[int]:
        cap = 256
        while True:
            class _LIST(ctypes.Structure):
                _fields_ = [("Assigned", wintypes.DWORD), ("InList", wintypes.DWORD),
                            ("Ids", ctypes.c_size_t * cap)]
            lst = _LIST()
            ok = _k32.QueryInformationJobObject(self.handle, JobObjectBasicProcessIdList,
                                                ctypes.byref(lst), ctypes.sizeof(lst),
                                                None)
            if ok and lst.InList >= lst.Assigned:
                return [int(lst.Ids[i]) for i in range(lst.InList)]
            if not ok and ctypes.get_last_error() != 234:   # ERROR_MORE_DATA
                return []
            cap *= 4

    def terminate(self, code: int) -> None:
        _k32.TerminateJobObject(self.handle, code)


class Tree:
    """What the guard must end: the job's processes plus every descendant
    of the child by parent pid (a packaged launcher escapes the job)."""

    def __init__(self, job: Job | None, root: int) -> None:
        self.job, self.root = job, root
        self.root_born = created_at(root)

    def pids(self) -> tuple[list[int], list[int]]:
        """(all live pids in the tree, the ones outside the job)."""
        jobbed = set(self.job.pids()) if self.job is not None else set()
        table = process_table()
        desc = set(descendants(self.root, table, root_born=self.root_born))
        if self.root in table:
            desc.add(self.root)
        escaped = sorted(p for p in desc - jobbed if p in table)
        return sorted(jobbed | set(escaped)), escaped

    def terminate(self, code: int, pids: list[int]) -> None:
        if self.job is not None:
            self.job.terminate(code)
        for p in pids:                     # the escaped ones; jobbed are gone
            _terminate(p, code)


# ---- locks ---------------------------------------------------------------

UNREADABLE = -1


def lock_holder(path: Path, now: float | None = None) -> int | None:
    """The pid a lock file names: chain.lock is JSON {"pid": ...}, the
    append FileLock is "<pid> <time>". None when there is no lock file, or
    one that has named nobody for over UNREADABLE_FRESH_S (not a writer
    mid-creation, so not ours to wait on); UNREADABLE when it exists, names
    nobody yet and is fresh."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return UNREADABLE
    try:
        pid = json.loads(text).get("pid")
        if isinstance(pid, int):
            return pid
    except (ValueError, AttributeError):
        pass
    head = text.split(" ", 1)[0] if text else ""
    if head.isdigit():
        return int(head)
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except OSError:
        return None
    return UNREADABLE if age <= UNREADABLE_FRESH_S else None


def busy_reasons(pids: list[int], locks: list[Path], hard_locks: list[Path],
                 git_dir: Path | None, image_name=None) -> tuple[list[str], list[str]]:
    """Why the tree must not be killed right now: (soft reasons, hard
    reasons). Both empty = safe."""
    if image_name is None:
        table = process_table()
        image_name = lambda pid: table.get(pid, (0, ""))[1]   # noqa: E731
    live = set(pids)
    soft, hard = [], []
    for group, out in ((locks, soft), (hard_locks, hard)):
        for p in group:
            h = lock_holder(p)
            if h is None:
                continue
            if h == UNREADABLE:
                out.append(f"{p.name} exists but names no holder yet")
            elif h in live:
                out.append(f"{p.name} held by pid {h} in the tree")
    if git_dir is not None and (git_dir / "index.lock").exists():
        if any(image_name(pid) == "git.exe" for pid in pids):
            hard.append("git index.lock with a git process in the tree")
    return soft, hard


def quiesce_and_kill(tree, code: int, locks: list[Path], hard_locks: list[Path],
                     git_dir: Path | None, grace_s: float, hard_grace_s: float,
                     clock=time.monotonic, sleep=time.sleep):
    """Freeze the tree; kill it once nothing in it holds a lock. A soft lock
    is waited for up to grace_s, a hard one up to hard_grace_s; past that it
    kills anyway. Between checks the tree runs, so a holder can finish.
    Returns (reasons still busy at the kill, pids killed, escaped pids)."""
    t0 = clock()
    while True:
        frozen: list[int] = []
        escaped: list[int] = []
        for _ in range(3):                      # catch a child spawned mid-pass
            pids, esc = tree.pids()
            new = [p for p in pids if p not in frozen]
            if not new:
                break
            for p in new:
                _suspend(p)
            frozen += new
            escaped = sorted(set(escaped) | set(esc))
        soft, hard = busy_reasons(frozen, locks, hard_locks, git_dir)
        waited = clock() - t0
        if (not soft and not hard) or (not hard and waited >= grace_s) \
                or waited >= hard_grace_s:
            tree.terminate(code, escaped)
            return soft + hard, frozen, escaped
        for p in frozen:
            _resume(p)
        sleep(QUIESCE_POLL_S)


def release_if_killed(path: Path, killed: list[int]) -> bool:
    """Remove an instance lock whose holder the guard just killed. A lock
    naming anyone else (or nobody readable) is left alone."""
    holder = lock_holder(path)
    if holder is None or holder == UNREADABLE or holder not in set(killed):
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _wait_gone(tree, timeout_s: float) -> list[int]:
    end = time.monotonic() + timeout_s
    while True:
        left = [p for p in tree.pids()[0] if _alive(p)]
        if not left or time.monotonic() >= end:
            return left
        time.sleep(0.1)


# ---- main ----------------------------------------------------------------

def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wall-s", type=float, required=True,
                    help="end the tree this many seconds after the task started")
    ap.add_argument("--grace-s", type=float, default=DEFAULT_GRACE_S,
                    help="how long a kill may wait for a --lock to be released")
    ap.add_argument("--hard-grace-s", type=float, default=DEFAULT_HARD_GRACE_S,
                    help="how long a kill may wait for a --hard-lock or git's index.lock")
    ap.add_argument("--log", type=Path, default=None,
                    help="run log for the guard's own lines (opened per line)")
    ap.add_argument("--lock", type=Path, action="append", default=[],
                    help="a lock the tree should not be killed holding (soft)")
    ap.add_argument("--hard-lock", type=Path, action="append", default=[],
                    help="a lock whose holder must not be killed mid-write (hard)")
    ap.add_argument("--release-lock", type=Path, action="append", default=[],
                    help="an instance lock removed after a kill IF it names a pid "
                         "the guard just killed (its holder is then provably dead)")
    ap.add_argument("--git-dir", type=Path, default=None,
                    help="git dir whose index.lock an in-tree git must not be killed holding")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- then the command to run")
    a = ap.parse_args(argv)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("no command given (put it after --)")

    if os.name != "nt":                       # never scheduled there; tests only
        env = dict(os.environ, **{GUARD_ENV: "1", STARTED_ENV: _utc_now()})
        try:
            return subprocess.run(cmd, env=env, timeout=a.wall_s).returncode
        except subprocess.TimeoutExpired:
            note(a.log, f"wall {a.wall_s:.0f}s reached; child killed (exit {WALL_EXIT})")
            return WALL_EXIT

    table = process_table()
    target = watch_target(table)
    t_start = task_start(target, table, time.time())
    t_wall = time.monotonic() + max(0.0, a.wall_s - (time.time() - t_start))
    env = dict(os.environ)
    env[GUARD_ENV] = "1"
    env[STARTED_ENV] = datetime.fromtimestamp(t_start, timezone.utc).isoformat()

    job = None
    try:
        job = Job()
    except OSError as exc:
        note(a.log, f"WARNING no job object ({exc}); running with the descendant "
                    f"sweep only")
    proc = subprocess.Popen(cmd, env=env,
                            creationflags=CREATE_SUSPENDED if job else 0)
    if job is not None:
        try:
            job.assign(int(proc._handle))
        except OSError as exc:
            note(a.log, f"WARNING could not assign to the job ({exc}); running with "
                        f"the descendant sweep only")
            job = None
        finally:
            _ntdll.NtResumeProcess(int(proc._handle))
    tree = Tree(job, proc.pid)

    parent = _k32.OpenProcess(SYNCHRONIZE, False, target) if target else None
    if not parent:
        note(a.log, "WARNING cannot watch the task's cmd.exe; only the wall applies")
    handles = [int(proc._handle)] + ([parent] if parent else [])
    arr = (wintypes.HANDLE * len(handles))(*handles)
    while True:
        left_ms = max(0, int((t_wall - time.monotonic()) * 1000))
        r = _k32.WaitForMultipleObjects(len(handles), arr, False, left_ms)
        if r == WAIT_OBJECT_0:
            rc = proc.wait()
            if tree.pids()[0]:
                busy, killed, escaped = quiesce_and_kill(
                    tree, rc, a.lock, a.hard_lock, a.git_dir, a.grace_s, a.hard_grace_s)
                _wait_gone(tree, TREE_GONE_WAIT_S)
                note(a.log, f"the run left {len(killed)} process(es) running "
                            f"(pids {killed}); ended them" + _tail(busy, escaped))
                release_after_kill(a, killed)
            return rc
        if r == WAIT_OBJECT_0 + 1:
            why, code = "parent process exited (the task's cmd.exe: task stopped?)", PARENT_EXIT
        elif r == WAIT_TIMEOUT:
            if time.monotonic() < t_wall:
                continue
            why, code = f"wall {a.wall_s:.0f}s reached", WALL_EXIT
        else:
            note(a.log, f"WARNING wait failed ({ctypes.get_last_error()}); "
                        f"waiting on the child alone")
            return proc.wait()
        break

    busy, killed, escaped = quiesce_and_kill(
        tree, code, a.lock, a.hard_lock, a.git_dir, a.grace_s, a.hard_grace_s)
    survivors = _wait_gone(tree, TREE_GONE_WAIT_S)    # handles released first
    extra = f"; STILL ALIVE: {survivors}" if survivors else ""
    note(a.log, f"{why}; ended the task's process tree (exit {code})"
                + _tail(busy, escaped) + extra)
    release_after_kill(a, killed)
    return code


def release_after_kill(a, killed: list[int]) -> None:
    """Remove the locks the killed tree held and can no longer release:
    instance locks always, soft locks (chain.lock) only while no killed pid
    holds a hard lock (an interrupted append: the tail may be torn, so
    chain.lock stays and says so). Hard locks are never removed."""
    dead = set(killed)
    torn = [h.name for h in a.hard_lock if lock_holder(h) in dead]
    for path in a.release_lock:
        if release_if_killed(path, killed):
            note(a.log, f"removed {path.name}: its holder was in the killed tree")
    for path in a.lock:
        if lock_holder(path) not in dead:
            continue
        if torn:
            note(a.log, f"LEFT {path.name} in place: the killed tree also held "
                        f"{', '.join(torn)}, so a write may be torn; check the chain "
                        f"(verify_registry.py) before removing it")
        elif release_if_killed(path, killed):
            note(a.log, f"removed {path.name}: its holder was in the killed tree "
                        f"and held no write lock")


def _tail(busy: list[str], escaped: list[int]) -> str:
    out = ""
    if escaped:
        out += f"; {len(escaped)} had escaped the job (pids {escaped})"
    if busy:
        out += f"; killed while busy: {'; '.join(busy)}"
    return out


if __name__ == "__main__":
    sys.exit(run())
