"""The loop's commit asks the registry commit guard (commit_guard.py) first:
at or over the guard it commits everything else in its scope and leaves
registry_log.jsonl out, so no commit ever holds a registry GitHub would
refuse (2026-10-09 stopgap)."""
from __future__ import annotations

import json

from . import commit_guard, loop
from .test_loop import _no_real_schtasks  # noqa: F401 (autouse)
from .test_loop_commit_list import FakeRunner, _bundle, _git, _layer

REG = "research-layer/registry_log.jsonl"


def _ledger(layer):
    return json.loads((layer / "logs" / commit_guard.LEDGER).read_text(encoding="utf-8"))


def test_under_the_guard_the_registry_is_committed_as_before(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "bbbb000000000001")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == [REG] + paths
    assert _ledger(layer)["items"] == []


def test_over_the_guard_bundles_commit_without_the_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(commit_guard, "GUARD_BYTES", 1)
    layer = _layer(tmp_path)
    paths = _bundle(layer, "bbbb000000000002")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_add,), (i_commit,) = _git(fr, "add"), _git(fr, "commit")
    assert fr.pathspecs[i_add] == paths
    assert fr.pathspecs[i_commit] == paths
    assert not any(REG in c for c in fr.calls if c and c[0] == "git")
    assert not (layer / "logs" / "screen_commit_paths.txt").exists()   # committed
    (item,) = _ledger(layer)["items"]
    assert item["source"] == "registry_commit"


def test_over_the_guard_a_single_listed_file_is_still_committed(tmp_path, monkeypatch):
    """With the registry out, ONE listed file is the whole scope. The old
    "more than just the registry line" rule (len > 1) would read it as
    nothing new, skip the commit AND delete the list."""
    monkeypatch.setattr(commit_guard, "GUARD_BYTES", 1)
    layer = _layer(tmp_path)
    one = _bundle(layer, "bbbb000000000004")[:1]
    lst = layer / "logs" / "screen_commit_paths.txt"
    lst.write_text(one[0] + "\n", encoding="utf-8")
    fr = FakeRunner()                        # `git diff` reports no tracked change
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == one
    assert not lst.exists()


def test_over_the_guard_with_nothing_else_to_commit_runs_no_git_add(tmp_path, monkeypatch):
    monkeypatch.setattr(commit_guard, "GUARD_BYTES", 1)
    layer = _layer(tmp_path)
    fr = FakeRunner(codes={"git": 1})        # `git diff` would report a changed registry
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert not _git(fr, "add") and not _git(fr, "commit")
    assert not any(REG in c for c in fr.calls if c and c[0] == "git")
    # An empty scope must never become a bare, whole-tree `git diff --`.
    assert ["git", "diff", "--quiet", "--"] not in fr.calls


def test_a_guard_that_raises_never_commits_the_registry_or_fails_the_cycle(
        tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("guard exploded")
    monkeypatch.setattr(commit_guard, "registry_committable", boom)
    layer = _layer(tmp_path)
    paths = _bundle(layer, "bbbb000000000003")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == paths
    assert "registry left out" in capsys.readouterr().out


def test_a_listed_registry_path_never_bypasses_the_guard(tmp_path, monkeypatch):
    """Review finding 3: the screen list holds bundle files only; a hand edit
    naming the registry must not put it back into a held-back commit."""
    monkeypatch.setattr(commit_guard, "GUARD_BYTES", 1)
    layer = _layer(tmp_path)
    paths = _bundle(layer, "bbbb000000000005")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join([REG] + paths) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == paths
