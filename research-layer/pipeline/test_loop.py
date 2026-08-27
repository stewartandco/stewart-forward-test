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
from .test_pipeline import make_strategy, register_example_blocks


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
    """Records invocations; returns preset exit codes per python -m module,
    or per argv[0] for anything else (e.g. every git subcommand keys as
    "git"). argv[0] == sys.executable is checked FIRST: only a python
    invocation's "-m" is a module flag -- `git commit -q -m "..."` also
    contains the literal token "-m", but it means something else entirely,
    and must never be mistaken for a module invocation.
    call_kwargs parallels calls (same index) so a test can check e.g. cwd
    without disturbing every existing assertion that treats fr.calls as a
    plain list of argv lists."""
    def __init__(self, codes=None):
        self.calls = []
        self.call_kwargs = []
        self.codes = codes or {}

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.call_kwargs.append(dict(kw))
        if argv[0] == sys.executable and "-m" in argv:
            key = argv[argv.index("-m") + 1]
        else:
            key = argv[0]
        class R: pass
        r = R(); r.returncode = self.codes.get(key, 0)
        return r


def _modules(fr):
    return [c[c.index("-m") + 1] for c in fr.calls if c[0] == sys.executable and "-m" in c]


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
    # composer appears twice: --dry-run preflight then the real run.
    # Narrowed to python calls (argv[0] == sys.executable) -- a plain "-m"
    # in c would also catch `git commit -q -m ...`, and picking this by
    # fixed index would silently break if a git call ever moved earlier.
    py_calls = [c for c in fr.calls if c[0] == sys.executable and "-m" in c]
    dry = py_calls[1]; real = py_calls[2]
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
    assert status["items"]["chain_growth"] == "0"       # FakeRunner touches nothing
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


def test_composer_dry_run_cap_refusal_also_parks_not_fails(tmp_path):
    """composer.run() calls the metered propose_families BEFORE it ever
    branches on --dry-run, so the hard-cap refusal can surface on the
    DRY-RUN preflight call too -- the park remap must cover both composer
    invocations, not only the real run."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    ledger = layer / "logs" / "budget_ledger.jsonl"

    class _ComposerDryRunCapRefusalRunner(FakeRunner):
        def __call__(self, argv, **kw):
            if ("-m" in argv and argv[argv.index("-m") + 1] == "pipeline.composer"
                    and "--dry-run" in argv):
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

    fr = _ComposerDryRunCapRefusalRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert _modules(fr) == ["pipeline.triage_batch", "pipeline.composer"]  # never reached real run
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_budget"
    assert status["overall"] == "WARN"
    assert "budget_cap" in status["escalations"]


def test_composer_failure_in_batch_stop_band_without_cap_crossing_stays_stage_failed(tmp_path):
    """16-20 USD is the batch-stop band, not the hard cap: a genuine
    composer crash there must remain stage_failed, never get remapped to a
    budget park. The remap predicate is may_spend (>=20 hard cap), NOT
    may_start_batch (>=16 batch-stop) -- this pins that it is narrow."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    ledger = layer / "logs" / "budget_ledger.jsonl"

    class _ComposerCrashInBandRunner(FakeRunner):
        def __call__(self, argv, **kw):
            if ("-m" in argv and argv[argv.index("-m") + 1] == "pipeline.composer"
                    and "--dry-run" not in argv):
                # Spend $17 (below the hard cap) before crashing for some
                # unrelated reason -- no cap-crossing side effect.
                from datetime import datetime, timezone
                month = datetime.now(timezone.utc).strftime("%Y-%m")
                with ledger.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                        "usd": 17.0, "purpose": "composer",
                                        "model": "claude-sonnet-5",
                                        "agent": "pipeline"}) + "\n")
                self.calls.append(list(argv))
                class R: pass
                r = R(); r.returncode = 1
                return r
            return super().__call__(argv, **kw)

    fr = _ComposerCrashInBandRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "stage_failed"
    assert status["overall"] == "FAIL"
    assert status["items"]["failed_stage"] == "pipeline.composer"


