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
