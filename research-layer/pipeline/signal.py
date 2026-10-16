"""Next-bar targets for every quarantined/live crypto 1d strategy (build 4a,
spec 4.2). READ-ONLY: never appends to the chain, never takes chain.lock,
never writes data/. Output: logs/signals/targets-<bar_end>.json (gitignored).

P1 (the synthetic next bar): the engine fills entries at the NEXT open from
the previous close's signal, and fills declared/time exits at the next open
too, so a decision taken on the last close is invisible in a plain run. One
flat bar (open = high = low = close = the last close) is appended and the
engine's own position after it is read: that is the position the strategy
holds through the next bar, sized exactly as the engine sizes it.

Usage (from research-layer/):
    python -m pipeline.signal [--registry registry_log.jsonl] [--data-dir data]
                              [--out-dir logs/signals]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from . import cells
from .engine import ENGINE_REV, simulate_asset
from .registry import Registry
from .screen import load_bars

STATES = {"quarantine", "live"}


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


def _spec_sha(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(argv=None) -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(prog="pipeline.signal")
    ap.add_argument("--registry", type=Path, default=here / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=here / "data")
    ap.add_argument("--out-dir", type=Path, default=here / "logs" / "signals")
    a = ap.parse_args(argv)
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
    out, bar_end = {}, None
    for sid, spec in specs.items():
        if snap.states.get(sid) not in STATES:
            continue
        assets = {}
        for asset in spec["universe"]["assets"]:
            bars = load_bars(a.data_dir, asset, "9999-12-31")
            for b in bars:
                b["date"] = b["date"][:10]
            if bars:
                bar_end = max(bar_end or bars[-1]["date"], bars[-1]["date"])
            assets[asset] = next_bar_position(spec, bars)
        out[sid] = {"spec_sha": _spec_sha(spec), "max_dd": max_dd.get(sid), "assets": assets}
    if bar_end is None:
        print("no crypto strategies in quarantine/live; nothing written")
        return 0
    used = sorted({x for s in out.values() for x in s["assets"]})
    digest = {x: hashlib.sha256((a.data_dir / f"{x}_1d.csv").read_bytes()).hexdigest() for x in used}
    doc = {"bar_end": bar_end, "engine_rev": ENGINE_REV, "data_digest": digest,
           "written_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "strategies": out}
    a.out_dir.mkdir(parents=True, exist_ok=True)
    path = a.out_dir / f"targets-{bar_end}.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)          # the executor never sees a torn file
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"wrote {path} ({len(out)} strategies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
