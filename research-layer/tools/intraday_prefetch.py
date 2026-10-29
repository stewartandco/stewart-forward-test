"""intraday_prefetch: the phase-2 history fetch with the per-cell check
(2b-3 plan 4, D-i1). HAND-RUN ONLY, never scheduled.

    python -m tools.intraday_prefetch --data-dir DIR --out REPORT.json \
        [--timeframes 1h,4h,12h] [--assets A,B] [--now ISO|EPOCH_MS]

Fetches the FULL listing history of the admitted crypto assets (the 76 of
`cells.ACTIVE_CELLS["crypto"]`) at 1h, 4h and 12h (228 cells) through the
existing `crypto_refresh.refresh` path (`crypto_data.fetch_symbol`, capped at
00:00 UTC of the run's UTC day, incremental, validated, atomic), then clips
every intraday file it completed so it never starts before its asset's 1d
file, and reports admission, holes, failures and timing per cell.

THE CLIP. An intraday file may not start before `<asset>_1d.csv`: the 1d file
is the research layer's history (SUN's starts 2021-06-18, the redenomination
clip), and an intraday cell reaching further back would hand a strategy bars
its 1d twin never had. `clip_to_daily_start` drops rows whose `date[:10]` is
before the 1d file's first day, writes `.tmp`, `os.replace`s it with the
signal service's retry (8 retries, 0.25 s apart, on PermissionError) and then
RE-STAMPS the cell's `crypto_snapshot_manifest.json` entry, because refresh
stamped sha256/rows/first_date before the clip. A file that needs no clip is
not touched (and not re-stamped). A clip that fails BEFORE the replace
(locked file, no 1d anchor, a malformed file) is a FAILED cell: the file and
its manifest entry are left exactly as refresh wrote them. One failure is
different: if the file WAS replaced and the manifest re-stamp then raises, the
file is clipped (its true row count is reported, `clipped` names it) but its
manifest entry is stale (still refresh's pre-clip sha256/rows/first_date);
that cell is `clipped_manifest_stale` and a `failed` row. A plain re-run
HEALS it: `refresh` re-stamps every cell it completes from the file on disk
(`fetch_symbol` returns the on-disk sha256/rows/first_date even when it adds no
bar, and refresh writes them to the manifest in its `finally`), and the clip
then finds nothing to drop. Check that the re-run's report shows the cell
`fetched`. Never hand-edit the tracked manifest and never refetch to repair it.

CELLS. The admitted assets are read from `cells.ACTIVE_CELLS["crypto"]`,
DELIBERATELY not from `cells.active_cells()`: a cell in `EXCLUDED_CELLS` is
still fetched, because the admission rows it produces are what decides the
exclusion. Do not "fix" this to `active_cells()`: that would silently stop
fetching exactly the cells the exclusion decision needs.

THE STOP TIMES. `refresh` is given `stop_ms` = the EARLIEST of the next 07:58
Perth (the run day's, or the next day's when the run starts at or after 08:40)
and 20:00 Perth of the run day (the evening stop: no live-tree write
20:00-07:00): no attempt starts at or after it, so a long run stops cleanly
before the 08:02 `33_CryptoGridRefresh`, which shares the IP and
read-merge-writes the same tracked manifest, and never runs on into the night.
A cold full run takes about 45 minutes (2,542 s measured), so on the live tree
start it by 18:30. A start at or after 20:00 has no window left: it fetches and
writes nothing and reports every cell `aborted`. The cells not reached are
`aborted` report rows and a re-run resumes incrementally.
The local tail obeys the same stop: a cell that NEEDS a clip checks the clock
right before it writes, and at or after the stop (07:58 or 20:00) it writes
nothing (no `.tmp`, no replace, no manifest write: the tracked manifest is
read-merge-written by the live refresh at 08:02). That cell is `clip_deferred`
and a `failed` row ("clip skipped: past the stop time HH:MM"); its file is still
unclipped, so a re-run (after 08:40) fetches nothing new and clips it. A cell
that needs no clip is only read, never written, and is unaffected.
The CLI refuses (exit 2) to START between 07:58 and 08:40 Perth unless `--now`
is given (tests); the zone is fixed UTC+8 as in `crypto_refresh`.

BAN / RATE LIMIT. A `BinanceBanned` or `RateLimited` that reaches the caller
stops the whole run at once (refresh's own rule): the failing cell is a
`failed` row, every cell not yet fetched an `aborted` row, nothing is retried.
Any other cell failure is retried by refresh's own two extra passes.

WRITES. The cell CSVs, `data/crypto_snapshot_manifest.json` and `--out`. NEVER
`logs/degraded_crypto_refresh.json` (the live refresh ledger: this tool does
not call `degraded.record`), the chain, git or any lock.

REPORT (`run`'s dict, JSON-serialisable):
  cells        one row per cell in run order (coarsest timeframe first per
               asset): {cell, status fetched|failed|clip_failed|clip_deferred|
               clipped_manifest_stale|aborted,
               attempts (FETCH CALLS, not HTTP requests: each call pages 1000
               klines per request, so a cold cell costs about ceil(rows/1000)
               requests),
               seconds (time in fetch), rows, first_date, last_date, clipped,
               reason}
  fetched      {cell: {rows, first_date, last_date}} for every cell refresh
               completed (a clip_failed, clip_deferred or clipped_manifest_stale
               cell is in here AND in `failed`)
  failed       [{cell, reason}]: fetch failures (incl. the ban cell) and clip
               failures
  clipped      {cell: rows dropped} for cells that needed a clip
  admission    crypto_refresh.admission_problems over the cell list
  feed_health  feed_hole / feed_stale items (holes for every cell with a file,
               stale only for cells fetched OK)
  aborted      [{cell, reason}] cells never attempted (ban, rate limit, the
               07:58 or 20:00 stop)
  elapsed_s    wall seconds
  bytes_by_tf  {tf: total file bytes of that timeframe's files after the run}

Exit codes: 0 = ran (failures are report rows), 2 = --data-dir missing, a bad
argument, or a start inside the morning window, 1 = crash.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAYER = Path(__file__).resolve().parent.parent
if str(LAYER) not in sys.path:
    sys.path.insert(0, str(LAYER))

from pipeline import cells                                              # noqa: E402
from pipeline import crypto_refresh as cr                               # noqa: E402
from pipeline import qcells                                             # noqa: E402
from pipeline.crypto_data import (FIELDS, TF_MS, fetch_symbol,          # noqa: E402
                                  update_snapshot_manifest, validate_rows)

# cells.py names its four timeframes "PHASE_1_TIMEFRAMES" (the SP5 phase-1
# grid); the grid record's "phase 2" cells are the intraday three of that same
# set. Derived, never hard-coded: 1h, 4h, 12h (Coen 10-29).
PHASE_2_TIMEFRAMES = tuple(t for t in cells.PHASE_1_TIMEFRAMES if t != "1d")

PERTH = cr.PERTH
STOP_LOCAL = (7, 58)            # no attempt starts at or after this (Perth)
RESUME_LOCAL = (8, 40)          # the morning jobs are done by here
EVENING_STOP_LOCAL = (20, 0)    # no attempt starts at or after this either (Perth)
REPLACE_RETRIES = 8             # the signal service's os.replace convention
REPLACE_SLEEP_S = 0.25


# ---------------------------------------------------------------- cells

def cells_for(timeframes, assets=None) -> list[tuple[str, str]]:
    """The admitted assets (manifest order) x `timeframes`, coarsest first per
    asset. `assets` restricts to a subset (still in manifest order); an asset
    outside the admitted set, or "1d", is a ValueError."""
    admitted = tuple(cells.ACTIVE_CELLS["crypto"]["assets"])
    tfs = list(dict.fromkeys(timeframes))
    for tf in tfs:
        if tf == "1d" or tf not in cells.TIMEFRAMES:
            raise ValueError(f"timeframe {tf!r} is not a prefetch target "
                             f"(declared {cells.TIMEFRAMES}, never 1d)")
    if not tfs:
        raise ValueError("no timeframes given")
    tfs.sort(key=lambda t: -TF_MS[t])
    if assets is not None:
        unknown = [a for a in assets if a not in admitted]
        if unknown:
            raise ValueError(f"not admitted assets: {unknown}")
        want = set(assets)
        admitted = tuple(a for a in admitted if a in want)
        if not admitted:
            raise ValueError("no assets given")
    return [(a, tf) for a in admitted for tf in tfs]


# ---------------------------------------------------------------- the clock

def _local(now_ms: int) -> datetime:
    return datetime.fromtimestamp(now_ms / 1000, tz=PERTH)


def in_morning_window(now_ms: int) -> bool:
    """True from 07:58:00 up to (not including) 08:40:00 Perth."""
    t = _local(now_ms)
    start = t.replace(hour=STOP_LOCAL[0], minute=STOP_LOCAL[1], second=0, microsecond=0)
    end = t.replace(hour=RESUME_LOCAL[0], minute=RESUME_LOCAL[1], second=0, microsecond=0)
    return start <= t < end


def stop_ms_for(now_ms: int) -> int:
    """The earliest of two stops: the next 07:58 Perth (the run day's, or the
    next day's when the run starts at or after 08:40: the morning jobs are
    over) and 20:00 Perth of the run day (the evening stop: no live-tree write
    20:00-07:00). A run starting at or after 20:00 therefore gets a stop that
    has already passed: it fetches and writes nothing and reports every cell
    `aborted`."""
    t = _local(now_ms)
    stop = t.replace(hour=STOP_LOCAL[0], minute=STOP_LOCAL[1], second=0, microsecond=0)
    resume = t.replace(hour=RESUME_LOCAL[0], minute=RESUME_LOCAL[1], second=0, microsecond=0)
    if t >= resume:
        stop += timedelta(days=1)
    evening = t.replace(hour=EVENING_STOP_LOCAL[0], minute=EVENING_STOP_LOCAL[1],
                        second=0, microsecond=0)
    return int(min(stop, evening).timestamp() * 1000)


def _wall_now_ms() -> int:
    return int(time.time() * 1000)


def _parse_now(text: str) -> int:
    text = text.strip()
    if text.isdigit():
        return int(text)
    return cr._parse_now(text)


# ---------------------------------------------------------------- the clip

def _replace_with_retry(tmp: Path, path: Path, sleep) -> None:
    """os.replace, retried REPLACE_RETRIES times on PermissionError (Windows
    refuses the replace while a reader holds the target open), then raised.
    `fetch_symbol` itself has no such retry."""
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_RETRIES:
                raise
            sleep(REPLACE_SLEEP_S)


class ManifestStale(Exception):
    """The CSV was clipped and replaced but re-stamping its manifest entry
    raised: the file is right, the manifest entry is stale. Carries the rows
    dropped and the rows now in the file."""

    def __init__(self, dropped: int, rows: int, cause: BaseException):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.dropped = dropped
        self.rows = rows
        self.cause = cause


class ClipDeferred(Exception):
    """`past_stop()` said the stop time has passed: nothing was written."""


def clip_to_daily_start(data_dir: Path, asset: str, tf: str, *, sleep=time.sleep,
                        past_stop=None) -> int:
    """Drop the rows of `<asset>_<tf>.csv` dated before the first bar of
    `<asset>_1d.csv` (compared on `date[:10]`); returns the rows dropped.

    Nothing to drop = the file is not touched and the manifest not re-stamped.
    Otherwise: validate, write `.tmp`, replace (with the retry), then re-stamp
    the cell's manifest entry (sha256 / rows / first_date / last_date of the
    file now on disk). Any failure BEFORE the replace leaves the CSV as it was and
    no `.tmp` (a failed re-stamp comes after the replace: the CSV WAS replaced).
    Raises FileNotFoundError when there is no 1d anchor, ValueError when the
    clip would empty the file (both before any write), and ManifestStale when
    the file was replaced but the re-stamp failed (the clip DID happen).
    `past_stop()` (optional) is asked right before the first write; True raises
    ClipDeferred with nothing written."""
    data_dir = Path(data_dir)
    anchor = cr._first_bar(data_dir / f"{asset}_1d.csv")
    if anchor is None:
        raise FileNotFoundError(f"{asset}_1d.csv is missing or empty: no 1d start to clip to")
    first_day = anchor[:10]

    path = data_dir / f"{asset}_{tf}.csv"
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    validate_rows(rows, tf, path.name)          # a malformed file is refused, not clipped
    keep = [r for r in rows if r["date"][:10] >= first_day]
    dropped = len(rows) - len(keep)
    if dropped == 0:
        return 0
    if not keep:
        raise ValueError(f"{path.name}: every bar is before the 1d start {first_day}; "
                         f"refusing to empty the file")

    if past_stop is not None and past_stop():
        raise ClipDeferred()
    tmp = Path(str(path) + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(FIELDS)
            w.writerows([r[k] for k in FIELDS] for r in keep)
        _replace_with_retry(tmp, path, sleep)
    except BaseException:
        tmp.unlink(missing_ok=True)             # never leave a .tmp masquerading as data
        raise

    try:
        update_snapshot_manifest(data_dir, {f"{asset}_{tf}": {
            "rows": len(keep),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "first_date": keep[0]["date"],
            "last_date": keep[-1]["date"]}})
    except Exception as exc:
        raise ManifestStale(dropped, len(keep), exc) from exc
    return dropped


# ---------------------------------------------------------------- the run

def run(data_dir: Path, timeframes, *, now_ms: int, assets=None, fetch=fetch_symbol,
        sleep=time.sleep, clock=None) -> dict:
    """Fetch, clip and check every cell of `cells_for(timeframes, assets)`.
    `clock()` (epoch ms) overrides refresh's own now-plus-elapsed clock (tests).
    See the module docstring for the report."""
    data_dir = Path(data_dir)
    t0 = time.monotonic()
    cell_list = cells_for(timeframes, assets)
    key_of = {c: qcells.cell_key(*c) for c in cell_list}

    attempts: Counter = Counter()
    spent: dict = defaultdict(float)

    def counting_fetch(symbol, tf, ddir, now_ms=None, end_ms=None):
        attempts[(symbol, tf)] += 1
        t = time.monotonic()
        try:
            return fetch(symbol, tf, ddir, now_ms=now_ms, end_ms=end_ms)
        finally:
            spent[(symbol, tf)] += time.monotonic() - t

    stop_ms = stop_ms_for(now_ms)
    if clock is None:                       # refresh's own default, shared with the clip tail
        def clock() -> int:
            return now_ms + int((time.monotonic() - t0) * 1000)
    stop_hhmm = _local(stop_ms).strftime("%H:%M")

    out = cr.refresh(data_dir, cell_list, now_ms=now_ms, deadline_ms=None,
                     fetch=counting_fetch, sleep=sleep, clock=clock, stop_ms=stop_ms)

    reasons: dict[str, str] = {}
    failed: list[dict] = []
    aborted: list[dict] = []
    for item in out["items"]:
        row = {"cell": item["key"], "reason": item["reason"]}
        reasons[item["key"]] = item["reason"]
        (aborted if item["reason"].startswith("skipped:") else failed).append(row)

    clipped: dict[str, int] = {}
    clip_failed: dict[str, tuple[str, str]] = {}      # cell -> (status, reason)
    for cell in cell_list:
        if f"{cell[0]}_{cell[1]}" not in out["fetched"]:
            continue
        key = key_of[cell]
        try:
            n = clip_to_daily_start(data_dir, cell[0], cell[1], sleep=sleep,
                                    past_stop=lambda: clock() >= stop_ms)
        except ClipDeferred:
            # past the stop: no clip, so no manifest write (the live 08:02
            # refresh read-merge-writes the same file)
            reason = f"clip skipped: past the stop time {stop_hhmm}; file left unclipped"
            clip_failed[key] = ("clip_deferred", reason)
            failed.append({"cell": key, "reason": reason})
        except ManifestStale as exc:
            reason = (f"clipped, manifest stale: dropped {exc.dropped} row(s), "
                      f"file now {exc.rows} rows, but the manifest re-stamp "
                      f"failed ({exc})")
            clipped[key] = exc.dropped
            clip_failed[key] = ("clipped_manifest_stale", reason)
            failed.append({"cell": key, "reason": reason})
            print(f"intraday_prefetch: {key}: {reason}")
        except Exception as exc:                # one cell's clip must not stop the rest
            reason = f"clip failed: {type(exc).__name__}: {exc}"
            clip_failed[key] = ("clip_failed", reason)
            failed.append({"cell": key, "reason": reason})
            print(f"intraday_prefetch: {key}: {reason}")
        else:
            if n:
                clipped[key] = n

    admission = cr.admission_problems(data_dir, cell_list)
    on_disk = [c for c in cell_list if (data_dir / f"{c[0]}_{c[1]}.csv").exists()]
    feed_health = cr._feed_health_items(data_dir, on_disk, out, now_ms)

    rows_out = []
    fetched: dict[str, dict] = {}
    for cell in cell_list:
        key = key_of[cell]
        name = f"{cell[0]}_{cell[1]}"
        row = {"cell": key, "attempts": attempts[cell],
               "seconds": round(spent[cell], 3), "clipped": clipped.get(key, 0)}
        if name in out["fetched"]:
            res = out["fetched"][name]
            first = cr._first_bar(data_dir / f"{name}.csv") or res["first_date"]
            info = {"rows": res["rows"] - clipped.get(key, 0),
                    "first_date": first, "last_date": res["last_date"]}
            fetched[key] = info
            row.update(info)
            row["status"] = "fetched"
            if key in clip_failed:
                row["status"], row["reason"] = clip_failed[key]
        elif key in reasons and not reasons[key].startswith("skipped:"):
            row.update(status="failed", reason=reasons[key])
        else:
            row.update(status="aborted", reason=reasons.get(key))
        rows_out.append(row)

    bytes_by_tf = {tf: sum(p.stat().st_size for a, t in cell_list if t == tf
                           for p in [data_dir / f"{a}_{t}.csv"] if p.exists())
                   for tf in dict.fromkeys(t for _, t in cell_list)}

    return {"cells": rows_out, "fetched": fetched, "failed": failed, "clipped": clipped,
            "admission": admission, "feed_health": feed_health, "aborted": aborted,
            "elapsed_s": round(time.monotonic() - t0, 3), "bytes_by_tf": bytes_by_tf}


# ---------------------------------------------------------------- the CLI

def _summary(report: dict, out_path: Path) -> str:
    n = len(report["cells"])
    mb = sum(report["bytes_by_tf"].values()) / 1e6
    return (f"intraday_prefetch: {n} cells: {len(report['fetched'])} fetched, "
            f"{len(report['failed'])} failed, {len(report['aborted'])} aborted, "
            f"{len(report['clipped'])} clipped, {len(report['admission'])} admission, "
            f"{len(report['feed_health'])} feed health; {mb:.1f} MB in "
            f"{report['elapsed_s']:.0f} s; report {out_path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.intraday_prefetch",
        description="Hand-run: fetch the admitted crypto assets' full 1h/4h/12h "
                    "history, clip it to the 1d start and report per cell.")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="report JSON path")
    ap.add_argument("--timeframes", default=",".join(PHASE_2_TIMEFRAMES))
    ap.add_argument("--assets", default=None, help="comma-separated admitted symbols")
    ap.add_argument("--now", default=None,
                    help="ISO8601 UTC or epoch ms; sets the clock AND skips the "
                         "07:58-08:40 refusal (tests)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"intraday_prefetch: data dir {data_dir} does not exist; nothing written",
              file=sys.stderr)
        return 2

    now_ms = _parse_now(args.now) if args.now is not None else _wall_now_ms()
    if args.now is None and in_morning_window(now_ms):
        print("intraday_prefetch: REFUSED: it is inside 07:58-08:40 Perth, when "
              "33_CryptoGridRefresh runs on this IP and the same manifest; "
              "start it after 08:40", file=sys.stderr)
        return 2

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    assets = ([a.strip() for a in args.assets.split(",") if a.strip()]
              if args.assets is not None else None)       # "" is an empty list, never "all"
    try:
        cells_for(timeframes, assets)           # fail on a bad argument before any fetch
    except ValueError as exc:
        print(f"intraday_prefetch: {exc}", file=sys.stderr)
        return 2

    report = run(data_dir, timeframes, now_ms=now_ms, assets=assets,
                 fetch=fetch_symbol)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(_summary(report, Path(args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
