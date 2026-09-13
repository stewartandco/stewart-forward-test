"""Research-layer-native fetcher for the expanded crypto grid (SP5 spec s6).

Keyless Binance spot klines, run as an explicit deliberate command -- never on
a schedule, never implicit -- because a generation pins to the snapshot it was
bred on (same posture as `pipeline.tradfi_data`):

    python -m pipeline.crypto_data fetch --timeframes 1d
    python -m pipeline.crypto_data fetch --timeframes 1d,4h --assets BTCUSDT

Ban safety is the design constraint, not a nicety: HTTP 418 is an IP-WIDE ban
on this box, and every Binance consumer (legacy `data_fetch`, trading-systems'
own fetchers) shares the IP. So 418 aborts the whole run immediately and is
never retried; 429 sleeps out the server's own Retry-After; pages are paced at
0.2s. Incremental resume keeps re-runs cheap: an existing CSV's last bar sets
`startTime`, so a daily top-up is one request per cell, not a full history.

Provenance mirrors the tradfi conventions: every successful cell lands in
`data/crypto_snapshot_manifest.json` (key-wise merged, so unrelated entries
survive subset runs) with row count, sha256 of the file bytes, and date span.

TF_MS below is fetch-step arithmetic for THIS module only. The declared
research grid is `cells.TIMEFRAMES`; fetch refuses any tf outside it. Same
separation as trading-systems' cfg.timeframes vs TF_MS -- never equalise.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import cells

BASE = "https://api.binance.com/api/v3/klines"
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
         "4h": 14_400_000, "12h": 43_200_000, "1d": 86_400_000}

FIELDS = ("date", "open", "high", "low", "close", "volume")
DATE_FMT = "%Y-%m-%d %H:%M:%S"
UNIVERSE_TOOL = "tools_select_crypto_universe.py"

# Clock-skew margin on the still-open-bar drop: resume never re-serves a bar,
# so a skew-admitted partial bar would be PERMANENT in a byte-deterministic
# corpus. The margin only costs admitting a genuinely-closed bar one run
# later; a partial bar admitted once would poison every downstream read of
# full history forever.
SKEW_MARGIN_MS = 60_000


class RateLimited(Exception):
    """HTTP 429: the server told us how long to back off.

    `retry_ok=False` marks a Retry-After beyond the 120s cap: the server is
    telling us to go away, so `_get_with_retry` never retries it -- it goes
    straight to main's abort path, exactly like a ban. The stored retry_after
    is capped at 120s because the cap bounds how long a LEGITIMATE wait can
    be; it is never a license to sleep less and retry early.
    """

    def __init__(self, retry_after: float, retry_ok: bool = True):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after
        self.retry_ok = retry_ok


class BinanceBanned(Exception):
    """HTTP 418: an IP-WIDE ban. Every Binance consumer on this box shares the
    IP, so this is never retried and aborts the whole run."""


def _http_get_json(url: str):
    """The single network boundary -- every test monkeypatches here or below.

    429 -> RateLimited honoring the Retry-After header (capped at 120s;
    missing header defaults to 10s). 418 -> BinanceBanned. Anything else
    propagates untouched.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "research-layer/crypto_data"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            try:
                raw = float(e.headers.get("Retry-After", 10))
            except (TypeError, ValueError):
                raw = 10.0
            raise RateLimited(min(raw, 120.0), retry_ok=raw <= 120.0) from e
        if e.code == 418:
            raise BinanceBanned(
                "HTTP 418 from Binance: this IP is BANNED (exchange-wide, shared by "
                "every Binance consumer on this box) - do NOT re-run until the ban "
                "expires") from e
        raise


def _get_with_retry(url: str, tries: int = 3):
    """RateLimited sleeps its own retry_after (counts as a try); other
    transient errors back off 2*attempt seconds. BinanceBanned is NEVER
    retried -- see the class docstring -- and neither is a RateLimited whose
    Retry-After exceeded the 120s cap (`retry_ok=False`): both go straight
    up to main's abort path. Exhausted tries raise the last error.
    """
    for attempt in range(1, tries + 1):
        try:
            return _http_get_json(url)
        except BinanceBanned:
            raise
        except RateLimited as e:
            if not e.retry_ok or attempt == tries:
                raise
            time.sleep(e.retry_after)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == tries:
                raise
            time.sleep(2 * attempt)


def _date_to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, DATE_FMT)
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def _ms_to_date(open_ms: int) -> str:
    return datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).strftime(DATE_FMT)


