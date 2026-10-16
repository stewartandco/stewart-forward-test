"""Next-bar targets for every quarantined/live crypto 1d strategy (build 4a,
spec 4.2). READ-ONLY: never appends to the chain, never takes chain.lock,
never writes data/. Output: logs/signals/targets-<bar_end>.json (gitignored).

P1 (the synthetic next bar): the engine fills entries at the NEXT open from
the previous close's signal, and fills declared/time exits at the next open
too, so a decision taken on the last close is invisible in a plain run. One
flat bar (open = high = low = close = the last close) is appended and the
engine's own position after it is read: that is the position the strategy
holds through the next bar, sized exactly as the engine sizes it.

FRESHNESS (fix round 1, I-1): every asset a served strategy names must end on
the SAME last bar date (bar_end). An asset whose CSV is missing, empty or ends
earlier makes the run REFUSE: it prints every asset's last date, writes
nothing and exits REFUSED_EXIT (2), so the executor holds (its 26 h rule) and
the Sentinel sees task 31 fail. A lagging asset is never published as flat or
omitted -- the same case quarantine.observe_day refuses ("no <asset> bar for
<date>").

NOTHING FRESH IS A REFUSAL TOO (fix wave, I2): bar_end must be at least the
bar that closed at the start of the UTC day (`--as-of` - 1 day; `--as-of`
defaults to the UTC date today), so uniformly stale data never rewrites an
old targets file and exits 0. A registry file that does not exist refuses
too (Registry.snapshot would read it as an empty chain: "nothing written",
exit 0). Exit codes: 0 written (or nothing served on an existing chain),
REFUSED_EXIT = 2 for every refusal, 1 = an unhandled crash (Python's own).

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
import os
import time
from pathlib import Path

from . import cells
from .engine import ENGINE_REV, simulate_asset
from .registry import Registry

STATES = {"quarantine", "live"}
REFUSED_EXIT = 2          # every refusal; an unhandled crash exits 1
REPLACE_RETRIES = 8       # os.replace retries on PermissionError (a Windows reader)
REPLACE_SLEEP_S = 0.25


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


def parse_daily_csv(raw: bytes) -> list[dict]:
    """screen.load_bars' parse, from bytes already read (cutoff = everything),
    with each date cut to its date part as quarantine._daily_bars does.
    test_signal_service pins it equal to load_bars on the same file."""
    bars = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=None)):
        bars.append({"date": row["date"], "open": float(row["open"]),
                     "high": float(row["high"]), "low": float(row["low"]),
                     "close": float(row["close"]),
                     "volume": float(row["volume"])})
    bars.sort(key=lambda b: b["date"])
    for b in bars:
        b["date"] = b["date"][:10]
    return bars


def load_assets(data_dir: Path, users: dict[str, list[str]]) -> tuple[str, dict, dict]:
    """Read each asset's data/<asset>_1d.csv once. `users` maps asset -> the
    served strategy ids naming it. Returns (bar_end, bars_by_asset,
    sha256_by_asset), or raises DataRefused when a file is missing, empty or
    does not end on the common (latest) last bar date."""
    bars_by, sha_by = {}, {}
    for asset in sorted(users):
        path = data_dir / f"{asset}_1d.csv"
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise DataRefused(f"no price file {path} for asset {asset} named by "
                              f"strategy {', '.join(sorted(users[asset]))}") from None
        sha_by[asset] = hashlib.sha256(raw).hexdigest()
        bars_by[asset] = parse_daily_csv(raw)
    last = {x: (b[-1]["date"] if b else None) for x, b in bars_by.items()}
    bar_end = max((d for d in last.values() if d is not None), default=None)
    stale = sorted(x for x, d in last.items() if d is None or d != bar_end)
    if bar_end is None or stale:
        detail = ", ".join(f"{x} {last[x] or 'EMPTY'}" for x in sorted(last))
        raise DataRefused(f"assets do not all end on bar_end {bar_end}: "
                          f"{', '.join(stale)} behind or empty (last bar dates: {detail})")
    return bar_end, bars_by, sha_by


def check_fresh(bar_end: str, as_of: dt.date) -> None:
    """I2: refuse unless bar_end is at least the daily bar that closed at the
    start of `as_of` (UTC), i.e. as_of - 1 day."""
    want = as_of - dt.timedelta(days=1)
    if dt.date.fromisoformat(bar_end) < want:
        raise DataRefused(f"stale: bar_end {bar_end} is older than {want.isoformat()}, "
                          f"the bar that closed at the start of {as_of.isoformat()} UTC")


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
    users: dict[str, list[str]] = {}
    for sid, spec in served.items():
        for asset in spec["universe"]["assets"]:
            users.setdefault(asset, []).append(sid)
    try:
        bar_end, bars_by, digest = load_assets(a.data_dir, users)
        check_fresh(bar_end, as_of)
    except DataRefused as exc:
        print(f"REFUSED (nothing written): {exc}")
        return REFUSED_EXIT
    out = {}
    for sid, spec in served.items():
        assets = {x: next_bar_position(spec, bars_by[x]) for x in spec["universe"]["assets"]}
        out[sid] = {"spec_sha": _spec_sha(spec), "max_dd": max_dd.get(sid), "assets": assets}
    doc = {"bar_end": bar_end, "engine_rev": ENGINE_REV, "data_digest": digest,
           "written_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "strategies": out}
    a.out_dir.mkdir(parents=True, exist_ok=True)
    path = a.out_dir / f"targets-{bar_end}.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        _publish(tmp, path)            # the executor never sees a torn file
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"wrote {path} ({len(out)} strategies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
