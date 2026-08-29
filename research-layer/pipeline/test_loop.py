"""Offline tests for the pipeline loop orchestrator (no network, no API).

Run: python -m pytest pipeline/test_loop.py -q

DEADLOCK REGRESSION PIN: test_pending_cards_alone_fire_a_cycle is the guard
against re-introducing the 2026-08-28 trigger deadlock (the trigger counting
ACCEPTED cards only, which nothing outside a cycle can ever increase). Its
failure mode is silent -- the loop reports no_trigger at exit 0, which reads
as healthy -- so that test is the only thing standing between a bad merge
resolution and a dead pipeline. Do not delete or weaken it.
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
from . import cells, loop, loop_state
from .test_pipeline import make_strategy, register_example_blocks


@pytest.fixture(autouse=True)
def _no_real_schtasks(monkeypatch):
    """Stub the live-task window reader for EVERY test in this module.

    loop.run() reads the scheduled task's ExecutionTimeLimit at startup, which
    without this spawns a real `schtasks` subprocess on every one of the ~50
    loop.run() calls in this file. That is slow, and worse it makes the suite
    MACHINE-DEPENDENT: on this box the task exists at PT1H so the WARN prints
    into capsys, while on a box where it is absent (CI, a fresh clone) or
    already fixed at PT4H nothing prints. Tests must not care.

    None is the "cannot determine" answer, i.e. the silent no-op path. The
    warning itself is covered directly, with explicit stubs, by the
    test_*_task_window_* tests below."""
    monkeypatch.setattr(loop, "_live_task_window_s", lambda *a, **k: None)


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


def _add_cards(reg, n, *, status, asset_classes, prefix, reject_reason=None):
    """Append n cards in a given review state with a given asset_classes tag.

    Separate from _mk_layer (whose signature every existing test depends on)
    so the trigger-basis tests can build PENDING and REJECTED populations --
    the two states _mk_layer cannot express, and the two the triggerable
    count has to treat differently."""
    for i in range(n):
        cid = f"{prefix}{i:04d}"
        reg.register_card({"card_id": cid, "claim": f"{prefix}{i}", "quote": "q",
                           "topics": [], "tags": {"asset_classes": list(asset_classes)},
                           "review": {"status": "pending", "reject_reason": None},
                           "source": {}, "links": [], "credibility_tier": "practitioner"})
        if status != "pending":
            reg.review_card(cid, status, "coen", reject_reason)


def _seed_all_classes_caught_up(layer, registry_path):
    """Seed every LIVE_CLASSES watermark to the CURRENT triggerable count, so
    a test's subsequently-added cards are the ONLY thing that can fire a
    class. Mirrors what --seed-watermarks does at activation."""
    state_path = layer / "logs" / "loop_state.json"
    state = loop_state.load(state_path)
    counts = loop._triggerable_counts(Registry(registry_path))
    for cls in cells.LIVE_CLASSES:
        loop_state.record_generation(state, cls, run_id="seed",
                                     watermark_count=counts.get(cls, 0),
                                     ts_utc="2020-01-01T00:00:00+00:00")
    loop_state.save(state_path, state)


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
                                 watermark_count=count, ts_utc="2020-01-01T00:00:00+00:00")
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
    triage_call = next(c for c in fr.calls
                       if "-m" in c and c[c.index("-m") + 1] == "pipeline.triage_batch")
    # Pinned to the constant, not a literal: the limit is sized against the
    # scheduled task's ExecutionTimeLimit (see loop.TRIAGE_LIMIT), so a change
    # here must be a deliberate edit of that constant, and the window-fit test
    # below is what actually guards the number.
    assert ("--limit" in triage_call
            and triage_call[triage_call.index("--limit") + 1] == str(loop.TRIAGE_LIMIT))
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
    assert "watermark" not in st.get("classes", {}).get("fx", {})          # watermark NOT advanced
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
    assert "watermark" not in st.get("classes", {}).get("fx", {})           # watermark NOT advanced


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
    assert "watermark" not in st.get("classes", {}).get("fx", {})           # watermark NOT advanced


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
    assert "watermark" not in st.get("classes", {}).get("fx", {})           # watermark NOT advanced


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


def _make_stale_dead_pid_chain_lock(layer, pid=4_000_000):
    """A stale (>3h) chain.lock whose recorded holder pid does NOT exist --
    unlike _make_stale_foreign_lock (which uses ChainLock.acquire() and so
    always records our OWN, alive, pid), this must be written directly."""
    lock_path = layer / "logs" / "chain.lock"
    lock_path.write_text(json.dumps({"holder": "session", "pid": pid,
                                     "ts_utc": "2020-01-01T00:00:00+00:00",
                                     "purpose": "manual"}), encoding="utf-8")
    old = time.time() - (4 * 3600)   # 4h > STALE_AFTER_S's 3h
    os.utime(lock_path, (old, old))
    return lock_path


def test_stale_dead_pid_chain_lock_breaks_on_first_sighting_and_proceeds(tmp_path):
    """The dead-pid fast path: a hard-killed loop or crashed writer orphans
    chain.lock with a provably-dead pid. Unlike an ambiguous stale holder,
    that must break on the FIRST sighting (not wait for two-strike), or
    scanner + quarantine + the loop itself stay frozen for up to 3h plus two
    scheduled fires."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    lock_path = _make_stale_dead_pid_chain_lock(layer)
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert not lock_path.exists()          # broken on the FIRST sighting
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"   # proceeded, never deferred
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st.get("stale_lock") is None    # fast path never records a strike


