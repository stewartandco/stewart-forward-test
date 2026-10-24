"""Screen lists every bundle it chains in logs/screen_commit_paths.txt, so the
loop's commit carries it whichever cycle commits next (2026-10-09)."""
from __future__ import annotations

from . import screen
from .chainlock import ChainLock
from .test_screen_lock import _registry_with, _run

NAMES = ("config.json", "equity.csv", "trades.csv")


def _run_live_layout(reg, data, tmp_path, *extra):
    """The live layout: artifacts/ beside the registry, as the loop runs it."""
    return screen.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(reg.log_path.parent / "artifacts"),
                       "--logs-dir", str(tmp_path / "logs"), "--workers", "1", *extra])


def _listed(tmp_path):
    p = tmp_path / "logs" / "screen_commit_paths.txt"
    return p.read_text(encoding="utf-8").split() if p.exists() else []


def test_every_chained_bundle_is_listed_for_the_commit(tmp_path):
    reg, sids, data = _registry_with(tmp_path, 3)
    assert _run_live_layout(reg, data, tmp_path) == 0
    assert sorted(_listed(tmp_path)) == sorted(
        f"research-layer/artifacts/{s}/{n}" for s in sids for n in NAMES)


def test_the_list_is_appended_to_never_overwritten(tmp_path):
    reg, sids, data = _registry_with(tmp_path, 1)
    (tmp_path / "logs").mkdir(exist_ok=True)
    earlier = "research-layer/artifacts/0000000000000000/trades.csv"
    (tmp_path / "logs" / "screen_commit_paths.txt").write_text(earlier + "\n", encoding="utf-8")
    assert _run_live_layout(reg, data, tmp_path) == 0
    listed = _listed(tmp_path)
    assert listed[0] == earlier and len(listed) == 1 + len(NAMES)


def test_nothing_is_listed_when_the_lock_is_held_for_the_whole_run(tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 2)
    monkeypatch.setattr(screen, "_sleep", lambda s: None)
    monkeypatch.setattr(screen, "DRAIN_RESERVE_S", 0.0)
    holder = ChainLock(tmp_path / "logs", holder="session", purpose="test")
    holder.acquire()
    try:
        assert _run_live_layout(reg, data, tmp_path) == 0
    finally:
        holder.release()
    assert _listed(tmp_path) == []


def test_a_bundle_outside_the_registry_layer_is_never_listed(tmp_path):
    """A scratch --artifacts-dir (tests, hand runs) must never reach a live commit."""
    reg, sids, data = _registry_with(tmp_path, 1)
    assert _run(reg, data, tmp_path) == 0           # artifacts in tmp_path/"art"
    assert _listed(tmp_path) == []


def test_a_dry_run_lists_nothing(tmp_path):
    reg, sids, data = _registry_with(tmp_path, 1)
    assert _run_live_layout(reg, data, tmp_path, "--dry-run") == 0
    assert _listed(tmp_path) == []


def test_the_listed_names_are_exactly_the_files_a_bundle_holds(tmp_path):
    from .commit_list import BUNDLE_FILES
    reg, sids, data = _registry_with(tmp_path, 1)
    assert _run_live_layout(reg, data, tmp_path) == 0
    on_disk = {p.name for p in (reg.log_path.parent / "artifacts" / sids[0]).iterdir()}
    assert on_disk == set(BUNDLE_FILES)


def test_a_spec_dropped_stale_before_its_flush_is_not_listed(tmp_path, monkeypatch):
    reg, sids, data = _registry_with(tmp_path, 3)
    real_run_all = screen.run_all

    def run_all_then_bury(*a, **k):
        out = real_run_all(*a, **k)
        if reg.strategy_states()[sids[1]] == "proposed":
            reg.record_state_change(sids[1], "graveyard", "hand")
        return out
    monkeypatch.setattr(screen, "run_all", run_all_then_bury)
    assert _run_live_layout(reg, data, tmp_path) == 0
    listed_sids = {p.split("/")[2] for p in _listed(tmp_path)}
    assert listed_sids == {sids[0], sids[2]}


def test_a_batch_whose_chain_write_was_refused_is_not_listed(tmp_path, monkeypatch):
    from .registry import Registry
    reg, sids, data = _registry_with(tmp_path, 2)
    monkeypatch.setattr(screen, "SCREEN_CHUNK_PER_WORKER", 1)
    real_advance = Registry.advance
    calls = {"n": 0}

    def advance_then_foreign_append(self, snap):
        out = real_advance(self, snap)
        calls["n"] += 1
        if calls["n"] == 2:
            Registry(self.log_path).append("note", {"text": "foreign"})
        return out
    monkeypatch.setattr(Registry, "advance", advance_then_foreign_append)
    assert _run_live_layout(reg, data, tmp_path) == 1
    assert {p.split("/")[2] for p in _listed(tmp_path)} == {sids[0]}


def test_a_live_layout_run_with_a_foreign_logs_dir_lists_nothing(tmp_path):
    """Review M7: the loop reads <registry dir>/logs; a list anywhere else is
    never committed, so screen must not pretend."""
    reg, sids, data = _registry_with(tmp_path, 1)
    assert screen.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(reg.log_path.parent / "artifacts"),
                       "--logs-dir", str(tmp_path / "elsewhere"), "--workers", "1"]) == 0
    assert not (tmp_path / "elsewhere" / "screen_commit_paths.txt").exists()
