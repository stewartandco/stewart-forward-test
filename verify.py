"""
verify.py — standalone chain integrity walker for forward_test_log.jsonl.

Mirrors tasks/verify_forward_test.py but ships inside the public repo so anyone
who clones can verify the chain without needing Stewart & Co.'s internal code.

Usage:
    python verify.py
    python verify.py path/to/forward_test_log.jsonl
"""
from __future__ import annotations

import sys
import json
import hashlib
import argparse
from pathlib import Path

# Force UTF-8 stdout — Windows cp1252 console can't render the status glyphs.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GENESIS_HASH = "0" * 64


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)


def _entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical_json(entry).encode("utf-8")).hexdigest()


def verify(log_path: Path) -> int:
    if not log_path.exists():
        print(f"ERROR: log file not found: {log_path}")
        return 2

    prev_hash  = GENESIS_HASH
    n_ok       = 0
    n_bad      = 0
    first_date = None
    last_date  = None
    by_system: dict[str, int] = {}

    with log_path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"  line {lineno}: PARSE ERROR — {exc}")
                n_bad += 1
                continue

            chained = entry.get("prev_entry_hash")
            if chained != prev_hash:
                print(f"  line {lineno}: BROKEN CHAIN")
                print(f"    expected prev_entry_hash = {prev_hash}")
                print(f"    actual prev_entry_hash   = {chained}")
                n_bad += 1
            else:
                n_ok += 1

            d = entry.get("decision_date", "?")
            if first_date is None:
                first_date = d
            last_date = d
            sk = entry.get("system", "?")
            by_system[sk] = by_system.get(sk, 0) + 1

            prev_hash = _entry_hash(entry)

    total = n_ok + n_bad
    print()
    print(f"  Chain length      : {total} entries")
    if total > 0:
        print(f"  First decision    : {first_date}")
        print(f"  Last decision     : {last_date}")
    print(f"  By system         : "
          + ", ".join(f"{s}={n}" for s, n in sorted(by_system.items())))
    print()
    if n_bad == 0 and total > 0:
        print(f"  CHAIN VALID — all {total} entries link correctly.")
        return 0
    elif total == 0:
        print("  Empty log.")
        return 1
    else:
        print(f"  CHAIN BROKEN — {n_bad}/{total} entries fail integrity check.")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("path", nargs="?", default="forward_test_log.jsonl",
                    help="Path to forward_test_log.jsonl (default: ./forward_test_log.jsonl)")
    args = ap.parse_args()

    log_path = Path(args.path)
    print(f"Verifying chain at {log_path}")
    print("=" * 70)
    return verify(log_path)


if __name__ == "__main__":
    sys.exit(main())