def test_stale_alive_pid_chain_lock_still_first_sighting_defers(tmp_path):
    """A stale chain.lock whose holder pid IS alive (our own, here) must
    stay on the existing, more cautious two-strike rule -- age alone is
    never evidence of death for an ambiguous holder."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    lock_path = _make_stale_foreign_lock(layer)   # writes os.getpid() -- alive
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_lock"
    assert status["items"]["lock_stale"] == "true"
    assert status["overall"] == "WARN"
    assert lock_path.exists()              # first sighting never breaks an alive holder


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
    assert "watermark" not in st.get("classes", {}).get("fx", {})            # watermark NOT advanced


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


def test_seed_watermarks_seeds_all_classes_and_prevents_immediate_trigger(tmp_path):
    """ACTIVATION step: --seed-watermarks must set EVERY LIVE_CLASSES
    watermark to the current TRIGGERABLE (accepted+pending) count -- the same
    basis the trigger reads, or the seed would not actually suppress the
    first fire -- not just the class with cards seeded by the fixture. A
    subsequent --once against the unchanged corpus must then report
    no_trigger (the whole point: a fresh state file must not fire a
    whole-corpus generation).

    The fixture deliberately carries PENDING cards as well as accepted ones:
    with an all-accepted corpus the two bases coincide numerically and this
    test would pass under the old accepted-only seed too."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    registry_path = layer / "registry_log.jsonl"
    _add_cards(reg, 12, status="pending", asset_classes=["crypto"], prefix="pend")
    expected = loop._triggerable_counts(Registry(registry_path))
    assert set(expected) == set(cells.LIVE_CLASSES)   # sanity: covers all five
    # The bases must actually differ here, or this test proves nothing.
    assert expected["crypto"] != loop._routable_counts(Registry(registry_path))["crypto"]

    rc = loop.run(["--seed-watermarks", "--layer", str(layer)], runner=FakeRunner())
    assert rc == 0

    state_path = layer / "logs" / "loop_state.json"
    st = json.loads(state_path.read_text(encoding="utf-8"))
    for cls in cells.LIVE_CLASSES:
        assert st["classes"][cls]["watermark"] == expected[cls]
        assert st["classes"][cls]["last_run_id"] == "seed"

    # No stage ran and no status file was written by the seed step itself.
    assert not (layer / "logs" / "pipeline_status.json").exists()

    # A subsequent --once against the SAME, unchanged corpus must not fire
    # for any class -- every watermark now equals its current count.
    fr = FakeRunner()
    rc2 = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc2 == 0
    assert fr.calls == []
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_trigger"


def test_seed_watermarks_and_once_together_is_argparse_error(tmp_path):
    """--seed-watermarks and --once are mutually exclusive, enforced by
    argparse itself (a mutually exclusive group), not by hand-rolled
    validation -- both together must exit via SystemExit before either mode
    ever runs."""
    with pytest.raises(SystemExit):
        loop.run(["--once", "--seed-watermarks", "--layer", str(tmp_path)],
                 runner=FakeRunner())


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


