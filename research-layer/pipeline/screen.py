"""Screening CLI: run proposed strategy specs on training data, apply the
pre-declared gate, chain verdicts + lifecycle transitions, write artifacts.

Usage:
    python -m pipeline.screen [--registry registry_log.jsonl]
        [--data-dir data] [--artifacts-dir artifacts]
        [--cutoff 2023-12-31] [--dry-run]

A real (non-dry) run HARD-REFUSES unless a `note` entry whose text starts
with the PROTOCOL string below is already on the chain — the gate and fence
provably predate every verdict.
"""
from __future__ import annotations

import csv
import sys
import json
import hashlib
import argparse
from pathlib import Path

from .registry import Registry
from .engine import run_spec

PROTOCOL = "screen-protocol-v1"
GATE_MIN_TRADES = 40
DEFAULT_CUTOFF = "2023-12-31"


def load_bars(data_dir: Path, asset: str, cutoff: str) -> list[dict]:
    path = data_dir / f"{asset}_1d.csv"
    bars = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"] > cutoff:
                continue                      # the train fence
            bars.append({"date": row["date"], "open": float(row["open"]),
                         "high": float(row["high"]), "low": float(row["low"]),
                         "close": float(row["close"]),
                         "volume": float(row["volume"])})
    bars.sort(key=lambda b: b["date"])
    return bars


def protocol_note_chained(registry: Registry) -> bool:
    return any(e["entry_type"] == "note"
               and str(e["payload"].get("text", "")).startswith(PROTOCOL)
               for e in registry.entries())


def write_artifacts(art_dir: Path, spec: dict, result: dict, cutoff: str,
                    data_hashes: dict[str, str]) -> Path:
    bundle = art_dir / spec["strategy_id"]
    bundle.mkdir(parents=True, exist_ok=True)
    with (bundle / "trades.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asset", "side", "entry_date",
                                          "entry_px", "exit_date", "exit_px",
                                          "exit_reason", "return_net"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(result["trades"])
    with (bundle / "equity.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "combined_equity"])
        w.writerows(result["equity"])
    (bundle / "config.json").write_text(json.dumps(
        {"protocol": PROTOCOL, "cutoff": cutoff, "spec": spec,
         "data_sha256": data_hashes}, indent=1, sort_keys=True), encoding="utf-8")
    return bundle


def bundle_hash(bundle: Path) -> str:
    h = hashlib.sha256()
    for name in ("trades.csv", "equity.csv", "config.json"):
        h.update((bundle / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data")
    ap.add_argument("--artifacts-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "artifacts")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    if not args.dry_run and not protocol_note_chained(registry):
        print(f"REFUSED: no '{PROTOCOL}' note on the chain. Chain the screen "
              f"protocol note before running for real (dry-run is allowed).")
        return 1

    states = registry.strategy_states()
    specs = [e["payload"] for e in registry.entries()
             if e["entry_type"] == "strategy_registered"
             and states.get(e["payload"]["strategy_id"]) == "proposed"]
    if not specs:
        print("No strategies in 'proposed' state.")
        return 0

    assets = sorted({a for s in specs for a in s["universe"]["assets"]})
    bars_by_asset, data_hashes = {}, {}
    for a in assets:
        bars_by_asset[a] = load_bars(args.data_dir, a, args.cutoff)
        data_hashes[a] = hashlib.sha256(
            (args.data_dir / f"{a}_1d.csv").read_bytes()).hexdigest()

    results = []
    for spec in specs:
        result = run_spec(spec, {a: bars_by_asset[a]
                                 for a in spec["universe"]["assets"]})
        m = result["metrics"]
        passed = m["trades"] >= GATE_MIN_TRADES and m["net_pnl"] > 0
        reason = None
        if not passed:
            reason = "trade_count" if m["trades"] < GATE_MIN_TRADES else "net_negative"
        results.append((spec, result, passed, reason))
        print(f"{spec['strategy_id']}  {'PASS' if passed else 'fail':<4} "
              f"trades={m['trades']:>3}  pnl={m['net_pnl']:+.4f}  "
              f"wr={m['win_rate']:.2f}  dd={m['max_dd']:+.4f}"
              + (f"  [{reason}]" if reason else ""))

    n_pass = sum(1 for _, _, p, _ in results if p)
    if args.dry_run:
        print(f"\nDRY RUN — {len(results)} screened, {n_pass} would pass, "
              f"{len(results) - n_pass} would fail; nothing written.")
        return 0

    n_written = 0
    try:
        for spec, result, passed, reason in results:
            sid = spec["strategy_id"]
            bundle = write_artifacts(args.artifacts_dir, spec, result,
                                     args.cutoff, data_hashes)
            registry.record_state_change(sid, "screened",
                                         f"screen run, cutoff {args.cutoff}")
            registry.record_verdict(sid, "screened",
                                    "pass" if passed else "fail",
                                    result["metrics"], bundle_hash(bundle))
            registry.record_state_change(
                sid, "gauntlet" if passed else "graveyard", reason)
            n_written += 1
    except BaseException:
        print(f"\nPARTIAL WRITE: {n_written}/{len(results)} strategies fully "
              f"chained before failure — review the registry tail before "
              f"re-running.", file=sys.stderr)
        raise

    print(f"\n{len(results)} screened: {n_pass} -> gauntlet, "
          f"{len(results) - n_pass} -> graveyard.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
