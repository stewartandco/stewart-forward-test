"""Old-vs-new replay of the quarantine recorder on a COPY of real data
(2026-10-16 fast path design s5.2). Read-only against the live chain:
`prefix` reads it and writes a copy; `run` and `compare` touch copies only.

    prefix   cut a copy of the chain just before a daily run's first entry,
             so that run's dates are owed again on the copy
    run      run pipeline.quarantine from a given code root (old or new),
             timing the run and every chain.lock hold
    compare  the entries two copies appended past the prefix, on
             (entry_type, payload) in order -- ts_utc and the hash links
             differ by construction -- plus the degraded ledgers

Nothing from `pipeline` is imported at module level: `run` must load the
code under test from --code-root, so old and new are each measured as
themselves. Never scheduled; never pointed at the live chain except as the
`prefix` source."""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

QUARANTINE_TYPES = ("quarantine_data_snapshot",
                    "quarantine_data_snapshot_supplement",
                    "quarantine_decision")


def cmd_prefix(a) -> int:
    lines = Path(a.registry).read_bytes().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        e = json.loads(line)
        if e["entry_type"] in QUARANTINE_TYPES and e["ts_utc"] >= a.cut_utc:
            Path(a.out).write_bytes(b"".join(lines[:i]))
            print(f"cut at line {i + 1} ({e['entry_type']} {e['ts_utc']})")
            print(f"kept {i} lines")
            return 0
    print(f"no quarantine entry at or after {a.cut_utc}", file=sys.stderr)
    return 2


def cmd_run(a) -> int:
    sys.path.insert(0, str(Path(a.code_root).resolve()))
    from pipeline import chainlock
    from pipeline import quarantine
    holds: list[float] = []
    started: dict[int, float] = {}
    real_acquire = chainlock.ChainLock.acquire
    real_release = chainlock.ChainLock.release

    def acquire(self):
        real_acquire(self)
        started[id(self)] = time.perf_counter()

    def release(self):
        t0 = started.pop(id(self), None)
        real_release(self)
        if t0 is not None:
            holds.append(time.perf_counter() - t0)

    chainlock.ChainLock.acquire = acquire
    chainlock.ChainLock.release = release
    t0, error = time.perf_counter(), None
    try:
        rc = quarantine.run(a.argv)
    except BaseException as exc:            # the measurement must survive it
        traceback.print_exc()
        rc, error = 1, repr(exc)
    wall = time.perf_counter() - t0
    Path(a.timing).write_text(json.dumps({
        "rc": rc, "wall_s": wall, "holds_s": holds,
        "max_hold_s": max(holds) if holds else 0.0, "error": error},
        indent=1), encoding="utf-8")
    print(f"rc={rc} wall={wall:.1f}s holds={len(holds)} "
          f"max_hold={max(holds) if holds else 0.0:.2f}s")
    return rc


def _appended(path: str, skip: int) -> list[tuple[str, dict]]:
    lines = [l for l in Path(path).read_bytes().splitlines() if l.strip()]
    return [(e["entry_type"], e["payload"])
            for e in (json.loads(l) for l in lines[skip:])]


def _ledger(path: str | None) -> set[tuple[str, str, str]]:
    if path is None or not Path(path).exists():
        return set()
    items = json.loads(Path(path).read_text(encoding="utf-8"))["items"]
    return {(i["source"], i["key"], i["reason"]) for i in items}


def cmd_compare(a) -> int:
    ea, eb = _appended(a.a, a.skip), _appended(a.b, a.skip)
    print(f"A appended {len(ea)}, B appended {len(eb)}")
    for i, (x, y) in enumerate(zip(ea, eb)):
        if x != y:
            print(f"MISMATCH at index {i}:\n  A {json.dumps(x)[:400]}\n"
                  f"  B {json.dumps(y)[:400]}")
            return 1
    if len(ea) != len(eb):
        print(f"MISMATCH: lengths differ ({len(ea)} vs {len(eb)})")
        return 1
    la, lb = _ledger(a.ledger_a), _ledger(a.ledger_b)
    if la != lb:
        print(f"MISMATCH in the degraded ledgers: only A {sorted(la - lb)}, "
              f"only B {sorted(lb - la)}")
        return 1
    print("MATCH")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prefix")
    p.add_argument("--registry", required=True)
    p.add_argument("--cut-utc", required=True)
    p.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--code-root", required=True)
    r.add_argument("--timing", required=True)
    r.add_argument("argv", nargs=argparse.REMAINDER)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--skip", type=int, required=True)
    c.add_argument("--ledger-a")
    c.add_argument("--ledger-b")
    a = ap.parse_args(argv)
    if a.cmd == "run" and a.argv[:1] == ["--"]:
        a.argv = a.argv[1:]
    return {"prefix": cmd_prefix, "run": cmd_run,
            "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