# -- trigger basis: accepted + pending (the 2026-08-29 deadlock fix) ---------
# Regression cover for a LIVE defect: the trigger used to count ACCEPTED
# cards only, but the only thing that ever accepts a card is the D31 triage
# panel, which runs INSIDE a cycle -- after the trigger decision. Nothing
# else triages (no scheduled task runs pipeline.triage_batch; the resident
# scanner only registers PENDING cards). So the accepted count could never
# move between fires: routable == watermark forever, every fire honestly
# reported no_trigger, and the pending backlog never drained. Two live fires
# (2026-08-28 15:30 and 21:30) both no_trigger, exit 0, with 539 pending
# cards on the chain.


def test_pending_cards_alone_fire_a_cycle(tmp_path):
    """*** THE DEADLOCK REGRESSION PIN -- DO NOT DELETE OR WEAKEN. ***

    30 PENDING crypto-tagged cards over the watermark and ZERO new accepted
    cards must START a cycle. Under the accepted-only trigger basis this was
    the exact live wedge: the loop reported no_trigger at exit 0 on every
    fire, which reads as healthy, which is how it survived unnoticed through
    2026-08-28.

    If this test ever fails with outcome == "no_trigger", the trigger has
    been reverted to _routable_counts (accepted-only) and the loop is dead
    again. See loop._triggerable_counts.

    Pins the EXACT expected outcome. With a FakeRunner triage is a no-op, so
    the no_new_accepted_cards guard correctly stops the cycle before the
    metered composer -- that specific outcome is what a healthy trigger
    produces here, and asserting it is strictly stronger than asserting
    "not no_trigger" (which every other outcome, including a crash, would
    also satisfy). The full happy path is covered by
    test_a_real_accept_still_proceeds_to_the_composer."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="pend")

    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    # THE pin. The deadlock's signature was no_trigger; the healthy outcome
    # for this fixture is exactly no_new_accepted_cards (a class WAS selected,
    # a cycle began, triage ran, and the guard then stopped it because the
    # FakeRunner accepted nothing).
    assert status["items"]["outcome"] == "no_new_accepted_cards", (
        f"DEADLOCK REGRESSION or changed cycle shape: expected "
        f"no_new_accepted_cards, got {status['items']['outcome']!r}. If this "
        f"says 'no_trigger', pending cards no longer fire a cycle and the "
        f"trigger has been reverted to the accepted-only basis.")
    assert status["items"]["asset_class"] == "crypto"
    # Triage is the FIRST stage: the pending cards that fired this cycle are
    # exactly the cards the cycle then triages.
    assert _modules(fr)[0] == "pipeline.triage_batch"
    # ...and the honest accepted-only figure is still zero. The cycle fired
    # on work-to-do, not on a routable count that does not exist yet.
    assert loop._routable_counts(Registry(registry_path))["crypto"] == 0


def test_rejected_cards_never_count_toward_the_trigger(tmp_path):
    """A rejected card is not work a cycle could act on -- it is settled.
    30 rejected cards must leave every class under threshold; the same 30 as
    pending would fire."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="rejected", asset_classes=["crypto"],
               prefix="rej", reject_reason="off_topic")

    counts = loop._triggerable_counts(Registry(registry_path))
    assert counts["crypto"] == 0                 # rejected are invisible to the trigger
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []                        # no stage ran
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_trigger"


