"""tools_variation_coverage_report.py -- SP5 Task 6 variation coverage map.

Pre-registered in docs/2026-08-28-market-data-universe-design.md (s7b, D12):
the declared per-block param grids (pipeline.blocks.BLOCK_TYPES, imported
here via pipeline.composer) are the "reasonable" fence -- params snap to
grid values at fingerprint time, so an absurd combination cannot exist
unless the grid declares it, and widening a grid is a reviewed, declared
edit. This report maps what INSIDE the fence has never been asked: per
family structure (the sorted set of (role, type) block pairs -- params
ignored) and per cell (assets + timeframe), it shows tested vs
declared-untested grid points, so unexplored variations are visible instead
of dependent on proposer luck.

STEERING IS OUT OF SCOPE: per the spec's own words, steering the proposer
toward the gaps this map exposes is explicitly OUT of this spec -- its own
decision later, once the map exists to steer by. This tool only reports.

READ ONLY: this script never writes to registry_log.jsonl or artifacts/. It
writes exactly one new file, this report. It walks the chain's
strategy_registered payloads, snaps every param to its declared grid with
the same expression composition_fingerprint uses (2 and 2.0 are ONE value),
and counts distinct snapped param combinations against the product of
declared grid sizes. Params without a declared grid are excluded from the
declared-combo denominator (they have no fence to measure against); they
still appear in the per-param lines when observed at more than one value.

Edge numbers (D11) are deliberately absent: structures aggregate many
strategies, so per-strategy labels have no row to live on here.

Usage:
    python tools_variation_coverage_report.py
        [--registry registry_log.jsonl]
        [--out docs/runs/2026-08-28-variation-coverage-report.md]

Refuses (exit 2, nothing written) if the chain contains no
strategy_registered entries at all -- an empty map would be a statement
about the fence, and there is no population to make it from.
"""
from __future__ import annotations

import sys
import math
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Runnable from any cwd, exactly like tools_benchmark_backfill_report.py.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:                    # idempotent under re-import
    sys.path.insert(0, _HERE)

from pipeline.composer import BLOCK_TYPES  # noqa: E402
from pipeline.registry import Registry     # noqa: E402

LAYER_ROOT = Path(__file__).resolve().parent

# Truncation bound for rendered value lists (test_untested_truncation).
MAX_LISTED = 20


def structure_key(payload: dict) -> tuple:
    """Identity of a family STRUCTURE: the sorted tuple of (role, type)
    pairs from a registered payload's blocks. Params are ignored -- two
    registrations differing only in params share a structure."""
    return tuple(sorted((b["role"], b["type"]) for b in payload["blocks"]))


def cell_key(payload: dict) -> str:
    """assets+timeframe cell label; pooled legacy universes render as
    BTCUSD+ETHUSD_1d."""
    u = payload["universe"]
    return f"{'+'.join(u['assets'])}_{u['timeframe']}"


def snap_value(role: str, btype: str, param: str, value):
    """composition_fingerprint's grid-snap rule, verbatim: a value equal to
    a grid element becomes THAT element (so 2 and 2.0 are one value); no
    declared grid means the value stands as-is."""
    grid = BLOCK_TYPES.get((role, btype), {}).get(param, {}).get("grid")
    return next((g for g in grid if g == value), value) if grid else value


def gridded_params(skey: tuple) -> dict:
    """{(role, type, param): grid} for every param of the structure's block
    types that declares a grid. Duplicate (role, type) pairs in a structure
    collapse to one param set -- the fence is per type, not per instance."""
    out = {}
    for role, btype in dict.fromkeys(skey):
        for param, schema in BLOCK_TYPES.get((role, btype), {}).items():
            grid = schema.get("grid")
            if grid:
                out[(role, btype, param)] = grid
    return out


def declared_combo_count(skey: tuple) -> int:
    """Product of declared grid sizes over the structure's gridded params.
    Params WITHOUT a declared grid are excluded from this denominator."""
    return math.prod(len(g) for g in gridded_params(skey).values())


