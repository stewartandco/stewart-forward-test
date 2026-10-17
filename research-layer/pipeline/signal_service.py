"""Next-bar targets for every quarantined/live crypto 1d strategy (build 4a,
spec 4.2). READ-ONLY: never appends to the chain, never takes chain.lock,
never writes data/. Output: logs/signals/targets-<bar_end>.json (gitignored).

P1 (the synthetic next bar): the engine fills entries at the NEXT open from
the previous close's signal, and fills declared/time exits at the next open
too, so a decision taken on the last close is invisible in a plain run. One
flat bar (open = high = low = close = the last close) is appended and the
engine's own position after it is read: that is the position the strategy
holds through the next bar, sized exactly as the engine sizes it.

FRESHNESS, PER STRATEGY (fix round 1, I-1; minors item 1): bar_end is the
latest last-bar date across every served asset. A strategy is published only
if EVERY asset it trades has a price file, is non-empty and ends on bar_end;
any other strategy is DEFERRED: listed in the targets file under `deferred`
{strategy_id: reason} and absent from `strategies`, never published flat or
with an asset omitted. The executor reads a strategy absent from a fresh file
as "signal stale, holding" (its 26 h rule). When NO strategy can be published
the run REFUSES: it prints every asset's last date and every deferral, writes
nothing and exits REFUSED_EXIT (2), so the Sentinel sees task 31 fail.

NOTHING FRESH IS A REFUSAL TOO (fix wave, I2; minors item 2): bar_end must be
exactly the bar that closed at the start of the UTC day (`--as-of` - 1 day;
`--as-of` defaults to the UTC date today). Older = stale, so uniformly stale
data never rewrites an old targets file and exits 0; newer = a bar dated the
run day or later, i.e. a still-open (partial) bar, which data_fetch never
writes, so it is bad data. A registry file that does not exist refuses too
(Registry.snapshot would read it as an empty chain: "nothing written", exit
0). A malformed CSV row (short row, BOM-prefixed or incomplete header,
non-numeric or non-finite price, a date that is not YYYY-MM-DD, undecodable
bytes) refuses the run naming the file and row (minors item 3). Exit codes:
0 written (or nothing served on an existing chain), REFUSED_EXIT = 2 for every
refusal, 1 = an unhandled crash (Python's own).

RETENTION (minors item 4): after a publish, targets-<date>.json files beyond
the newest KEEP_TARGETS by the date in the name are deleted (never the file
just written; a name without a date is left alone; a file that cannot be
deleted is logged and skipped). A refusal prunes nothing.

The module is NOT named signal.py (fix wave, C1): run by path, a pipeline/
script puts pipeline/ first on sys.path, and a pipeline/signal.py shadowed
the stdlib `signal` that subprocess imports, so taskguard.py died before it
started. test_signal_service pins every pipeline/ stem off the stdlib names.

Each CSV is read ONCE: data_digest is the sha256 of exactly the bytes the bars
were parsed from (M-1), with screen.load_bars' parsing rules (test-pinned).

Usage (from research-layer/):
    python -m pipeline.signal_service [--registry registry_log.jsonl]
        [--data-dir data] [--out-dir logs/signals] [--as-of YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import time
from pathlib import Path

from . import cells
from .engine import ENGINE_REV, simulate_asset
from .registry import Registry

STATES = {"quarantine", "live"}
REFUSED_EXIT = 2          # every refusal; an unhandled crash exits 1
REPLACE_RETRIES = 8       # os.replace retries on PermissionError (a Windows reader)
REPLACE_SLEEP_S = 0.25
KEEP_TARGETS = 30         # targets files kept after a publish (newest by bar_end)
COLUMNS = ("date", "open", "high", "low", "close", "volume")
_TARGETS_NAME = re.compile(r"^targets-(\d{4}-\d{2}-\d{2})\.json$")


def _synthetic_next(bars: list[dict]) -> dict:
    last = bars[-1]
    d = dt.date.fromisoformat(last["date"][:10]) + dt.timedelta(days=1)
    c = float(last["close"])
    return {"date": d.isoformat(), "open": c, "high": c, "low": c, "close": c, "volume": 0.0}


def next_bar_position(spec: dict, bars: list[dict]) -> dict:
    """P1: append a flat synthetic next bar so the engine takes its own
    next-open decisions; read the position after it. Periods per year and
    version are derived exactly as quarantine.observe_day derives them for a
    daily spec."""
    if not bars:
        return {"side": "flat", "frac": 0.0}
    ppy = cells.SESSION_PERIODS.get(spec["universe"].get("session"), 365)
    book = simulate_asset(spec["blocks"], bars + [_synthetic_next(bars)], spec["cost_model"],
                          ppy, version=spec.get("version", 1))
    pos = book["position"]
    if pos is None:
        return {"side": "flat", "frac": 0.0}
    return {"side": "long" if pos["side"] == 1 else "short", "frac": float(pos["notional_frac"])}


class DataRefused(Exception):
    """The price files cannot back a fresh targets file; nothing is written."""


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_daily_csv(raw: bytes, name: str = "price file") -> list[dict]:
    """screen.load_bars' parse, from bytes already read (cutoff = everything),
    with each date cut to its date part as quarantine._daily_bars does.
    test_signal_service pins it equal to load_bars on the same file.
    Minors item 3: a header without every column (a BOM-prefixed `date`
    included), a short row, a row longer than the header, a non-numeric or
    non-finite price, a non-positive open/high/low/close (volume may be 0), a
    date that is not YYYY-MM-DD, a duplicate date (same day, time part
    ignored), or bytes that are not UTF-8 raise DataRefused naming `name` and
    the row (the physical line, the header being row 1)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataRefused(f"{name}: not UTF-8 ({exc})") from None
    reader = csv.DictReader(io.StringIO(text, newline=None))
    head = reader.fieldnames
    if head is not None:
        lacking = [c for c in COLUMNS if c not in head]
        if lacking:
            raise DataRefused(f"{name}: row 1 (header {head!r}) lacks column(s) "
                              f"{', '.join(lacking)}")
    bars = []
    seen: dict[str, int] = {}               # day -> the physical row it was first on
    for row in reader:
        cells_ = [row.get(c) for c in COLUMNS]
        try:
            if None in row:                  # csv.DictReader files extra fields under None
                raise ValueError(f"long row ({len(head) + len(row[None])} fields, header "
                                 f"has {len(head)})")
            if any(v is None for v in cells_):
                raise ValueError(f"short row ({sum(v is not None for v in cells_)} of "
                                 f"{len(COLUMNS)} columns)")
            if not _DATE.match(cells_[0]):
                raise ValueError(f"date {cells_[0]!r} is not YYYY-MM-DD")
            dt.date.fromisoformat(cells_[0][:10])
            day = cells_[0][:10]
            if day in seen:
                raise ValueError(f"duplicate date {day} (first on row {seen[day]})")
            seen[day] = reader.line_num
            bar = {"date": cells_[0]}
            for c, v in zip(COLUMNS[1:], cells_[1:]):
                bar[c] = float(v)
                if not math.isfinite(bar[c]):
                    raise ValueError(f"{c} {v!r} is not finite")
                if c != "volume" and bar[c] <= 0:
                    raise ValueError(f"{c} {v!r} is not positive")
        except ValueError as exc:
            raise DataRefused(f"{name}: row {reader.line_num}: {exc}") from None
        bars.append(bar)
    bars.sort(key=lambda b: b["date"])
    for b in bars:
        b["date"] = b["date"][:10]
    return bars


