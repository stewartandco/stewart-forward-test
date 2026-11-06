"""Study harness (off-chain survivorship study): train run, full run, gauntlet
secondary, and a resumable windowed runner. Calls the real pipeline engine;
re-implements nothing."""
from __future__ import annotations

import json
import os
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.beats_hold import gauntlet_beats_hold
from pipeline.engine import run_spec
from pipeline.gauntlet_core import (available_commit_mb, daily_returns_with_dates,
                                    evaluate_standalone, worker_count)
from pipeline.parallel import CellError, run_all
from pipeline.screen import GATE_MIN_TRADES, SpecJob, load_bars
from tools.survivorship_universe import CUTOFF, END

CLOCK = ZoneInfo("Australia/Perth")
WINDOW_START = dtime(8, 40)
WINDOW_END = dtime(20, 0)


def _pair(spec: dict) -> str:
    return spec["universe"]["assets"][0]


def train_job(item: tuple[dict, str, str]) -> dict:
    spec, data_dir, tf = item
    pair = _pair(spec)
    bars = load_bars(Path(data_dir), pair, CUTOFF, tf)
    result, passed, reason = SpecJob(GATE_MIN_TRADES)((spec, {pair: bars}))
    m = result["metrics"]
    return {"sid": spec.get("strategy_id"), "screen_pass": bool(passed),
            "reason": reason, "trades": m["trades"], "net_pnl": m["net_pnl"]}


def full_job(item: tuple[dict, str, str]) -> dict:
    spec, data_dir, tf = item
    pair = _pair(spec)
    bars = load_bars(Path(data_dir), pair, END, tf)
    res = run_spec(spec, {pair: bars})
    dated = daily_returns_with_dates(res["equity"])
    _, bh = gauntlet_beats_hold(spec, {pair: bars}, dated, CUTOFF)
    s = bh["strategy_sharpe"]
    return {"m": s if s is not None else 0.0, "m_none": s is None,
            "hold_sharpe": bh["hold_sharpe"], "n_days": bh["n_days"]}


def gauntlet_job(item: tuple[dict, str, str]) -> dict:
    spec, data_dir, tf = item
    pair = _pair(spec)
    bars = load_bars(Path(data_dir), pair, END, tf)
    r = evaluate_standalone(spec, {pair: bars}, CUTOFF, perturb=True, battery="v6.2")
    return {"sid": r["sid"], "passed": r["passed"], "reason": r["reason"],
            "beats_hold": r["metrics"].get("beats_hold")}


def default_workers(requested: int) -> int:
    return max(1, min(requested, worker_count(os.cpu_count() or 1, available_commit_mb())))


def _now() -> datetime:
    return datetime.now(CLOCK)


def _done_keys(out: Path) -> set[str]:
    keys: set[str] = set()
    if not out.exists():
        return keys
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            keys.add(json.loads(line)["key"])
        except (ValueError, KeyError, TypeError):
            continue          # partial/torn line: its key is recomputed
    return keys


def _trim_torn_tail(out: Path) -> None:
    """Drop a torn final fragment (no trailing newline): it was never a
    complete record, and appending after it would glue the next line on."""
    if not out.exists() or out.stat().st_size == 0:
        return
    data = out.read_bytes()
    if data.endswith(b"\n"):
        return
    keep = data.rfind(b"\n") + 1
    with out.open("r+b") as f:
        f.truncate(keep)
        f.flush()
        os.fsync(f.fileno())


def _chunk_ok(now, n: int, est_s: float) -> bool:
    t = now().astimezone(CLOCK)
    if not (WINDOW_START <= t.time() < WINDOW_END):
        return False
    end = t.replace(hour=WINDOW_END.hour, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    return t + timedelta(seconds=n * est_s) < end


def run_arm(rows_in: list[dict], job, out: Path, workers: int, now=_now,
            chunk: int = 50, est_s_per_item: float = 2.0) -> dict:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _trim_torn_tail(out)
    done_keys = _done_keys(out)
    todo = [r for r in rows_in if r["key"] not in done_keys]
    summary = {"done": 0, "skipped": len(rows_in) - len(todo),
               "stopped_at_window": False, "errors": []}
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        if not _chunk_ok(now, len(batch), est_s_per_item):
            summary["stopped_at_window"] = True
            break
        results = run_all(job, [r["item"] for r in batch], workers)
        lines = []
        for r, res in zip(batch, results):
            if isinstance(res, CellError):
                summary["errors"].append({"key": r["key"], "error": str(res)})
                continue
            rec = dict(res)
            rec.update(key=r["key"], stratum=r["stratum"], tkey=r["tkey"],
                       pair=r["pair"], tf=r["tf"])
            lines.append(json.dumps(rec, sort_keys=True) + "\n")
        if lines:
            with out.open("a", encoding="utf-8", newline="") as f:
                f.write("".join(lines))
                f.flush()
                os.fsync(f.fileno())
        summary["done"] += len(lines)
    return summary
