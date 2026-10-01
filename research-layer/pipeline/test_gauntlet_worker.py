# research-layer/pipeline/test_gauntlet_worker.py
import copy
import json
import subprocess
import sys
from pathlib import Path

from . import gauntlet_worker as gw
from .chainlock import ChainLock
from .common import content_id
from .test_gauntlet import gauntlet_registry, run_verifier
from .test_screen import write_data_dir, dated_target_hit_bars

V61 = "gauntlet-protocol-v6.1: test anchor"
LAYER = Path(__file__).resolve().parent.parent


def _setup(tmp_path, note=True):
    reg, spec = gauntlet_registry(tmp_path)
    if note:
        reg.append("note", {"text": V61})
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    return reg, spec, data


def _run(reg, data, tmp_path, *extra):
    return gw.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--artifacts-dir", str(tmp_path / "art"),
                   "--logs-dir", str(tmp_path / "logs"), "--no-perturb",
                   "--max-workers", "1", *extra])


def _status(tmp_path):
    return json.loads((tmp_path / "logs" / "gauntlet_worker_status.json").read_text())


def test_refuses_without_the_v61_note(tmp_path):
    reg, spec, data = _setup(tmp_path, note=False)
    assert _run(reg, data, tmp_path) == 1
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"


def test_judges_a_candidate_and_chains_verdict_then_state(tmp_path):
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    sid = spec["strategy_id"]
    v = [e for e in reg.entries() if e["entry_type"] == "verdict"
         and e["payload"]["stage"] == "gauntlet"]
    assert len(v) == 1 and v[0]["payload"]["metrics"]["protocol"] == "gauntlet-protocol-v6.1"
    assert "deflated_sharpe" not in v[0]["payload"]["metrics"]
    assert reg.strategy_states()[sid] in ("quarantine", "graveyard")
    assert run_verifier(reg.log_path).returncode == 0
    assert _status(tmp_path)["evaluated"] == 1


def test_orphan_is_repaired_not_reevaluated(tmp_path):
    reg, spec, data = _setup(tmp_path)
    sid = spec["strategy_id"]
    reg.record_verdict(sid, "gauntlet", "fail",
                       {"protocol": "gauntlet-protocol-v6.1"}, "0" * 64)
    assert _run(reg, data, tmp_path) == 0
    assert reg.strategy_states()[sid] == "graveyard"
    assert sum(1 for e in reg.entries() if e["entry_type"] == "verdict"
               and e["payload"]["stage"] == "gauntlet") == 1
    assert _status(tmp_path)["repaired"] == 1


def test_a_held_chain_lock_defers_and_exits_0(tmp_path):
    reg, spec, data = _setup(tmp_path)
    (tmp_path / "logs").mkdir()
    held = ChainLock(tmp_path / "logs", "scanner", "test")
    held.acquire()
    try:
        assert _run(reg, data, tmp_path) == 0
    finally:
        held.release()
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"
    assert _status(tmp_path)["deferred_lock"] == 1


def test_a_raising_candidate_exits_1_and_stays_queued(tmp_path, monkeypatch):
    reg, spec, data = _setup(tmp_path)
    def boom(*a, **k):
        raise ValueError("engine blew up")
    monkeypatch.setattr(gw, "evaluate_standalone", boom)
    assert _run(reg, data, tmp_path) == 1
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"
    assert _status(tmp_path)["errored"] == 1


def test_a_missing_price_file_fails_that_candidate_only(tmp_path):
    reg, spec, data = _setup(tmp_path)
    (data / "BTCUSD_1d.csv").unlink()
    assert _run(reg, data, tmp_path) == 1
    assert _status(tmp_path)["errored"] == 1


def test_queue_is_cheapest_first():
    a = {"strategy_id": "a", "universe": {"assets": ["X", "Y"], "timeframe": "1d"}}
    b = {"strategy_id": "b", "universe": {"assets": ["X"], "timeframe": "1d"}}
    rows = {("X", "1d"): 100, ("Y", "1d"): 100}
    assert sorted([a, b], key=lambda s: gw.cost_key(s, rows))[0]["strategy_id"] == "b"


def test_the_deadline_leaves_candidates_queued(tmp_path):
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path, "--deadline-minutes", "0") == 0
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"
    assert _status(tmp_path)["deferred_deadline"] == 1


