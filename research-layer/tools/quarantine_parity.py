"""Quarantine parity for Morpheus 2b-2 (design s6.1): recompute a seeded
sample of chained quarantine DAYS from the registry and the price files, so
the OLD recorder and the NEW one can be compared EXACTLY. For each sampled
date the dump holds the snapshot digests (`hash_price_files`,
`hash_bars_used`) and the decision rows (`observe_day`) the recorder produces
for the daily strategies that have rows on that date.

Read-only: it never writes the chain or the data directory. Runs unchanged
under the OLD code, which has no `pipeline.qcells` (old `quarantine` names
the cells parameter `assets`, so every call here is positional, and its
`_load_eligible_bars` returns a 2-tuple where the new one returns a
3-tuple, so only element 0 is taken)."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

LAYER = Path.cwd()
if str(LAYER) not in sys.path:
    sys.path.insert(0, str(LAYER))

import pipeline                                                        # noqa: E402
from pipeline import quarantine as q                                   # noqa: E402
from pipeline.registry import Registry                                 # noqa: E402

try:
    import pipeline.qcells                                             # noqa: E402,F401
except ModuleNotFoundError as e:             # the pre-2b-2 code has no cell keys
    if e.name != "pipeline.qcells":
        raise

try:
    from pipeline.common import canonical_json
except ImportError:                                                    # pragma: no cover
    def canonical_json(obj) -> str:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))

REQUIRED_META = ("seed", "sample_n", "registry_sha256", "registry_entries")
MISSING = "<missing>"
SHOW_DIFFS = 5


def _sorted_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["strategy_id"], r["asset"]))


def _daily(spec: dict) -> bool:
    return spec["universe"].get("timeframe", "1d") == "1d"


def _one_date(date: str, data_dir: Path, specs: dict, entered: dict,
              chained: list[dict]) -> dict:
    """The fingerprint of one chained day. `chained` = the decision payloads
    chained for `date` by daily strategies; their assets are the cells."""
    sids = sorted({r["strategy_id"] for r in chained})
    cells = sorted({r["asset"] for r in chained})
    # old code returns a 2-tuple, new code a 3-tuple: element 0 is the bars
    bars = q._load_eligible_bars(data_dir, cells, date)[0]
    rows: list[dict] = []
    for sid in sids:
        rows.extend(q.observe_day(specs[sid], bars, date, entered[sid]))
    rows = _sorted_rows(rows)
    return {
        "data_sha256": q.hash_price_files(data_dir, cells),
        "bars_sha256": q.hash_bars_used(data_dir, cells, date),
        "rows": rows,
        # information only: an older engine revision may have written the rows
        "chain_rows_equal": canonical_json(rows) == canonical_json(_sorted_rows(chained)),
    }


def dump(registry_path: Path, data_dir: Path, out: Path, sample: int, seed: int) -> int:
    registry = Registry(registry_path)
    entries = list(registry.entries())
    specs = {e["payload"]["strategy_id"]: e["payload"]
             for e in entries if e["entry_type"] == "strategy_registered"}
    entered = q.quarantine_entry_dates(registry)
    # A strategy whose spec is missing stays IN: observe_day then raises for its
    # date, which is recorded as an error (fail closed). Only a known intraday
    # spec is left out: this tool proves DAILY rows, and old code cannot run one.
    rows_by_date: dict[str, list[dict]] = {}
    for e in entries:
        if e["entry_type"] == "quarantine_decision":
            p = e["payload"]
            spec = specs.get(p["strategy_id"])
            if spec is not None and not _daily(spec):
                continue
            rows_by_date.setdefault(p["date"], []).append(p)
    dates = sorted({e["payload"]["date"] for e in entries
                    if e["entry_type"] == "quarantine_data_snapshot"})
    picked = sorted(random.Random(seed).sample(dates, min(sample, len(dates))))
    results, errors = {}, {}
    for i, date in enumerate(picked, 1):
        try:
            results[date] = _one_date(date, data_dir, specs, entered,
                                      rows_by_date.get(date, []))
        except Exception as exc:        # identical errors on both sides are still a failure
            errors[date] = repr(exc)
        if i % 20 == 0:
            print(f"{i}/{len(picked)}", flush=True)
    meta = {
        "pipeline_file": str(pipeline.__file__),
        "qcells_module": "pipeline.qcells" in sys.modules,
        "seed": seed,
        "sample_n": sample,
        "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "registry_entries": len(entries),
    }
    out.write_text(json.dumps({"meta": meta, "results": results, "errors": errors},
                              indent=1, sort_keys=True), encoding="utf-8")
    print(f"wrote {out}: {len(results)} dates, {len(errors)} errors")
    return 0


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return exc


def compare(path_a: Path, path_b: Path) -> int:
    x, y = _load(path_a), _load(path_b)
    bad: list[str] = []
    for tag, d in (("A", x), ("B", y)):
        if isinstance(d, Exception):
            bad.append(f"{tag}: unreadable ({d!r})")
        elif not (isinstance(d, dict) and isinstance(d.get("meta"), dict)
                  and isinstance(d.get("results"), dict)
                  and isinstance(d.get("errors"), dict)):
            bad.append(f"{tag}: malformed dump (meta/results/errors missing)")
    if bad:
        for line in bad:
            print(line)
        print(f"compared 0; mismatches {len(bad)}")
        return 1
    mx, my = x["meta"], y["meta"]
    print(f"A pipeline_file: {mx.get('pipeline_file')}, qcells_module: {mx.get('qcells_module')}")
    print(f"B pipeline_file: {my.get('pipeline_file')}, qcells_module: {my.get('qcells_module')}")
    for k in REQUIRED_META:
        a, b = mx.get(k, MISSING), my.get(k, MISSING)
        if a == MISSING or b == MISSING:
            bad.append(f"meta {k} missing: A={a!r} B={b!r}")
        elif a != b:
            bad.append(f"{k} differs: {a!r} != {b!r}")
    for tag, d in (("A", x), ("B", y)):
        if not d["results"]:
            bad.append(f"{tag}: empty results")
        if d["errors"]:
            bad.append(f"{tag}: errors present ({len(d['errors'])})")
            for date, err in sorted(d["errors"].items()):
                print(f"{tag} error {date}: {err}")
    if set(x["results"]) != set(y["results"]):
        only = sorted(set(x["results"]) ^ set(y["results"]))
        bad.append(f"results key sets differ: {only[:SHOW_DIFFS]}")
    both = sorted(set(x["results"]) & set(y["results"]))
    diff = [d for d in both
            if canonical_json(x["results"][d]) != canonical_json(y["results"][d])]
    if diff:
        bad.append(f"{len(diff)} date(s) differ; first {min(len(diff), SHOW_DIFFS)}: "
                   f"{diff[:SHOW_DIFFS]}")
    for line in bad:
        print(line)
    print(f"compared {len(both)}; mismatches {len(bad)}")
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.quarantine_parity")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--registry", type=Path, required=True)
    d.add_argument("--data-dir", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--sample", type=int, default=60)
    d.add_argument("--seed", type=int, default=20261011)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        return compare(a.a, a.b)
    return dump(a.registry, a.data_dir, a.out, a.sample, a.seed)


if __name__ == "__main__":
    sys.exit(main())
