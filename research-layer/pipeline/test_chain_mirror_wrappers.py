"""The three task wrappers sync the chain mirror and commit
research-layer/registry_log.d, never registry_log.jsonl (segments design
s4.2). Each test RUNS a copy of the real .bat under cmd.exe against a
scratch git repo: the wrapper's live paths are rewritten to the scratch repo,
the pipeline stages are stubs that append to the registry (and list a bundle
or touch a price CSV), and chain_mirror / lock / common / degraded are the
real modules, with SEGMENT_MAX_BYTES lowered in the copy where a test needs
seals."""
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


def _run(bat: Path) -> int:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    r = subprocess.run(["cmd", "/c", str(bat)], env=env, capture_output=True, text=True,
                       timeout=120)
    return r.returncode


def _head_files(repo) -> list[str]:
    return sorted(_git(repo, "show", "--name-only", "--format=", "HEAD").split())


BUNDLE = "research-layer/artifacts/cccc000000000001/config.json"


MIRROR = "research-layer/registry_log.d"
REAL = ("chain_mirror.py", "lock.py", "common.py", "degraded.py")


def _scratch(tmp_path, bat: str, max_bytes: int | None = None) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    layer = repo / "research-layer"
    pkg = layer / "pipeline"
    pkg.mkdir(parents=True)
    for d in ("logs", "data", "tasks"):
        (layer / d).mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in REAL:
        shutil.copy(LAYER / "pipeline" / name, pkg / name)
    if max_bytes is not None:
        src = (pkg / "chain_mirror.py").read_text(encoding="utf-8")
        line = "SEGMENT_MAX_BYTES = 40 * 1024 * 1024"
        assert src.count(line) == 1
        (pkg / "chain_mirror.py").write_text(
            src.replace(line, f"SEGMENT_MAX_BYTES = {max_bytes}"), encoding="utf-8")
    for mod, body in STUBS.items():
        (pkg / f"{mod}.py").write_text(body, encoding="utf-8")
    (layer / "registry_log.jsonl").write_bytes(b'{"genesis": 1}\r\n')
    for csv in ("BTCUSD_1d.csv", "ETHUSD_1d.csv"):
        (layer / "data" / csv).write_text("date,close\n", encoding="utf-8")
    text = (LAYER / "tasks" / bat).read_bytes().decode("ascii")
    assert LIVE_REPO in text
    out = layer / "tasks" / bat
    out.write_bytes(text.replace(LIVE_REPO, str(repo)).encode("ascii"))
    (repo / ".gitignore").write_text(
        "research-layer/logs/\n__pycache__/\n*.lock\nresearch-layer/registry_log.d/*.tmp\n",
        encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo, out


def _mirror_files(repo) -> list[str]:
    """Every mirror file in HEAD's TREE (not only the last commit's changes:
    after two runs the first run's sealed segments are not in HEAD's diff)."""
    return sorted(_git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", MIRROR).split())


def _git_bytes(repo, *args) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          check=True).stdout


def _ledger(repo):
    return json.loads((repo / "research-layer/logs/degraded_commit.json").read_text(encoding="utf-8"))


def _refuse_next_sync(repo):
    """A manifest with the wrong format makes sync refuse (exit 3)."""
    d = repo / MIRROR
    d.mkdir(parents=True, exist_ok=True)
    (d / "MANIFEST.json").write_text('{"format": "other"}', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bad manifest")


def _live_lf(repo) -> bytes:
    return (repo / "research-layer/registry_log.jsonl").read_bytes().replace(b"\r\n", b"\n")


def test_worker_commits_the_mirror_and_bundles_never_the_live_file(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat")
    assert _run(bat) == 0
    assert _head_files(repo) == sorted([f"{MIRROR}/000001.jsonl", f"{MIRROR}/MANIFEST.json", BUNDLE])
    assert _git(repo, "show", f"HEAD:{MIRROR}/000001.jsonl").encode() == _live_lf(repo)
    assert _ledger(repo)["items"] == []


def test_worker_seals_segments_as_the_chain_grows(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat", max_bytes=16)
    assert _run(bat) == 0
    assert _run(bat) == 0
    files = _mirror_files(repo)
    assert files == sorted(f"{MIRROR}/{n}" for n in
                           ("000001.jsonl", "000002.jsonl", "000003.jsonl", "MANIFEST.json"))
    joined = b"".join(_git_bytes(repo, "show", f"HEAD:{f}") for f in files
                      if f.endswith(".jsonl"))
    assert joined == _live_lf(repo)


def test_worker_with_no_bundles_still_commits_a_new_untracked_mirror(tmp_path):
    """Detection must see UNTRACKED segments (`git diff` never reports them):
    with no bundle list, the mirror alone is what makes the worker commit."""
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat")
    (repo / "research-layer/pipeline/gauntlet_worker.py").write_text(
        STUBS["gauntlet_stats"], encoding="utf-8")        # appends to the registry, lists nothing
    _git(repo, "add", "research-layer/pipeline/gauntlet_worker.py")
    _git(repo, "commit", "-q", "-m", "registry-only worker stub")
    assert _run(bat) == 0
    assert _head_files(repo) == sorted([f"{MIRROR}/000001.jsonl", f"{MIRROR}/MANIFEST.json"])


def test_worker_with_a_refused_sync_commits_bundles_only(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat")
    _refuse_next_sync(repo)
    assert _run(bat) == 0
    assert _head_files(repo) == [BUNDLE]
    (item,) = _ledger(repo)["items"]
    assert item["source"] == "chain_mirror"


def test_worker_refused_sync_and_empty_list_never_makes_a_bare_commit(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat")
    _refuse_next_sync(repo)
    (repo / "research-layer/pipeline/gauntlet_worker.py").write_text(
        "import pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "(L / 'logs' / 'gauntlet_worker_commit_paths.txt').write_text('')\n",
        encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "empty-list stub")
    (repo / "other.txt").write_text("another session's work\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(bat) == 0
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "diff", "--cached", "--name-only").split() == ["other.txt"]


CRASH = "import os\nos._exit(-1073741819)\n"


@pytest.mark.parametrize("bat,want", [
    ("run_gauntlet_worker.bat", [BUNDLE]),
    ("run_gauntlet_stats.bat", None),
    ("run_quarantine.bat", ["research-layer/data/BTCUSD_1d.csv"]),
])
def test_a_sync_that_crashes_with_a_negative_code_leaves_the_mirror_out(tmp_path, bat, want):
    repo, path = _scratch(tmp_path, bat)
    (repo / "research-layer/pipeline/chain_mirror.py").write_text(CRASH, encoding="utf-8")
    _git(repo, "add", "research-layer/pipeline/chain_mirror.py")
    _git(repo, "commit", "-q", "-m", "crashing mirror")
    # An UNTRACKED leftover in the mirror dir: a wrong "sync succeeded" reading
    # of the crash code would commit it (the crashing module makes no mirror).
    (repo / MIRROR).mkdir(parents=True, exist_ok=True)
    (repo / MIRROR / "000001.jsonl").write_bytes(b'{"genesis": 1}' + bytes([10]))
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(path) == 0
    if want is None:
        assert _git(repo, "rev-parse", "HEAD") == head
    else:
        assert _head_files(repo) == want


def test_worker_list_lines_outside_artifacts_never_enter_the_scope(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat")
    _refuse_next_sync(repo)
    with (repo / "research-layer/pipeline/gauntlet_worker.py").open("a", encoding="utf-8") as f:
        f.write("with open(L / 'logs' / 'gauntlet_worker_commit_paths.txt', 'a') as f:\n"
                "    f.write('research-layer/registry_log.jsonl\\nresearch-layer\\n')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "hand-edited list stub")
    assert _run(bat) == 0
    assert _head_files(repo) == [BUNDLE]


def test_stats_commits_the_mirror_and_skips_an_unchanged_one(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_stats.bat")
    assert _run(bat) == 0
    assert _head_files(repo) == sorted([f"{MIRROR}/000001.jsonl", f"{MIRROR}/MANIFEST.json"])
    (repo / "research-layer/pipeline/gauntlet_stats.py").write_text("", encoding="utf-8")
    _git(repo, "add", "research-layer/pipeline/gauntlet_stats.py")
    _git(repo, "commit", "-q", "-m", "no-op stats stub")
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(bat) == 0
    assert _git(repo, "rev-parse", "HEAD") == head


def test_stats_with_a_refused_sync_skips_its_commit(tmp_path):
    repo, bat = _scratch(tmp_path, "run_gauntlet_stats.bat")
    _refuse_next_sync(repo)
    head = _git(repo, "rev-parse", "HEAD")
    assert _run(bat) == 0
    assert _git(repo, "rev-parse", "HEAD") == head


def test_quarantine_commits_mirror_and_prices(tmp_path):
    repo, bat = _scratch(tmp_path, "run_quarantine.bat")
    assert _run(bat) == 0
    assert _head_files(repo) == sorted([f"{MIRROR}/000001.jsonl", f"{MIRROR}/MANIFEST.json",
                                        "research-layer/data/BTCUSD_1d.csv"])


def test_quarantine_with_unchanged_prices_still_commits_a_new_untracked_mirror(tmp_path):
    """Detection must see UNTRACKED segments: with the price CSVs unchanged,
    the mirror alone is what makes quarantine commit."""
    repo, bat = _scratch(tmp_path, "run_quarantine.bat")
    (repo / "research-layer/pipeline/data_fetch.py").write_text("", encoding="utf-8")
    _git(repo, "add", "research-layer/pipeline/data_fetch.py")
    _git(repo, "commit", "-q", "-m", "no-op fetch stub")
    assert _run(bat) == 0
    assert _head_files(repo) == sorted([f"{MIRROR}/000001.jsonl", f"{MIRROR}/MANIFEST.json"])


def test_quarantine_with_a_refused_sync_commits_prices_only(tmp_path):
    repo, bat = _scratch(tmp_path, "run_quarantine.bat")
    _refuse_next_sync(repo)
    assert _run(bat) == 0
    assert _head_files(repo) == ["research-layer/data/BTCUSD_1d.csv"]


def test_quarantine_never_sweeps_a_live_file_someone_else_staged(tmp_path):
    repo, bat = _scratch(tmp_path, "run_quarantine.bat")
    with (repo / "research-layer/registry_log.jsonl").open("ab") as f:
        f.write(b'{"hand": 1}\r\n')
    _git(repo, "add", "research-layer/registry_log.jsonl")
    assert _run(bat) == 0
    assert "research-layer/registry_log.jsonl" not in _head_files(repo)
    assert "research-layer/registry_log.jsonl" in _git(repo, "diff", "--cached", "--name-only")


REPO_ROOT = LAYER.parent


def test_repo_attributes_store_segments_verbatim_under_autocrlf(tmp_path):
    """Review Focus 1: core.autocrlf=true on this machine must not touch a
    segment; the committed blob is the LF bytes exactly."""
    repo, bat = _scratch(tmp_path, "run_gauntlet_worker.bat")
    shutil.copy(REPO_ROOT / ".gitattributes", repo / ".gitattributes")
    _git(repo, "config", "core.autocrlf", "true")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-q", "-m", "attributes")
    assert _run(bat) == 0
    blob = subprocess.run(["git", "-C", str(repo), "cat-file", "-p", f"HEAD:{MIRROR}/000001.jsonl"],
                          capture_output=True, check=True).stdout
    assert b"\r" not in blob and blob == _live_lf(repo)
    assert _git(repo, "check-attr", "text", "--", f"{MIRROR}/000001.jsonl").strip().endswith("unset")


def test_repo_ignores_the_live_file_and_mirror_tmp_files():
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", "-v",
                          "research-layer/registry_log.jsonl",
                          "research-layer/registry_log.d/000001.jsonl.tmp"],
                         capture_output=True, text=True)
    lines = out.stdout.splitlines()
    assert len(lines) == 2, out.stdout + out.stderr
    assert subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", "-q",
                           "research-layer/registry_log.d/000001.jsonl"]).returncode == 1


def test_the_stopgap_guard_is_gone():
    assert not (LAYER / "pipeline" / "commit_guard.py").exists()
    for bat in ("run_gauntlet_worker.bat", "run_gauntlet_stats.bat", "run_quarantine.bat"):
        assert b"commit_guard" not in (LAYER / "tasks" / bat).read_bytes()
