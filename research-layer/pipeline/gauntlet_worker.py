"""The gauntlet worker (Build 2a; docs/2026-09-30-gauntlet-at-scale-design.md).

Run every 30 minutes by \\Morpheus\\27_GauntletWorker. Each run: repair
orphans, then judge queued candidates cheapest-first with the six standalone
gates (gauntlet_core), chaining each verdict and state change as soon as it
exists, until its own deadline. Computes no clustering and no PBO: nothing on
this module's import graph can (test_gauntlet_worker pins it).

Exit 0: drained, stopped at the deadline, or deferred on a lock (routine).
Exit 1: a candidate raised, or setup was refused. The Ops Sentinel alarms on 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import deadline as _deadline
from .chainlock import ChainLock, ChainLockHeld
from .registry import Registry
from .screen import (load_cell_data, assert_cells_comparable, bundle_hash,
                     comparable_cells)
from .gauntlet_core import (PROTOCOL_V61, DEFAULT_CUTOFF, evaluate_standalone,
                            write_gauntlet_artifacts, _spec_bars,
                            GAUNTLET_PRIOR_S_PER_CANDIDATE,
                            GAUNTLET_CHUNK_PER_WORKER, worker_count,
                            available_commit_mb, worker_env)

LAYER = Path(__file__).resolve().parent.parent
PRIOR_S_PER_CANDIDATE = GAUNTLET_PRIOR_S_PER_CANDIDATE
# The worker's own instance lock goes stale after an hour: a run is bounded
# at 25 minutes plus one chunk's overrun, and the task fires every 30.
INSTANCE_STALE_AFTER_S = 3600
ARTIFACT_NAMES = ("oos_trades.csv", "mc_summary.json", "config.json")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cells(spec: dict) -> list[tuple[str, str]]:
    tf = spec["universe"].get("timeframe", "1d")
    return sorted({(a, tf) for a in spec["universe"]["assets"]})


def cost_key(spec: dict, row_counts: dict) -> tuple:
    """Cheapest first (design E2): total bars across the spec's own cells,
    then asset count, then strategy id as the deterministic tie-break."""
    return (sum(row_counts.get(c, 0) for c in _cells(spec)),
            len(spec["universe"]["assets"]), spec["strategy_id"])


def _row_counts(data_dir: Path, cells) -> dict:
    """Data rows per cell (lines minus the header); 0 when the file is
    missing -- that candidate then errors at load, where it is reported."""
    out = {}
    for asset, tf in cells:
        p = Path(data_dir) / f"{asset}_{tf}.csv"
        if p.exists():
            with p.open("rb") as f:
                out[(asset, tf)] = max(0, sum(1 for _ in f) - 1)
        else:
            out[(asset, tf)] = 0
    return out


def _gauntlet_verdicts(registry: Registry) -> dict[str, dict]:
    return {e["payload"]["strategy_id"]: e["payload"] for e in registry.entries()
            if e["entry_type"] == "verdict" and e["payload"].get("stage") == "gauntlet"}


def queue(registry: Registry) -> list[dict]:
    """Every strategy in state 'gauntlet' with no gauntlet verdict, as its
    registered spec, in registry order (the caller sorts cheapest-first)."""
    states = registry.strategy_states()
    judged = _gauntlet_verdicts(registry)
    return [e["payload"] for e in registry.entries()
            if e["entry_type"] == "strategy_registered"
            and states.get(e["payload"]["strategy_id"]) == "gauntlet"
            and e["payload"]["strategy_id"] not in judged]


def _entered_gauntlet(registry: Registry) -> dict[str, str]:
    return {e["payload"]["strategy_id"]: e["ts_utc"] for e in registry.entries()
            if e["entry_type"] == "state_change" and e["payload"].get("to") == "gauntlet"}


def _state_and_judged(registry: Registry, sid: str) -> tuple[str | None, bool]:
    """sid's current state and whether it already has a gauntlet verdict,
    in ONE pass over the chain (this runs under chain.lock)."""
    state, judged = None, False
    for e in registry.entries():
        p = e["payload"]
        if p.get("strategy_id") != sid:
            continue
        if e["entry_type"] == "strategy_registered":
            state = "proposed"
        elif e["entry_type"] == "state_change":
            state = p["to"]
        elif e["entry_type"] == "verdict" and p.get("stage") == "gauntlet":
            judged = True
    return state, judged


def repair_orphans(registry: Registry, logs_dir: Path) -> int:
    """A v6.1 gauntlet verdict whose strategy is still in state 'gauntlet'
    (a crash between the two writes) gets the state change its verdict
    implies. Idempotent; never re-evaluates. Raises ChainLockHeld when
    another writer holds chain.lock -- the next run repairs."""
    states = registry.strategy_states()
    orphans = [(sid, v) for sid, v in _gauntlet_verdicts(registry).items()
               if states.get(sid) == "gauntlet"
               and (v.get("metrics") or {}).get("protocol") == PROTOCOL_V61]
    if not orphans:
        return 0
    n = 0
    with ChainLock(logs_dir, "gauntlet-worker", "orphan repair"):
        states = registry.strategy_states()
        for sid, v in orphans:
            if states.get(sid) != "gauntlet":
                continue
            if v["verdict"] == "pass":
                registry.record_state_change(sid, "quarantine", "gauntlet pass")
            else:
                reason = (v.get("metrics") or {}).get("fail_reason") or "gauntlet fail"
                registry.record_state_change(sid, "graveyard", reason)
            print(f"repaired orphan {sid}: verdict {v['verdict']} -> state change",
                  flush=True)
            n += 1
    return n


def _evaluate_payload(payload: dict) -> dict:
    """One candidate's standalone battery. Module-level and plain-data in,
    plain-data out, so it is the only thing a spawned pool worker runs."""
    return evaluate_standalone(payload["spec"], payload["spec_bars"],
                               payload["cutoff"], payload["perturb"])


def _write_status(logs_dir: Path, status: dict) -> None:
    p = logs_dir / "gauntlet_worker_status.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path, default=LAYER / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=LAYER / "data")
    ap.add_argument("--artifacts-dir", type=Path, default=LAYER / "artifacts")
    ap.add_argument("--logs-dir", type=Path, default=LAYER / "logs")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    ap.add_argument("--deadline-minutes", type=float, default=25.0)
    ap.add_argument("--no-perturb", dest="perturb", action="store_false")
    ap.add_argument("--max-workers", type=int, default=1,
                    help="pool width cap (bounded by worker_count on this box); "
                         "1 = serial in this process")
    a = ap.parse_args(argv)
    a.logs_dir.mkdir(parents=True, exist_ok=True)
    status = {"evaluated": 0, "passed": 0, "failed_gates": 0, "errored": 0,
              "deferred_lock": 0, "deferred_deadline": 0,
              "deferred_not_comparable": 0, "queued": 0,
              "oldest_queued_age_hours": None, "repaired": 0, "exit_reason": None}

    def finish(rc: int, reason: str) -> int:
        status["exit_reason"] = reason
        status["ts_utc"] = _now().isoformat()
        _write_status(a.logs_dir, status)
        print(f"gauntlet_worker: {reason} {json.dumps(status, sort_keys=True)}",
              flush=True)
        return rc

    # One worker at a time. A second instance defers (exit 0); a lock left by
    # a dead holder is broken only once it is also stale, as loop.lock is.
    inst = ChainLock(a.logs_dir, "gauntlet-worker", "worker run",
                     stale_after_s=INSTANCE_STALE_AFTER_S,
                     name="gauntlet_worker.lock")
    try:
        inst.acquire()
    except ChainLockHeld:
        if not (inst.is_stale() and not inst.holder_alive()):
            return finish(0, "deferred_instance")
        try:
            inst.break_stale()
            inst.acquire()
        except ChainLockHeld:
            return finish(0, "deferred_instance")
    try:
        registry = Registry(a.registry)
        if not any(e["entry_type"] == "note"
                   and str(e["payload"].get("text", "")).startswith(PROTOCOL_V61 + ":")
                   for e in registry.entries()):
            print(f"REFUSED: no '{PROTOCOL_V61}:' note on the chain.", flush=True)
            return finish(1, "refused_no_protocol_note")
        try:
            status["repaired"] = repair_orphans(registry, a.logs_dir)
        except ChainLockHeld:
            print("orphan repair deferred: chain.lock held; the next run repairs",
                  flush=True)

        todo = queue(registry)
        rows = _row_counts(a.data_dir, {c for s in todo for c in _cells(s)})
        todo.sort(key=lambda s: cost_key(s, rows))
        entered = _entered_gauntlet(registry)
        budget = _deadline.DeadlineBudget(
            (_now() + timedelta(minutes=a.deadline_minutes)).isoformat())

        max_workers = 1
        if a.max_workers > 1:
            max_workers = min(a.max_workers,
                              worker_count(os.cpu_count() or 3, available_commit_mb()))
        chunk_size = 1 if max_workers <= 1 else max_workers * GAUNTLET_CHUNK_PER_WORKER
        print(f"gauntlet_worker: {len(todo)} queued, {max_workers} worker(s), "
              f"deadline in {a.deadline_minutes:g} min", flush=True)

        def prepare(spec: dict) -> dict | None:
            """Load the candidate's own cells. None = deferred or errored
            (already counted); a dict = the payload plus its provenance."""
            sid = spec["strategy_id"]
            try:
                bars, hashes, data_end = load_cell_data(a.data_dir, _cells(spec),
                                                        "9999-12-31")
            except Exception:
                traceback.print_exc(file=sys.stdout)
                print(f"{sid}  ERROR loading data: no verdict written; stays queued",
                      flush=True)
                status["errored"] += 1
                return None
            try:
                _, class_of = comparable_cells([spec])
                assert_cells_comparable(data_end, class_of=class_of)
            except Exception as exc:
                print(f"{sid}  deferred: cells not comparable ({exc})", flush=True)
                status["deferred_not_comparable"] += 1
                return None
            return {"payload": {"spec": spec, "spec_bars": _spec_bars(bars, spec),
                                "cutoff": a.cutoff, "perturb": a.perturb},
                    "hashes": hashes, "data_end": data_end}

        def chain(prep: dict, r: dict) -> None:
            """The write block, identical on both paths: under a short
            chain.lock hold, re-check the candidate is still queued, write its
            bundle, chain the verdict, then its state change."""
            spec = prep["payload"]["spec"]
            sid = spec["strategy_id"]
            r["metrics"]["fail_reason"] = r["reason"]
            try:
                with ChainLock(a.logs_dir, "gauntlet-worker", f"verdict {sid}"):
                    state, judged = _state_and_judged(registry, sid)
                    if state != "gauntlet" or judged:
                        print(f"{sid}  skipped: moved on before its write "
                              f"(state {state}, judged {judged})", flush=True)
                        return
                    # The bundle is written only once the candidate is known
                    # to be ours to judge: a bundle on disk is what a chained
                    # artifacts_hash and invariant 8's cutoff read refer to,
                    # so a skipped candidate must never overwrite one.
                    bundle = write_gauntlet_artifacts(
                        a.artifacts_dir, spec, r["oos_trades"], r["mc_summary"],
                        r["metrics"], a.cutoff, prep["hashes"], prep["data_end"],
                        {}, protocol=PROTOCOL_V61)
                    registry.record_verdict(
                        sid, "gauntlet", "pass" if r["passed"] else "fail",
                        r["metrics"], bundle_hash(bundle, names=ARTIFACT_NAMES))
                    if r["passed"]:
                        registry.record_state_change(sid, "quarantine", "gauntlet pass")
                        status["passed"] += 1
                    else:
                        registry.record_state_change(sid, "graveyard", r["reason"])
                        status["failed_gates"] += 1
                    status["evaluated"] += 1
                    print(f"{sid}  {'PASS' if r['passed'] else 'FAIL ' + str(r['reason'])}",
                          flush=True)
            except ChainLockHeld:
                print(f"{sid}  deferred: chain.lock held; next run", flush=True)
                status["deferred_lock"] += 1

        def errored(sid: str) -> None:
            traceback.print_exc(file=sys.stdout)
            print(f"{sid}  ERROR: no verdict written; stays queued", flush=True)
            status["errored"] += 1

        for start in range(0, len(todo), chunk_size):
            chunk = todo[start:start + chunk_size]
            if not budget.fits(len(chunk), budget.rate_s(PRIOR_S_PER_CANDIDATE)):
                status["deferred_deadline"] += len(todo) - start
                break
            t0 = time.time()
            try:
                preps = [p for p in (prepare(s) for s in chunk) if p is not None]
                if max_workers <= 1:
                    for prep in preps:
                        try:
                            r = _evaluate_payload(prep["payload"])
                        except Exception:
                            errored(prep["payload"]["spec"]["strategy_id"])
                            continue
                        chain(prep, r)
                elif preps:
                    with worker_env(), ProcessPoolExecutor(max_workers=max_workers) as ex:
                        futs = {ex.submit(_evaluate_payload, p["payload"]): p
                                for p in preps}
                        for fut in as_completed(futs):
                            prep = futs[fut]
                            try:
                                r = fut.result()
                            except Exception:
                                errored(prep["payload"]["spec"]["strategy_id"])
                                continue
                            chain(prep, r)
            finally:
                budget.record(len(chunk), time.time() - t0)

        left = queue(registry)
        status["queued"] = len(left)
        now = _now()
        ages = []
        for s in left:
            ts = entered.get(s["strategy_id"])
            if ts:
                ages.append((now - _deadline._parse_iso(ts)).total_seconds() / 3600)
        status["oldest_queued_age_hours"] = round(max(ages), 2) if ages else 0.0
        if status["errored"]:
            return finish(1, "candidate_errors")
        if status["deferred_deadline"]:
            return finish(0, "deadline")
        if status["deferred_lock"]:
            return finish(0, "deferred_lock")
        return finish(0, "drained")
    except Exception:
        # A chain or disk failure outside a candidate's own evaluation: abort
        # (never keep writing after it) but leave a status file that says so.
        # A verdict chained without its state change is repaired next run.
        finish(1, "crashed")
        raise
    finally:
        inst.release()


if __name__ == "__main__":
    sys.exit(run())