def test_post_cycle_watermark_is_recorded_on_the_triggerable_basis(tmp_path):
    """The watermark MUST be comparable to what the trigger measures. If the
    trigger reads accepted+pending but the watermark recorded accepted-only,
    the delta would stay >= threshold forever and the loop would re-fire on
    every scheduled run against an unchanged corpus."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="pend")

    rc = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc == 0
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    # FakeRunner never actually triages, so the corpus is unchanged: the
    # triggerable count (30) is what must be banked, NOT the accepted count (0).
    assert st["classes"]["crypto"]["watermark"] == 30

    # An immediate second fire against the SAME corpus must NOT re-trigger.
    fr2 = FakeRunner()
    rc2 = loop.run(["--once", "--dry-run", "--layer", str(layer)], runner=fr2)
    assert rc2 == 0
    assert fr2.calls == []
    status2 = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status2["items"]["outcome"] == "no_trigger"


def test_status_reports_both_routable_and_triggerable_counts(tmp_path):
    """The digest must stay honest about BOTH numbers: routable_<cls> is the
    accepted-only figure a composer could consume today; triggerable_<cls> is
    the accepted+pending figure that actually fired (or did not fire) the
    cycle. Reporting only one would either hide the backlog or overstate the
    routable corpus."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=3)      # 3 accepted, fx-tagged
    registry_path = layer / "registry_log.jsonl"
    _add_cards(reg, 4, status="pending", asset_classes=["crypto"], prefix="pend")

    rc = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc == 0
    items = json.loads((layer / "logs" / "pipeline_status.json")
                       .read_text(encoding="utf-8"))["items"]
    assert items["outcome"] == "no_trigger"              # 7 and 3 are both < 25
    for cls in cells.LIVE_CLASSES:
        assert f"routable_{cls}" in items and f"triggerable_{cls}" in items
    # crypto is unrestricted: 3 accepted (fx-tagged) route to it, plus 4 pending.
    assert items["routable_crypto"] == "3"
    assert items["triggerable_crypto"] == "7"
    # fx: the 3 accepted fx cards route; the crypto-tagged pending ones do not.
    assert items["routable_fx"] == "3"
    assert items["triggerable_fx"] == "3"


def test_triage_limit_fits_the_scheduled_execution_window():
    """Gate 2 (2026-08-29). The triage limit is not a free knob: it must fit
    the scheduled task's ExecutionTimeLimit alongside the rest of the cycle,
    or Windows hard-kills the cycle mid-flight, the watermark never advances,
    and the class re-fires forever paying full freight every time.

    Measured: 3.85 s per reviewer call, PANEL_SIZE 3 reviewers per card. The
    rest of the cycle (composer --dry-run + real run, screen, gauntlet) is
    budgeted at 90 min worst case. The task XML is being moved to PT4H.

    This is arithmetic, not a mock -- it fails the moment someone raises
    TRIAGE_LIMIT without re-checking the window."""
    from .triage_batch import PANEL_SIZE
    seconds_per_call = 3.85
    triage_minutes = loop.TRIAGE_LIMIT * PANEL_SIZE * seconds_per_call / 60
    rest_of_cycle_minutes = 90
    window_minutes = 4 * 60          # PT4H, quant/tasks/xml/25_PipelineLoop.xml

    assert triage_minutes < 15, (
        f"triage alone is {triage_minutes:.1f} min at limit "
        f"{loop.TRIAGE_LIMIT}; keep it short enough that a slow panel cannot "
        f"eat the window")
    assert triage_minutes + rest_of_cycle_minutes < window_minutes, (
        f"cycle needs {triage_minutes + rest_of_cycle_minutes:.1f} min but the "
        f"task allows {window_minutes} min -- raise ExecutionTimeLimit in "
        f"quant/tasks/xml/25_PipelineLoop.xml BEFORE raising TRIAGE_LIMIT")
    # And the old value must not silently come back: 200 cards is ~38.5 min of
    # triage, which did not fit even the XML's PT2H once the rest ran.
    assert loop.TRIAGE_LIMIT <= 40


# -- item 3: no_new_accepted_cards -------------------------------------------

def test_cycle_stops_before_composer_when_triage_accepted_nothing(tmp_path):
    """Spec Decision 2: "no new information, no new trials". The
    accepted+pending trigger basis lets a class fire on pending cards that
    triage then rejects or escalates wholesale -- leaving the composer with
    the SAME routable corpus it already swept, for two metered calls. Stop at
    zero further cost."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="pend")

    fr = FakeRunner()          # FakeRunner never really triages -> accepted stays 0
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    mods = _modules(fr)
    assert "pipeline.triage_batch" in mods          # triage DID run
    assert "pipeline.composer" not in mods          # but nothing metered after it
    assert "pipeline.screen" not in mods
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_new_accepted_cards"
    assert status["overall"] == "OK"                 # routine, not a defect


def test_watermark_still_advances_when_triage_accepted_nothing(tmp_path):
    """The cards WERE seen and dispositioned, so the watermark must move even
    though no generation ran -- otherwise the same class re-fires every tick
    on the same already-triaged cards."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="pend")

    loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"]["crypto"]["watermark"] == 30

    fr2 = FakeRunner()
    loop.run(["--once", "--layer", str(layer)], runner=fr2)
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_trigger"     # did not re-fire


