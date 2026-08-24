"""Screening CLI: run proposed strategy specs on training data, apply the
pre-declared gate, chain verdicts + lifecycle transitions, write artifacts.

Usage:
    python -m pipeline.screen [--registry registry_log.jsonl]
        [--data-dir data] [--artifacts-dir artifacts]
        [--cutoff 2023-12-31] [--workers N] [--dry-run]

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

from .cells import cell_id
from .parallel import run_all, CellError
from .registry import Registry
from .engine import run_spec

PROTOCOL = "screen-protocol-v1"
GATE_MIN_TRADES = 40
DEFAULT_CUTOFF = "2023-12-31"


def load_bars(data_dir: Path, asset: str, cutoff: str,
              timeframe: str = "1d") -> list[dict]:
    """Bars for one cell, up to and including the cutoff DATE.

    The fence compares the date part only. A naive string compare is wrong for
    intraday bars: "2023-12-31 00:00:00" > "2023-12-31" is True in Python, so
    every intraday bar on the cutoff day would be silently dropped.
    """
    path = data_dir / f"{asset}_{timeframe}.csv"
    bars = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"][:10] > cutoff[:10]:
                continue                      # the train fence, date-inclusive
            bars.append({"date": row["date"], "open": float(row["open"]),
                         "high": float(row["high"]), "low": float(row["low"]),
                         "close": float(row["close"]),
                         "volume": float(row["volume"])})
    bars.sort(key=lambda b: b["date"])
    return bars


def load_cell_data(data_dir: Path, cells, cutoff: str) -> tuple[dict, dict, dict]:
    """Load a set of cells once, and return everything keyed by CELL.

    Returns (bars_by_cell, data_sha256_by_cell_id, data_end_by_cell_id).

    This exists because screen.py and gauntlet.py each hand-rolled the same
    three lines and drifted apart: the screen became timeframe-aware while the
    gauntlet kept loading `_1d` for every spec and hashing by bare asset. Two
    stages evaluating one spec on different bars makes the chained verdict
    meaningless, so cell identity lives here and both stages call it.

    `data_end` is the last bar actually LOADED, not the last on disk, so a
    fenced run never claims data it deliberately refused to read. It is the
    field that makes cross-cell comparison checkable: the cache is not
    time-aligned, so two cells can stop on different dates for reasons that
    have nothing to do with the strategies being compared.
    """
    bars_by_cell: dict[tuple[str, str], list[dict]] = {}
    data_hashes: dict[str, str] = {}
    data_end: dict[str, str] = {}
    for asset, tf in sorted(set(cells)):
        bars = load_bars(data_dir, asset, cutoff, timeframe=tf)
        bars_by_cell[(asset, tf)] = bars
        cid = cell_id(asset, tf)
        data_hashes[cid] = hashlib.sha256(
            (data_dir / f"{asset}_{tf}.csv").read_bytes()).hexdigest()
        data_end[cid] = bars[-1]["date"] if bars else ""
    return bars_by_cell, data_hashes, data_end


def assert_cells_comparable(data_end: dict[str, str],
                            class_of: dict[str, str] | None = None) -> None:
    """Refuse to compare cells whose data stops on different DAYS.

    A cell is the unit of survival, so a strategy that "died on SOL 1d" when
    that cell's bars stopped two weeks early was not tested, it was truncated.
    The cache is not time-aligned by default: on 2026-08-18 the imported grid
    had BTCUSDT_1h through 08-15 and SOLUSDT_1d through 08-01.

    DAYS, not timestamps, because a 4h bar cannot close at 23:00 -- BTCUSDT_1h
    ends 15:23:00 and BTCUSDT_4h ends 15:20:00 while both cover through the
    15th. Comparing whole timestamps would refuse every multi-timeframe run
    forever. load_bars already compares date[:10] at its fence for this reason.

    This raises rather than dropping the offending cells, because the pipeline
    contract's success metrics require every cell tested to be present in the
    trial denominator: no quiet subsetting of the reported search space. The
    fix is a re-fetch to a common end date, not a smaller comparison.

    `class_of` (cell_id -> asset class, built by the caller from each spec's
    own declared universe) makes the rule PER-CLASS (spec s10.6): the same-day
    rule above still applies WITHIN a class exactly as before, but cells in
    DIFFERENT classes may end up to 3 calendar days apart, because a 24x7
    crypto close and a 5-day fx fix are never going to land on the same
    calendar day and a strict same-day rule would refuse every mixed-class
    gauntlet run forever. `class_of=None` (the default, every caller before
    this amendment) keeps today's single-class behaviour exactly -- one
    same-day rule across every cell -- so crypto-only runs are byte-identical.
    """
    empty = sorted(cid for cid, end in data_end.items() if not end)
    if empty:
        raise ValueError(
            f"cell(s) loaded with no bars, so their data end is unknown: "
            f"{', '.join(empty)}. An empty cell must not compare equal to "
            f"every other cell.")
    if class_of is None:
        by_day: dict[str, list[str]] = {}
        for cid, end in sorted(data_end.items()):
            by_day.setdefault(end[:10], []).append(cid)
        if len(by_day) > 1:
            detail = "; ".join(f"{day}: {', '.join(cids)}"
                               for day, cids in sorted(by_day.items()))
            raise ValueError(
                f"cells stop on different days, so they cannot be compared: "
                f"{detail}. Re-fetch to a common end date; excluding the "
                f"short cells would quietly shrink the reported search space.")
        return

    # Per-class: the exact same-day rule, applied WITHIN each declared class.
    by_class: dict[str, dict[str, str]] = {}
    for cid, end in data_end.items():
        cls = class_of.get(cid)
        if cls is None:
            raise ValueError(f"{cid!r} has no declared class in class_of; "
                             f"every cell being compared must name one.")
        by_class.setdefault(cls, {})[cid] = end
    for cls, ends in by_class.items():
        by_day = {}
        for cid, end in sorted(ends.items()):
            by_day.setdefault(end[:10], []).append(cid)
        if len(by_day) > 1:
            detail = "; ".join(f"{day}: {', '.join(cids)}"
                               for day, cids in sorted(by_day.items()))
            raise ValueError(
                f"cells stop on different days within class {cls!r}, so "
                f"they cannot be compared: {detail}. Re-fetch to a common "
                f"end date; excluding the short cells would quietly shrink "
                f"the reported search space.")

    # Cross-class: a weekend/holiday gap between a 24x7 close and an fx fix
    # is expected, not truncation, so allow up to 3 calendar days apart.
    from datetime import date as _date
    days = {cid: _date.fromisoformat(end[:10]) for cid, end in data_end.items()}
    ids = sorted(days)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if class_of[a] == class_of[b]:
                continue
            gap = abs((days[a] - days[b]).days)
            if gap > 3:
                raise ValueError(
                    f"cells stop more than 3 calendar days apart across "
                    f"classes, so they cannot be compared: {a} "
                    f"({data_end[a][:10]}, {class_of[a]}) vs {b} "
                    f"({data_end[b][:10]}, {class_of[b]}). Re-fetch to a "
                    f"common end date.")


class SpecJob:
    """One spec's evaluation, picklable so it can cross a process boundary.

    The gate threshold travels WITH the job rather than being read from the
    module global inside the worker: a subprocess re-imports this module and
    would otherwise see the shipped 40 even when the parent is running a
    different gate.
    """

    def __init__(self, min_trades: int):
        self.min_trades = min_trades

    def __call__(self, item: tuple[dict, dict]) -> tuple[dict, bool, str | None]:
        spec, bars_by_asset = item
        result = run_spec(spec, bars_by_asset)
        m = result["metrics"]
        passed = m["trades"] >= self.min_trades and m["net_pnl"] > 0
        reason = None
        if not passed:
            reason = "trade_count" if m["trades"] < self.min_trades else "net_negative"
        return result, passed, reason


def protocol_note_chained(registry: Registry) -> bool:
    return any(e["entry_type"] == "note"
               and str(e["payload"].get("text", "")).startswith(PROTOCOL)
               for e in registry.entries())


def write_artifacts(art_dir: Path, spec: dict, result: dict, cutoff: str,
                    data_hashes: dict[str, str],
                    data_end: dict[str, str]) -> Path:
    """`data_end` is required on purpose: the hash says WHICH bytes produced a
    verdict, not when they stopped, and a caller that forgot would record an
    empty provenance for the very field that exists to catch truncation."""
    bundle = art_dir / spec["strategy_id"]
    bundle.mkdir(parents=True, exist_ok=True)
    with (bundle / "trades.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asset", "side", "entry_date",
                                          "entry_px", "exit_date", "exit_px",
                                          "exit_reason", "return_net"],
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(result["trades"])
    with (bundle / "equity.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "combined_equity"])
        w.writerows(result["equity"])
    (bundle / "config.json").write_text(json.dumps(
        {"protocol": PROTOCOL, "cutoff": cutoff, "spec": spec,
         "data_sha256": data_hashes, "data_end": data_end},
        indent=1, sort_keys=True), encoding="utf-8")
    return bundle


def bundle_hash(bundle: Path,
                names: tuple[str, ...] = ("trades.csv", "equity.csv",
                                          "config.json")) -> str:
    h = hashlib.sha256()
    for name in names:
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
    ap.add_argument("--workers", type=int, default=0,
                    help="fan-out width; 0 = cpu_count-1, 1 = serial")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)

    # any strategy resting in "screened" between runs is a crash artifact:
    # only this CLI's write-triplet ever advances that state
    orphans = [sid for sid, st in registry.strategy_states().items()
               if st == "screened"]
    if orphans:
        print("ORPHANED: strategies stuck in 'screened' with no verdict "
              "(mid-run crash?) — repair manually before proceeding:")
        for sid in orphans:
            print(f"  {sid}")
        return 1

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

    # A CELL is (asset, timeframe) — the unit that is loaded and hashed. Keying
    # the manifest by bare asset would let ETHUSDT_1h and ETHUSDT_4h overwrite
    # each other, and the manifest is the record of which bytes produced which
    # verdict. Specs with no timeframe are the legacy dailies.
    cells_needed = sorted({(a, s["universe"].get("timeframe", "1d"))
                           for s in specs for a in s["universe"]["assets"]})
    bars_by_cell, data_hashes, data_end = load_cell_data(
        args.data_dir, cells_needed, args.cutoff)

    jobs = []
    for spec in specs:
        tf = spec["universe"].get("timeframe", "1d")
        jobs.append((spec, {a: bars_by_cell[(a, tf)]
                            for a in spec["universe"]["assets"]}))

    # ordered results; the engine is pure, so fan-out changes scheduling only
    evaluated = run_all(SpecJob(GATE_MIN_TRADES), jobs, workers=args.workers)

    results = []
    for spec, outcome in zip(specs, evaluated):
        if isinstance(outcome, CellError):
            # a verdict chain must not silently graveyard a spec that crashed
            raise RuntimeError(f"{spec['strategy_id']}: {outcome}")
        result, passed, reason = outcome
        m = result["metrics"]
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
                                     args.cutoff, data_hashes, data_end)
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
