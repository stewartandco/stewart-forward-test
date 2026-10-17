"""tasks/run_crypto_refresh.bat, scheduled task 33_CryptoGridRefresh (08:02 Perth).

Each behaviour test RUNS a copy of the real .bat under cmd.exe against a
scratch layer: the wrapper's live LAYER path is rewritten to the scratch
directory, `pipeline.crypto_refresh` is a stub that exits with a chosen code,
and `pipeline.taskguard` is the real module. Pattern:
`test_chain_mirror_wrappers.py` (no git here: the wrapper holds no git, no
chain and no lock)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="runs .bat wrappers under cmd.exe")

LAYER = Path(__file__).resolve().parent.parent
LIVE_LAYER = r"E:\Users\Coen\Claude\stewart-forward-test\research-layer"
BAT = "run_crypto_refresh.bat"
LOG = "crypto-refresh-run.log"


def _env():
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.pop("RL_TASKGUARD", None)
    return env


def _scratch(tmp_path, exit_code: int) -> tuple[Path, Path]:
    layer = tmp_path / "research-layer"
    pkg = layer / "pipeline"
    pkg.mkdir(parents=True)
    (layer / "tasks").mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(LAYER / "pipeline" / "taskguard.py", pkg / "taskguard.py")
    (pkg / "crypto_refresh.py").write_text(
        "import sys\n"
        "print('stub crypto_refresh ran')\n"
        f"sys.exit({exit_code})\n", encoding="utf-8")
    text = (LAYER / "tasks" / BAT).read_bytes().decode("ascii")
    assert LIVE_LAYER in text
    out = layer / "tasks" / BAT
    out.write_bytes(text.replace(LIVE_LAYER, str(layer)).encode("ascii"))
    return layer, out


def _run(bat: Path) -> int:
    return subprocess.run(["cmd", "/c", str(bat)], env=_env(), capture_output=True,
                          text=True, timeout=120).returncode


@pytest.mark.parametrize("code", [0, 1, 2])
def test_crypto_refresh_wrapper_passes_the_python_exit_code_through(tmp_path, code):
    layer, bat = _scratch(tmp_path, code)
    assert _run(bat) == code
    lines = (layer / "logs" / LOG).read_text(encoding="utf-8", errors="replace").splitlines()
    assert re.match(r"==== .+ crypto_refresh ====\s*$", lines[0])
    assert "stub crypto_refresh ran" in lines
    assert re.match(rf"==== .+ exit {code} ====\s*$", lines[-1])


def test_crypto_refresh_wrapper_uses_an_absolute_interpreter_and_the_guard():
    text = (LAYER / "tasks" / BAT).read_text(encoding="ascii")
    pys = re.findall(r"(?im)^set PY=(.+?)\s*$", text)
    assert pys == [r"C:\Users\Coen\AppData\Local\Python\bin\python.exe"]
    assert re.search(r'"%PY%" -m pipeline\.taskguard --wall-s 780 --grace-s 30 '
                     r'--log "%LOG%" -- cmd /d /c "%~f0"', text)
    code = [ln for ln in text.splitlines() if not ln.strip().lower().startswith("rem")]
    # it holds no lock and runs no git: the guard gets none of those flags
    assert not [ln for ln in code
                if re.search(r"--(lock|hard-lock|release-lock|git-dir)\b", ln)]
    assert not [ln for ln in code if re.search(r"(?i)\bgit\b", ln)]
    assert "pipeline.crypto_refresh" in text
    # the deadline and the hard stop land the ledger write before the guard's
    # 08:15:00 wall (08:02 + 780 s; no lock, so no grace): final review I1
    runs = [ln for ln in code if "pipeline.crypto_refresh" in ln]
    assert runs == ['"%PY%" -m pipeline.crypto_refresh --deadline-local 08:11 '
                    '--stop-local 08:12 >> "%LOG%" 2>&1']
    # exit tests are exact, never `if errorlevel` (>= 1)
    assert not re.search(r"(?i)if\s+(not\s+)?errorlevel", text)


def test_a_locked_run_log_exits_4_and_runs_nothing(tmp_path):
    """An orphan holding the log the way cmd's `>>` does (no write sharing):
    the wrapper must not run blind and exit 0."""
    from .test_taskguard import _alive, _kill, _wait_file
    layer, bat = _scratch(tmp_path, 0)
    logs = layer / "logs"
    logs.mkdir()
    holder = tmp_path / "holder.bat"
    pid_file = tmp_path / "holder.pid"
    holder.write_text(
        "@echo off\r\n"
        f"\"{sys.executable}\" -c \"import os, time; open(r'{pid_file}', 'w')"
        f".write(str(os.getpid())); time.sleep(120)\" >> \"{logs / LOG}\" 2>&1\r\n",
        encoding="ascii")
    h = subprocess.Popen(["cmd", "/c", str(holder)], env=_env())
    orphan = int(_wait_file(pid_file))
    h.kill()                                   # the orphan keeps the handle
    h.wait(timeout=10)
    try:
        assert _run(bat) == 4
        assert "run log locked" in (logs / "crypto-refresh-run.locked.log").read_text()
        assert "stub crypto_refresh ran" not in (logs / LOG).read_text(errors="replace")
    finally:
        if _alive(orphan):
            _kill(orphan)


def test_the_wall_ends_a_hung_run_with_124_and_the_next_fire_logs_normally(tmp_path):
    """The guard is wired in: a python step that never returns is killed at the
    wall (exit 124) and the next fire opens the log and runs."""
    layer, bat = _scratch(tmp_path, 0)
    stub = layer / "pipeline" / "crypto_refresh.py"
    stub.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    text = bat.read_bytes().decode("ascii")
    new, n = re.subn(r"--wall-s \d+", "--wall-s 4", text)
    assert n == 1
    bat.write_bytes(new.encode("ascii"))
    assert _run(bat) == 124
    log = layer / "logs" / LOG
    assert "taskguard: wall 4s reached" in log.read_text(encoding="utf-8").splitlines()[-1]
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    assert _run(bat) == 0
    assert re.match(r"==== .+ exit 0 ====\s*$", log.read_text(encoding="utf-8").splitlines()[-1])
