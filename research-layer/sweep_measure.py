"""Task 7 measurement harness: time the phase-1 sweep, prove parallel == serial.

Writes NOTHING. No registry, no artifacts, no chain. It exists to produce the
numbers Task 7 asks for before anything is activated (D29).

Not part of the pipeline package: this is a measuring instrument, and shipping
it inside pipeline/ would imply it is part of the protocol. Delete after use.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from pipeline import cells
from pipeline.composer import expand_universe
from pipeline.parallel import CellError, run_all
from pipeline.registry import Registry
from pipeline.screen import DEFAULT_CUTOFF, GATE_MIN_TRADES, SpecJob, load_bars

DATA = Path("data")


def distinct_block_sets(registry: Registry, n: int) -> list[dict]:
    """n registered specs with distinct block structures, as sweep seeds."""
    seen, out = set(), []
    for e in registry.entries():
        if e["entry_type"] != "strategy_registered":
            continue
        spec = e["payload"]
        key = tuple(sorted((b["role"], b["type"]) for b in spec["blocks"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
        if len(out) >= n:
            break
    return out


def build_jobs(specs: list[dict], cell_list, bars_by_cell) -> list[tuple]:
    jobs = []
    for spec in specs:
        for asset, tf in cell_list:
            for s in expand_universe(spec, [(asset, tf)]):
                jobs.append((s, {asset: bars_by_cell[(asset, tf)]}))
    return jobs


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "proof"
    registry = Registry("registry_log.jsonl")
    cell_list = cells.phase_cells(1)

    print(f"loading {len(cell_list)} phase-1 cells (cutoff {DEFAULT_CUTOFF}) ...")
    t0 = time.perf_counter()
    bars_by_cell = {(a, tf): load_bars(DATA, a, DEFAULT_CUTOFF, timeframe=tf)
                    for a, tf in cell_list}
    nbars = sum(len(v) for v in bars_by_cell.values())
    print(f"  {nbars:,} bars in {time.perf_counter() - t0:.1f}s")

    if mode == "proof":
        # Task 7 step 2: per-bar rate on one spec, one mid-sized cell.
        spec = distinct_block_sets(registry, 1)[0]
        cell = ("BTCUSDT", "4h")
        one = expand_universe(spec, [cell])[0]
        bars = {cell[0]: bars_by_cell[cell]}
        t0 = time.perf_counter()
        SpecJob(GATE_MIN_TRADES)((one, bars))
        dt = time.perf_counter() - t0
        n = len(bars_by_cell[cell])
        print(f"\nstep 2  one spec x {cell[0]}_{cell[1]} ({n:,} bars): "
              f"{dt * 1000:.0f} ms  ->  {dt / n * 1e6:.1f} us/bar")

        # Task 7 step 3: extrapolate before running the sweep.
        rate = dt / n
        print("\nstep 3  extrapolation over the phase-1 grid")
        for nspecs in (34, 100):
            secs = nbars * nspecs * rate
            print(f"  {nspecs:>3} specs x 20 cells = {nbars * nspecs / 1e6:>5.0f}M "
                  f"bar-evals -> {secs / 60:>6.1f} min serial, "
                  f"{secs / 60 / 7:>5.1f} min on 7 workers")

        # Task 7 step 4: parallel MUST equal serial, or fan-out is illegal
        # under screen-protocol-v1.
        specs = distinct_block_sets(registry, 3)
        small = cell_list[:4]
        jobs = build_jobs(specs, small, bars_by_cell)
        print(f"\nstep 4  parallel == serial proof over {len(jobs)} jobs")
        t0 = time.perf_counter()
        ser = run_all(SpecJob(GATE_MIN_TRADES), jobs, workers=1)
        t_ser = time.perf_counter() - t0
        t0 = time.perf_counter()
        par = run_all(SpecJob(GATE_MIN_TRADES), jobs, workers=4)
        t_par = time.perf_counter() - t0

        errs = [r for r in ser + par if isinstance(r, CellError)]
        if errs:
            print(f"  CellError: {errs[0]}")
            return 1
        same = all(a[0]["metrics"] == b[0]["metrics"] and a[1] == b[1]
                   for a, b in zip(ser, par))
        print(f"  serial {t_ser:.1f}s | parallel {t_par:.1f}s | "
              f"metrics identical: {same}")
        return 0 if same else 1

    # Full phase-1 sweep, timed. Writes nothing.
    nspecs = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    specs = distinct_block_sets(registry, nspecs)
    jobs = build_jobs(specs, cell_list, bars_by_cell)
    print(f"\nsweep  {len(specs)} distinct block-sets x {len(cell_list)} cells "
          f"= {len(jobs)} spec-cell evaluations")
    t0 = time.perf_counter()
    out = run_all(SpecJob(GATE_MIN_TRADES), jobs, workers=0)
    dt = time.perf_counter() - t0

    errs = [r for r in out if isinstance(r, CellError)]
    passed = sum(1 for r in out if not isinstance(r, CellError) and r[1])
    reasons: dict[str, int] = {}
    for r in out:
        if not isinstance(r, CellError) and not r[1]:
            reasons[r[2]] = reasons.get(r[2], 0) + 1

    print(f"\n  wall-clock {dt / 60:.1f} min ({dt:.0f}s)")
    print(f"  {passed} passed / {len(out) - len(errs) - passed} failed "
          f"/ {len(errs)} CellError")
    print(f"  fail reasons: {reasons}")
    if errs:
        print(f"  first error: {errs[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