def load_assets(data_dir: Path, assets) -> tuple[str | None, dict, dict, dict, dict]:
    """Read each asset's data/<asset>_1d.csv once. Returns (bar_end,
    bars_by_asset, sha256_by_asset, problem_by_asset, last_date_by_asset):
    bar_end is the latest last-bar date over the assets that have bars (None
    if none has); problem_by_asset names every asset that cannot back a
    target on bar_end (no price file, EMPTY, or behind). A malformed file
    raises DataRefused (the whole run refuses)."""
    bars_by, sha_by, problems = {}, {}, {}
    for asset in sorted(set(assets)):
        path = data_dir / f"{asset}_1d.csv"
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            problems[asset] = f"{asset}: no price file {path}"
            continue
        sha_by[asset] = hashlib.sha256(raw).hexdigest()
        bars_by[asset] = parse_daily_csv(raw, str(path))
    last = {x: (b[-1]["date"] if b else None) for x, b in bars_by.items()}
    bar_end = max((d for d in last.values() if d is not None), default=None)
    for x, d in last.items():
        if d is None:
            problems[x] = f"{x} EMPTY"
        elif d != bar_end:
            problems[x] = f"{x} behind: last bar {d}, bar_end {bar_end}"
    return bar_end, bars_by, sha_by, problems, last


def check_fresh(bar_end: str, as_of: dt.date) -> None:
    """I2 + minors item 2: refuse unless bar_end is exactly the daily bar that
    closed at the start of `as_of` (UTC), i.e. as_of - 1 day. Older is stale;
    newer is a bar still open on as_of (partial), which data_fetch never
    writes, so it is bad data."""
    want = as_of - dt.timedelta(days=1)
    got = dt.date.fromisoformat(bar_end)
    if got < want:
        raise DataRefused(f"stale: bar_end {bar_end} is older than {want.isoformat()}, "
                          f"the bar that closed at the start of {as_of.isoformat()} UTC")
    if got > want:
        raise DataRefused(f"partial: bar_end {bar_end} is newer than {want.isoformat()}, "
                          f"the bar that closed at the start of {as_of.isoformat()} UTC; "
                          f"a bar dated {as_of.isoformat()} or later has not closed")