def test_worker_skips_a_candidate_whose_state_changed_before_its_write(tmp_path, monkeypatch):
    reg, spec, data = _setup(tmp_path)
    real = gw.evaluate_standalone
    def moved_on(*a, **k):
        out = real(*a, **k)
        reg.record_state_change(spec["strategy_id"], "graveyard", "test")
        return out
    monkeypatch.setattr(gw, "evaluate_standalone", moved_on)
    assert _run(reg, data, tmp_path) == 0
    assert not [e for e in reg.entries() if e["entry_type"] == "verdict"
                and e["payload"]["stage"] == "gauntlet"]


def test_two_workers_never_chain_the_same_verdict(tmp_path):
    reg, spec, data = _setup(tmp_path)
    (tmp_path / "logs").mkdir()
    inst = ChainLock(tmp_path / "logs", "gauntlet-worker", "other instance",
                     name="gauntlet_worker.lock")
    inst.acquire()
    try:
        assert _run(reg, data, tmp_path) == 0
    finally:
        inst.release()
    assert _status(tmp_path)["exit_reason"] == "deferred_instance"
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"


def test_worker_defers_a_candidate_whose_cells_are_not_comparable(tmp_path, monkeypatch):
    """screening_registry's fixture spec is single-asset (BTCUSD), so the
    refusal is injected: the comparability rule itself is screen's, tested
    there; this pins what the WORKER does with a refusal."""
    reg, spec, data = _setup(tmp_path)
    def refuse(*a, **k):
        raise ValueError("cells end 30 days apart")
    monkeypatch.setattr(gw, "assert_cells_comparable", refuse)
    assert _run(reg, data, tmp_path) == 0
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"
    assert _status(tmp_path)["deferred_not_comparable"] == 1


# ---------------- additions beyond the brief's list ----------------

def _two_candidates(tmp_path):
    """A = the fixture spec (BTCUSD), registered FIRST, on a 28-bar file.
    B = the same spec on ETHUSD, registered SECOND, on a 23-bar file.
    B is the cheaper candidate, so cheapest-first must chain B before A even
    though registry order says A first: an order test that registry order,
    or a descending sort, would both fail."""
    reg, a = gauntlet_registry(tmp_path)
    reg.append("note", {"text": V61})
    b = copy.deepcopy(a)
    b["universe"]["assets"] = ["ETHUSD"]
    b["name"] = "test breakout eth"
    b["strategy_id"] = None
    b["strategy_id"] = content_id(b, "strategy_id")
    reg.register_strategy(b)
    reg.record_state_change(b["strategy_id"], "screened", "test")
    reg.record_verdict(b["strategy_id"], "screened", "pass",
                       {"trades": 50, "net_pnl": 0.5, "win_rate": 0.5,
                        "max_dd": -0.1}, "0" * 64)
    reg.record_state_change(b["strategy_id"], "gauntlet", None)
    longer = dated_target_hit_bars()
    for i in range(5):
        extra = dict(longer[-1])
        extra["date"] = f"2023-01-{24 + i:02d}"
        longer.append(extra)
    data = write_data_dir(tmp_path, {"BTCUSD": longer,
                                     "ETHUSD": dated_target_hit_bars()})
    rows = gw._row_counts(data, {("BTCUSD", "1d"), ("ETHUSD", "1d")})
    assert gw.cost_key(b, rows) < gw.cost_key(a, rows)       # precondition
    return reg, a, b, data


def _gauntlet_writes(reg):
    """[(entry_type, sid)] for every gauntlet verdict and every state change
    out of 'gauntlet', in chain order."""
    out = []
    for e in reg.entries():
        p = e["payload"]
        if e["entry_type"] == "verdict" and p["stage"] == "gauntlet":
            out.append(("verdict", p["strategy_id"]))
        elif e["entry_type"] == "state_change" and p["from"] == "gauntlet":
            out.append(("state_change", p["strategy_id"]))
    return out


