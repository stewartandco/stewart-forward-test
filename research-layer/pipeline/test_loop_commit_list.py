"""The loop's commit carries every screen bundle listed in
logs/screen_commit_paths.txt, from any earlier cycle, and keeps the list until
a commit succeeds (2026-10-09: 8,627 screen bundles from 14 cycles that died
before commit_cycle were never committed)."""
from __future__ import annotations

import json

from . import loop
from .test_loop import (FakeRunner, _mk_layer, _no_real_schtasks,  # noqa: F401 (autouse)
                        _seed_crypto_caught_up)

NAMES = ("config.json", "equity.csv", "trades.csv")


def _bundle(layer, sid):
    d = layer / "artifacts" / sid
    d.mkdir(parents=True)
    for n in NAMES:
        (d / n).write_text("x\n", encoding="utf-8")
    return [f"research-layer/artifacts/{sid}/{n}" for n in NAMES]


def _git(fr, sub):
    return [i for i, c in enumerate(fr.calls) if c and c[0] == "git" and c[1] == sub]


def _layer(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    return layer


def test_bundles_listed_by_an_earlier_screen_are_committed_and_the_list_cleared(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa000000000001")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_add,), (i_commit,) = _git(fr, "add"), _git(fr, "commit")
    want = ["research-layer/registry_log.d"] + paths
    assert fr.pathspecs[i_add] == want
    assert fr.pathspecs[i_commit] == want
    assert not (layer / "logs" / "screen_commit_paths.txt").exists()
    assert not (layer / "logs" / "screen_commit_paths.taking").exists()


def test_a_failed_commit_keeps_the_list_for_the_next_cycle(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa000000000002")
    lst = layer / "logs" / "screen_commit_paths.txt"
    lst.write_text("\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner(codes={"git": 1})            # add fails
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert _git(fr, "add") and not _git(fr, "commit")
    assert lst.read_text(encoding="utf-8").split() == paths
    assert not (layer / "logs" / "screen_commit_paths.taking").exists()


def test_a_leftover_taking_file_from_a_killed_commit_is_committed_too(tmp_path):
    layer = _layer(tmp_path)
    old = _bundle(layer, "aaaa000000000003")
    new = _bundle(layer, "aaaa000000000004")
    (layer / "logs" / "screen_commit_paths.taking").write_text(
        "\n".join(old) + "\n", encoding="utf-8")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(new) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == ["research-layer/registry_log.d"] + old + new
    assert not (layer / "logs" / "screen_commit_paths.taking").exists()
    assert not (layer / "logs" / "screen_commit_paths.txt").exists()


def test_a_listed_path_missing_on_disk_is_never_handed_to_git(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa000000000005")
    gone = "research-layer/artifacts/aaaa00000000dead/trades.csv"
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join([paths[0], gone] + paths[1:] + [paths[0]]) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_add,) = _git(fr, "add")
    assert fr.pathspecs[i_add] == ["research-layer/registry_log.d"] + paths   # deduped


def test_a_cycle_that_aborts_before_its_commit_leaves_the_list_alone(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa000000000006")
    lst = layer / "logs" / "screen_commit_paths.txt"
    lst.write_text("\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner(codes={"pipeline.screen": 1})
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 1
    assert not any(c and c[0] == "git" for c in fr.calls)
    assert lst.read_text(encoding="utf-8").split() == paths
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] != "cycle_complete"


def test_a_list_holding_only_vanished_files_is_cleared_without_git_add(tmp_path):
    layer = _layer(tmp_path)
    lst = layer / "logs" / "screen_commit_paths.txt"
    lst.write_text("research-layer/artifacts/aaaa00000000dead/trades.csv\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert not _git(fr, "add") and not _git(fr, "commit")
    assert not lst.exists()
    assert not (layer / "logs" / "screen_commit_paths.taking").exists()


def test_an_unreadable_list_never_fails_a_completed_cycle(tmp_path, capsys):
    """Review I1: a list written by hand in UTF-16 (PowerShell 5.1 `>`) must
    not crash the cycle at its commit, tonight or any later night."""
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa000000000007")
    (layer / "logs" / "screen_commit_paths.taking").write_bytes(
        ("\n".join(paths) + "\n").encode("utf-16"))
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"
    # Not stuck: the garbled lines match nothing on disk, are skipped LOUDLY,
    # and the list does not come back to warn every night.
    assert not (layer / "logs" / "screen_commit_paths.taking").exists()
    assert "not on disk" in capsys.readouterr().out


def test_a_take_that_raises_anything_never_fails_a_completed_cycle(tmp_path, monkeypatch):
    layer = _layer(tmp_path)
    (layer / "logs" / "screen_commit_paths.txt").write_text("x\n", encoding="utf-8")

    def boom(path):
        raise ValueError("not an OSError")
    monkeypatch.setattr(loop, "_read_list", boom)
    assert loop.run(["--once", "--layer", str(layer)], runner=FakeRunner()) == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"


def test_a_bom_at_the_head_of_the_list_does_not_drop_the_first_path(tmp_path):
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa000000000008")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "﻿" + "\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_add,) = _git(fr, "add")
    assert fr.pathspecs[i_add] == ["research-layer/registry_log.d"] + paths


def test_an_orphaned_merging_segment_is_folded_back_in(tmp_path):
    """Review M1: a take killed between its rename and its append."""
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa000000000009")
    (layer / "logs" / "screen_commit_paths.txt.merging").write_text(
        "\n".join(paths) + "\n", encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    (i_commit,) = _git(fr, "commit")
    assert fr.pathspecs[i_commit] == ["research-layer/registry_log.d"] + paths
    assert not (layer / "logs" / "screen_commit_paths.txt.merging").exists()


def test_a_failed_commit_keeps_both_the_taken_list_and_one_listed_meanwhile(tmp_path):
    """Review M4: add succeeds, commit fails (index.lock held by the worker),
    and a hand-run screen listed a bundle while the commit ran."""
    layer = _layer(tmp_path)
    old = _bundle(layer, "aaaa00000000000a")
    new = _bundle(layer, "aaaa00000000000b")
    lst = layer / "logs" / "screen_commit_paths.txt"
    lst.write_text("\n".join(old) + "\n", encoding="utf-8")

    class _CommitFails(FakeRunner):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if argv and argv[0] == "git" and argv[1] == "commit":
                lst.write_text("\n".join(new) + "\n", encoding="utf-8")   # screen, meanwhile
                r.returncode = 1
            return r

    fr = _CommitFails()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert sorted(lst.read_text(encoding="utf-8").split()) == sorted(old + new)
    assert not (layer / "logs" / "screen_commit_paths.taking").exists()


def _kept_or_committed(layer, fr, paths):
    """Every path is either in the commit's scope or still on a list file."""
    committed = set()
    for i in _git(fr, "commit"):
        committed |= set(fr.pathspecs[i])
    kept = set()
    for name in ("screen_commit_paths.txt", "screen_commit_paths.taking",
                 "screen_commit_paths.txt.merging"):
        f = layer / "logs" / name
        if f.exists():
            kept |= set(f.read_text(encoding="utf-8").split())
    return [p for p in paths if p not in committed and p not in kept]


def test_a_read_that_fails_after_the_take_rename_loses_nothing(tmp_path, monkeypatch):
    """Re-review: take renamed the list to .taking, then its read raised. A
    commit that succeeds without those paths must not delete .taking."""
    layer = _layer(tmp_path)
    paths = _bundle(layer, "aaaa00000000000c")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(paths) + "\n", encoding="utf-8")
    real = loop._read_list
    calls = {"n": 0}

    def fail_once(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("transient")
        return real(path)
    monkeypatch.setattr(loop, "_read_list", fail_once)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert _kept_or_committed(layer, fr, paths) == []


def test_a_leftover_taking_survives_a_take_whose_merge_rename_fails(tmp_path, monkeypatch):
    """Re-review: a leftover .taking exists and renaming the list to .merging
    fails (a hand-run screen has it open). Neither file's paths may be lost."""
    layer = _layer(tmp_path)
    old = _bundle(layer, "aaaa00000000000d")
    new = _bundle(layer, "aaaa00000000000e")
    (layer / "logs" / "screen_commit_paths.taking").write_text(
        "\n".join(old) + "\n", encoding="utf-8")
    (layer / "logs" / "screen_commit_paths.txt").write_text(
        "\n".join(new) + "\n", encoding="utf-8")
    real_replace = loop.os.replace

    def replace(src, dst):
        if str(dst).endswith(".merging"):
            raise PermissionError("list open in screen")
        return real_replace(src, dst)
    monkeypatch.setattr(loop.os, "replace", replace)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    monkeypatch.setattr(loop.os, "replace", real_replace)
    assert _kept_or_committed(layer, fr, old + new) == []
