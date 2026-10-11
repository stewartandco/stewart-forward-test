# research-layer/pipeline/test_taskguard.py
"""The task guard (incident 2026-10-10/11): a task stopped at its scheduler
time limit must not leave its python running. Real processes throughout:
the property is about Windows process trees, which no fake can show."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from . import taskguard as tg

LAYER = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows job objects")


def _alive(pid: int) -> bool:
    h = tg._k32.OpenProcess(tg.SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        return tg._k32.WaitForSingleObject(h, 0) == tg.WAIT_TIMEOUT
    finally:
        tg._k32.CloseHandle(h)


def _wait_dead(pid: int, timeout_s: float = 15.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def _wait_file(path: Path, timeout_s: float = 30.0) -> str:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def _kill(pid: int) -> None:
    h = tg._k32.OpenProcess(tg.PROCESS_TERMINATE, False, pid)
    if h:
        tg._k32.TerminateProcess(h, 1)
        tg._k32.CloseHandle(h)


def _tree_script(tmp: Path) -> str:
    """A child that starts a grandchild (the stand-in for the wrapper's
    python under cmd.exe), records both pids, then sleeps."""
    gpid = str(tmp / "grandchild.pid")
    cpid = str(tmp / "child.pid")
    grandchild = (f"import os, time; open({gpid!r}, 'w').write(str(os.getpid())); "
                  "time.sleep(120)")
    return (
        "import subprocess, sys, time, os\n"
        f"g = subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
        f"open({cpid!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n")


def _guard_argv(*args) -> list[str]:
    return [sys.executable, "-m", "pipeline.taskguard", *args]


def test_child_exit_code_and_env_pass_through(tmp_path):
    out = tmp_path / "env.json"
    code = ("import json, os, sys; json.dump({k: os.environ.get(k) for k in "
            f"('{tg.GUARD_ENV}', '{tg.STARTED_ENV}')}}, open(r'{out}', 'w')); sys.exit(7)")
    r = subprocess.run(_guard_argv("--wall-s", "60", "--", sys.executable, "-c", code),
                       cwd=LAYER, timeout=60)
    assert r.returncode == 7
    env = json.loads(out.read_text())
    assert env[tg.GUARD_ENV] == "1"
    assert env[tg.STARTED_ENV]          # ISO start stamp for the job's deadline


def test_wall_kills_the_whole_tree_and_returns_wall_exit(tmp_path):
    log = tmp_path / "run.log"
    p = subprocess.Popen(_guard_argv("--wall-s", "3", "--grace-s", "5", "--log", str(log),
                                     "--", sys.executable, "-c", _tree_script(tmp_path)),
                         cwd=LAYER)
    g = int(_wait_file(tmp_path / "grandchild.pid"))
    c = int(_wait_file(tmp_path / "child.pid"))
    assert p.wait(timeout=60) == tg.WALL_EXIT
    assert _wait_dead(c) and _wait_dead(g), "the wall left part of the tree running"
    text = log.read_text()
    assert "wall 3s reached" in text
    assert "escaped" not in text, "a real interpreter must stay inside the job"


def test_parent_death_kills_the_tree(tmp_path):
    """Exactly the scheduler's stop: TerminateProcess on the guard's PARENT
    (the wrapper's cmd.exe). The guard and its whole tree must follow."""
    log = tmp_path / "run.log"
    gpid = tmp_path / "guard.pid"
    parent_code = (
        "import subprocess, sys\n"
        f"p = subprocess.Popen({_guard_argv('--wall-s', '120', '--log', str(log), '--', sys.executable, '-c', _tree_script(tmp_path))!r})\n"
        f"open(r'{gpid}', 'w').write(str(p.pid))\n"
        "p.wait()\n")
    parent = subprocess.Popen([sys.executable, "-c", parent_code], cwd=LAYER)
    g = int(_wait_file(tmp_path / "grandchild.pid"))
    c = int(_wait_file(tmp_path / "child.pid"))
    guard = int(_wait_file(gpid))
    parent.kill()                                  # TerminateProcess, as event 329
    parent.wait(timeout=10)
    assert _wait_dead(guard), "guard outlived its parent"
    assert _wait_dead(c) and _wait_dead(g), "the tree outlived the stopped task"
    assert "parent process exited" in log.read_text()


def test_control_without_the_guard_the_grandchild_survives(tmp_path):
    """The bug itself, kept as a control: killing the parent alone leaves
    its child running. If this ever fails, the tests above prove nothing."""
    child = subprocess.Popen([sys.executable, "-c", _tree_script(tmp_path)])
    g = int(_wait_file(tmp_path / "grandchild.pid"))
    try:
        child.kill()
        child.wait(timeout=10)
        time.sleep(1.0)
        assert _alive(g), "Windows killed the orphan: the incident premise changed"
    finally:
        _kill(g)


def test_killed_guard_takes_the_tree_with_it(tmp_path):
    """KILL_ON_JOB_CLOSE: a hard kill of the guard itself closes the job."""
    p = subprocess.Popen(_guard_argv("--wall-s", "120", "--", sys.executable, "-c",
                                     _tree_script(tmp_path)), cwd=LAYER)
    g = int(_wait_file(tmp_path / "grandchild.pid"))
    c = int(_wait_file(tmp_path / "child.pid"))
    p.kill()
    p.wait(timeout=10)
    assert _wait_dead(c) and _wait_dead(g)


def test_kill_waits_for_a_held_write_lock_then_releases_instance_lock(tmp_path):
    """The wall passes while the child holds a chain-style lock: the guard
    must wait for the release (the 'released' marker is written only after
    the unlink), then kill, then remove the instance lock it named."""
    lock = tmp_path / "chain.lock"
    inst = tmp_path / "worker.lock"
    marker = tmp_path / "released"
    code = (
        "import json, os, time\n"
        f"open(r'{inst}', 'w').write(json.dumps({{'pid': os.getpid()}}))\n"
        f"open(r'{lock}', 'w').write(json.dumps({{'pid': os.getpid()}}))\n"
        "time.sleep(4)\n"
        f"os.unlink(r'{lock}')\n"
        f"open(r'{marker}', 'w').write('x')\n"
        "time.sleep(120)\n")
    t0 = time.monotonic()
    r = subprocess.run(_guard_argv("--wall-s", "1", "--grace-s", "30",
                                   "--lock", str(lock), "--release-lock", str(inst),
                                   "--log", str(tmp_path / "run.log"),
                                   "--", sys.executable, "-c", code),
                       cwd=LAYER, timeout=90)
    assert r.returncode == tg.WALL_EXIT
    assert marker.exists(), "killed while the write lock was held"
    assert time.monotonic() - t0 >= 4
    assert not inst.exists(), "instance lock of the killed holder left behind"
    assert not lock.exists()


def test_grace_expiry_kills_anyway_and_releases_a_dead_holders_chain_lock(tmp_path):
    """chain.lock held past the soft grace: killed anyway, and since the
    killed holder held no append lock, its chain.lock is removed (only the
    20:00 loop would otherwise break it: up to a day of stalled writers)."""
    lock = tmp_path / "chain.lock"
    append = tmp_path / "registry_log.jsonl.lock"
    code = (
        "import json, os, time\n"
        f"open({str(lock)!r}, 'w').write(json.dumps({{'pid': os.getpid()}}))\n"
        "time.sleep(120)\n")
    log = tmp_path / "run.log"
    r = subprocess.run(_guard_argv("--wall-s", "1", "--grace-s", "2", "--lock", str(lock),
                                   "--hard-lock", str(append), "--log", str(log),
                                   "--", sys.executable, "-c", code),
                       cwd=LAYER, timeout=60)
    assert r.returncode == tg.WALL_EXIT
    text = log.read_text()
    assert "killed while busy" in text and "removed chain.lock" in text
    assert not lock.exists()


def test_a_killed_append_keeps_chain_lock_and_its_own_lock(tmp_path):
    """Killed holding the append lock too (hard grace ran out): the tail may
    be torn, so chain.lock stays to keep writers off, and the append lock
    is never removed by the guard."""
    lock = tmp_path / "chain.lock"
    append = tmp_path / "registry_log.jsonl.lock"
    code = (
        "import json, os, time\n"
        f"open({str(lock)!r}, 'w').write(json.dumps({{'pid': os.getpid()}}))\n"
        f"open({str(append)!r}, 'w').write(f'{{os.getpid()}} {{time.time()}}')\n"
        "time.sleep(120)\n")
    log = tmp_path / "run.log"
    r = subprocess.run(_guard_argv("--wall-s", "1", "--grace-s", "1", "--hard-grace-s", "2",
                                   "--lock", str(lock), "--hard-lock", str(append),
                                   "--log", str(log), "--", sys.executable, "-c", code),
                       cwd=LAYER, timeout=60)
    assert r.returncode == tg.WALL_EXIT
    assert lock.exists() and append.exists()
    assert "LEFT chain.lock in place" in log.read_text()


# ---- pure parts --------------------------------------------------------

def test_lock_holder_formats(tmp_path):
    p = tmp_path / "l"
    assert tg.lock_holder(p) is None
    p.write_text(json.dumps({"holder": "x", "pid": 4321}))
    assert tg.lock_holder(p) == 4321
    p.write_text("1234 1760000000.5")            # FileLock's "<pid> <time>"
    assert tg.lock_holder(p) == 1234
    p.write_text("")
    assert tg.lock_holder(p) == tg.UNREADABLE     # fresh: a writer mid-creation
    old = p.stat().st_mtime + tg.UNREADABLE_FRESH_S + 1
    assert tg.lock_holder(p, now=old) is None     # stale garbage: not ours to wait on


def test_busy_reasons_split_soft_and_hard(tmp_path):
    chain = tmp_path / "chain.lock"
    chain.write_text(json.dumps({"pid": 11}))
    append = tmp_path / "registry_log.jsonl.lock"
    append.write_text("12 1760000000.0")
    soft, hard = tg.busy_reasons([11, 12], [chain], [append], None, image_name=lambda p: "")
    assert soft and hard
    assert tg.busy_reasons([13], [chain], [append], None,
                           image_name=lambda p: "") == ([], [])   # someone else's locks
    git = tmp_path / ".git"
    git.mkdir()
    (git / "index.lock").write_text("")
    names = {12: "git.exe", 13: "python.exe"}.get
    assert tg.busy_reasons([12], [], [], git, image_name=names)[1]
    assert tg.busy_reasons([13], [], [], git, image_name=names) == ([], [])


class _FakeTree:
    def __init__(self, pids, escaped=()):
        self._pids, self._esc, self.killed = list(pids), list(escaped), None

    def pids(self):
        return list(self._pids), list(self._esc)

    def terminate(self, code, pids):
        self.killed = (code, list(pids))


def _no_suspend(monkeypatch):
    monkeypatch.setattr(tg, "_suspend", lambda pid: None)
    monkeypatch.setattr(tg, "_resume", lambda pid: None)


def test_quiesce_kills_when_free_or_at_the_soft_grace(tmp_path, monkeypatch):
    _no_suspend(monkeypatch)
    lock = tmp_path / "chain.lock"
    lock.write_text(json.dumps({"pid": 5}))
    t = [0.0]

    def sleep(s):
        t[0] += s
        if t[0] >= 1.0 and lock.exists():
            lock.unlink()                         # the holder finishes
    tree = _FakeTree([5, 6], escaped=[6])
    busy, killed, escaped = tg.quiesce_and_kill(tree, 124, [lock], [], None, 30.0, 300.0,
                                                clock=lambda: t[0], sleep=sleep)
    assert tree.killed == (124, [6]) and busy == [] and killed == [5, 6] and escaped == [6]
    assert 1.0 <= t[0] < 30.0

    lock.write_text(json.dumps({"pid": 5}))
    t[0] = 0.0
    tree = _FakeTree([5])
    busy, _, _ = tg.quiesce_and_kill(tree, 124, [lock], [], None, 2.0, 300.0,
                                     clock=lambda: t[0],
                                     sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert tree.killed and busy and 2.0 <= t[0] < 3.0      # soft grace ran out


def test_a_hard_lock_outlasts_the_soft_grace(tmp_path, monkeypatch):
    """The chain's append lock: a kill there tears the chain's tail, so it
    is waited for up to the hard grace, not the soft one."""
    _no_suspend(monkeypatch)
    append = tmp_path / "registry_log.jsonl.lock"
    append.write_text("5 1760000000.0")
    t = [0.0]

    def sleep(s):
        t[0] += s
        if t[0] >= 40.0 and append.exists():
            append.unlink()
    tree = _FakeTree([5])
    busy, _, _ = tg.quiesce_and_kill(tree, 124, [], [append], None, 2.0, 300.0,
                                     clock=lambda: t[0], sleep=sleep)
    assert tree.killed and busy == [] and 40.0 <= t[0] < 41.0
    append.write_text("5 1760000000.0")
    t[0] = 0.0
    tree = _FakeTree([5])
    busy, _, _ = tg.quiesce_and_kill(tree, 124, [], [append], None, 2.0, 10.0,
                                     clock=lambda: t[0],
                                     sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert tree.killed and busy and 10.0 <= t[0] < 11.0    # hard grace ran out


def test_release_if_killed_only_touches_the_killed_holder(tmp_path):
    p = tmp_path / "worker.lock"
    p.write_text(json.dumps({"pid": 99}))
    assert not tg.release_if_killed(p, [1, 2])
    assert p.exists()
    assert tg.release_if_killed(p, [99])
    assert not p.exists()


ALIAS = r"c:\program files\windowsapps\pythonsoftwarefoundation.pythonmanager_25.2_x64\python.exe"


def test_watch_target_skips_an_interpreter_launcher():
    table = {100: (50, "python.exe"), 50: (40, "python.exe"), 40: (1, "cmd.exe")}
    paths = {50: ALIAS, 40: r"c:\windows\system32\cmd.exe"}.get
    assert tg.watch_target(table, me=100, path_of=paths) == 40
    real = {50: r"c:\users\coen\appdata\local\python\bin\python.exe"}.get
    assert tg.watch_target(table, me=100, path_of=real) == 50    # a real python parent
    assert tg.is_launcher(r"C:\Windows\py.exe") and not tg.is_launcher(r"C:\x\python.exe")


def test_task_start_is_the_cmds_creation_clamped():
    table = {40: (1, "cmd.exe"), 41: (1, "python.exe")}
    now = 10_000.0
    assert tg.task_start(40, table, now, created=lambda p: now - 30) == now - 30
    assert tg.task_start(40, table, now, created=lambda p: now - 99_999) == \
        now - tg.MAX_STARTUP_S                     # a long-lived console
    assert tg.task_start(41, table, now, created=lambda p: now - 30) == now   # not cmd
    assert tg.task_start(40, table, now, created=lambda p: None) == now
    assert tg.task_start(None, table, now) == now


def test_descendants_follow_parent_pids_and_drop_reused_ones():
    table = {10: (1, "cmd.exe"), 11: (10, "python.exe"), 12: (11, "python.exe"),
             13: (10, "old.exe"), 20: (2, "other.exe")}
    born = {10: 100.0, 11: 101.0, 12: 102.0, 13: 50.0, 20: 1.0}.get
    assert sorted(tg.descendants(10, table, created=born)) == [11, 12]   # 13 predates 10
    # the root itself is gone: its orphans still name it as their parent
    gone = {k: v for k, v in table.items() if k != 10}
    assert sorted(tg.descendants(10, gone, created=born, root_born=100.0)) == [11, 12]


# ---- review 2026-10-11: real processes ----------------------------------

ALIAS_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "python.exe"


@pytest.mark.skipif(not ALIAS_EXE.exists(), reason="no WindowsApps python alias here")
def test_a_python_that_escapes_the_job_is_still_ended(tmp_path):
    """The WindowsApps alias is a packaged app: it and the runtime it starts
    break out of the job. The descendant sweep must end them anyway and say
    they escaped."""
    pid_file = tmp_path / "sleeper.pid"
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import os, sys, time\n"
                       "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                       "time.sleep(120)\n")
    bat = tmp_path / "alias.bat"
    bat.write_text(f'@echo off\r\n"{ALIAS_EXE}" "{sleeper}" "{pid_file}"\r\n', encoding="ascii")
    log = tmp_path / "run.log"
    p = subprocess.Popen(_guard_argv("--wall-s", "8", "--grace-s", "2", "--log", str(log),
                                     "--", "cmd", "/d", "/c", str(bat)), cwd=LAYER)
    pid = int(_wait_file(pid_file))
    try:
        assert p.wait(timeout=90) == tg.WALL_EXIT
        assert _wait_dead(pid), "an escaped python outlived the guard"
        assert "escaped the job" in log.read_text()
    finally:
        if _alive(pid):
            _kill(pid)


def test_stragglers_after_a_normal_exit_are_ended(tmp_path):
    """The child exits 0 but leaves a grandchild running: the guard ends it,
    says so, and still returns the child's code."""
    gpid = tmp_path / "g.pid"
    code = ("import subprocess, sys, time\n"
            f"g = subprocess.Popen([sys.executable, '-c', {('import os, time; open(' + repr(str(gpid)) + ', chr(119)).write(str(os.getpid())); time.sleep(120)')!r}])\n"
            "time.sleep(1.5)\n"
            "sys.exit(0)\n")
    log = tmp_path / "run.log"
    r = subprocess.run(_guard_argv("--wall-s", "60", "--log", str(log), "--",
                                   sys.executable, "-c", code), cwd=LAYER, timeout=90)
    g = int(_wait_file(gpid))
    try:
        assert r.returncode == 0
        assert _wait_dead(g)
        assert "left 1 process(es) running" in log.read_text()
    finally:
        if _alive(g):
            _kill(g)


def test_an_inherited_start_stamp_is_replaced(tmp_path):
    out = tmp_path / "env.txt"
    env = dict(os.environ, **{tg.STARTED_ENV: "2026-01-01T00:00:00+00:00"})
    code = f"import os; open(r'{out}', 'w').write(os.environ['{tg.STARTED_ENV}'])"
    subprocess.run(_guard_argv("--wall-s", "60", "--", sys.executable, "-c", code),
                   cwd=LAYER, env=env, timeout=60, check=True)
    assert not out.read_text().startswith("2026-01-01")