def test_a_real_accept_still_proceeds_to_the_composer(tmp_path):
    """Guard must not swallow the happy path: when triage genuinely accepts a
    card the routable count rises and the cycle continues."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="pend")

    class _AcceptingRunner(FakeRunner):
        """Simulates triage actually accepting a card, the way a real
        pipeline.triage_batch --apply run chains card_reviewed entries."""
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if "-m" in argv and argv[argv.index("-m") + 1] == "pipeline.triage_batch":
                reg.review_card("pend0000", "accepted", "auto-d31")
            return r

    fr = _AcceptingRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert "pipeline.composer" in _modules(fr)
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"


# -- item 4: gauntlet-orphan pre-flight ---------------------------------------

def test_gauntlet_orphan_aborts_at_zero_metered_cost(tmp_path):
    """gauntlet.py refuses (exit 1) when a strategy sits in state 'gauntlet'
    with a gauntlet verdict already chained. Without a pre-flight the loop
    pays for triage AND both composer calls before reaching that guaranteed
    failure -- ~$12.60/day at 3 fires. Detect it next to the existing
    pre-spend chain verify instead."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    reg.append("strategy_registered", {"strategy_id": "orphan-sid"})
    reg.append("state_change", {"strategy_id": "orphan-sid", "from": "proposed",
                                "to": "gauntlet"})
    reg.append("verdict", {"strategy_id": "orphan-sid", "stage": "gauntlet",
                           "passed": False, "metrics": {}})

    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    assert _modules(fr) == []            # NOTHING metered ran
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "gauntlet_orphan"
    assert status["overall"] == "FAIL"
    assert "orphan-sid" in status["items"]["orphans"]


def test_a_clean_chain_has_no_orphans_and_proceeds(tmp_path):
    """A strategy that reached a verdict AND moved out of 'gauntlet' state is
    the normal completed case, not an orphan."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    reg.append("strategy_registered", {"strategy_id": "done-sid"})
    reg.append("state_change", {"strategy_id": "done-sid", "from": "proposed",
                                "to": "gauntlet"})
    reg.append("verdict", {"strategy_id": "done-sid", "stage": "gauntlet",
                           "passed": True, "metrics": {}})
    reg.append("state_change", {"strategy_id": "done-sid", "from": "gauntlet",
                                "to": "quarantine"})

    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"


# -- item 5: budget park must not starve the round-robin ----------------------

def test_budget_park_records_state_and_rotates_the_class_to_the_back(tmp_path):
    """A parked cycle banks no watermark (correctly - no work was done), so
    pick_class would re-select the same class on every fire and the
    round-robin stops dead. The park timestamp rotates it to the back so
    another over-threshold class gets the next fire, WITHOUT pretending its
    work was done."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    # Two classes over threshold: crypto-tagged and fx-tagged pending cards.
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="cry")
    _add_cards(reg, 30, status="pending", asset_classes=["fx"], prefix="fxc")

    ledger = layer / "logs" / "budget_ledger.jsonl"
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ledger.write_text(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                  "usd": 17.0, "purpose": "triage",
                                  "model": "claude-sonnet-5"}) + "\n",
                      encoding="utf-8")

    rc = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_budget"
    first = status["items"]["asset_class"]
    assert status["items"]["budget_state"] == "batch_stop"
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"][first]["last_park_ts_utc"] is not None
    # Watermark NOT banked: no work was done for it.
    assert st["classes"][first]["watermark"] == 0

    # Next fire, still parked: a DIFFERENT over-threshold class is selected.
    rc2 = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert rc2 == 0
    status2 = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status2["items"]["asset_class"] != first


