"""Screen's commit list: the bundles screen chained, waiting for the loop's commit.

2026-10-09: 8,627 screen bundles (3 GB) chained between 08-31 and 09-28 were
never committed. commit_cycle's scope was "strategies registered THIS cycle",
and 14 cycles died between screen and commit (the in-loop v6 gauntlet killed
at the task limit, crashed or aborted), so no later commit ever reached them.
A spec screened in a later cycle than its registration (a deadline or a held
chain.lock defers it) would be missed the same way.

So screen appends each bundle's repo-relative paths here after the chain
write that judges it, and loop.commit_cycle takes the list into its scope and
deletes it only after the commit succeeds: the gauntlet worker's pattern
(logs/gauntlet_worker_commit_paths.txt). The file lives in logs/ (gitignored).
The 10-09 backlog itself (8,627 screen + 2,195 v6 gauntlet bundles, every
one re-hashed against its chained artifacts_hash) was committed by hand in
per-day scoped commits on 2026-10-09; it was never on this list.
"""
from __future__ import annotations

from pathlib import Path

SCREEN_COMMIT_LIST = "screen_commit_paths.txt"
SCREEN_COMMIT_TAKING = "screen_commit_paths.taking"
BUNDLE_FILES = ("config.json", "equity.csv", "trades.csv")


def append(logs_dir: Path, paths: list[str]) -> None:
    """Append repo-relative paths, one per line. Opened, written and closed in
    one go, so a concurrent take (a rename) never meets a long-held handle."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    with (logs_dir / SCREEN_COMMIT_LIST).open("a", encoding="utf-8", newline="\n") as f:
        f.write("".join(p + "\n" for p in paths))