def test_chain_invalid_before_triage_aborts_with_zero_spend(tmp_path):
    """A pre-existing invalid chain must abort BEFORE triage (zero metered
    spend), not merely be caught after the whole cycle has already spent
    against it."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    # verify_registry.py is invoked WITHOUT "-m" -- FakeRunner keys it by
    # argv[0] (sys.executable), which every "-m" stage call also starts
    # with but is keyed by module name instead, so this uniquely targets
    # the (first) verify step.
    fr = FakeRunner(codes={sys.executable: 1})
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    assert _modules(fr) == []                 # no -m stage ran -- caught before triage
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "chain_invalid"
    assert status["overall"] == "FAIL"
    assert "chain_invalid" in status["escalations"]


class _FailSecondVerifyRunner(FakeRunner):
    """The loop calls verify_registry.py TWICE (pre-triage and
    post-gauntlet), both without "-m" -- FakeRunner's simple keying can't
    tell them apart by argv alone, so this counts occurrences and fails
    only the second, letting the whole cycle run first."""
    def __init__(self, codes=None):
        super().__init__(codes)
        self._verify_calls = 0

    def __call__(self, argv, **kw):
        if "-m" not in argv:
            self._verify_calls += 1
            self.calls.append(list(argv))
            class R: pass
            r = R()
            r.returncode = 1 if self._verify_calls == 2 else 0
            return r
        return super().__call__(argv, **kw)


def test_chain_invalid_after_gauntlet_aborts_and_does_not_advance_watermark(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = _FailSecondVerifyRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    assert _modules(fr) == ["pipeline.triage_batch", "pipeline.composer",
                            "pipeline.composer", "pipeline.screen", "pipeline.gauntlet"]
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
    assert status["overall"] == "WARN"     # the one deferral that can persist


def _write_loop_lock(layer, pid, age_hours=0):
    """A loop.lock file with a deliberately chosen pid -- ChainLock.acquire()
    always writes os.getpid(), so a dead/bogus pid must be written directly."""
    path = layer / "logs" / "loop.lock"
    path.write_text(json.dumps({"holder": "loop-instance", "pid": pid,
                                "ts_utc": "2020-01-01T00:00:00+00:00",
                                "purpose": "manual"}), encoding="utf-8")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))
    return path


def test_instance_lock_dead_holder_is_broken_and_cycle_proceeds(tmp_path):
    """A hard kill / reboot / crash orphans loop.lock -- left unbroken, every
    later fire would defer forever at overall OK, a wedge that reads as
    healthy. A stale (>2h) lock whose recorded pid is provably not running
    must be broken on a SINGLE sighting and the cycle must proceed."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    lock_path = _write_loop_lock(layer, pid=4_000_000, age_hours=3)  # >2h, dead pid
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"   # proceeded past the dead lock
    assert not lock_path.exists()      # released again after our own cycle finished


