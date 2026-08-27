"""Pipeline loop orchestrator: triage -> compose -> screen -> gauntlet when a
class accumulates enough new accepted cards.

Spec: docs/2026-08-27-pipeline-loop-design.md. Invoked by
\\StewartCo\\25_PipelineLoop (~3x daily) as `python -m pipeline.loop --once`.

Exit 0: cycle_complete | no_trigger | deferred_lock | deferred_budget |
        dry_run_would_fire   (distinguished in logs/pipeline_status.json)
Exit 1: a stage failed or the loop itself hit a defect.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import cells, loop_state, pipeline_budget, pipeline_status
from .budget import BudgetMeter, PIPELINE_CAP_USD
from .chainlock import ChainLock, ChainLockHeld
from .composer import routable_cards
from .registry import Registry

LAYER_DEFAULT = Path(__file__).resolve().parent.parent


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _routable_counts(registry: Registry) -> dict[str, int]:
    accepted = registry.cards(status="accepted")
    return {cls: len(routable_cards(accepted, cls)[0]) for cls in cells.LIVE_CLASSES}


def _write_status(logs_dir, outcome, *, overall="OK", extra=None, spent=0.0,
                  escalations=None):
    items = {"outcome": outcome}
    items.update(extra or {})
    payload = pipeline_status.build({"loop": overall}, spent, escalations)
    payload["items"] = {**payload.get("items", {}), **items}
    payload["overall"] = overall
    payload["summary"] = f"loop: {outcome}"
    pipeline_status.write(Path(logs_dir) / "pipeline_status.json", payload)


def _stage(runner, argv, cwd):
    print(f"loop: running {' '.join(argv)}", flush=True)
    return runner(argv, cwd=str(cwd)).returncode


def run(argv=None, runner=subprocess.run) -> int:
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

    state = loop_state.load(state_path)
    # Normalise/create the state file up front so every return path below
    # (including a stage failure before any watermark mutation) leaves a
    # readable logs/loop_state.json on disk, per the "save before every
    # return" contract loop_state's two-strike bookkeeping depends on.
    loop_state.save(state_path, state)

    # -- 1. foreign-lock probe (pure info() read, never an acquire attempt) --
    probe = ChainLock(logs_dir, holder="loop", purpose="probe")
    info = probe.info()
    if info is not None:
        if probe.is_stale():
            second_strike = loop_state.record_stale_lock(state, info)
            loop_state.save(state_path, state)
            if second_strike:
                probe.break_stale()
                loop_state.clear_stale_lock(state)
                loop_state.save(state_path, state)
                # fall through: the cycle proceeds under a now-clear lock
            else:
                msg = (f"deferred_lock: chain.lock STALE (holder="
                       f"{info.get('holder')!r}), first sighting -- WARN, "
                       f"deferring; will break on next stale sighting")
                print(msg, flush=True)
                _write_status(logs_dir, "deferred_lock", overall="WARN",
                              extra={"lock_holder": str(info.get("holder")),
                                     "lock_stale": "true"})
                return 0
        else:
            msg = f"deferred_lock: chain.lock held by {info.get('holder')!r}, deferring"
            print(msg, flush=True)
            _write_status(logs_dir, "deferred_lock",
                          extra={"lock_holder": str(info.get("holder"))})
            return 0
    else:
        # No lock on disk: any previously-recorded stale-lock strike is moot
        # (that lock is gone, broken by us or released by its holder).
        if state.get("stale_lock") is not None:
            loop_state.clear_stale_lock(state)
            loop_state.save(state_path, state)

    # -- 2. trigger check --------------------------------------------------
    registry = Registry(registry_path)
    routable_counts = _routable_counts(registry)
    asset_class = loop_state.pick_class(state, routable_counts)
    if asset_class is None:
        print("no_trigger: no live class is over threshold", flush=True)
        _write_status(logs_dir, "no_trigger",
                      extra={f"routable_{c}": str(n)
                             for c, n in routable_counts.items()})
        return 0

    # -- 3. budget gate (checked before ANY metered stage may start) -------
    meter = BudgetMeter(logs_dir / "budget_ledger.jsonl",
                        monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")
    spent = meter.month_spend()
    if not pipeline_budget.may_start_batch(spent):
        msg = (f"deferred_budget: pipeline spend USD {spent:.2f} is at/above "
               f"the batch-start threshold -- parking the {asset_class} cycle")
        print(msg, flush=True)
        _write_status(logs_dir, "deferred_budget",
                      extra={"asset_class": asset_class}, spent=spent)
        return 0

    routable_count = routable_counts[asset_class]
    if args.dry_run:
        print(f"dry_run_would_fire: {asset_class} would trigger "
              f"(routable={routable_count})", flush=True)
        _write_status(logs_dir, "dry_run_would_fire",
                      extra={"asset_class": asset_class,
                             "routable_count": routable_count},
                      spent=spent)
        return 0

    # -- 4. run the cycle ----------------------------------------------------
    run_id = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-loop-{asset_class}"
    py = sys.executable
    reg_argv = ["--registry", str(registry_path)]
    stages = [
        ("pipeline.triage_batch",
         [py, "-m", "pipeline.triage_batch", *reg_argv, "--apply"], True),
        ("pipeline.composer",
         [py, "-m", "pipeline.composer", *reg_argv, "--run-id", run_id,
          "--asset-class", asset_class, "--dry-run"], False),
        ("pipeline.composer",
         [py, "-m", "pipeline.composer", *reg_argv, "--run-id", run_id,
          "--asset-class", asset_class], True),
        ("pipeline.screen",
         [py, "-m", "pipeline.screen", *reg_argv], True),
        ("pipeline.gauntlet",
         [py, "-m", "pipeline.gauntlet", *reg_argv], True),
    ]

    for module_key, stage_argv, writes_chain in stages:
        if writes_chain:
            lock = ChainLock(logs_dir, holder="loop", purpose=f"{run_id} {module_key}")
            try:
                lock.acquire()
            except ChainLockHeld:
                msg = (f"deferred_lock: chain.lock taken out from under the "
                       f"loop before {module_key} -- deferring mid-cycle")
                print(msg, flush=True)
                _write_status(logs_dir, "deferred_lock",
                              extra={"asset_class": asset_class,
                                     "at_stage": module_key},
                              spent=spent)
                return 0
            try:
                rc = _stage(runner, stage_argv, layer)
            finally:
                lock.release()
        else:
            rc = _stage(runner, stage_argv, layer)

        if rc != 0:
            print(f"loop: stage {module_key} failed rc={rc}, aborting cycle",
                 flush=True)
            _write_status(logs_dir, "stage_failed", overall="FAIL",
                          extra={"asset_class": asset_class,
                                 "failed_stage": module_key},
                          spent=spent, escalations=["run_aborted"])
            return 1

    # -- 5. success: advance the watermark, report clean -------------------
    routable_counts_after = _routable_counts(registry)
    new_watermark = routable_counts_after[asset_class]
    loop_state.record_generation(state, asset_class, run_id=run_id,
                                 routable_count=new_watermark, ts_utc=_now_utc())
    loop_state.save(state_path, state)

    # The subprocess stages may have appended to the ledger; re-read fresh
    # rather than trusting the pre-cycle `meter` instance, which is stale.
    fresh_meter = BudgetMeter(logs_dir / "budget_ledger.jsonl",
                              monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")
    print(f"cycle_complete: {asset_class} watermark now {new_watermark}", flush=True)
    _write_status(logs_dir, "cycle_complete",
                  extra={"asset_class": asset_class, "watermark": new_watermark},
                  spent=fresh_meter.month_spend())
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
