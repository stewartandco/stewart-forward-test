"""crypto_refresh: the daily top-up of every ACTIVE crypto cell (2b-3, switch 1).

A scheduled task (08:02 Perth, UTC+8) runs `python -m pipeline.crypto_refresh`
so the 08:20 quarantine daily and the 08:45 signal service read fresh daily
bars. Binance's 1d bar closes 00:00 UTC = 08:00 Perth.

READS / WRITES. Reads `cells.active_cells("crypto")` and each cell's CSV
(admission check). Writes the cell CSVs (atomically, inside
`crypto_data.fetch_symbol`; this module only ever APPENDS through it, never
repairs or trims rows), `<data>/crypto_snapshot_manifest.json` (completed
cells only, key-wise merge), and its own ledger `<logs>/degraded_crypto_refresh.json`.
It never touches the chain, git, or `chain.lock`.

THE END CAP. Every fetch is capped at 00:00 UTC of today's UTC date
(`crypto_data.last_complete_utc_day_end_ms`), so no bar dated "today UTC" is
ever written, whatever the hour or however late a hand run is. Two consumers
need that one end day: the loop preflight's same-day rule (all of a class's
cells must end on the same day) and the signal service, which serves every
asset on a single `bar_end`.

THE DEADLINE. A failed cell (any exception other than a ban / hard rate
limit) is retried after the pass, at most `MAX_RETRY_PASSES` more passes, and
no RETRY attempt starts at or after `--deadline-local` (default 08:15 Perth),
so the run is over well before the 08:20 quarantine daily. The first pass is
not gated: a late or hand run still refreshes every cell. A cell still failed
at the end keeps a `crypto_refresh_failed` item.

BAN / RATE LIMIT. `BinanceBanned` (418) or ANY `RateLimited` that reaches
this module (429; fetch_symbol has already retried it, sleeping up to 120 s
per try, so retry_ok or not it is exhausted) stops the run at once, with no
sleep (more calls could extend an IP-wide ban shared with every Binance
consumer on this box): that cell gets a failure item and every cell not yet
completed a `skipped: ...` item.

LEDGER. Items = fetch failures + `admission_problems` over the active cells,
written with `degraded.record(..., remove_unseen=True)`: a recovered item
leaves, and an empty run still refreshes `ts_utc` so the Sentinel sees a live
writer.

EXIT CODES. 0 whenever the run completed or stopped on a ban (the ledger items
are the alarm; a non-zero exit would only invite a retry that hits Binance
again); 2 when `--data-dir` does not exist (nothing written); 1 on a crash
(the exception propagates).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import cells, degraded, screen
from . import qcells
from .crypto_data import (BinanceBanned, RateLimited, fetch_symbol,
                          last_complete_utc_day_end_ms, update_snapshot_manifest)

LEDGER = "degraded_crypto_refresh.json"
WRITER = "crypto_refresh"
FETCH_SOURCE = "crypto_refresh_failed"
ADMISSION_SOURCE = "crypto_admission"

MIN_HISTORY_DAYS = 365
MAX_RETRY_PASSES = 2
FETCH_PACING_S = 0.2                    # same convention as crypto_data.main and
                                        # quarantine.FETCH_PACING_S: the IP budget
                                        # is shared with every Binance consumer here
PERTH = timezone(timedelta(hours=8))    # the box's zone; Perth has no DST
DEFAULT_DEADLINE_LOCAL = "08:15"


def admission_floor(cutoff: str = screen.DEFAULT_CUTOFF) -> str:
    """The latest first-bar date an active cell may have: MIN_HISTORY_DAYS
    before `cutoff` ("2022-12-31" for the default cutoff)."""
    d = date.fromisoformat(cutoff) - timedelta(days=MIN_HISTORY_DAYS)
    return d.isoformat()


def _first_bar(path: Path) -> str | None:
    """The first data row's date text, or None for a missing/empty file."""
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            f.readline()                              # header
            line = f.readline().strip()
    except OSError:
        return None
    return line.split(",", 1)[0] if line else None


def admission_problems(data_dir: Path, cell_list: list[tuple[str, str]]) -> list[dict]:
    """One ledger item per cell whose CSV is missing, empty, or whose first
    bar is after `admission_floor()` (too little history before the cutoff)."""
    floor = admission_floor()
    items = []
    for asset, tf in cell_list:
        key = qcells.cell_key(asset, tf)
        first = _first_bar(Path(data_dir) / f"{asset}_{tf}.csv")
        if first is None:
            reason = (f"admission: {asset} {tf} has no data (CSV missing or "
                      f"empty); needs a first bar on or before {floor}")
        elif first[:10] > floor:
            reason = (f"admission: {asset} {tf} first bar {first[:10]} is after "
                      f"{floor} (needs {MIN_HISTORY_DAYS} days of history "
                      f"before {screen.DEFAULT_CUTOFF})")
        else:
            continue
        items.append({"source": ADMISSION_SOURCE, "key": key, "reason": reason})
    return items