def collect(registry: Registry) -> dict:
    """Walk strategy_registered payloads -> {structure_key: {
        "cells": {cell: {"registrations": int,
                         "tested_values": {(role, type, param): set},
                         "tested_combos": set[tuple]}},
        "param_values": {(role, type, param): set}   # across ALL cells
    }}. Every value is snapped before counting."""
    structures: dict = {}
    for e in registry.entries():
        if e.get("entry_type") != "strategy_registered":
            continue
        p = e["payload"]
        skey = structure_key(p)
        s = structures.setdefault(skey, {"cells": {}, "param_values": {}})
        cell = s["cells"].setdefault(cell_key(p), {
            "registrations": 0, "tested_values": {}, "tested_combos": set()})
        cell["registrations"] += 1
        combo = []
        for b in p["blocks"]:
            for param, v in b["params"].items():
                sv = snap_value(b["role"], b["type"], param, v)
                pkey = (b["role"], b["type"], param)
                cell["tested_values"].setdefault(pkey, set()).add(sv)
                s["param_values"].setdefault(pkey, set()).add(sv)
                combo.append((pkey, sv))
        cell["tested_combos"].add(tuple(sorted(combo, key=lambda kv: kv[0])))
    return structures


def swept_params(skey: tuple, s: dict) -> list:
    """The params this report lines out for a structure: every param with a
    declared grid, PLUS any param observed at >1 distinct snapped value
    across the structure's registrations ANYWHERE (any cell)."""
    keys = set(gridded_params(skey))
    keys |= {pk for pk, vals in s["param_values"].items() if len(vals) > 1}
    return sorted(keys)


def _fmt_value(v) -> str:
    return str(v)


def _fmt_values(values: list) -> str:
    """Comma list, truncated past MAX_LISTED with '... (+N more)'."""
    strs = [_fmt_value(v) for v in values]
    if len(strs) > MAX_LISTED:
        return (", ".join(strs[:MAX_LISTED])
                + f" ... (+{len(strs) - MAX_LISTED} more)")
    return ", ".join(strs)


def _ordered_tested(pkey: tuple, tested: set, grids: dict) -> list:
    """Tested values in grid order (for gridded params), off-grid extras
    after, string-sorted -- deterministic without cross-type comparisons."""
    grid = grids.get(pkey, [])
    on_grid = [g for g in grid if g in tested]
    extras = sorted((v for v in tested if v not in grid), key=str)
    return on_grid + extras


def structure_name(skey: tuple) -> str:
    return " + ".join(f"{role}/{btype}" for role, btype in skey)


