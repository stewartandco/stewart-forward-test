"""The three task wrappers that commit registry_log.jsonl obey the commit
guard (2026-10-09 stopgap). Each test RUNS a copy of the real .bat under
cmd.exe against a scratch git repo: the wrapper's hardcoded live paths are
rewritten to the scratch repo, the pipeline stages are stubs that append to
the registry (and list a bundle / touch a price CSV the way the real stage
does), and commit_guard.py / degraded.py are the real modules, with only the
guard constant lowered in the copy so a small file can be "over" it."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="runs .bat wrappers under cmd.exe")

LAYER = Path(__file__).resolve().parent.parent
LIVE_REPO = r"E:\Users\Coen\Claude\stewart-forward-test"
REG = "research-layer/registry_log.jsonl"

STUBS = {
    "gauntlet_worker": (
        "import pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "with open(L / 'registry_log.jsonl', 'a') as f: f.write('{\"worker\": 1}\\n')\n"
        "b = L / 'artifacts' / 'cccc000000000001'\n"
        "b.mkdir(parents=True, exist_ok=True)\n"
        "(b / 'config.json').write_text('{}\\n')\n"
        "with open(L / 'logs' / 'gauntlet_worker_commit_paths.txt', 'a') as f:\n"
        "    f.write('research-layer/artifacts/cccc000000000001/config.json\\n')\n"),
    "gauntlet_stats": (
        "import pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "with open(L / 'registry_log.jsonl', 'a') as f: f.write('{\"stats\": 1}\\n')\n"),
    "data_fetch": (
        "import pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "with open(L / 'data' / 'BTCUSD_1d.csv', 'a') as f: f.write('2026-10-09,1\\n')\n"),
    "quarantine": (
        "import pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "with open(L / 'registry_log.jsonl', 'a') as f: f.write('{\"q\": 1}\\n')\n"),
}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


def _scratch(tmp_path, bat: str, guard_bytes: int) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    layer = repo / "research-layer"
    pkg = layer / "pipeline"
    pkg.mkdir(parents=True)
    (layer / "logs").mkdir()
    (layer / "data").mkdir()
    (layer / "tasks").mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(LAYER / "pipeline" / "degraded.py", pkg / "degraded.py")
    src = (LAYER / "pipeline" / "commit_guard.py").read_text(encoding="utf-8")
    line = "GUARD_BYTES = 95 * 1024 * 1024"
    assert src.count(line) == 1
    (pkg / "commit_guard.py").write_text(src.replace(line, f"GUARD_BYTES = {guard_bytes}"),
                                         encoding="utf-8")
    for mod, body in STUBS.items():
        (pkg / f"{mod}.py").write_text(body, encoding="utf-8")
    (layer / "registry_log.jsonl").write_text('{"genesis": 1}\n', encoding="utf-8")
    for csv in ("BTCUSD_1d.csv", "ETHUSD_1d.csv"):
        (layer / "data" / csv).write_text("date,close\n", encoding="utf-8")
    text = (LAYER / "tasks" / bat).read_bytes().decode("ascii")
    assert LIVE_REPO in text
    out = layer / "tasks" / bat
    out.write_bytes(text.replace(LIVE_REPO, str(repo)).encode("ascii"))
    (repo / ".gitignore").write_text("research-layer/logs/\n__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo, out


def _run(bat: Path) -> int:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    r = subprocess.run(["cmd", "/c", str(bat)], env=env, capture_output=True, text=True,
                       timeout=120)
    return r.returncode


def _head_files(repo) -> list[str]:
    return sorted(_git(repo, "show", "--name-only", "--format=", "HEAD").split())


def _ledger(repo):
    return json.loads((repo / "research-layer" / "logs" / "degraded_commit.json")
                      .read_text(encoding="utf-8"))


BUNDLE = "research-layer/artifacts/cccc000000000001/config.json"


def test_worker_under_the_guard_commits_registry_and_bundles(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat", 10**9)
    assert _run(bat) == 0
    assert _head_files(repo) == sorted([REG, BUNDLE])
    assert _ledger(repo)["items"] == []


def test_worker_over_the_guard_commits_bundles_only(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat", 5)
    assert _run(bat) == 0
    assert _head_files(repo) == [BUNDLE]
    assert REG in _git(repo, "status", "--porcelain")          # still on disk, uncommitted
    assert not (repo / "research-layer/logs/gauntlet_worker_commit_paths.txt").exists()
    assert not (repo / "research-layer/logs/gauntlet_worker_commit_paths.taking").exists()
    (item,) = _ledger(repo)["items"]
    assert item["source"] == "registry_commit"
    log = (repo / "research-layer/logs/gauntlet-worker-run.log").read_text(encoding="utf-8")
    assert "NOT committed" in log


def test_worker_over_the_guard_with_no_bundles_makes_no_commit(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat", 5)
    stub = repo / "research-layer/pipeline/gauntlet_worker.py"
    stub.write_text(STUBS["gauntlet_stats"], encoding="utf-8")   # registry only
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "registry-only stub")
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(bat) == 0
    assert _git(repo, "rev-parse", "HEAD") == head


def test_stats_under_the_guard_commits_the_registry(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_stats.bat", 10**9)
    assert _run(bat) == 0
    assert _head_files(repo) == [REG]


def test_stats_over_the_guard_skips_its_commit(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_stats.bat", 5)
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(bat) == 0
    assert _git(repo, "rev-parse", "HEAD") == head
    assert len(_ledger(repo)["items"]) == 1


def test_quarantine_under_the_guard_commits_registry_and_prices(tmp_path):
    repo, bat = _scratch(tmp_path, "run_quarantine.bat", 10**9)
    assert _run(bat) == 0
    assert _head_files(repo) == sorted([REG, "research-layer/data/BTCUSD_1d.csv"])


def test_quarantine_over_the_guard_commits_prices_only(tmp_path):
    repo, bat = _scratch(tmp_path, "run_quarantine.bat", 5)
    assert _run(bat) == 0
    assert _head_files(repo) == ["research-layer/data/BTCUSD_1d.csv"]
    assert len(_ledger(repo)["items"]) == 1


def test_quarantine_never_sweeps_a_registry_someone_else_staged(tmp_path):
    """The commit is the --only form: a registry staged by another session
    stays staged and out of the quarantine commit when the guard says no."""
    repo, bat = _scratch(tmp_path, "run_quarantine.bat", 5)
    reg = repo / "research-layer/registry_log.jsonl"
    with reg.open("a", encoding="utf-8") as f:
        f.write('{"hand": 1}\n')
    _git(repo, "add", REG)
    assert _run(bat) == 0
    assert _head_files(repo) == ["research-layer/data/BTCUSD_1d.csv"]
    assert REG in _git(repo, "diff", "--cached", "--name-only")


def test_a_guard_that_cannot_run_keeps_the_registry_out(tmp_path):
    """Fail-safe: only exit 0 commits. A guard module that crashes on import
    (any nonzero code) must leave the registry out, not in."""
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat", 10**9)
    (repo / "research-layer/pipeline/commit_guard.py").write_text(
        "raise SystemExit(1)\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "broken guard")
    assert _run(bat) == 0
    assert _head_files(repo) == [BUNDLE]


# --- cold review 2026-10-09 -------------------------------------------------

def test_worker_empty_list_with_registry_held_never_makes_a_bare_commit(tmp_path):
    """Review finding 1: with the registry held back and an EMPTY list (the
    worker's prune writes one when every listed bundle vanished), the scope
    file was empty, and `git commit --pathspec-from-file=<empty>` commits the
    WHOLE index: here another session's staged file and a staged registry."""
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat", 5)
    (repo / "research-layer/pipeline/gauntlet_worker.py").write_text(
        "import pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "(L / 'logs' / 'gauntlet_worker_commit_paths.txt').write_text('')\n",
        encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "empty-list stub")
    (repo / "other.txt").write_text("another session's work\n", encoding="utf-8")
    with (repo / REG).open("a", encoding="utf-8") as f:
        f.write('{"hand": 1}\n')
    _git(repo, "add", "other.txt", REG)
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(bat) == 0
    assert _git(repo, "rev-parse", "HEAD") == head
    assert sorted(_git(repo, "diff", "--cached", "--name-only").split()) == sorted(
        ["other.txt", REG])
    assert not (repo / "research-layer/logs/gauntlet_worker_commit_paths.taking").exists()


CRASH = "import os\nos._exit(-1073741819)\n"     # 0xC0000005: cmd sees a NEGATIVE errorlevel


@pytest.mark.parametrize("bat,want", [
    ("run_gauntlet_worker.bat", [BUNDLE]),
    ("run_gauntlet_stats.bat", None),             # None: no commit at all
    ("run_quarantine.bat", ["research-layer/data/BTCUSD_1d.csv"]),
])
def test_a_guard_that_crashes_with_a_negative_code_holds_the_registry_back(tmp_path, bat, want):
    """Review finding 2: `if errorlevel 1` is ERRORLEVEL >= 1, so an NTSTATUS
    crash code (negative) read as "yes". Only an exact 0 commits."""
    repo, path = _scratch(tmp_path, bat, 10**9)
    (repo / "research-layer/pipeline/commit_guard.py").write_text(CRASH, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "crashing guard")
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(path) == 0
    if want is None:
        assert _git(repo, "rev-parse", "HEAD") == head
    else:
        assert _head_files(repo) == want


def test_worker_list_lines_outside_artifacts_never_enter_the_scope(tmp_path):
    """Review finding 3: a hand-edited list naming the registry (or anything
    outside research-layer/artifacts/) must not smuggle it past the guard."""
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat", 5)
    with (repo / "research-layer/pipeline/gauntlet_worker.py").open("a", encoding="utf-8") as f:
        f.write("with open(L / 'logs' / 'gauntlet_worker_commit_paths.txt', 'a') as f:\n"
                "    f.write('research-layer/registry_log.jsonl\\nresearch-layer\\n')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "hand-edited list stub")
    assert _run(bat) == 0
    assert _head_files(repo) == [BUNDLE]
