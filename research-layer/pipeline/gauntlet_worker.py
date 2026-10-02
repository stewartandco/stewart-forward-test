"""The gauntlet worker (Build 2a; docs/2026-09-30-gauntlet-at-scale-design.md).

Run every 30 minutes by \\Morpheus\\27_GauntletWorker. Each run: repair
orphans, then judge queued candidates cheapest-first with the six standalone
gates (gauntlet_core), chaining each verdict and state change as soon as it
exists, until its own deadline. Computes no clustering and no PBO: nothing on
this module's import graph can (test_gauntlet_worker pins it).

A verdict whose write finds chain.lock held is KEPT (in memory, this run
only) and its write retried between chunks and in a final drain pass; it is
never waited for and never persisted across runs.

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
from .registry import ChainMoved, ChainSnapshot, Registry, UnstableEntry
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
# I2 (final review): the judged bundles the wrapper must commit with the
# registry. One repo-relative path per line, appended after each verdict
# write; tasks/run_gauntlet_worker.bat commits registry_log.jsonl plus these
# in ONE pathspec-scoped commit and clears the list only after that commit
# succeeded. logs/ is gitignored.
COMMIT_LIST = "gauntlet_worker_commit_paths.txt"
# Verdicts evaluated but not yet written because chain.lock was held are
# retried in a final drain pass, inside this slice of the run's budget (the
# chunk loop holds it back, so no chunk is started into it). The drain makes
# one non-blocking acquire attempt every DRAIN_INTERVAL_S and stops at the
# reserve or the deadline, whichever comes first. Rationale in the 2026-10-02
# worker-retry report: it outlasts a scanner card batch several times over,
# costs ~5% of a 25-minute run, and cannot rescue the loop's hours-long screen
# hold or a quarantine catch-up, which no affordable reserve could.
DRAIN_RESERVE_S = 75.0
DRAIN_INTERVAL_S = 5.0
# Injectable so tests drive the drain on a fake clock instead of sleeping.
_monotonic = time.monotonic
_sleep = time.sleep


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


class _View:
    """What the worker needs from the chain, collected during the SAME single
    read that builds its ChainSnapshot (Registry.snapshot(on_entry=...)), so
    the two describe the same bytes. Specs are kept only while their strategy
    can still reach the gauntlet."""

    def __init__(self) -> None:
        self.has_note = False
        self.specs: dict[str, dict] = {}       # sid -> spec, registry order
        self.verdicts: dict[str, dict] = {}    # sid -> last gauntlet verdict
        self.entered: dict[str, str] = {}      # sid -> ts of its move to 'gauntlet'

    def on_entry(self, e: dict) -> None:
        et, p = e["entry_type"], e["payload"]
        if et == "strategy_registered":
            self.specs[p["strategy_id"]] = p
        elif et == "state_change":
            if p.get("to") == "gauntlet":
                self.entered[p["strategy_id"]] = e["ts_utc"]
            elif p.get("to") not in ("proposed", "screened"):
                self.specs.pop(p["strategy_id"], None)
        elif et == "verdict" and p.get("stage") == "gauntlet":
            self.verdicts[p["strategy_id"]] = p
        elif et == "note" and str(p.get("text", "")).startswith(PROTOCOL_V61 + ":"):
            self.has_note = True

    def queue(self, snap: ChainSnapshot) -> list[dict]:
        return [spec for sid, spec in self.specs.items()
                if snap.states.get(sid) == "gauntlet"
                and sid not in snap.gauntlet_judged]

    def orphans(self, snap: ChainSnapshot) -> list[tuple[str, dict]]:
        return [(sid, v) for sid, v in self.verdicts.items()
                if snap.states.get(sid) == "gauntlet"
                and (v.get("metrics") or {}).get("protocol") == PROTOCOL_V61]


def _read(registry: Registry) -> tuple[ChainSnapshot, _View]:
    """ONE full chain read: the snapshot plus the worker's view of it."""
    view = _View()
    snap = registry.snapshot(on_entry=view.on_entry)
    return snap, view


def queue(registry: Registry) -> list[dict]:
    """Every strategy in state 'gauntlet' with no gauntlet verdict, as its
    registered spec, in registry order (the caller sorts cheapest-first)."""
    snap, view = _read(registry)
    return view.queue(snap)


def _repair(registry: Registry, logs_dir: Path, snap: ChainSnapshot,
            view: _View) -> tuple[int, ChainSnapshot]:
    orphans = view.orphans(snap)
    if not orphans:
        return 0, snap
    n = 0
    with ChainLock(logs_dir, "gauntlet-worker", "orphan repair"):
        snap = registry.advance(snap)            # O(tail) under the lock
        for sid, v in orphans:
            if snap.states.get(sid) != "gauntlet":
                continue
            if v["verdict"] == "pass":
                to, reason = "quarantine", "gauntlet pass"
            else:
                to = "graveyard"
                reason = (v.get("metrics") or {}).get("fail_reason") or "gauntlet fail"
            snap = registry.record_state_change_at(snap, sid, to, reason)
            print(f"repaired orphan {sid}: verdict {v['verdict']} -> {to}", flush=True)
            n += 1
    return n, snap