def test_budget_state_item_distinguishes_cap_from_batch_stop(tmp_path):
    """The digest must be able to tell "parked at the 80% batch-stop line"
    from "parked at the hard cap" without reading escalations."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    ledger = layer / "logs" / "budget_ledger.jsonl"
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ledger.write_text(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                  "usd": 20.0, "purpose": "triage",
                                  "model": "claude-sonnet-5"}) + "\n",
                      encoding="utf-8")
    loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["budget_state"] == "hard_cap"


def test_both_count_series_appear_on_cycle_complete_and_failure_paths(tmp_path):
    """CLAUDE.md documents routable_<cls> and triggerable_<cls> as always
    present. They used to be written only on the no_trigger and dry_run
    paths, so a digest could not see an undrained pending backlog on any
    cycle that actually ran -- the exact case where it matters most."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _add_cards(reg, 4, status="pending", asset_classes=["crypto"], prefix="pend")

    def _items(argv, runner):
        loop.run(argv, runner=runner)
        return json.loads((layer / "logs" / "pipeline_status.json")
                          .read_text(encoding="utf-8"))["items"]

    items = _items(["--once", "--layer", str(layer)], FakeRunner())
    assert items["outcome"] == "cycle_complete"
    for cls in cells.LIVE_CLASSES:
        assert f"routable_{cls}" in items and f"triggerable_{cls}" in items
    assert items["routable_crypto"] == "30" and items["triggerable_crypto"] == "34"


def test_both_count_series_appear_on_a_stage_failure(tmp_path):
    """Same guarantee on the failure path, where the digest most needs the
    context: a FAIL line that cannot say how big the backlog was is a report
    nobody can act on."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _add_cards(reg, 4, status="pending", asset_classes=["crypto"], prefix="pend")

    rc = loop.run(["--once", "--layer", str(layer)],
                  runner=FakeRunner(codes={"pipeline.screen": 1}))
    assert rc == 1
    items = json.loads((layer / "logs" / "pipeline_status.json")
                       .read_text(encoding="utf-8"))["items"]
    assert items["outcome"] == "stage_failed"
    for cls in cells.LIVE_CLASSES:
        assert f"routable_{cls}" in items and f"triggerable_{cls}" in items


# -- wave 3: guard baseline, budget_state coverage, task-window warning ------


def test_guard_baseline_is_the_last_swept_generation_not_this_cycle(tmp_path):
    """The no_new_accepted_cards guard used to compare against THIS cycle's
    pre-triage count, which stranded genuinely new cards: cycle 1 accepts 30
    then the composer fails (watermark not advanced), cycle 2 re-fires, triage
    adds nothing, and the guard fires on 30 -> 30 even though 30 cards have
    never been swept. Baseline is now routable_at_last_generation."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="pend")

    class _AcceptAllOnTriage(FakeRunner):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if "-m" in argv and argv[argv.index("-m") + 1] == "pipeline.triage_batch":
                for i in range(30):
                    try:
                        reg.review_card(f"pend{i:04d}", "accepted", "auto-d31")
                    except Exception:
                        pass
            return r

    # Cycle 1: triage accepts 30, then the composer fails -> no watermark, and
    # crucially no routable_at_last_generation either.
    rc = loop.run(["--once", "--layer", str(layer)],
                  runner=_AcceptAllOnTriage(codes={"pipeline.composer": 1}))
    assert rc == 1
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert "routable_at_last_generation" not in st["classes"].get("crypto", {})
    assert loop._routable_counts(Registry(registry_path))["crypto"] == 30

    # Cycle 2: triage now accepts nothing (all 30 already accepted). The old
    # one-cycle baseline fired the guard here; the swept baseline must not,
    # because those 30 cards have still never reached a composer.
    fr2 = FakeRunner()
    rc2 = loop.run(["--once", "--layer", str(layer)], runner=fr2)
    assert rc2 == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete", (
        "guard stranded 30 accepted cards that no composer has ever swept")
    assert "pipeline.composer" in _modules(fr2)
    st2 = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st2["classes"]["crypto"]["routable_at_last_generation"] == 30


