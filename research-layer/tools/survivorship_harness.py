"""Study harness (off-chain survivorship study): train run, full run, gauntlet
secondary, and a resumable windowed runner. Calls the real pipeline engine;
re-implements nothing."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.beats_hold import gauntlet_beats_hold
from pipeline.engine import run_spec
# worker_env lives in gauntlet_core (research-layer/CLAUDE.md "Gauntlet worker
# pool must fit the box" names gauntlet.py, which re-exports it); this module
# already imports gauntlet_core, so it pulls no clustering/PBO module in.
from pipeline.gauntlet_core import (available_commit_mb, daily_returns_with_dates,
                                    evaluate_standalone, worker_count, worker_env)
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


MAX_WORKERS = 4


def default_worker_request() -> int:
    """The --workers default: half the cores left after two for the box (the
    half-hourly gauntlet worker and the desktop), at most MAX_WORKERS."""
    n = os.cpu_count() or 1
    return min(MAX_WORKERS, max(1, (n - 2) // 2))


def default_workers(requested: int) -> int:
    return max(1, min(requested, worker_count(os.cpu_count() or 1, available_commit_mb())))


def run_pool(job, items: list, workers: int) -> list:
    """parallel.run_all inside the pipeline's worker env (one BLAS thread per
    spawned worker, OPENBLAS/OMP/MKL_NUM_THREADS=1, restored afterwards)."""
    with worker_env():
        return run_all(job, items, workers)


def eta(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s"


def progress(label: str, done: int, total: int, new: int, t0: float,
             unit: str = "items", extra: str = "") -> str:
    """One progress line: done/total, rate over this run, ETA of the rest."""
    el = max(time.monotonic() - t0, 1e-9)
    rate = new / el
    rest = (total - done) / rate if rate > 0 else float("nan")
    return (f"{label}: {done}/{total} {unit} ({new} this run{extra}), {rate:.2f} {unit}/s, "
            f"ETA {eta(rest) if rest == rest else 'n/a'}")


def _now() -> datetime:
    return datetime.now(CLOCK)


def _done_keys(out: Path) -> set[str]:
    """Keys with a complete line in `out` (after _drop_stale, every such line
    of a current row carries the current stamp)."""
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


def _stamp_ok(rec: dict, stamp: dict | None) -> bool:
    return not stamp or all(rec.get(f) == v for f, v in stamp.items())


def _drop_stale(out: Path, stamps: dict) -> int:
    """Move every line whose key is a current row but whose stamp differs from
    that row's current stamp (final review I2) to `<stem>.stale.jsonl`, and
    rewrite `out` without them (tmp + replace). So each key keeps at most one
    line and analyse's duplicate refusal stays meaningful; nothing is deleted.
    Unparseable lines and lines for keys not in `stamps` are kept as they are."""
    if not stamps or not out.exists():
        return 0
    keep, stale = [], []
    for line in out.read_text(encoding="utf-8").splitlines(keepends=True):
        try:
            rec = json.loads(line)
            k = rec["key"]
        except (ValueError, KeyError, TypeError):
            keep.append(line)
            continue
        (keep if k not in stamps or _stamp_ok(rec, stamps[k]) else stale).append(line)
    if not stale:
        return 0
    side = out.with_name(out.stem + ".stale.jsonl")
    with side.open("a", encoding="utf-8", newline="") as f:
        f.write("".join(stale))
        f.flush()
        os.fsync(f.fileno())
    tmp = out.with_name(out.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write("".join(keep))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)
    return len(stale)


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
    return t + timedelta(seconds=n * est_s) < end


def run_arm(rows_in: list[dict], job, out: Path, workers: int, now=_now,
            chunk: int = 50, est_s_per_item: float = 2.0,
            label: str | None = None) -> dict:
    """Run `job` over the rows not yet done in `out`, in window-checked chunks,
    appending one JSON line per result. A row's optional `stamp` dict is
    written into its line; a line whose stamp differs from the row's current
    one is not done: it is moved to `<stem>.stale.jsonl` (the file is
    rewritten without it) and the row is recomputed."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _trim_torn_tail(out)
    stamps = {r["key"]: r["stamp"] for r in rows_in if r.get("stamp")}
    n_stale = _drop_stale(out, stamps)
    done_keys = _done_keys(out)
    todo = [r for r in rows_in if r["key"] not in done_keys]
    summary = {"done": 0, "skipped": len(rows_in) - len(todo),
               "stopped_at_window": False, "errors": [], "stale_moved": n_stale}
    t0 = time.monotonic()
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        if not _chunk_ok(now, len(batch), est_s_per_item):
            summary["stopped_at_window"] = True
            break
        results = run_pool(job, [r["item"] for r in batch], workers)
        lines = []
        for r, res in zip(batch, results):
            if isinstance(res, CellError):
                summary["errors"].append({"key": r["key"], "error": str(res)})
                continue
            rec = dict(res)
            rec.update(key=r["key"], stratum=r["stratum"], tkey=r["tkey"],
                       pair=r["pair"], tf=r["tf"])
            rec.update(r.get("stamp") or {})
            lines.append(json.dumps(rec, sort_keys=True) + "\n")
        if lines:
            with out.open("a", encoding="utf-8", newline="") as f:
                f.write("".join(lines))
                f.flush()
                os.fsync(f.fileno())
        summary["done"] += len(lines)
        if label:
            print(progress(label, summary["skipped"] + i + len(batch), len(rows_in),
                           i + len(batch), t0), flush=True)
    return summary
