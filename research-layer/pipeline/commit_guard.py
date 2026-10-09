"""Registry commit guard (2026-10-09 stopgap, Coen's go).

GitHub blocks any file larger than 100 MiB, and it checks every blob in the
pushed history, not just the tip. registry_log.jsonl was 81.6 MB on
2026-10-09 and growing about 2.9 MB a day. One COMMIT of a version over the
limit would make that commit and everything after it unpushable without a
history rewrite of the shared live branch. So every path that commits the
registry asks this guard first, and leaves the registry out of its commit
once the file reaches GUARD_BYTES. The chain itself keeps growing on disk
untouched; only committing it pauses, until the chain is split into segments
(option B in the vault note project_registry_log_size_2026-10-09).

The four committers: loop.commit_cycle, tasks/run_gauntlet_worker.bat,
tasks/run_gauntlet_stats.bat and tasks/run_quarantine.bat. Everything else
each of them commits (bundles, price CSVs) still commits.

A pause is degraded, not failed: the writers exit 0 as before, and the pause
is an item in logs/degraded_commit.json (writer "commit") for the Sentinel's
research_degraded check. Every guard call rewrites the ledger, so its ts_utc
shows the guard is still running. An item seen again keeps its original
since_utc.

Fail-safe: a registry the guard cannot stat is never committed, and the CLI's
only "commit it" answer is exit 0, so a crash, a missing python or any other
exit code leaves the registry out. A ledger that cannot be written is logged
and never changes the decision.

CLI (the wrappers call it with no arguments, from the layer directory):
    python -m pipeline.commit_guard [--registry P] [--logs-dir D] [--guard-bytes N]
    exit 0 = commit the registry; exit 3 = held back (logged + ledger item).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import degraded

GITHUB_LIMIT_BYTES = 100 * 1024 * 1024   # docs.github.com: "blocks files larger than 100 MiB"
# 5 MiB under the limit. The check and the `git add` that follows are seconds
# apart, while the biggest DAY of growth measured was 8.7 MB, so the margin is
# for the race, not for a day's growth.
GUARD_BYTES = 95 * 1024 * 1024
LEDGER = "degraded_commit.json"
WRITER = "commit"
SOURCE = "registry_commit"
KEY = "registry_log.jsonl"
EXIT_OK = 0
EXIT_PAUSED = 3

LAYER = Path(__file__).resolve().parent.parent


def _record(logs_dir, seen: list[dict]) -> None:
    try:
        degraded.record(Path(logs_dir) / LEDGER, WRITER, seen, remove_unseen=True)
    except Exception as exc:                    # noqa: BLE001 -- never changes the decision
        print(f"commit_guard: WARNING ledger not written ({type(exc).__name__}: {exc})",
              flush=True)


def registry_committable(registry_path, logs_dir, *, guard_bytes: int = GUARD_BYTES) -> bool:
    """True when the registry may be committed now. Never raises."""
    try:
        size = Path(registry_path).stat().st_size
    except OSError as exc:
        reason = f"cannot stat the registry ({type(exc).__name__}); not committed"
        print(f"commit_guard: WARNING {reason}", flush=True)
        _record(logs_dir, [{"source": SOURCE, "key": KEY, "reason": reason}])
        return False
    if size >= guard_bytes:
        reason = (f"registry_log.jsonl is {size:,} bytes, at or over the {guard_bytes:,}-byte "
                  f"commit guard (GitHub blocks files over {GITHUB_LIMIT_BYTES:,}); the chain "
                  f"keeps growing on disk but is NOT committed until it is segmented")
        print(f"commit_guard: WARNING {reason}", flush=True)
        _record(logs_dir, [{"source": SOURCE, "key": KEY, "reason": reason}])
        return False
    print(f"commit_guard: registry_log.jsonl {size:,} bytes < {guard_bytes:,} guard; commit",
          flush=True)
    _record(logs_dir, [])
    return True


def _positive(text: str) -> int:
    n = int(text)
    if n <= 0:
        raise argparse.ArgumentTypeError("must be a positive byte count")
    return n


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.commit_guard",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--registry", type=Path, default=LAYER / "registry_log.jsonl")
    ap.add_argument("--logs-dir", type=Path, default=LAYER / "logs")
    ap.add_argument("--guard-bytes", type=_positive, default=GUARD_BYTES)
    return ap


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    ok = registry_committable(ns.registry, ns.logs_dir, guard_bytes=ns.guard_bytes)
    if not ok:
        print("commit_guard: registry_log.jsonl NOT committed this run", flush=True)
    return EXIT_OK if ok else EXIT_PAUSED


if __name__ == "__main__":
    sys.exit(main())
