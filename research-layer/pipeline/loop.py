"""Pipeline loop orchestrator: triage -> compose -> screen -> gauntlet when a
class accumulates enough new accepted cards.

Spec: docs/2026-08-27-pipeline-loop-design.md. Invoked by
\\StewartCo\\25_PipelineLoop (~3x daily) as `python -m pipeline.loop --once`.

Exit 0: cycle_complete | no_trigger | deferred_lock | deferred_budget |
        deferred_instance | dry_run_would_fire
        (distinguished in logs/pipeline_status.json items.outcome)
Exit 1: stage_failed | chain_invalid | loop_crashed -- a real defect.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import cells, loop_state, pipeline_budget, pipeline_status
from .budget import BudgetMeter, PIPELINE_CAP_USD
from .chainlock import ChainLock, ChainLockHeld
from .composer import routable_cards
from .registry import Registry

LAYER_DEFAULT = Path(__file__).resolve().parent.parent
Runner = Callable[..., object]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_run_id(asset_class: str) -> str:
    """Second-resolution, not date-only: a same-day retry after a stage
    failure/deferral must get a DIFFERENT run_id, or composer's
    sibling_group_id = f"{family}-{run_id}" silently merges the retry's new
    specs into the earlier (aborted) run's sibling group, feeding
    plateau.qualifies() a neighbor set the spec was never actually swept
    with."""
    return (datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
           + f"-loop-{asset_class}")


def _routable_counts(registry: Registry) -> dict[str, int]:
    accepted = registry.cards(status="accepted")
    return {cls: len(routable_cards(accepted, cls)[0]) for cls in cells.LIVE_CLASSES}


def _entry_count(registry_path: Path) -> int:
    """Raw chain-line count. Read-only, no lock -- read paths never take
    chain.lock (chainlock.py's own rule)."""
    if not registry_path.exists():
        return 0
    with registry_path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def collect_commit_paths(layer: Path, start_line: int) -> list[str]:
    """Repo-relative paths for this cycle's chain delta: the registry plus
    artifacts/<sid> for every strategy_registered entry appended after
    start_line whose bundle exists on disk. start_line is the chain-line
    count taken by the SAME helper (_entry_count) that also measures
    chain_growth -- one counting rule, not two."""
    rel_root = "research-layer"
    paths = [f"{rel_root}/registry_log.jsonl"]
    registry_path = layer / "registry_log.jsonl"
    with registry_path.open("r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    for ln in lines[start_line:]:
        try:
            entry = json.loads(ln)
        except json.JSONDecodeError:
            continue                # partial concurrent line; tail rules apply
        if entry.get("entry_type") != "strategy_registered":
            continue
        sid = entry.get("payload", {}).get("strategy_id")
        if sid and (layer / "artifacts" / sid).is_dir():
            paths.append(f"{rel_root}/artifacts/{sid}")
    return paths


def commit_cycle(layer: Path, start_line: int, run_id: str, runner: Runner) -> None:
    """Scoped commit of this cycle's chain delta (registry_log.jsonl plus the
    artifact bundles registered this cycle). Best-effort: a git failure is
    LOUD (printed) but never fails the cycle -- the chain itself is the
    trust asset, this commit is bookkeeping. Never `git add -A`, never
    push."""
    repo = layer.parent
    paths = collect_commit_paths(layer, start_line)
    add = runner(["git", "add", "--"] + paths, cwd=str(repo))
    if add.returncode != 0:
        print("loop: WARNING git add failed; chain delta left uncommitted", flush=True)
        return
    cm = runner(["git", "commit", "-q", "-m", f"loop: {run_id} chain delta"],
               cwd=str(repo))
    if cm.returncode != 0:
        print("loop: WARNING git commit failed (possibly nothing staged)", flush=True)


def _spent(logs_dir: str | Path) -> float:
    """A fresh pipeline-scoped ledger read. Deliberately re-instantiates
    BudgetMeter every call instead of caching one: a subprocess stage may
    have appended to the ledger since the last read, and spent_usd on a
    status write must never lag reality. Cheap -- a ledger scan, not an
    API call."""
    meter = BudgetMeter(Path(logs_dir) / "budget_ledger.jsonl",
                        monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")
    return meter.month_spend()


def _budget_escalations(spent: float) -> list[str]:
    """budget_cap (a PUSH_TRIGGERS entry, interrupts the digest) only at the
    hard cap. The 80% batch-stop line parks work just as surely, but it is
    routine, not urgent -- escalating it would train Coen to ignore the
    channel exactly as pipeline_status.py's own docstring warns against."""
    return ["budget_cap"] if spent >= PIPELINE_CAP_USD else []


def _watermark_items(state: dict) -> dict[str, str]:
    return {f"watermark_{c}": str(e.get("watermark", 0))
            for c, e in state.get("classes", {}).items()}


def _write_status(logs_dir: str | Path, outcome: str, *, overall: str = "OK",
                  extra: dict[str, str] | None = None, spent: float = 0.0,
                  escalations: list[str] | None = None,
                  state: dict | None = None) -> None:
    items: dict[str, str] = {"outcome": outcome}
    if state is not None:
        items.update(_watermark_items(state))
    items.update(extra or {})
    payload = pipeline_status.build({"loop": overall}, spent, escalations)
    payload["items"] = {**payload.get("items", {}), **items}
    payload["overall"] = overall
    payload["summary"] = f"loop: {outcome}"
    pipeline_status.write(Path(logs_dir) / "pipeline_status.json", payload)


def _stage(runner: Runner, argv: list[str], cwd: str | Path) -> int:
    print(f"loop: running {' '.join(argv)}", flush=True)
    return runner(argv, cwd=str(cwd)).returncode


def _abort_stage_failed(logs_dir: str | Path, state: dict, asset_class: str,
                        module_key: str, rc: int) -> int:
    print(f"loop: stage {module_key} failed rc={rc}, aborting cycle", flush=True)
    _write_status(logs_dir, "stage_failed", overall="FAIL",
                  extra={"asset_class": asset_class, "failed_stage": module_key,
                         "exit_code": str(rc)},
                  spent=_spent(logs_dir), escalations=["run_aborted"], state=state)
    return 1


def _composer_rc_or_park(logs_dir: str | Path, state: dict, asset_class: str,
                         module_key: str, rc: int) -> int:
    """After EITHER composer invocation (the --dry-run preflight or the real
    run) exits nonzero, decide whether this is composer's OWN hard-cap
    refusal (propose_families's meter.can_spend() check raises SystemExit,
    which the dry-run preflight hits too -- composer.run() calls the metered
    propose_families before it ever branches on --dry-run) -- which must map
    to a budget park, never a stage defect -- or a genuine composer crash.

    Deliberately narrower than the proactive pre-triage/post-triage gates
    (which use may_start_batch, the 80% batch-stop line, to decide whether
    to START new work): this is a REACTIVE check explaining why composer
    itself already failed, and composer's own guard only fires at the true
    100% hard cap (pipeline_budget.may_spend). A failure in the 80-100% band
    with no cap-crossing spend is a real defect, not a park."""
    post_spent = _spent(logs_dir)
    if not pipeline_budget.may_spend(post_spent):
        msg = (f"deferred_budget: {module_key} exited nonzero (rc={rc}) with "
               f"pipeline spend USD {post_spent:.2f} at/above the hard cap "
               f"-- treating as a budget park, not a stage defect")
        print(msg, flush=True)
        _write_status(logs_dir, "deferred_budget", overall="WARN",
                      extra={"asset_class": asset_class},
                      spent=post_spent, escalations=_budget_escalations(post_spent),
                      state=state)
        return 0
    return _abort_stage_failed(logs_dir, state, asset_class, module_key, rc)


def _acquire_instance_lock_or_break_dead(instance_lock: ChainLock) -> bool:
    """True if the instance lock is now held by THIS run and the cycle may
    proceed; False means defer.

    A hard kill (the scheduled task's execution-time-limit), a reboot, or an
    acquire-then-crash can orphan loop.lock. Left unbroken, deferred_instance
    would recur forever at overall=OK -- a wedge that reads as healthy
    indefinitely. A LIVE holder still defers unconditionally however old the
    lock is: age alone is not evidence of death (a slow but legitimate
    cycle). Only a holder whose recorded pid is provably not running
    licenses a break, and a single strike suffices here -- unlike
    chain.lock's ambiguous holders, a dead pid is decisive, not merely
    suspicious."""
    try:
        instance_lock.acquire()
        return True
    except ChainLockHeld:
        pass
    if not (instance_lock.is_stale() and not instance_lock.holder_alive()):
        print("deferred_instance: another loop instance holds loop.lock, deferring",
             flush=True)
        return False
    try:
        instance_lock.break_stale()
    except ChainLockHeld:
        print("deferred_instance: loop.lock changed hands during a "
             "dead-holder break, deferring", flush=True)
        return False
    try:
        instance_lock.acquire()
        return True
    except ChainLockHeld:
        print("deferred_instance: lost the race to reclaim loop.lock after "
             "breaking a dead holder, deferring", flush=True)
        return False


def run(argv: list[str] | None = None, runner: Runner = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true", required=True,
                    help="run a single trigger-check cycle (the only mode "
                         "implemented; a daemon/scheduling loop is not this "
                         "module's job -- the OS scheduler owns cadence)")
    ap.add_argument("--layer", type=Path, default=LAYER_DEFAULT,
                    help="research-layer root (holds registry_log.jsonl and logs/)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report whether a class would fire; run nothing")
    args = ap.parse_args(argv)

    layer = Path(args.layer)
    logs_dir = layer / "logs"
    registry_path = layer / "registry_log.jsonl"
    state_path = logs_dir / "loop_state.json"
    logs_dir.mkdir(parents=True, exist_ok=True)

    try:
        return _run_cycle(args, runner, layer, logs_dir, registry_path, state_path)
    except Exception as exc:                        # unattended -- never silent
        traceback.print_exc()
        print(f"loop_crashed: {exc}", flush=True)
        try:
            _write_status(logs_dir, "loop_crashed", overall="FAIL",
                          extra={"error": str(exc)[:200]},
                          spent=_spent(logs_dir), escalations=["run_aborted"])
        except Exception:
            # A corrupt ledger or an unwritable logs dir must not swallow
            # the ORIGINAL crash -- its traceback is already on record above
            # either way.
            traceback.print_exc()
        return 1


def _run_cycle(args, runner: Runner, layer: Path, logs_dir: Path,
               registry_path: Path, state_path: Path) -> int:
    # loop_state.json is a pure read (atomic tmp+replace writer, so a
    # concurrent writer never leaves a torn file to read) -- safe before the
    # instance lock below.
    state = loop_state.load(state_path)

    # -- 0. instance guard: one loop.run() at a time, for the WHOLE cycle --
    # Protects the supervised-session-vs-scheduled-fire overlap (a human
    # running `--once` by hand while the scheduled task also fires).
    instance_lock = ChainLock(logs_dir, holder="loop-instance",
                              purpose=f"run start {_now_utc()}",
                              name="loop.lock", stale_after_s=2 * 3600)
    if not _acquire_instance_lock_or_break_dead(instance_lock):
        # _acquire_instance_lock_or_break_dead is the single printer for
        # every sub-case (live holder, race-during-break, lost-reacquire) so
        # each defer logs once with its actual cause, not a generic line
        # here on top of it.
        # ALWAYS WARN, never OK: this is the one deferral that can persist
        # indefinitely (a live sibling instance is not a transient race like
        # a chain-write window), so it must never read as healthy.
        _write_status(logs_dir, "deferred_instance", overall="WARN",
                      spent=_spent(logs_dir), state=state)
        return 0
    try:
        return _run_locked_cycle(args, runner, layer, logs_dir, registry_path,
                                 state_path, state)
    finally:
        instance_lock.release()


def _run_locked_cycle(args, runner: Runner, layer: Path, logs_dir: Path,
                      registry_path: Path, state_path: Path, state: dict) -> int:
    # Normalise/create the state file up front so every return path below
    # (including a stage failure before any watermark mutation) leaves a
    # readable logs/loop_state.json on disk.
    loop_state.save(state_path, state)

    # -- 1. foreign chain-lock probe (pure info() read, never an acquire) --
    probe = ChainLock(logs_dir, holder="loop", purpose="probe")
    info = probe.info()
    if info is not None:
        if probe.is_stale():
            second_strike = loop_state.record_stale_lock(state, info)
            loop_state.save(state_path, state)
            if second_strike:
                try:
                    probe.break_stale()
                    broke = True
                except ChainLockHeld:
                    broke = False
                if broke:
                    loop_state.clear_stale_lock(state)
                    loop_state.save(state_path, state)
                    # fall through: the cycle proceeds under a now-clear lock
                else:
                    # Race: something changed between our is_stale() read and
                    # break_stale() (which re-checks is_stale() itself).
                    fresh_info = probe.info()
                    if fresh_info is None:
                        # The stale holder released it entirely in the gap --
                        # there is no lock left to defer to; proceed exactly
                        # as a successful break would have. NOT prefixed
                        # "deferred_lock:" -- this path does not defer, and
                        # the greppable-token convention would miscount it.
                        print("stale_lock_vanished: chain.lock vanished on "
                             "its own before the break -- proceeding",
                             flush=True)
                        loop_state.clear_stale_lock(state)
                        loop_state.save(state_path, state)
                    else:
                        holder = fresh_info.get("holder")
                        msg = (f"deferred_lock: chain.lock changed hands "
                               f"mid-break (now held by {holder!r}), deferring")
                        print(msg, flush=True)
                        _write_status(logs_dir, "deferred_lock",
                                      extra={"lock_holder": str(holder),
                                             "lock_stale": "false"},
                                      spent=_spent(logs_dir), state=state)
                        return 0
            else:
                msg = (f"deferred_lock: chain.lock STALE (holder="
                       f"{info.get('holder')!r}), first sighting -- WARN, "
                       f"deferring; will break on next stale sighting")
                print(msg, flush=True)
                _write_status(logs_dir, "deferred_lock", overall="WARN",
                              extra={"lock_holder": str(info.get("holder")),
                                     "lock_stale": "true"},
                              spent=_spent(logs_dir), state=state)
                return 0
        else:
            # A fresh foreign lock at CYCLE START is routine (a human
            # session or the scanner happened to be mid-write when the loop
            # fired) -- overall stays OK, unlike the mid-cycle case below
            # where the loop itself unexpectedly loses a lock it believed
            # was free, which is WARN.
            msg = f"deferred_lock: chain.lock held by {info.get('holder')!r}, deferring"
            print(msg, flush=True)
            _write_status(logs_dir, "deferred_lock",
                          extra={"lock_holder": str(info.get("holder")),
                                 "lock_stale": "false"},
                          spent=_spent(logs_dir), state=state)
            return 0
    else:
        # No lock on disk: any previously-recorded stale-lock strike is moot
        # (that lock is gone, broken by us or released by its holder).
        if state.get("stale_lock") is not None:
            loop_state.clear_stale_lock(state)
            loop_state.save(state_path, state)

    # -- 2. trigger check ----------------------------------------------------
    registry = Registry(registry_path)
    routable_counts = _routable_counts(registry)
    asset_class = loop_state.pick_class(state, routable_counts)
    if asset_class is None:
        print("no_trigger: no live class is over threshold", flush=True)
        _write_status(logs_dir, "no_trigger",
                      extra={f"routable_{c}": str(n) for c, n in routable_counts.items()},
                      spent=_spent(logs_dir), state=state)
        return 0

    # -- 3. budget gate (before ANY metered stage may start) -----------------
    spent = _spent(logs_dir)
    if not pipeline_budget.may_start_batch(spent):
        msg = (f"deferred_budget: pipeline spend USD {spent:.2f} is at/above "
               f"the batch-start threshold -- parking the {asset_class} cycle")
        print(msg, flush=True)
        _write_status(logs_dir, "deferred_budget", overall="WARN",
                      extra={"asset_class": asset_class},
                      spent=spent, escalations=_budget_escalations(spent), state=state)
        return 0

    routable_count = routable_counts[asset_class]
    if args.dry_run:
        print(f"dry_run_would_fire: {asset_class} would trigger "
              f"(routable={routable_count})", flush=True)
        _write_status(logs_dir, "dry_run_would_fire",
                      extra={"asset_class": asset_class,
                             "routable_count": str(routable_count)},
                      spent=spent, state=state)
        return 0

    # -- 4. run the cycle ------------------------------------------------------
    run_id = _make_run_id(asset_class)
    py = sys.executable
    reg_argv = ["--registry", str(registry_path)]
    data_argv = ["--data-dir", str(layer / "data"),
                "--artifacts-dir", str(layer / "artifacts")]

    # 4.0 chain verify BEFORE any spend (spec s6): a pre-existing invalid
    # chain must abort here, at zero metered cost, rather than let triage
    # and composer spend against a chain the loop is about to reject anyway.
    verify_argv = [py, str(layer / "verify_registry.py"), str(registry_path)]
    rc = _stage(runner, verify_argv, layer)
    if rc != 0:
        print(f"chain_invalid: verify_registry.py rc={rc} before triage -- "
             f"aborting, zero spend, watermark NOT advanced", flush=True)
        _write_status(logs_dir, "chain_invalid", overall="FAIL",
                      extra={"asset_class": asset_class, "exit_code": str(rc)},
                      spent=_spent(logs_dir), escalations=["chain_invalid"], state=state)
        return 1

    entries_before = _entry_count(registry_path)

    def _lock_and_run(module_key: str, stage_argv: list[str]) -> tuple[int | None, bool]:
        """(rc, lock_lost). rc is None when the lock could not be acquired."""
        lock = ChainLock(logs_dir, holder="loop", purpose=f"{run_id} {module_key}")
        try:
            lock.acquire()
        except ChainLockHeld:
            return None, True
        try:
            return _stage(runner, stage_argv, layer), False
        finally:
            lock.release()

    def _defer_midcycle_lock(module_key: str) -> int:
        print(f"deferred_lock: chain.lock taken out from under the loop before "
             f"{module_key} -- deferring mid-cycle", flush=True)
        _write_status(logs_dir, "deferred_lock", overall="WARN",
                      extra={"asset_class": asset_class, "at_stage": module_key,
                             "lock_stale": "false"},
                      spent=_spent(logs_dir), state=state)
        return 0

    # 4a. triage --apply (chain-writing, metered)
    triage_argv = [py, "-m", "pipeline.triage_batch", *reg_argv, "--apply"]
    rc, lock_lost = _lock_and_run("pipeline.triage_batch", triage_argv)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.triage_batch")
    if rc != 0:
        return _abort_stage_failed(logs_dir, state, asset_class, "pipeline.triage_batch", rc)

    # Watermark truth: the routable-accepted set the composer is about to
    # consume is the set right after triage, not whatever the chain looks
    # like after screen/gauntlet (and possibly a foreign writer) have also
    # run. Recorded now, used at the end.
    watermark_after_triage = _routable_counts(registry)[asset_class]

    # Budget re-check (plan: "before triage AND before composer"): triage may
    # itself have spent against the cap; a composer batch must not start on
    # a stale pre-triage read.
    spent = _spent(logs_dir)
    if not pipeline_budget.may_start_batch(spent):
        msg = (f"deferred_budget: pipeline spend USD {spent:.2f} is at/above "
               f"the batch-start threshold after triage -- parking before "
               f"the {asset_class} composer run")
        print(msg, flush=True)
        _write_status(logs_dir, "deferred_budget", overall="WARN",
                      extra={"asset_class": asset_class},
                      spent=spent, escalations=_budget_escalations(spent), state=state)
        return 0

    # 4b. composer --dry-run preflight (no chain write, no lock). NOTE:
    # composer.run() calls the metered propose_families BEFORE it ever
    # branches on --dry-run, so a hard-cap refusal can surface HERE, not
    # only on the real run below -- _composer_rc_or_park covers both calls.
    composer_dry_argv = [py, "-m", "pipeline.composer", *reg_argv, "--run-id",
                         run_id, "--asset-class", asset_class, "--dry-run"]
    rc = _stage(runner, composer_dry_argv, layer)
    if rc != 0:
        return _composer_rc_or_park(logs_dir, state, asset_class, "pipeline.composer", rc)

    # 4c. composer real run (chain-writing, metered)
    composer_argv = [py, "-m", "pipeline.composer", *reg_argv, "--run-id",
                     run_id, "--asset-class", asset_class]
    rc, lock_lost = _lock_and_run("pipeline.composer", composer_argv)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.composer")
    if rc != 0:
        return _composer_rc_or_park(logs_dir, state, asset_class, "pipeline.composer", rc)

    # 4d. screen (chain-writing)
    screen_argv = [py, "-m", "pipeline.screen", *reg_argv, *data_argv]
    rc, lock_lost = _lock_and_run("pipeline.screen", screen_argv)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.screen")
    if rc != 0:
        return _abort_stage_failed(logs_dir, state, asset_class, "pipeline.screen", rc)

    # 4e. gauntlet (chain-writing)
    gauntlet_argv = [py, "-m", "pipeline.gauntlet", *reg_argv, *data_argv]
    rc, lock_lost = _lock_and_run("pipeline.gauntlet", gauntlet_argv)
    if lock_lost:
        return _defer_midcycle_lock("pipeline.gauntlet")
    if rc != 0:
        return _abort_stage_failed(logs_dir, state, asset_class, "pipeline.gauntlet", rc)

    # 4f. chain verify again, post-gauntlet (spec s6): the loop's OWN writes
    # this cycle must satisfy the same invariants a human session's would.
    # Distinct from 4.0 above -- this one unambiguously attributes a break
    # to THIS cycle's stages, not to whatever was on disk before it started.
    # Read-only: no lock.
    rc = _stage(runner, verify_argv, layer)
    if rc != 0:
        print(f"chain_invalid: verify_registry.py rc={rc} after a clean "
             f"gauntlet -- aborting, watermark NOT advanced", flush=True)
        _write_status(logs_dir, "chain_invalid", overall="FAIL",
                      extra={"asset_class": asset_class, "exit_code": str(rc)},
                      spent=_spent(logs_dir), escalations=["chain_invalid"], state=state)
        return 1

    # -- 5. success: advance the watermark, report clean -----------------------
    # chain_growth counts ALL chain growth over the cycle (this cycle's own
    # writes AND any foreign append that landed in a gap between our own
    # stage lock windows, which are per-append-window, not whole-cycle) --
    # it is a chain-health signal, not an attribution of "specs this run
    # produced".
    chain_growth = _entry_count(registry_path) - entries_before
    loop_state.record_generation(state, asset_class, run_id=run_id,
                                 routable_count=watermark_after_triage,
                                 ts_utc=_now_utc())
    loop_state.save(state_path, state)

    print(f"cycle_complete: {asset_class} watermark now {watermark_after_triage}",
         flush=True)
    _write_status(logs_dir, "cycle_complete",
                  extra={"asset_class": asset_class,
                         "watermark": str(watermark_after_triage),
                         "run_id": run_id,
                         "chain_growth": str(chain_growth)},
                  spent=_spent(logs_dir), state=state)

    # -- 6. scoped chain commit -- bookkeeping, never a cycle-failure cause --
    # Runs only after a clean cycle, only after the watermark and final
    # status are already on disk: a commit failure must never look like it
    # cost this cycle its success.
    commit_cycle(layer, entries_before, run_id, runner)

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
