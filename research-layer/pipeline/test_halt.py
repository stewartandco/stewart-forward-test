"""D35 kill switch (pipeline/halt.py): logs/pipeline.halted stops the loop, the
gauntlet worker and the livegate on presence alone. Offline; reuses the loop,
worker and livegate fixtures. Harness audit 2026-11-06, Tier 1 #3.

Run: python -m pytest pipeline/test_halt.py -q
"""
from __future__ import annotations

import json

import pytest

from . import gauntlet_worker as gw
from . import halt, livegate, loop
from .test_gauntlet_worker import _run as _gw_run, _setup as _gw_setup, _status as _gw_status
from .test_gauntlet_worker import _two_candidates, _verdicts
from .test_livegate_run import _chain, _quarantined
from .test_loop import FakeRunner, _mk_layer, _modules, _no_real_schtasks, _seed_crypto_caught_up  # noqa: F401


def _halt(logs, text="coen: spend review"):
    logs.mkdir(parents=True, exist_ok=True)
    (logs / halt.HALT_NAME).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- the reader

def test_reason_absent_empty_text_and_bom(tmp_path):
    assert halt.halt_reason(tmp_path) is None
    (tmp_path / halt.HALT_NAME).write_text("", encoding="utf-8")
    assert halt.halt_reason(tmp_path) == ""                     # presence alone halts
    (tmp_path / halt.HALT_NAME).write_bytes("﻿budget\nsecond line".encode("utf-8"))
    assert halt.halt_reason(tmp_path) == "budget"
    with pytest.raises(halt.PipelineHalted):
        halt.check(tmp_path)


def test_the_code_never_writes_the_artifact():
    # Coen's artifact (D35): no module may create or delete it
    import pathlib
    import re
    verbs = re.compile(r"write_text|write_bytes|unlink|\.touch\(|os\.remove|os\.replace|open\(.*['\"][wax]")
    for p in pathlib.Path(halt.__file__).parent.glob("*.py"):
        if p.name.startswith("test_"):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"pipeline\.halted|HALT_NAME|halt_path", line):
                assert not verbs.search(line), f"{p.name}:{n}: {line.strip()}"
    body = pathlib.Path(halt.__file__).read_text(encoding="utf-8").split('"""', 2)[-1]
    assert not verbs.search(body)


# ---------------------------------------------------------------- the loop

