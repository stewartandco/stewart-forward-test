"""The loop's commit stages research-layer/registry_log.d (the chain's git
mirror, chain_mirror.py), never registry_log.jsonl. A refused or crashing
sync leaves the mirror out and everything else still commits (segments
design s4.2)."""
from __future__ import annotations

from . import chain_mirror, loop
from .test_loop import _no_real_schtasks  # noqa: F401 (autouse)
from .test_loop_commit_list import FakeRunner, _bundle, _git, _layer

MIRROR = "research-layer/registry_log.d"
LIVE = "research-layer/registry_log.jsonl"


def _list(layer, paths):
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(paths) + "\n", encoding="utf-8")


def _refuse(monkeypatch):
    monkeypatch.setattr(chain_mirror, "sync",
                        lambda *a, **k: chain_mirror.SyncResult(ok=False, reason="test refusal"))


def test_the_mirror_is_synced_and_committed_with_the_bundles(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "dddd000000000001")
    _list(layer, paths)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == [MIRROR] + paths
    assert (layer / "registry_log.d" / chain_mirror.MANIFEST).is_file()
    assert chain_mirror.check(layer / "registry_log.jsonl", layer / "registry_log.d") == "MATCH"
    assert not any(LIVE in c for c in fr.calls if c and c[0] == "git")


def test_new_untracked_segments_alone_trigger_a_commit(tmp_path):
    """git diff never sees untracked files; the loop asks git status."""
    layer = _layer(tmp_path)
    fr = FakeRunner(git_status_out=f"?? {MIRROR}/000002.jsonl\n")
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == [MIRROR]
    assert any(c[:3] == ["git", "status", "--porcelain"] for c in fr.calls)


def test_a_clean_mirror_and_nothing_listed_makes_no_commit(tmp_path):
    layer = _layer(tmp_path)
    fr = FakeRunner()                      # status empty, diff clean
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert not _git(fr, "add") and not _git(fr, "commit")


def test_a_refused_sync_commits_the_bundles_without_the_mirror(tmp_path, monkeypatch):
    _refuse(monkeypatch)
    layer = _layer(tmp_path)
    paths = _bundle(layer, "dddd000000000002")
    _list(layer, paths)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_add,), (i_commit,) = _git(fr, "add"), _git(fr, "commit")
    assert fr.pathspecs[i_add] == paths and fr.pathspecs[i_commit] == paths
    assert not (layer / "logs" / "screen_commit_paths.txt").exists()


def test_a_refused_sync_with_a_single_listed_file_still_commits_it(tmp_path, monkeypatch):
    _refuse(monkeypatch)
    layer = _layer(tmp_path)
    one = _bundle(layer, "dddd000000000003")[:1]
    _list(layer, one)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == one


def test_a_refused_sync_with_nothing_else_runs_no_git_add(tmp_path, monkeypatch):
    _refuse(monkeypatch)
    layer = _layer(tmp_path)
    fr = FakeRunner(codes={"git": 1}, git_status_out="?? x\n")
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert not _git(fr, "add") and not _git(fr, "commit")
    assert ["git", "diff", "--quiet", "--"] not in fr.calls
    assert not any(c[:2] == ["git", "status"] for c in fr.calls)


def test_a_sync_that_raises_never_fails_the_cycle(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("mirror exploded")
    monkeypatch.setattr(chain_mirror, "sync", boom)
    layer = _layer(tmp_path)
    paths = _bundle(layer, "dddd000000000004")
    _list(layer, paths)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == paths
    assert "registry_log.d left out" in capsys.readouterr().out


def test_listed_paths_outside_artifacts_never_enter_the_scope(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "dddd000000000005")
    _list(layer, [LIVE, f"{MIRROR}/000001.jsonl"] + paths)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == [MIRROR] + paths


def test_listed_paths_with_dotdot_or_backslash_never_enter_the_scope(tmp_path, capsys):
    """`research-layer/artifacts/../registry_log.jsonl` starts with the
    artifacts prefix but git resolves it to the live file; a backslash path
    names a real bundle on Windows but is never one screen writes. Both are
    dropped as foreign, loudly (final review 2026-10-09)."""
    layer = _layer(tmp_path)
    paths = _bundle(layer, "dddd000000000006")
    foreign = ["research-layer/artifacts/../registry_log.jsonl",
               "research-layer/artifacts/dddd000000000006/../../registry_log.jsonl",
               "research-layer/artifacts/dddd000000000006\\config.json"]
    _list(layer, foreign + paths)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_add,), (i_commit,) = _git(fr, "add"), _git(fr, "commit")
    assert fr.pathspecs[i_add] == [MIRROR] + paths
    assert fr.pathspecs[i_commit] == [MIRROR] + paths
    assert "WARNING skipped 3 listed screen path(s)" in capsys.readouterr().out