def prune_targets(out_dir: Path, written: Path, keep: int = KEEP_TARGETS) -> None:
    """Minors item 4: delete targets-<date>.json files beyond the newest `keep`
    by the date in the name. Never `written` (the file just published); a name
    without a date is left alone; a file that cannot be deleted (a reader holds
    it: PermissionError) is logged and skipped."""
    dated = sorted(((m.group(1), p) for p in out_dir.glob("targets-*.json")
                    if (m := _TARGETS_NAME.match(p.name))), reverse=True)
    gone = 0
    for _, p in dated[keep:]:
        if p == written:
            continue
        try:
            p.unlink()
            gone += 1
        except OSError as exc:
            print(f"could not prune {p.name}: {exc}; skipped")
    if gone:
        print(f"pruned {gone} targets file(s) beyond the newest {keep}")


def _utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _publish(tmp: Path, path: Path) -> None:
    """os.replace, retried REPLACE_RETRIES times on PermissionError (Windows
    refuses the replace while a reader holds the target open), then raised."""
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_RETRIES:
                raise
            time.sleep(REPLACE_SLEEP_S)


def _spec_sha(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(argv=None) -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(prog="pipeline.signal_service")
    ap.add_argument("--registry", type=Path, default=here / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=here / "data")
    ap.add_argument("--out-dir", type=Path, default=here / "logs" / "signals")
    ap.add_argument("--as-of", type=dt.date.fromisoformat, default=None,
                    help="UTC date of the run (default: today UTC); bar_end must be >= this - 1 day")
    a = ap.parse_args(argv)
    as_of = a.as_of or _utc_today()
    if not a.registry.is_file():
        print(f"REFUSED (nothing written): no registry file {a.registry}")
        return REFUSED_EXIT
    specs, max_dd = {}, {}

    def on_entry(e):
        t, p = e.get("entry_type"), e.get("payload") or {}
        if t == "strategy_registered":
            u = p.get("universe") or {}
            if u.get("asset_class") == "crypto" and u.get("timeframe", "1d") == "1d":
                specs[p["strategy_id"]] = p
        elif t == "verdict" and p.get("stage") == "screened":
            max_dd[p.get("strategy_id")] = (p.get("metrics") or {}).get("max_dd")

    # one read of COMPLETE lines; Registry.snapshot never writes or locks
    snap = Registry(a.registry).snapshot(on_entry=on_entry)
    served = {sid: spec for sid, spec in specs.items() if snap.states.get(sid) in STATES}
    if not served:
        print("no crypto strategies in quarantine/live; nothing written")
        return 0
    wanted = {x for spec in served.values() for x in spec["universe"]["assets"]}
    try:
        bar_end, bars_by, digest, problems, last = load_assets(a.data_dir, wanted)
        detail = ", ".join(f"{x} {last[x] or 'EMPTY'}" if x in last else f"{x} MISSING"
                           for x in sorted(wanted))
        if bar_end is None:
            raise DataRefused(f"no served asset has a bar (last bar dates: {detail})")
        check_fresh(bar_end, as_of)
    except DataRefused as exc:
        print(f"REFUSED (nothing written): {exc}")
        return REFUSED_EXIT
    # minors item 1: per-strategy deferral -- every asset on bar_end, or none
    deferred = {}
    for sid, spec in served.items():
        bad = [problems[x] for x in spec["universe"]["assets"] if x in problems]
        if bad:
            deferred[sid] = "; ".join(bad)
    publish = {sid: spec for sid, spec in served.items() if sid not in deferred}
    if not publish:
        print(f"REFUSED (nothing written): no strategy can be published on bar_end "
              f"{bar_end} (last bar dates: {detail})")
        for sid in sorted(deferred):
            print(f"  deferred {sid}: {deferred[sid]}")
        return REFUSED_EXIT
    out = {}
    for sid, spec in publish.items():
        assets = {x: next_bar_position(spec, bars_by[x]) for x in spec["universe"]["assets"]}
        out[sid] = {"spec_sha": _spec_sha(spec), "max_dd": max_dd.get(sid), "assets": assets}
    used = {x for spec in publish.values() for x in spec["universe"]["assets"]}
    doc = {"bar_end": bar_end, "engine_rev": ENGINE_REV,
           "data_digest": {x: digest[x] for x in sorted(used)},
           "written_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "strategies": out, "deferred": deferred}
    a.out_dir.mkdir(parents=True, exist_ok=True)
    path = a.out_dir / f"targets-{bar_end}.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        _publish(tmp, path)            # the executor never sees a torn file
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"wrote {path} ({len(out)} strategies, {len(deferred)} deferred)")
    for sid in sorted(deferred):
        print(f"  deferred {sid}: {deferred[sid]}")
    prune_targets(a.out_dir, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