def test_the_cheaper_candidate_is_chained_first_each_verdict_with_its_state(tmp_path):
    reg, a, b, data = _two_candidates(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    sa, sb = a["strategy_id"], b["strategy_id"]
    assert _gauntlet_writes(reg) == [("verdict", sb), ("state_change", sb),
                                     ("verdict", sa), ("state_change", sa)]
    assert run_verifier(reg.log_path).returncode == 0
    assert _status(tmp_path)["evaluated"] == 2
    assert _status(tmp_path)["queued"] == 0


def test_the_pool_path_judges_and_chains_every_candidate(tmp_path, monkeypatch):
    """The scheduled default is the pool. Force two workers (the box's
    commit probe could otherwise size it down to the serial path) and spy
    that a real ProcessPoolExecutor ran."""
    reg, a, b, data = _two_candidates(tmp_path)
    monkeypatch.setattr(gw, "worker_count", lambda n_cpu, avail: 2)
    made = []
    real = gw.ProcessPoolExecutor

    class Spy(real):
        def __init__(self, max_workers=None, **kw):
            made.append(max_workers)
            super().__init__(max_workers=max_workers, **kw)

    monkeypatch.setattr(gw, "ProcessPoolExecutor", Spy)
    rc = gw.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                 "--artifacts-dir", str(tmp_path / "art"),
                 "--logs-dir", str(tmp_path / "logs"), "--no-perturb",
                 "--max-workers", "2"])
    assert rc == 0
    assert made == [2]
    writes = _gauntlet_writes(reg)
    assert len(writes) == 4
    # each verdict is chained immediately before its own state change
    for i in (0, 2):
        assert writes[i][0] == "verdict" and writes[i + 1] == ("state_change", writes[i][1])
    assert {writes[0][1], writes[2][1]} == {a["strategy_id"], b["strategy_id"]}
    states = reg.strategy_states()
    assert states[a["strategy_id"]] in ("quarantine", "graveyard")
    assert states[b["strategy_id"]] in ("quarantine", "graveyard")
    assert run_verifier(reg.log_path).returncode == 0
    assert _status(tmp_path)["evaluated"] == 2


