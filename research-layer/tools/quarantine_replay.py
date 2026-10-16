"""Old-vs-new replay of the quarantine recorder on a COPY of real data
(2026-10-16 fast path design s5.2). Read-only against the live chain:
`prefix` reads it and writes a copy; `run` and `compare` touch copies only.

    prefix   cut a copy of the chain just before a daily run's first entry,
             so that run's dates are owed again on the copy
    run      run pipeline.quarantine from a given code root (old or new),
             timing the run and every chain.lock hold
    compare  the entries two copies appended past the prefix, on
             (entry_type, payload) in order -- ts_utc and the hash links
             differ by construction -- plus the degraded ledgers and the
             run return codes

Nothing from `pipeline` is imported at module level: `run` must load the
code under test from --code-root, so old and new are each measured as
themselves. Never scheduled; never pointed at the live chain except as the
`prefix` source."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

QUARANTINE_TYPES = ("quarantine_data_snapshot",
                    "quarantine_data_snapshot_supplement",
                    "quarantine_decision")
CUT_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class _Refused(Exception):
    """A usage problem: the tool exits 2 and changes nothing."""


class _Unparseable(Exception):
    def __init__(self, side: str, index: int):
        super().__init__(side, index)
        self.side, self.index = side, index


def _refuse(msg: str) -> int:
    print(f"refused: {msg}", file=sys.stderr)
    return 2


def cmd_prefix(a) -> int:
    if Path(a.out).resolve() == Path(a.registry).resolve():
        return _refuse("--out is the same file as --registry")
    if not CUT_UTC_RE.fullmatch(a.cut_utc):
        return _refuse(f"--cut-utc {a.cut_utc!r} is not YYYY-MM-DDTHH:MM:SSZ")
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
    root = Path(a.code_root).resolve()
    if not (root / "pipeline" / "quarantine.py").is_file():
        return _refuse(f"no pipeline/quarantine.py under --code-root {root}")
    sys.path.insert(0, str(root))
    from pipeline import chainlock
    from pipeline import quarantine
    for mod in (chainlock, quarantine):
        src = getattr(mod, "__file__", None)
        if src is None or not Path(src).resolve().is_relative_to(root):
            return _refuse(f"pipeline loaded from {src}, not from "
                           f"--code-root {root}")
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
    except SystemExit as exc:               # argparse inside run: its code is the rc
        code = exc.code
        if isinstance(code, str):
            print(code, file=sys.stderr)
        rc = code if isinstance(code, int) else (0 if code is None else 1)
        error = repr(exc)
    except Exception as exc:                # the measurement must survive a raise
        traceback.print_exc()
        rc, error = 1, repr(exc)
    wall = time.perf_counter() - t0
    Path(a.timing).write_text(json.dumps({
        "rc": rc, "wall_s": wall, "holds_s": holds,
        "max_hold_s": max(holds) if holds else 0.0, "error": error,
        "pipeline_file": str(Path(quarantine.__file__).resolve())},
        indent=1), encoding="utf-8")
    print(f"rc={rc} wall={wall:.1f}s holds={len(holds)} "
          f"max_hold={max(holds) if holds else 0.0:.2f}s")
    return rc


def _raw(path: str) -> list[bytes]:
    """Every raw line, blank ones included, so --skip counts the same lines
    `prefix` reports as 'kept N'."""
    return Path(path).read_bytes().splitlines()


def _appended(raw: list[bytes], skip: int, side: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for line in raw[skip:]:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            out.append((e["entry_type"], e["payload"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise _Unparseable(side, len(out)) from exc
    return out


def _pair(a_val: str | None, b_val: str | None, what: str) -> None:
    if (a_val is None) != (b_val is None):
        raise _Refused(f"--{what}-a and --{what}-b must be given together")
    if a_val is not None:
        for p in (a_val, b_val):
            if not Path(p).exists():
                raise _Refused(f"{what} {p} does not exist")


def _ledger(path: str | None) -> set[tuple[str, str, str]]:
    if path is None:
        return set()
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8"))["items"]
        return {(i["source"], i["key"], i["reason"]) for i in items}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _Refused(f"ledger {path} is not a ledger file ({exc})") from exc


def _timing_rc(path: str | None):
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["rc"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _Refused(f"timing {path} is not a timing file ({exc})") from exc


def cmd_compare(a) -> int:
    try:
        _pair(a.ledger_a, a.ledger_b, "ledger")
        _pair(a.timing_a, a.timing_b, "timing")
        la, lb = _ledger(a.ledger_a), _ledger(a.ledger_b)
        ta, tb = _timing_rc(a.timing_a), _timing_rc(a.timing_b)
    except _Refused as exc:
        return _refuse(str(exc))
    raw_a, raw_b = _raw(a.a), _raw(a.b)
    n = a.skip
    if (len(raw_a) < n or len(raw_b) < n
            or raw_a[:n] != raw_b[:n]):
        print(f"MISMATCH: the two copies do not share the first {n} lines")
        return 1
    try:
        ea = _appended(raw_a, n, "A")
        eb = _appended(raw_b, n, "B")
    except _Unparseable as exc:
        print(f"MISMATCH: unparseable line at index {exc.index} in {exc.side}")
        return 1
    print(f"A appended {len(ea)}, B appended {len(eb)}")
    if not ea and not eb:
        print("NO RESULT: neither copy appended anything past --skip")
        return 3
    for i, (x, y) in enumerate(zip(ea, eb)):
        if x != y:
            print(f"MISMATCH at index {i}:\n  A {json.dumps(x)[:400]}\n"
                  f"  B {json.dumps(y)[:400]}")
            return 1
    if len(ea) != len(eb):
        print(f"MISMATCH: lengths differ ({len(ea)} vs {len(eb)})")
        return 1
    if la != lb:
        print(f"MISMATCH in the degraded ledgers: only A {sorted(la - lb)}, "
              f"only B {sorted(lb - la)}")
        return 1
    if ta is not None and ta != tb:
        print(f"MISMATCH: run rc differs (A rc={ta}, B rc={tb})")
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
    c.add_argument("--timing-a")
    c.add_argument("--timing-b")
    a = ap.parse_args(argv)
    if a.cmd == "run" and a.argv[:1] == ["--"]:
        a.argv = a.argv[1:]
    return {"prefix": cmd_prefix, "run": cmd_run,
            "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