def test_instance_lock_live_holder_defers_even_when_stale(tmp_path):
    """Age alone is never evidence of death (a slow but legitimate cycle) --
    a LIVE holder must defer unconditionally, however old the lock is."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    lock_path = _write_loop_lock(layer, pid=os.getpid(), age_hours=3)  # >2h, but alive (us)
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_instance"
    assert status["overall"] == "WARN"
    assert lock_path.exists()          # never broken -- a live holder wins regardless of age


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


def test_stale_foreign_lock_first_sighting_warns_and_defers(tmp_path, monkeypatch):
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
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
    fr2 = FakeRunner()
    rc2 = loop.run(["--once", "--layer", str(layer)], runner=fr2)
    assert rc2 == 0
    assert not lock_path.exists()                         # broken on the 2nd sighting
    status2 = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status2["items"]["outcome"] == "cycle_complete"  # cycle continued after the break
    run_id_2 = next(c[c.index("--run-id") + 1] for c in fr2.calls
                    if "-m" in c and "--run-id" in c and "--dry-run" not in c)

    # A LATER, separately-triggered successful cycle must mint a DIFFERENT
    # run_id than this one -- proves _make_run_id() is evaluated fresh per
    # invocation, never cached or reused across cycles.
    for i in range(25):
        cid = f"card2_{i:04d}"
        reg.register_card({"card_id": cid, "claim": f"c2_{i}", "quote": "q",
                           "topics": [], "tags": {"asset_classes": ["fx"]},
                           "review": {"status": "pending", "reject_reason": None},
                           "source": {}, "links": [], "credibility_tier": "practitioner"})
        reg.review_card(cid, "accepted", "coen")
    _seed_crypto_caught_up(layer, 55)      # keep crypto caught up, isolate fx again
    # Force a distinct wall-clock second for this run -- a fast test can
    # otherwise land both invocations in the SAME real second, which would
    # make this assertion flaky rather than a genuine seam check.
    from datetime import datetime as real_datetime
    class _LaterDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 8, 27, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(loop, "datetime", _LaterDatetime)
    fr3 = FakeRunner()
    rc3 = loop.run(["--once", "--layer", str(layer)], runner=fr3)
    assert rc3 == 0
    run_id_3 = next(c[c.index("--run-id") + 1] for c in fr3.calls
                    if "-m" in c and "--run-id" in c and "--dry-run" not in c)
    assert run_id_2 != run_id_3


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
        # Simulate the actual race: unlink the stale lock and let a NEW
        # writer acquire it as "intruder" in the exact gap between
        # is_stale() and break_stale() -- then behave exactly like the real
        # break_stale() would against that now-fresh lock: refuse.
        self.path.unlink(missing_ok=True)
        ChainLock(self.path.parent, holder="intruder", purpose="race").acquire()
        raise ChainLockHeld("refusing to break a fresh chain.lock")
    monkeypatch.setattr(ChainLock, "break_stale", _racy_break_stale)

    rc2 = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc2 == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_lock"
    assert status["items"]["lock_stale"] == "false"
    assert status["items"]["lock_holder"] == "intruder"


def test_collect_commit_paths_scoped(tmp_path):
    """The registry always comes along; artifacts/<sid> only for a strategy
    registered AFTER start_line whose bundle actually exists on disk."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=2)
    register_example_blocks(reg)
    registry_path = layer / "registry_log.jsonl"
    start_line = loop._entry_count(registry_path)

    spec1 = make_strategy(["card0000"])
    spec2 = make_strategy(["card0001"])
    reg.register_strategy(spec1)
    reg.register_strategy(spec2)
    sid1, sid2 = spec1["strategy_id"], spec2["strategy_id"]
    assert sid1 != sid2
    (layer / "artifacts" / sid1).mkdir(parents=True)
    # sid2 deliberately gets no artifacts dir -- its bundle never landed.

    paths = loop.collect_commit_paths(registry_path, start_line)
    assert paths == ["research-layer/registry_log.jsonl",
                     f"research-layer/artifacts/{sid1}"]
    assert f"research-layer/artifacts/{sid2}" not in paths


class _ComposerRegistersStrategyRunner(FakeRunner):
    """Simulates composer's real run actually registering a strategy and
    writing its artifact bundle. FakeRunner otherwise never touches the
    registry at all, so a zero-delta cycle is the ONLY thing a plain
    FakeRunner can produce -- and the restored preflight correctly makes NO
    commit for that case. This subclass exercises the real-delta path."""
    def __init__(self, reg, layer, codes=None):
        super().__init__(codes)
        self.reg = reg
        self.layer = layer
        self.sid = None

    def __call__(self, argv, **kw):
        r = super().__call__(argv, **kw)
        if ("-m" in argv and argv[argv.index("-m") + 1] == "pipeline.composer"
                and "--dry-run" not in argv):
            spec = make_strategy(["card0000"])
            self.reg.register_strategy(spec)
            self.sid = spec["strategy_id"]
            (self.layer / "artifacts" / self.sid).mkdir(parents=True)
        return r


