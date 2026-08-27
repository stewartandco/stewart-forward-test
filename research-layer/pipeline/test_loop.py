"""Offline tests for the pipeline loop orchestrator (no network, no API).

Run: python -m pytest pipeline/test_loop.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from .chainlock import ChainLock
from .registry import Registry
from . import loop, loop_state


def _mk_layer(tmp_path, accepted_fx=0):
    """Minimal layer: registry with N accepted fx-routable cards, logs dir."""
    layer = tmp_path
    (layer / "logs").mkdir()
    reg = Registry(layer / "registry_log.jsonl")
    for i in range(accepted_fx):
        cid = f"card{i:04d}"
        reg.register_card({"card_id": cid, "claim": f"c{i}", "quote": "q",
                           "topics": [], "tags": {"asset_classes": ["fx"]},
                           "review": {"status": "pending", "reject_reason": None},
                           "source": {}, "links": [], "credibility_tier": "practitioner"})
        reg.review_card(cid, "accepted", "coen")
    return layer, reg


class FakeRunner:
    """Records invocations; returns preset exit codes per python -m module.
    Non-python argv (e.g. git) returns 0 unless a code is set for argv[0]."""
    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if "-m" in argv:
            key = argv[argv.index("-m") + 1]
        else:
            key = argv[0]
        class R: pass
        r = R(); r.returncode = self.codes.get(key, 0)
        return r


def _modules(fr):
    return [c[c.index("-m") + 1] for c in fr.calls if "-m" in c]


def _seed_crypto_caught_up(layer, count):
    """TEST-REALITY ADJUSTMENT (not in the plan's verbatim test file): the
    fixture's cards are tagged fx-only, but composer.routable_cards treats
    crypto as unrestricted -- every accepted card, tagged or not, routes to
    crypto too. With only the fx tag set, BOTH crypto and fx end up
    over-threshold and never-run, and loop_state.pick_class's tie-break
    (cells.LIVE_CLASSES order) picks crypto first, not fx. Seeding crypto's
    watermark at `count` here removes that ambiguity so the tests can assert
    the fx-specific behaviour the plan's comments describe, without changing
    loop.py's (correct) trigger/tie-break logic."""
    state_path = layer / "logs" / "loop_state.json"
    state = loop_state.load(state_path)
    loop_state.record_generation(state, "crypto", run_id="seed-crypto-caught-up",
                                 routable_count=count, ts_utc="2020-01-01T00:00:00+00:00")
    loop_state.save(state_path, state)


def test_no_trigger_exits_zero_and_says_so(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=3)   # 3 < 25
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_trigger"
    assert fr.calls == []                            # no stage ran


def test_foreign_lock_defers(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    other = ChainLock(layer / "logs", holder="session", purpose="manual")
    other.acquire()
    try:
        rc = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    finally:
        other.release()
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_lock"


def test_trigger_runs_stages_in_order_and_advances_watermark(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert _modules(fr) == ["pipeline.triage_batch", "pipeline.composer",
                            "pipeline.composer", "pipeline.screen", "pipeline.gauntlet"]
    # composer appears twice: --dry-run preflight then the real run
    dry = [c for c in fr.calls if "-m" in c][1]; real = [c for c in fr.calls if "-m" in c][2]
    assert "--dry-run" in dry and "--dry-run" not in real
    assert "--asset-class" in real and real[real.index("--asset-class") + 1] == "fx"
    git_calls = [c for c in fr.calls if c and c[0] == "git"]
    assert all("-A" not in c for c in git_calls)     # scoped adds only, ever
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"]["fx"]["watermark"] == 30
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"
    assert status["items"]["asset_class"] == "fx"


def test_stage_failure_exits_nonzero_and_does_not_advance_watermark(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    fr = FakeRunner(codes={"pipeline.screen": 1})
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"] == {}                       # watermark NOT advanced
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["overall"] == "FAIL"
    assert status["items"]["failed_stage"] == "pipeline.screen"


def test_budget_cap_parks_before_spending(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    ledger = layer / "logs" / "budget_ledger.jsonl"
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ledger.write_text(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                  "usd": 20.0, "purpose": "triage",
                                  "model": "claude-sonnet-5"}) + "\n",
                      encoding="utf-8")
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []                            # nothing metered was started
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_budget"


def test_dry_run_reports_trigger_without_running(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner()
    rc = loop.run(["--once", "--dry-run", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "dry_run_would_fire"
    assert status["items"]["asset_class"] == "fx"