def test_the_worker_imports_no_clustering_or_pbo():
    code = ("import sys; import pipeline.gauntlet_worker; "
            "bad = [m for m in ('pipeline.cluster', 'pipeline.pbo', 'pipeline.gauntlet') "
            "if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=LAYER,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_status_file_carries_every_key(tmp_path):
    reg, spec, data = _setup(tmp_path)
    assert _run(reg, data, tmp_path) == 0
    assert set(_status(tmp_path)) == {
        "ts_utc", "evaluated", "passed", "failed_gates", "errored",
        "deferred_lock", "deferred_deadline", "deferred_not_comparable",
        "queued", "oldest_queued_age_hours", "repaired", "exit_reason"}


def test_a_chain_write_failure_aborts_the_run_and_still_reports(tmp_path, monkeypatch):
    """A failure inside the write block is a chain/disk defect, not a
    candidate's: it must abort the run (never continue writing after it),
    still leave a status file naming it, and release the instance lock."""
    import pytest
    reg, spec, data = _setup(tmp_path)
    def disk_full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(gw, "write_gauntlet_artifacts", disk_full)
    with pytest.raises(OSError):
        _run(reg, data, tmp_path)
    assert _status(tmp_path)["exit_reason"] == "crashed"
    assert not (tmp_path / "logs" / "gauntlet_worker.lock").exists()
    assert not (tmp_path / "logs" / "chain.lock").exists()
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"


# ---------------- fix round 1 (Ruling 8): O(tail) chain.lock hold ----------------

def _verdicts(reg):
    return [e for e in reg.entries() if e["entry_type"] == "verdict"
            and e["payload"]["stage"] == "gauntlet"]


def test_a_foreign_append_before_the_write_is_absorbed(tmp_path, monkeypatch):
    """Another writer appends (a note: any entry for a DIFFERENT strategy)
    between the worker's snapshot and its write. advance() must pick it up,
    so the verdict chains onto it and the chain verifies."""
    from .common import entry_hash
    reg, spec, data = _setup(tmp_path)
    real = gw.evaluate_standalone
    seen = {}
    def foreign(*a, **k):
        out = real(*a, **k)
        seen["note"] = reg.append("note", {"text": "foreign writer"})
        return out
    monkeypatch.setattr(gw, "evaluate_standalone", foreign)
    assert _run(reg, data, tmp_path) == 0
    v = _verdicts(reg)
    assert len(v) == 1 and v[0]["prev_entry_hash"] == entry_hash(seen["note"])
    assert run_verifier(reg.log_path).returncode == 0
    assert _status(tmp_path)["evaluated"] == 1


def test_the_same_strategy_moved_by_a_foreign_append_is_skipped(tmp_path, monkeypatch):
    reg, spec, data = _setup(tmp_path)
    real = gw.evaluate_standalone
    def bury(*a, **k):
        out = real(*a, **k)
        reg.record_state_change(spec["strategy_id"], "graveyard", "foreign")
        return out
    monkeypatch.setattr(gw, "evaluate_standalone", bury)
    assert _run(reg, data, tmp_path) == 0
    assert not _verdicts(reg)
    assert run_verifier(reg.log_path).returncode == 0
    assert _status(tmp_path)["evaluated"] == 0


def test_no_full_chain_read_happens_under_chain_lock(tmp_path, monkeypatch):
    """The structural pin for Ruling 8: while logs/chain.lock exists, the
    worker may not walk the whole chain (entries(), strategy_states(),
    _head_hash(), snapshot()). Covers the verdict write and orphan repair."""
    from .registry import Registry
    reg, spec, data = _setup(tmp_path)
    lock = tmp_path / "logs" / "chain.lock"
    for name in ("entries", "strategy_states", "_head_hash", "snapshot"):
        real = getattr(Registry, name)
        def guarded(self, *a, _real=real, _name=name, **k):
            assert not lock.exists(), f"full-chain {_name}() under chain.lock"
            return _real(self, *a, **k)
        monkeypatch.setattr(Registry, name, guarded)
    assert _run(reg, data, tmp_path) == 0
    assert _status(tmp_path)["evaluated"] == 1


def test_orphan_repair_reads_no_full_chain_under_chain_lock(tmp_path, monkeypatch):
    from .registry import Registry
    reg, spec, data = _setup(tmp_path)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "pass",
                       {"protocol": "gauntlet-protocol-v6.1"}, "0" * 64)
    lock = tmp_path / "logs" / "chain.lock"
    for name in ("entries", "strategy_states", "_head_hash", "snapshot"):
        real = getattr(Registry, name)
        def guarded(self, *a, _real=real, _name=name, **k):
            assert not lock.exists(), f"full-chain {_name}() under chain.lock"
            return _real(self, *a, **k)
        monkeypatch.setattr(Registry, name, guarded)
    assert _run(reg, data, tmp_path) == 0
    assert reg.strategy_states()[spec["strategy_id"]] == "quarantine"
    assert _status(tmp_path)["repaired"] == 1
    assert run_verifier(reg.log_path).returncode == 0


def test_a_chain_that_moved_under_the_lock_is_an_error_not_a_write(tmp_path, monkeypatch):
    """A continuity break seen by advance() writes NOTHING for the candidate
    and counts it errored (exit 1)."""
    from .registry import ChainMoved
    reg, spec, data = _setup(tmp_path)
    def moved(self, snap):
        raise ChainMoved("tail does not link")
    monkeypatch.setattr(gw.Registry, "advance", moved)
    assert _run(reg, data, tmp_path) == 1
    assert not _verdicts(reg)
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"
    assert _status(tmp_path)["errored"] == 1


def test_a_candidate_whose_metrics_do_not_round_trip_is_errored_not_fatal(tmp_path, monkeypatch):
    """Ruling 9's refusal is per candidate: nothing is chained for it, the
    run exits 1, and it does not crash the worker (a crash would block every
    later run on the same cheapest-first candidate)."""
    reg, spec, data = _setup(tmp_path)
    real = gw.evaluate_standalone
    def int_keys(*a, **k):
        out = real(*a, **k)
        out["metrics"]["m"] = {9: 1.0, 10: 2.0}
        return out
    monkeypatch.setattr(gw, "evaluate_standalone", int_keys)
    assert _run(reg, data, tmp_path) == 1
    assert not _verdicts(reg)
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"
    st = _status(tmp_path)
    assert st["errored"] == 1 and st["exit_reason"] == "candidate_errors"
    assert run_verifier(reg.log_path).returncode == 0