def _read_existing(path: Path) -> dict[str, tuple]:
    """Existing rows keyed by date, field strings kept verbatim so an
    unchanged row round-trips byte-identically.

    A file this module cannot parse (missing columns, malformed last date,
    bad encoding) refuses LOUDLY rather than resuming from garbage: the
    resume start time comes from the last row, so a silently-misparsed file
    would refetch from the wrong offset or merge junk rows into a
    byte-deterministic corpus.
    """
    rows: dict[str, tuple] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows[row["date"]] = tuple(row[k] for k in FIELDS)
        if rows:
            _date_to_ms(next(reversed(rows)))   # resume anchor must parse
    except (KeyError, TypeError, ValueError, csv.Error, UnicodeDecodeError) as e:
        raise RuntimeError(
            f"{path} is not a readable fetch CSV ({e!r}) - "
            "delete it to refetch from scratch") from e
    return rows


def fetch_symbol(symbol: str, tf: str, data_dir: Path, now_ms: int | None = None) -> dict:
    """Fetch every COMPLETE `tf` kline for `symbol` into `data_dir`, resuming
    incrementally from any existing CSV.

    Completeness comes from the exchange's own close_time (index 6), not a
    local date comparison: the currently-open kline arrives as the last
    element of the last batch with a future close_time, and writing it would
    put a fabricated partial bar into every full-history consumer -- the
    gauntlet's out-of-sample window included (see `data_fetch.fetch_symbol`,
    where this bug was first fixed). Pagination advances from the RAW batch,
    not the filtered one, or a final batch consisting only of the open kline
    would be re-requested forever.

    The write is atomic (`.tmp` + os.replace): a failure mid-write leaves the
    original file untouched and no `.tmp` masquerading as data.

    RAM profile: the whole merged series is held in memory and written once
    at the end -- there is no intra-cell checkpoint. An intraday backfill
    (e.g. 15m from genesis, ~200k+ klines) that dies mid-cell writes nothing
    and restarts from its previous file on the next run.

    Resume trusts the file's own ordering: the LAST row is the newest bar.
    Historical gaps inside an existing file are never backfilled -- the fetch
    only ever asks the exchange for bars after the last row. A file with a
    hole stays holed until it is deleted and refetched from scratch.
    """
    if tf not in cells.TIMEFRAMES:
        raise ValueError(
            f"tf {tf!r} is not in the declared research grid cells.TIMEFRAMES "
            f"{cells.TIMEFRAMES} - TF_MS membership is not admission (never equalise)")
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    data_dir = Path(data_dir)
    path = data_dir / f"{symbol}_{tf}.csv"
    existing = _read_existing(path) if path.exists() else {}
    if existing:
        start = _date_to_ms(next(reversed(existing))) + TF_MS[tf]
    else:
        start = 0

    merged = dict(existing)
    while True:
        url = f"{BASE}?symbol={symbol}&interval={tf}&limit=1000&startTime={start}"
        batch = _get_with_retry(url)
        if not batch:
            break
        for k in batch:
            # Still-open bar: fabricated OHLCV, drop. The SKEW_MARGIN_MS
            # widens the drop window because resume never re-serves a bar: a
            # clock-skew-admitted partial bar would be PERMANENT in this
            # byte-deterministic corpus, while the margin only costs
            # admitting a genuinely-closed bar one run later.
            if k[6] >= now_ms - SKEW_MARGIN_MS:
                continue
            merged[_ms_to_date(k[0])] = (
                _ms_to_date(k[0]), str(float(k[1])), str(float(k[2])),
                str(float(k[3])), str(float(k[4])), str(float(k[5])))
        start = batch[-1][0] + 1         # advance from the RAW batch
        if len(batch) < 1000:
            break
        time.sleep(0.2)                  # ban-safety pacing between pages

    rows = [merged[d] for d in sorted(merged)]

    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(FIELDS)
            w.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)      # never leave a .tmp masquerading as data
        raise

    return {"rows": len(rows),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "first_date": rows[0][0] if rows else None,
            "last_date": rows[-1][0] if rows else None}