def render_report(structures: dict, generated_utc: datetime) -> str:
    total_regs = sum(c["registrations"]
                     for s in structures.values()
                     for c in s["cells"].values())

    # Structure-level coverage: distinct snapped combos across ALL of the
    # structure's cells vs its declared combo count.
    per_structure = []
    for skey in sorted(structures, key=structure_name):
        s = structures[skey]
        declared = declared_combo_count(skey)
        union = set().union(*(c["tested_combos"] for c in s["cells"].values()))
        pct = 100.0 * len(union) / declared if declared else 0.0
        per_structure.append((skey, s, declared, len(union), pct))

    lines = [
        "# Variation coverage map (D12)",
        "",
        "READ ONLY: this script never writes to registry_log.jsonl or "
        "artifacts/. It writes exactly one new file, this report.",
        "",
        "D12 (docs/2026-08-28-market-data-universe-design.md s7b): the "
        "declared per-block param grids are the \"reasonable\" fence -- "
        "params snap to grid values at fingerprint time, so an absurd "
        "combination cannot exist unless the grid declares it, and widening "
        "a grid is a reviewed, declared edit. This map shows what INSIDE "
        "the fence has never been asked: tested vs declared-untested grid "
        "points per family structure and cell. Steering the proposer toward "
        "these gaps is explicitly OUT of this spec's scope -- its own "
        "decision later, once this map exists to steer by.",
        "",
        "Declared combo counts are the product of grid sizes over the "
        "structure's gridded params; params without declared grids are "
        "excluded from the declared-combo denominator (they have no fence "
        "to measure against). Tested combos are distinct snapped param "
        "tuples over ALL params of all blocks, so an off-grid param can "
        "still split combos.",
        "",
        f"Generated {generated_utc:%Y-%m-%d} UTC. "
        f"{len(structures)} structure(s), {total_regs} registration(s).",
        "",
    ]

    for skey, s, declared, union_n, pct in per_structure:
        grids = gridded_params(skey)
        swept = swept_params(skey, s)
        lines += [
            f"## Structure: {structure_name(skey)}",
            "",
            "| cell | registrations | tested combos | declared combos | "
            "coverage % |",
            "|---|---|---|---|---|",
        ]
        for cell in sorted(s["cells"]):
            c = s["cells"][cell]
            tested_n = len(c["tested_combos"])
            cell_pct = 100.0 * tested_n / declared if declared else 0.0
            lines.append(
                f"| {cell} | {c['registrations']} | {tested_n} | "
                f"{declared} | {cell_pct:.1f}% |")
        lines.append("")
        for cell in sorted(s["cells"]):
            c = s["cells"][cell]
            lines.append(f"Per-param coverage (cell {cell}):")
            for pkey in swept:
                role, btype, param = pkey
                tested = c["tested_values"].get(pkey, set())
                ordered = _ordered_tested(pkey, tested, grids)
                grid = grids.get(pkey)
                if grid is not None:
                    untested = [g for g in grid if g not in tested]
                    untested_str = (_fmt_values(untested) if untested
                                    else "(none)")
                else:
                    untested_str = "(no declared grid)"
                lines.append(
                    f"- {role}/{btype}.{param}: "
                    f"tested = {_fmt_values(ordered) if ordered else '(none)'}"
                    f"; untested = {untested_str}")
            lines.append("")

    total_declared = sum(d for _, _, d, _, _ in per_structure)
    total_tested = sum(u for _, _, _, u, _ in per_structure)
    overall_pct = (100.0 * total_tested / total_declared
                   if total_declared else 0.0)
    by_pct = sorted(per_structure, key=lambda t: (t[4], structure_name(t[0])))
    least = by_pct[:5]
    most = list(reversed(by_pct[-5:]))

    lines += [
        "## Global summary",
        "",
        f"- structures seen: {len(structures)}",
        f"- total registrations: {total_regs}",
        f"- declared combo points (summed over structures): "
        f"{total_declared}",
        f"- tested combo points (distinct snapped combos across all cells, "
        f"summed over structures): {total_tested} ({overall_pct:.1f}%)",
        "- params without declared grids are excluded from the "
        "declared-combo denominator.",
        "",
        "Top-5 most-covered structures (distinct combos across all cells "
        "vs declared):",
    ]
    for skey, _, declared, union_n, pct in most:
        lines.append(f"- {structure_name(skey)}: {union_n} / {declared} "
                     f"({pct:.1f}%)")
    lines += ["", "Top-5 least-covered structures:"]
    for skey, _, declared, union_n, pct in least:
        lines.append(f"- {structure_name(skey)}: {union_n} / {declared} "
                     f"({pct:.1f}%)")
    lines += [
        "",
        "RECORDED, NOT GATED: nothing here changes any strategy's state or "
        "steers any proposer run. This is a map, not chain data.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=LAYER_ROOT / "registry_log.jsonl")
    ap.add_argument("--out", type=Path, default=LAYER_ROOT / "docs" / "runs"
                    / "2026-08-28-variation-coverage-report.md")
    args = ap.parse_args(argv)

    structures = collect(Registry(args.registry))
    if not structures:
        print(f"REFUSED: no strategy_registered entries on the chain at "
              f"{args.registry} -- there is no population to map coverage "
              f"from. Nothing written.", file=sys.stderr)
        raise SystemExit(2)

    report = render_report(structures, datetime.now(timezone.utc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    total_regs = sum(c["registrations"]
                     for s in structures.values()
                     for c in s["cells"].values())
    print(f"wrote {args.out} ({len(structures)} structures, "
          f"{total_regs} registrations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