def test_cycle_complete_commits_scoped(tmp_path):
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    register_example_blocks(reg)
    fr = _ComposerRegistersStrategyRunner(reg, layer)
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.sid is not None

    git_idx = [i for i, c in enumerate(fr.calls) if c and c[0] == "git"]
    assert len(git_idx) == 3                    # diff (preflight), add, commit
    i_diff, i_add, i_commit = git_idx
    assert i_diff < i_add < i_commit             # in order
    diff_call, add_call, commit_call = (fr.calls[i_diff], fr.calls[i_add],
                                        fr.calls[i_commit])
    assert "diff" in diff_call and "--quiet" in diff_call
    assert "add" in add_call and "commit" in commit_call
    reg_path = "research-layer/registry_log.jsonl"
    art_path = f"research-layer/artifacts/{fr.sid}"
    assert reg_path in add_call and art_path in add_call
    assert reg_path in commit_call and art_path in commit_call  # commit is scoped too
    assert not any("-A" in c for c in (diff_call, add_call, commit_call))   # never -A

    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    run_id = status["items"]["run_id"]
    assert any(run_id in a for a in commit_call)

    for i in (i_diff, i_add, i_commit):
        assert fr.call_kwargs[i].get("cwd") == str(layer.parent)


def test_zero_delta_cycle_makes_no_commit_and_no_warning(tmp_path, capsys):
    """A plain FakeRunner never touches the registry, so nothing changed
    this cycle: the diff preflight must be clean AND no new artifact dirs
    exist, which must skip the commit entirely -- no add, no commit, and no
    spurious WARNING (a git failure warns; "nothing to do" must not)."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    git_calls = [c for c in fr.calls if c and c[0] == "git"]
    assert len(git_calls) == 1                  # only the diff preflight ran
    assert git_calls[0][1] == "diff"
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out
    assert "committed chain delta" not in captured.out


def test_stage_failure_does_not_commit(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner(codes={"pipeline.screen": 1})
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    assert not any(c and c[0] == "git" for c in fr.calls)


def test_git_add_failure_is_loud_but_cycle_still_succeeds(tmp_path, capsys):
    """codes={"git": 1} hits every git subcommand: diff -> 1 (read as "there
    ARE changes", not an error -- proceeds to add), then add -> 1, a real
    failure. Commit is never reached."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner(codes={"git": 1})
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0                              # a git failure never fails the cycle
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"
    git_calls = [c for c in fr.calls if c and c[0] == "git"]
    assert len(git_calls) == 2                  # diff, add -- commit never attempted
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "git add failed" in captured.out


def test_git_commit_failure_is_loud_but_cycle_still_succeeds(tmp_path, capsys):
    """The OTHER git-failure branch: add succeeds, the final `git commit`
    itself fails. Needs a delta (an artifact bundle) so the preflight does
    not just skip the whole thing first."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    register_example_blocks(reg)

    class _CommitFailsRunner(_ComposerRegistersStrategyRunner):
        def __call__(self, argv, **kw):
            if argv and argv[0] == "git" and argv[1] == "commit":
                self.calls.append(list(argv))
                self.call_kwargs.append(dict(kw))
                class R: pass
                r = R(); r.returncode = 1
                return r
            return super().__call__(argv, **kw)

    fr = _CommitFailsRunner(reg, layer)
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0                              # a git failure never fails the cycle
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"
    git_calls = [c for c in fr.calls if c and c[0] == "git"]
    assert len(git_calls) == 3                  # diff, add (succeeded), commit (failed)
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "git commit failed" in captured.out
    assert "committed chain delta" not in captured.out   # the failed commit never prints success


def test_break_stale_race_lock_vanished_proceeds_with_cycle(tmp_path, monkeypatch):
    """If the stale lock's holder releases it entirely between our
    is_stale() read and break_stale() -- so break_stale() still raises, but
    by the time we re-check, NOTHING is there -- proceed with the cycle;
    there is no lock left to defer to."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    lock_path = _make_stale_foreign_lock(layer)

    rc1 = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc1 == 0     # first sighting

    def _vanishing_break_stale(self):
        self.path.unlink(missing_ok=True)     # holder released it entirely
        raise ChainLockHeld("refusing to break a fresh chain.lock")
    monkeypatch.setattr(ChainLock, "break_stale", _vanishing_break_stale)

    rc2 = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc2 == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"   # proceeded, nothing to defer to
    assert not lock_path.exists()