def test_guard_still_fires_once_the_corpus_has_actually_been_swept(tmp_path):
    """The other half: after a real generation banks the baseline, a later
    fire whose triage adds nothing must still stop before the composer."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    registry_path = layer / "registry_log.jsonl"
    _seed_all_classes_caught_up(layer, registry_path)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="a")

    class _AcceptOnce(FakeRunner):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if "-m" in argv and argv[argv.index("-m") + 1] == "pipeline.triage_batch":
                for i in range(30):
                    try:
                        reg.review_card(f"a{i:04d}", "accepted", "auto-d31")
                    except Exception:
                        pass
            return r

    loop.run(["--once", "--layer", str(layer)], runner=_AcceptOnce())
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"]["crypto"]["routable_at_last_generation"] == 30

    # New pending cards fire the class again; triage accepts none of them.
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="b")
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_new_accepted_cards"
    assert status["items"]["routable_at_last_generation"] == "30"
    assert "pipeline.composer" not in _modules(fr)


def test_budget_state_ok_appears_on_a_healthy_cycle(tmp_path):
    """CLAUDE.md documents ok | batch_stop | hard_cap. "ok" was unreachable:
    _budget_state was only called from the three budget-BLOCKED paths, so the
    one value describing a healthy run never appeared in a status file."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    items = json.loads((layer / "logs" / "pipeline_status.json")
                       .read_text(encoding="utf-8"))["items"]
    assert items["outcome"] == "cycle_complete"
    assert items["budget_state"] == "ok"


def test_budget_state_is_present_on_no_trigger_too(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=3)
    loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    items = json.loads((layer / "logs" / "pipeline_status.json")
                       .read_text(encoding="utf-8"))["items"]
    assert items["outcome"] == "no_trigger"
    assert items["budget_state"] == "ok"


# -- the execution-window warning -------------------------------------------

def test_iso_duration_parsing():
    assert loop._parse_iso_duration_s("PT1H") == 3600
    assert loop._parse_iso_duration_s("PT4H") == 4 * 3600
    assert loop._parse_iso_duration_s("PT4H30M") == 4 * 3600 + 1800
    assert loop._parse_iso_duration_s("P1DT2H") == 86400 + 7200
    assert loop._parse_iso_duration_s("PT0S") == 0
    # Unrecognised -> None, never a guessed number.
    for junk in ("", "   ", "banana", "1H", "P", None):
        assert loop._parse_iso_duration_s(junk) is None


def test_short_task_window_warns_loudly_and_names_the_fix(capsys):
    """The only failure mode the repo could not previously see: a task window
    shorter than a cycle kills the run mid-flight, leaving NO failure the
    Sentinel can detect."""
    msg = loop._warn_if_task_window_too_short(reader=lambda: 3600)   # PT1H
    assert msg is not None
    out = capsys.readouterr().out
    assert "WARN" in out
    assert "60 min" in out and "120 min" in out
    assert "schtasks" in out and "apply_retry_settings.ps1" in out   # the fix


def test_adequate_task_window_is_silent(capsys):
    assert loop._warn_if_task_window_too_short(reader=lambda: 4 * 3600) is None
    assert capsys.readouterr().out == ""


def test_unreadable_task_window_is_a_silent_no_op(capsys):
    """The loop must behave identically when the task is not registered at
    all -- tests, manual runs, a fresh clone."""
    assert loop._warn_if_task_window_too_short(reader=lambda: None) is None
    assert capsys.readouterr().out == ""


def test_a_raising_task_reader_never_breaks_the_cycle(capsys):
    """Defensive to the point of swallowing: a warning helper must never be
    the reason a cycle fails."""
    def _boom():
        raise OSError("schtasks exploded")
    assert loop._warn_if_task_window_too_short(reader=_boom) is None
    assert capsys.readouterr().out == ""


def test_live_task_window_reader_never_raises(monkeypatch):
    """The REAL reader (the autouse stub is undone first) against a task that
    certainly does not exist. Asserts only that a missing task yields None
    rather than an exception -- never that any task exists, so this holds on
    a fresh clone, in CI, and on a non-Windows host."""
    monkeypatch.undo()                  # drop the module-wide stub for this test
    assert loop._live_task_window_s("\\StewartCo\\definitely-not-a-real-task") is None


def test_the_startup_window_check_is_wired_into_run(tmp_path, monkeypatch):
    """The helper is only useful if run() actually calls it. Pins the wiring
    (and that it is late-bound, so the stub above genuinely takes effect)."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=3)
    calls = []
    monkeypatch.setattr(loop, "_live_task_window_s",
                        lambda *a, **k: calls.append(1) or 3600)
    loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    assert calls, "run() never consulted the live task window"