def test_loop_halted_at_start_runs_no_stage(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _halt(layer / "logs")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert fr.calls == []
    st = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert st["items"]["outcome"] == "halted" and st["overall"] == "WARN"
    assert st["items"]["halted_at"] == "run start"            # before any preflight or stage
    assert "coen: spend review" in st["items"]["halted"]
    assert "halted_by_coen" in st["escalations"]


def test_loop_halted_between_stages_stops_before_the_next(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)

    class HaltAfterTriage(FakeRunner):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if "-m" in argv and argv[argv.index("-m") + 1] == "pipeline.triage_batch":
                _halt(layer / "logs", "mid-cycle")
            return r

    fr = HaltAfterTriage()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert _modules(fr) == ["pipeline.triage_batch"]           # composer never started
    st = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert st["items"]["outcome"] == "halted" and st["items"]["halted_at"] == "before a stage"
    state = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert "watermark" not in state.get("classes", {}).get("fx", {})   # not watermarked
    assert not (layer / "logs" / "loop.lock").exists()              # locks released
    assert not (layer / "logs" / "chain.lock").exists()


def test_loop_without_the_artifact_is_unchanged(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert _modules(fr) == ["pipeline.triage_batch", "pipeline.composer",
                            "pipeline.composer", "pipeline.screen"]


def test_seed_watermarks_is_halted_too(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=3)
    _halt(layer / "logs")
    assert loop.run(["--seed-watermarks", "--layer", str(layer)]) == 0
    assert not (layer / "logs" / "loop_state.json").exists()


# ---------------------------------------------------------------- the gauntlet worker

def test_worker_halted_at_start_reads_and_writes_nothing(tmp_path):
    reg, spec, data = _gw_setup(tmp_path)
    before = len(list(reg.entries()))
    _halt(tmp_path / "logs")
    assert _gw_run(reg, data, tmp_path) == 0
    st = _gw_status(tmp_path)
    assert st["exit_reason"] == "halted" and "coen: spend review" in st["halted"]
    # stopped BEFORE the chain read and the orphan repair (which writes): queued was never read
    assert st["queued"] is None
    assert len(list(reg.entries())) == before
    assert not (tmp_path / "logs" / "gauntlet_worker.lock").exists()


def test_worker_halted_between_chunks_chains_only_the_finished_one(tmp_path, monkeypatch):
    reg, a, b, data = _two_candidates(tmp_path)
    real = gw._evaluate_payload

    def evaluate_then_halt(payload):
        r = real(payload)
        _halt(tmp_path / "logs", "between batches")
        return r

    monkeypatch.setattr(gw, "_evaluate_payload", evaluate_then_halt)
    assert _gw_run(reg, data, tmp_path) == 0
    st = _gw_status(tmp_path)
    assert st["exit_reason"] == "halted" and st["evaluated"] == 1
    assert len(_verdicts(reg)) == 1 and st["queued"] == 1      # the second stays queued


# ---------------------------------------------------------------- cold review 2026-11-06 additions

def test_a_second_loop_instance_never_writes_halted_over_the_holders_status(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    from .chainlock import ChainLock
    holder = ChainLock(layer / "logs", holder="loop-instance", purpose="other", name="loop.lock")
    holder.acquire()
    try:
        _halt(layer / "logs")
        assert loop.run(["--once", "--layer", str(layer)], runner=FakeRunner()) == 0
    finally:
        holder.release()
    st = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert st["items"]["outcome"] == "deferred_instance"      # the halt is the holder's to report


def test_quarantine_halted_writes_nothing_review_still_runs(tmp_path, capsys):
    from . import quarantine
    reg_path = tmp_path / "registry_log.jsonl"                 # absent: a non-halted run would REFUSE (1)
    args = ["--registry", str(reg_path), "--data-dir", str(tmp_path / "data")]
    assert quarantine.run(args + ["--date", "2026-11-05"]) == 1
    _halt(tmp_path / "logs")
    for mode in (["--date", "2026-11-05"], ["--catch-up"], ["--fetch-owed"]):
        assert quarantine.run(args + mode) == 0, mode
    assert "halted by pipeline.halted" in capsys.readouterr().out
    assert not reg_path.exists()
    assert quarantine.run(args + ["--review"]) == 1            # --review is not halted (writes nothing)


def test_gauntlet_stats_halted_at_start_writes_halted_status_and_nothing_else(tmp_path):
    from . import gauntlet_stats
    reg, spec, data = _gw_setup(tmp_path)
    before = len(list(reg.entries()))
    _halt(tmp_path / "logs")
    rc = gauntlet_stats.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                             "--logs-dir", str(tmp_path / "logs"), "--chain"])
    assert rc == 0 and len(list(reg.entries())) == before
    st = json.loads((tmp_path / "logs" / "gauntlet_stats_status.json").read_text(encoding="utf-8"))
    assert st["exit_reason"] == "halted" and "halted" in st


def test_screen_halted_before_its_first_batch_writes_nothing(tmp_path, monkeypatch):
    from . import screen
    from .screen import run as screen_run
    monkeypatch.setattr(screen, "run_all", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a batch was evaluated after the halt")))
    from .test_screen import chain_protocol_note, dated_target_hit_bars, screening_registry, write_data_dir
    reg, spec = screening_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    chain_protocol_note(reg)
    _halt(tmp_path / "logs")
    assert screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art")]) == 0
    assert reg.strategy_states()[spec["strategy_id"]] == "proposed"
    assert not [e for e in reg.entries() if e["entry_type"] == "verdict"]


def test_screen_halted_after_evaluating_does_not_write_the_batch(tmp_path, monkeypatch):
    from . import screen
    from .test_screen import chain_protocol_note, dated_target_hit_bars, screening_registry, write_data_dir
    reg, spec = screening_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    chain_protocol_note(reg)
    real = screen.run_all

    def run_then_halt(*a, **kw):
        out = real(*a, **kw)
        _halt(tmp_path / "logs", "mid-screen")
        return out

    monkeypatch.setattr(screen, "run_all", run_then_halt)
    assert screen.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art")]) == 0
    assert reg.strategy_states()[spec["strategy_id"]] == "proposed"   # flush refused to start


def test_triage_stops_paying_for_panels_once_halted(tmp_path):
    from . import triage_batch as tb
    from .test_triage_batch import _Meter
    _halt(tmp_path)

    class NoClient:
        class messages:
            @staticmethod
            def create(**kw):
                raise AssertionError("a panel was paid for after the halt")

    pending = {"p1": {"claim": "x", "quote": "q", "source": {}}}
    out = tb.build_decisions(NoClient(), "m", pending, {}, _Meter(), halt_dir=tmp_path)
    assert out["stopped"] == "halted" and "p1" not in out["decisions"]


def test_every_chain_writing_bat_skips_sync_and_commit_while_halted():
    import pathlib
    tasks = pathlib.Path(halt.__file__).resolve().parent.parent / "tasks"
    for name in ("run_quarantine.bat", "run_gauntlet_worker.bat", "run_gauntlet_stats.bat"):
        src = (tasks / name).read_text(encoding="utf-8")
        guard = src.find('if exist "%LAYER%\\logs\\pipeline.halted"')
        sync = src.find("pipeline.chain_mirror sync")
        assert guard != -1 and sync != -1 and guard < sync, name
        assert b"\r\n" in (tasks / name).read_bytes(), name


# ---------------------------------------------------------------- the livegate

def test_livegate_halted_assesses_and_writes_nothing(tmp_path, capsys):
    reg, sid = _quarantined(tmp_path)
    before = _chain(reg)
    _halt(tmp_path / "logs")
    assert livegate.run(["--registry", str(reg.log_path), "--data-dir", str(tmp_path / "data"),
                         "--report", str(tmp_path / "rep")]) == 0
    assert "halted by pipeline.halted" in capsys.readouterr().out
    assert _chain(reg) == before
    assert not (tmp_path / "rep").exists()
