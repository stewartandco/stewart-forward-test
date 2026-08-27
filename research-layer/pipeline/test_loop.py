"""Offline tests for the pipeline loop orchestrator (no network, no API).

Run: python -m pytest pipeline/test_loop.py -q
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from .chainlock import ChainLock, ChainLockHeld
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
    assert status["items"]["routable_fx"] == "3"
    assert "routable_crypto" in status["items"]
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
    assert status["items"]["lock_stale"] == "false"


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
    screen_call = next(c for c in fr.calls
                       if "-m" in c and c[c.index("-m") + 1] == "pipeline.screen")
    gauntlet_call = next(c for c in fr.calls
                         if "-m" in c and c[c.index("-m") + 1] == "pipeline.gauntlet")
    assert "--registry" in screen_call and "--data-dir" in screen_call
    assert "--registry" in gauntlet_call and "--data-dir" in gauntlet_call
    git_calls = [c for c in fr.calls if c and c[0] == "git"]
    assert all("-A" not in c for c in git_calls)     # scoped adds only, ever
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"]["fx"]["watermark"] == 30
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"
    assert status["items"]["asset_class"] == "fx"
    assert status["items"]["chain_entries_added"] == "0"       # FakeRunner touches nothing
    assert status["items"]["run_id"].endswith("-loop-fx")


def test_stage_failure_exits_nonzero_and_does_not_advance_watermark(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner(codes={"pipeline.screen": 1})
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert "fx" not in st.get("classes", {})          # watermark NOT advanced
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["overall"] == "FAIL"
    assert status["items"]["failed_stage"] == "pipeline.screen"
    assert status["items"]["asset_class"] == "fx"
    assert status["items"]["exit_code"] == "1"
    assert not (layer / "logs" / "chain.lock").exists()   # finally: release ran


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
    assert status["overall"] == "WARN"
    assert "budget_cap" in status["escalations"]      # at (not merely near) the hard cap


def test_budget_batch_stop_zone_defers_without_cap_escalation(tmp_path):
    """80-100% of cap parks the batch (WARN) but is NOT the hard-cap case --
    distinct from test_budget_cap_parks_before_spending's >=cap escalation."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    ledger = layer / "logs" / "budget_ledger.jsonl"
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ledger.write_text(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                  "usd": 17.0, "purpose": "triage",
                                  "model": "claude-sonnet-5"}) + "\n",
                      encoding="utf-8")
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_budget"
    assert status["overall"] == "WARN"
    assert status["escalations"] == []                # below the hard cap: no push


def test_run_id_is_unique_per_invocation(monkeypatch):
    """Same-day, same-class retries must not collide: composer's
    sibling_group_id = f"{family}-{run_id}" would otherwise merge a retry's
    new specs into an earlier (aborted) run's sibling group."""
    from datetime import datetime as real_datetime, timezone as tz

    times = iter([
        real_datetime(2026, 8, 27, 10, 30, 0, tzinfo=tz.utc),
        real_datetime(2026, 8, 27, 10, 30, 1, tzinfo=tz.utc),
    ])

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return next(times)

    monkeypatch.setattr(loop, "datetime", _FakeDatetime)
    id1 = loop._make_run_id("fx")
    id2 = loop._make_run_id("fx")
    assert id1 != id2
    assert id1 == "2026-08-27-103000-loop-fx"
    assert id2 == "2026-08-27-103001-loop-fx"


def test_budget_recheck_after_triage_parks_before_composer(tmp_path):
    """Triage itself may spend against the cap; the composer batch must not
    start on a stale pre-triage budget read."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    ledger = layer / "logs" / "budget_ledger.jsonl"

    class _TriageSpendsRunner(FakeRunner):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if "-m" in argv and argv[argv.index("-m") + 1] == "pipeline.triage_batch":
                from datetime import datetime, timezone
                month = datetime.now(timezone.utc).strftime("%Y-%m")
                with ledger.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                        "usd": 17.0, "purpose": "triage",
                                        "model": "claude-sonnet-5",
                                        "agent": "pipeline"}) + "\n")
            return r

    fr = _TriageSpendsRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert _modules(fr) == ["pipeline.triage_batch"]   # stopped before composer
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_budget"
    assert status["overall"] == "WARN"
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert "fx" not in st.get("classes", {})           # watermark NOT advanced


def test_composer_cap_refusal_parks_not_fails(tmp_path):
    """composer's own budget guard (meter.can_spend(), the hard cap) raises
    SystemExit -- rc != 0 that MEANS the ledger already parked this. That
    must map to deferred_budget, never stage_failed."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    ledger = layer / "logs" / "budget_ledger.jsonl"

    class _ComposerCapRefusalRunner(FakeRunner):
        def __call__(self, argv, **kw):
            if ("-m" in argv and argv[argv.index("-m") + 1] == "pipeline.composer"
                    and "--dry-run" not in argv):
                # The cap was crossed (by a concurrent writer, in this test)
                # in the instant before composer's own guard fires.
                from datetime import datetime, timezone
                month = datetime.now(timezone.utc).strftime("%Y-%m")
                with ledger.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                        "usd": 20.0, "purpose": "composer",
                                        "model": "claude-sonnet-5",
                                        "agent": "pipeline"}) + "\n")
                self.calls.append(list(argv))
                class R: pass
                r = R(); r.returncode = 1
                return r
            return super().__call__(argv, **kw)

    fr = _ComposerCapRefusalRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_budget"
    assert status["overall"] == "WARN"
    assert "budget_cap" in status["escalations"]
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert "fx" not in st.get("classes", {})           # watermark NOT advanced