def repair_orphans(registry: Registry, logs_dir: Path) -> int:
    """A v6.1 gauntlet verdict whose strategy is still in state 'gauntlet'
    (a crash between the two writes) gets the state change its verdict
    implies. Idempotent; never re-evaluates. Raises ChainLockHeld when
    another writer holds chain.lock -- the next run repairs."""
    snap, view = _read(registry)
    return _repair(registry, logs_dir, snap, view)[0]


def _evaluate_payload(payload: dict) -> dict:
    """One candidate's standalone battery. Module-level and plain-data in,
    plain-data out, so it is the only thing a spawned pool worker runs."""
    return evaluate_standalone(payload["spec"], payload["spec_bars"],
                               payload["cutoff"], payload["perturb"])


def _record_bundle(logs_dir: Path, repo_root: Path, bundle: Path) -> None:
    """Queue one judged bundle for the wrapper's scoped commit. A bundle
    outside the repo (a test's tmp dir) cannot be committed and is not
    listed. A failure to append is a disk failure and aborts the run like
    any other (exit 1, 'crashed'), so it is never silent."""
    try:
        rel = Path(bundle).resolve().relative_to(Path(repo_root).resolve())
    except ValueError:
        print(f"bundle {bundle} is outside {repo_root}: not queued for commit",
              flush=True)
        return
    with (Path(logs_dir) / COMMIT_LIST).open("a", encoding="utf-8",
                                             newline="\n") as f:
        f.write(rel.as_posix() + "\n")