def refresh(data_dir: Path, cell_list: list[tuple[str, str]], *, now_ms: int,
            deadline_ms: int | None, fetch=fetch_symbol, sleep=time.sleep,
            clock=None) -> dict:
    """Top up every cell in `cell_list`. Returns `{"fetched": {manifest_key:
    result}, "items": [fetch ledger items], "aborted": str | None}`.

    `fetched` is keyed `{asset}_{tf}`, the key crypto_data's manifest uses.
    `clock()` returns epoch ms and is compared with `deadline_ms` before each
    RETRY attempt; the default is `now_ms` plus real elapsed time."""
    data_dir = Path(data_dir)
    if clock is None:
        t0 = time.monotonic()

        def clock() -> int:
            return now_ms + int((time.monotonic() - t0) * 1000)

    end_ms = last_complete_utc_day_end_ms(now_ms)
    names = {c: f"{c[0]}_{c[1]}" for c in cell_list}
    fetched: dict[str, dict] = {}
    failed: dict[tuple[str, str], str] = {}           # cell -> failure reason
    skipped: dict[tuple[str, str], str] = {}
    aborted: str | None = None
    attempted = False

    def note_failure(cell, exc):
        asset, tf = cell
        failed[cell] = f"fetch of {asset} {tf} failed: {exc!r}"
        print(f"crypto_refresh: {names[cell]} FAILED: {exc!r}")

    pending = list(cell_list)
    try:
        for pass_no in range(1 + MAX_RETRY_PASSES):
            if not pending:
                break
            retry_pass = pass_no > 0
            still_failed: list[tuple[str, str]] = []
            for n, cell in enumerate(pending):
                if retry_pass and deadline_ms is not None and clock() >= deadline_ms:
                    print("crypto_refresh: past the deadline; no further retries")
                    pending = []
                    break
                if attempted:
                    sleep(FETCH_PACING_S)
                attempted = True
                asset, tf = cell
                key = qcells.cell_key(asset, tf)
                try:
                    res = fetch(asset, tf, data_dir, now_ms=now_ms, end_ms=end_ms)
                except BinanceBanned as exc:
                    note_failure(cell, exc)
                    aborted = f"Binance IP ban (418) on {key}"
                    skip_why = f"skipped: Binance IP ban (418) on {key}; not fetched this run"
                except RateLimited as exc:
                    # fetch_symbol has already retried (3 tries, up to 120 s
                    # each) before a RateLimited reaches here, retry_ok or not:
                    # stop like a ban, no sleep (crypto_data.main and
                    # quarantine.fetch_owed do the same).
                    failed[cell] = (f"fetch of {asset} {tf} failed: Binance rate "
                                    f"limit: {exc!r}")
                    print(f"crypto_refresh: {names[cell]} FAILED: {exc!r}; Binance "
                          f"rate limit (retry_after {exc.retry_after}s), stopping",
                          file=sys.stderr)
                    aborted = f"Binance rate limit (429) on {key}"
                    skip_why = (f"skipped: Binance rate limit (429) on {key}; "
                                f"not fetched this run")
                except Exception as exc:     # one dead cell must not stop the rest
                    note_failure(cell, exc)
                    still_failed.append(cell)
                    continue
                else:
                    fetched[names[cell]] = res
                    failed.pop(cell, None)
                    print(f"crypto_refresh: {names[cell]}: {res['rows']} rows")
                    continue
                # ban / hard rate limit: stop at once, owe every cell left
                for rest in cell_list:
                    if rest != cell and names[rest] not in fetched and rest not in failed:
                        skipped[rest] = skip_why
                pending, still_failed = [], []
                break
            else:
                pending = still_failed
                continue
            break
    finally:
        # Cells completed before a ban, a crash or a Ctrl+C hold valid,
        # already-replaced files: record their provenance however the run ends.
        if fetched:
            update_snapshot_manifest(data_dir, fetched)

    items = []
    for cell in cell_list:
        key = qcells.cell_key(*cell)
        if cell in failed:
            items.append({"source": FETCH_SOURCE, "key": key, "reason": failed[cell]})
        elif cell in skipped:
            items.append({"source": FETCH_SOURCE, "key": key, "reason": skipped[cell]})
    return {"fetched": fetched, "items": items, "aborted": aborted}


def _parse_now(text: str | None) -> int:
    if text is None:
        return int(time.time() * 1000)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _deadline_ms(hhmm: str, now_ms: int) -> int:
    """`hhmm` on now's local (UTC+8) date, as epoch ms."""
    hh, mm = (int(p) for p in hhmm.split(":"))
    local_now = datetime.fromtimestamp(now_ms / 1000, tz=PERTH)
    local = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return int(local.timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    layer = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.crypto_refresh",
        description="Daily top-up of every active crypto cell's CSV.")
    ap.add_argument("--data-dir", type=Path, default=layer / "data")
    ap.add_argument("--logs-dir", type=Path, default=layer / "logs")
    ap.add_argument("--deadline-local", default=DEFAULT_DEADLINE_LOCAL,
                    help="HH:MM today in UTC+8: no retry attempt starts after it")
    ap.add_argument("--now", default=None, help="ISO8601 override (tests)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"crypto_refresh: data dir {data_dir} does not exist; nothing written",
              file=sys.stderr)
        return 2

    now_ms = _parse_now(args.now)
    deadline_ms = _deadline_ms(args.deadline_local, now_ms)
    ledger = Path(args.logs_dir) / LEDGER
    cell_list = cells.active_cells("crypto")
    if not cell_list:
        print("crypto_refresh: no active crypto cells")
        degraded.record(ledger, WRITER, [], remove_unseen=True)
        return 0

    out = refresh(data_dir, cell_list, now_ms=now_ms, deadline_ms=deadline_ms,
                  fetch=fetch_symbol, sleep=time.sleep)
    items = out["items"] + admission_problems(data_dir, cell_list)
    degraded.record(ledger, WRITER, items, remove_unseen=True)
    if out["aborted"]:
        print(f"crypto_refresh: ABORTED: {out['aborted']}")
    print(f"crypto_refresh: {len(out['fetched'])}/{len(cell_list)} cells fetched, "
          f"{len(items)} degraded item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