def test_chain_invalid_after_gauntlet_aborts_and_does_not_advance_watermark(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    # verify_registry.py is invoked WITHOUT "-m" -- FakeRunner keys it by
    # argv[0] (sys.executable), which every "-m" stage call also starts
    # with but is keyed by module name instead, so this uniquely targets
    # only the verify step.
    fr = FakeRunner(codes={sys.executable: 1})
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "chain_invalid"
    assert status["overall"] == "FAIL"
    assert "chain_invalid" in status["escalations"]
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert "fx" not in st.get("classes", {})           # watermark NOT advanced


def test_instance_lock_defers_concurrent_run(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=3)
    other = ChainLock(layer / "logs", holder="loop-instance", purpose="manual",
                      name="loop.lock")
    other.acquire()
    try:
        rc = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    finally:
        other.release()
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_instance"


def test_loop_crashes_are_caught_and_reported(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)

    class _CrashingRunner(FakeRunner):
        def __call__(self, argv, **kw):
            if "-m" in argv and argv[argv.index("-m") + 1] == "pipeline.triage_batch":
                raise RuntimeError("boom")
            return super().__call__(argv, **kw)

    rc = loop.run(["--once", "--layer", str(layer)], runner=_CrashingRunner())
    assert rc == 1
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "loop_crashed"
    assert status["overall"] == "FAIL"
    assert "run_aborted" in status["escalations"]
    assert "boom" in status["items"]["error"]
    assert not (layer / "logs" / "loop.lock").exists()   # instance lock released


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


def _make_stale_foreign_lock(layer):
    """Foreign (non-loop) chain.lock, aged past ChainLock's default
    STALE_AFTER_S (3h) -- mirrors test_chainlock.test_stale_detection_and_break's
    os.utime trick, but loop.py's probe uses the class default stale_after_s,
    so the age must clear 3h, not the 1s a lower-stale_after_s test would need."""
    lock_path = layer / "logs" / "chain.lock"
    other = ChainLock(layer / "logs", holder="session", purpose="manual")
    other.acquire()
    old = time.time() - (4 * 3600)   # 4h > STALE_AFTER_S's 3h
    os.utime(lock_path, (old, old))
    return lock_path


def test_stale_foreign_lock_first_sighting_warns_and_defers(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    lock_path = _make_stale_foreign_lock(layer)

    rc = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_lock"
    assert status["items"]["lock_stale"] == "true"
    assert status["overall"] == "WARN"
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["stale_lock"] is not None                 # strike persisted
    assert lock_path.exists()                            # first strike never breaks it

    # Second run against the SAME (still-aged) lock file: second strike.
    rc2 = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc2 == 0
    assert not lock_path.exists()                         # broken on the 2nd sighting
    status2 = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status2["items"]["outcome"] == "cycle_complete"  # cycle continued after the break


class _ForeignAcquireOnDryRunRunner(FakeRunner):
    """Simulates another writer grabbing chain.lock during the loop's one
    lock-free stage (composer --dry-run), racing the loop's next acquire
    (composer's real run)."""
    def __init__(self, layer, codes=None):
        super().__init__(codes)
        self.layer = layer
        self.foreign_lock = None

    def __call__(self, argv, **kw):
        r = super().__call__(argv, **kw)
        if ("-m" in argv and argv[argv.index("-m") + 1] == "pipeline.composer"
                and "--dry-run" in argv):
            self.foreign_lock = ChainLock(self.layer / "logs", holder="intruder",
                                          purpose="race")
            self.foreign_lock.acquire()
        return r


def test_midcycle_foreign_acquire_defers(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = _ForeignAcquireOnDryRunRunner(layer)
    try:
        rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    finally:
        if fr.foreign_lock is not None:
            fr.foreign_lock.release()

    assert rc == 0
    assert _modules(fr) == ["pipeline.triage_batch", "pipeline.composer"]  # stops after dry-run
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_lock"
    assert status["items"]["at_stage"] == "pipeline.composer"
    assert status["items"]["lock_stale"] == "false"
    assert status["overall"] == "WARN"
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert "fx" not in st.get("classes", {})            # watermark NOT advanced


def test_break_stale_race_treated_as_fresh_lock_defer(tmp_path, monkeypatch):
    """Between the loop's is_stale() read and its break_stale() call, the
    stale holder can release and a NEW writer can acquire -- break_stale()
    then refuses (it re-checks is_stale() itself) and the loop must treat
    that as a plain fresh-lock defer, not crash or wrongly claim a break."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _make_stale_foreign_lock(layer)

    rc1 = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc1 == 0     # first sighting: unchanged behaviour

    def _racy_break_stale(self):
        raise ChainLockHeld("refusing to break a fresh chain.lock")
    monkeypatch.setattr(ChainLock, "break_stale", _racy_break_stale)

    rc2 = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc2 == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_lock"
    assert status["items"]["lock_stale"] == "false"