def _prune_commit_list(logs_dir: Path, repo_root: Path) -> None:
    """Drop duplicates and paths that no longer exist from the commit list,
    so one deleted bundle dir can never make the wrapper's `git add` fail
    (an unmatched pathspec is fatal to the whole add) on every later run."""
    p = Path(logs_dir) / COMMIT_LIST
    if not p.exists():
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    keep = list(dict.fromkeys(
        ln.strip() for ln in lines
        if ln.strip() and (Path(repo_root) / ln.strip()).exists()))
    if keep != lines:
        tmp = p.with_suffix(".txt.tmp")
        tmp.write_text("".join(k + "\n" for k in keep), encoding="utf-8",
                       newline="\n")
        tmp.replace(p)


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
    ap.add_argument("--repo-root", type=Path, default=LAYER.parent,
                    help="git root the commit list's paths are relative to")
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
              "oldest_queued_age_hours": None, "repaired": 0, "exit_reason": None,
              "retried_written": 0, "dropped_stale": 0}
    # Evaluated verdicts whose write found chain.lock held, oldest first. Each
    # holds only what its write needs (spec, data provenance, the result);
    # this run only, never persisted: the next run re-evaluates from scratch.
    pending: list[dict] = []

    def finish(rc: int, reason: str, write: bool = True) -> int:
        # deferred_lock = verdicts evaluated this run and still unwritten.
        status["deferred_lock"] = len(pending)
        status["exit_reason"] = reason
        status["ts_utc"] = _now().isoformat()
        if write:
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
        # A deferring instance prints but NEVER writes the status file: the live
        # holder owns it, and if that holder is wedged the file must go stale so
        # the Sentinel's 90-minute rule FAILs rather than a deferral refreshing it.
        if not (inst.is_stale() and not inst.holder_alive()):
            return finish(0, "deferred_instance", write=False)
        try:
            inst.break_stale()
            inst.acquire()
        except ChainLockHeld:
            return finish(0, "deferred_instance", write=False)
    try:
        _prune_commit_list(a.logs_dir, a.repo_root)
        registry = Registry(a.registry)
        # The run's ONE full chain read, outside any lock. Everything done
        # under chain.lock from here on advances this snapshot over the tail
        # appended since (Ruling 8: an O(tail) hold, never O(chain)).
        snap, view = _read(registry)
        if not view.has_note:
            print(f"REFUSED: no '{PROTOCOL_V61}:' note on the chain.", flush=True)
            return finish(1, "refused_no_protocol_note")
        try:
            status["repaired"], snap = _repair(registry, a.logs_dir, snap, view)
        except ChainLockHeld:
            print("orphan repair deferred: chain.lock held; the next run repairs",
                  flush=True)

        todo = view.queue(snap)
        del view
        rows = _row_counts(a.data_dir, {c for s in todo for c in _cells(s)})
        todo.sort(key=lambda s: cost_key(s, rows))
        budget = _deadline.DeadlineBudget(
            (_now() + timedelta(minutes=a.deadline_minutes)).isoformat(),
            reserve_s=DRAIN_RESERVE_S, clock=_monotonic)

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

        def write(item: dict) -> str:
            """The ONE write block, for first attempts and retries alike: one
            non-blocking chain.lock attempt; under that short hold, re-check
            the candidate is still queued on the advanced snapshot, write its
            bundle, chain the verdict, then its state change. Returns
            'written', 'held' (chain.lock taken by another writer; nothing
            done), 'stale' (moved on or judged meanwhile; nothing written) or
            'errored' (counted; nothing written; stays queued)."""
            nonlocal snap
            spec, r = item["spec"], item["r"]
            sid = spec["strategy_id"]
            r["metrics"]["fail_reason"] = r["reason"]
            try:
                with ChainLock(a.logs_dir, "gauntlet-worker", f"verdict {sid}"):
                    try:
                        snap = registry.advance(snap)
                    except ChainMoved:
                        errored(sid)
                        return "errored"
                    state, judged = snap.states.get(sid), sid in snap.gauntlet_judged
                    if state != "gauntlet" or judged:
                        print(f"{sid}  skipped: moved on before its write "
                              f"(state {state}, judged {judged})", flush=True)
                        return "stale"
                    # The bundle is written only once the candidate is known
                    # to be ours to judge: a bundle on disk is what a chained
                    # artifacts_hash and invariant 8's cutoff read refer to,
                    # so a skipped candidate must never overwrite one.
                    bundle = write_gauntlet_artifacts(
                        a.artifacts_dir, spec, r["oos_trades"], r["mc_summary"],
                        r["metrics"], a.cutoff, item["hashes"], item["data_end"],
                        {}, protocol=PROTOCOL_V61)
                    to, reason = (("quarantine", "gauntlet pass") if r["passed"]
                                  else ("graveyard", r["reason"]))
                    try:
                        snap = registry.record_gauntlet_outcome(
                            snap, sid, "pass" if r["passed"] else "fail",
                            r["metrics"], bundle_hash(bundle, names=ARTIFACT_NAMES),
                            to, reason)
                    except (ChainMoved, UnstableEntry):
                        # ChainMoved: someone appended without chain.lock
                        # between the advance and the write; the next
                        # candidate's advance() absorbs their entry.
                        # UnstableEntry: this candidate's metrics do not
                        # round-trip through JSON. Either way nothing was
                        # written and the candidate stays queued.
                        errored(sid)
                        return "errored"
                    _record_bundle(a.logs_dir, a.repo_root, bundle)
                    status["passed" if r["passed"] else "failed_gates"] += 1
                    status["evaluated"] += 1
                    print(f"{sid}  {'PASS' if r['passed'] else 'FAIL ' + str(r['reason'])}",
                          flush=True)
                    return "written"
            except ChainLockHeld:
                return "held"

        def chain(prep: dict, r: dict) -> None:
            """First write attempt for a fresh result. If chain.lock is held
            the result is kept for a retry this run, with only what its write
            needs (not the bars), and NO bundle is written yet."""
            item = {"spec": prep["payload"]["spec"], "hashes": prep["hashes"],
                    "data_end": prep["data_end"], "r": r}
            if write(item) == "held":
                print(f"{item['spec']['strategy_id']}  deferred: chain.lock held; "
                      f"kept for a retry this run", flush=True)
                pending.append(item)

        def retry_pending() -> None:
            """One retry pass over the kept results, oldest first. Each write
            is one non-blocking attempt; the first 'held' ends the pass (the
            lock is someone else's right now), leaving the rest kept. Never
            starts a write once the deadline has passed."""
            while pending:
                rem = budget.remaining_s()
                if rem is not None and rem <= 0:
                    return
                out = write(pending[0])
                if out == "held":
                    return
                item = pending.pop(0)
                if out == "written":
                    status["retried_written"] += 1
                elif out == "stale":
                    status["dropped_stale"] += 1
                # 'errored' was counted by write(); the candidate stays queued

        def final_drain() -> None:
            """Bounded non-blocking retries, DRAIN_INTERVAL_S apart, for at
            most DRAIN_RESERVE_S and never past the deadline. Never waits on,
            polls inside, or breaks chain.lock."""
            if not pending:
                return
            stop_at = _monotonic() + DRAIN_RESERVE_S
            rem = budget.remaining_s()
            if rem is not None:
                stop_at = min(stop_at, _monotonic() + rem)
            n0, attempts = len(pending), 0
            while pending and _monotonic() < stop_at:
                retry_pending()
                attempts += 1
                if not pending or stop_at - _monotonic() <= DRAIN_INTERVAL_S:
                    break
                _sleep(DRAIN_INTERVAL_S)
            print(f"gauntlet_worker: final drain, {attempts} pass(es), "
                  f"{n0 - len(pending)} of {n0} kept verdict(s) resolved",
                  flush=True)

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
                # The chunk is done and the next is not yet dispatched: one
                # opportunistic retry of what earlier holds left unwritten.
                # (Inside the timed block, so its writes count against the
                # measured rate: conservative for the deadline.)
                retry_pending()
            finally:
                budget.record(len(chunk), time.time() - t0)

        final_drain()
        for item in pending:
            print(f"{item['spec']['strategy_id']}  deferred: chain.lock held "
                  f"through the run; not written, stays queued", flush=True)

        snap_end, view_end = _read(registry)          # outside the lock
        left, entered = view_end.queue(snap_end), view_end.entered
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
        if pending:
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