def update_snapshot_manifest(data_dir: Path, results: dict[str, dict]) -> Path:
    """Merge this run's per-cell results into `crypto_snapshot_manifest.json`
    KEY-WISE: cells just fetched overwrite their entries, every other key on
    file carries forward unchanged (mirrors `tradfi_data.snapshot`'s merge) --
    a subset run never strips provenance from a CSV still sitting in data/.
    """
    data_dir = Path(data_dir)
    out = data_dir / "crypto_snapshot_manifest.json"
    previous: dict = {}
    if out.exists():
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except Exception as e:
            # A corrupt prior manifest is not this run's problem to fix, but
            # silently discarding provenance history is not acceptable either.
            print(f"WARN: corrupt snapshot manifest {out} reset to {{}} ({e!r})")
            previous = {}

    fetched_utc = datetime.now(timezone.utc).isoformat()
    merged = dict(previous)
    for key, res in results.items():
        merged[key] = {"fetched_utc": fetched_utc, "rows": res["rows"],
                       "sha256": res["sha256"], "first_date": res["first_date"],
                       "last_date": res["last_date"]}

    data_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", newline="") as f:
        return max(sum(1 for _ in f) - 1, 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.crypto_data",
        description="Fetch the expanded crypto grid's klines from Binance (explicit runs only).")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch/refresh one or more cells")
    fetch.add_argument("--timeframes", required=True,
                       help="comma-separated, each declared in cells.TIMEFRAMES, e.g. 1d,4h")
    fetch.add_argument("--assets", default=None,
                       help="comma-separated Binance symbols; default = the universe "
                            "manifest's admitted binance_symbol list")
    fetch.add_argument("--data-dir", type=Path,
                       default=Path(__file__).resolve().parent.parent / "data")
    fetch.add_argument("--manifest", type=Path, default=None,
                       help="universe manifest path (default: <data-dir>/crypto_universe_manifest.json)")

    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    bad = [t for t in timeframes if t not in cells.TIMEFRAMES]
    if bad:
        print(f"error: timeframe(s) {', '.join(bad)} not in the declared research grid "
              f"cells.TIMEFRAMES {cells.TIMEFRAMES}")
        return 1

    if args.assets:
        assets = [a.strip() for a in args.assets.split(",") if a.strip()]
    else:
        manifest_path = args.manifest or data_dir / "crypto_universe_manifest.json"
        if not manifest_path.exists():
            print(f"error: universe manifest not found at {manifest_path} - "
                  f"run {UNIVERSE_TOOL} (Task 1) to pin the universe first")
            return 1
        universe = json.loads(manifest_path.read_text(encoding="utf-8"))
        assets = [row["binance_symbol"] for row in universe["admitted"]]

    cells_todo = [(symbol, tf) for symbol in assets for tf in timeframes]
    results: dict[str, dict] = {}
    failures: list[str] = []
    abort_msg: str | None = None
    try:
        for i, (symbol, tf) in enumerate(cells_todo):
            if i:
                time.sleep(0.2)          # pacing between cells too: the IP budget
                                         # is shared with every Binance consumer here
            key = f"{symbol}_{tf}"
            prior = _csv_row_count(data_dir / f"{key}.csv")
            try:
                res = fetch_symbol(symbol, tf, data_dir)
            except BinanceBanned as e:
                abort_msg = str(e)
                break
            except RateLimited as e:
                # Retries exhausted (or Retry-After beyond the cap): the
                # server is saturated or telling us to go away. Continuing to
                # the next cell would keep hammering the shared IP, so this
                # aborts the WHOLE run -- and sleeps the final retry_after
                # first, so the abort itself respects the back-off.
                time.sleep(e.retry_after)
                abort_msg = (f"rate-limit exhaustion on {key} "
                             f"(retry_after {e.retry_after}s): aborting to protect "
                             f"the shared IP - wait out the back-off before re-running")
                break
            except Exception as e:       # one broken cell must not strand the rest
                failures.append(key)
                print(f"{key}: FAILED ({e})")
                continue
            results[key] = res
            print(f"{key}: {res['rows']} rows ({res['rows'] - prior} new)")
    finally:
        # Cells completed before a ban, an abort, a crash, or a Ctrl+C hold
        # valid, already-replaced files; record their provenance no matter
        # how the run ends, or the manifest would carry stale-wrong sha256s
        # for CSVs that were in fact rewritten.
        if results:
            update_snapshot_manifest(data_dir, results)

    if abort_msg is not None:
        print(f"ABORTED: {abort_msg}")
        print(f"summary: {len(results)} cell(s) completed before the abort; "
              f"manifest updated for those only")
        return 1
    print(f"summary: {len(results)} cell(s) fetched, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
