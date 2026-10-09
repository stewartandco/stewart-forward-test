"""Git-only segments of registry_log.jsonl (design:
docs/2026-10-09-registry-segments-design.md, Coen 2026-10-09).

The live chain stays ONE CRLF file that every writer and reader uses exactly
as before. Git tracks registry_log.d/ instead: LF segment files that, joined
in order, equal the live file's complete lines with CRLF -> LF. A segment
seals at SEGMENT_MAX_BYTES on an entry boundary and is never rewritten;
MANIFEST.json records each sealed segment. sync() is what the four committers
(loop.commit_cycle and the worker, stats and quarantine wrappers) call before
staging research-layer/registry_log.d.

CLI:  python -m pipeline.chain_mirror sync    exit 0 mirror current, 3 refused
      python -m pipeline.chain_mirror check   exit 0 MATCH, 1 BEHIND/MISMATCH
"""
from __future__ import annotations

import os
from pathlib import Path

SEGMENT_MAX_BYTES = 40 * 1024 * 1024   # under GitHub's 50 MiB warning
FORMAT = "registry-segments-v1"
MANIFEST = "MANIFEST.json"


class MirrorRefused(RuntimeError):
    """The mirror cannot be brought up to date without rewriting history."""


def segment_name(n: int) -> str:
    return f"{n:06d}.jsonl"


def read_live(registry_path) -> bytes:
    """The live file's COMPLETE lines, CRLF -> LF. Only the size seen at
    open is read and a trailing partial line (a writer caught mid-append) is
    cut at the last LF, the rule Registry.snapshot() uses. canonical_json
    escapes every control character inside a value, so the only CR in the
    file is the one in each line terminator and the replace is exact."""
    with Path(registry_path).open("rb") as f:
        data = f.read(os.fstat(f.fileno()).st_size)
    return data[: data.rfind(b"\n") + 1].replace(b"\r\n", b"\n")


def split_lines(buf: bytes) -> list[bytes]:
    """buf (empty, or ending with LF) as lines, each keeping its LF."""
    return [p + b"\n" for p in buf.split(b"\n")[:-1]]


def plan_pieces(lines: list[bytes], start: int, max_bytes: int) -> list[tuple[int, int]]:
    """Greedy (i, j) line ranges over lines[start:]. Every range but the
    last is a full segment to seal: adding its next line would pass
    max_bytes. The last range is the active remainder, possibly empty."""
    pieces: list[tuple[int, int]] = []
    i, size = start, 0
    for j in range(start, len(lines)):
        n = len(lines[j])
        if n > max_bytes:
            raise MirrorRefused(f"entry at line {j + 1} is {n} bytes, larger than "
                                f"segment_max_bytes {max_bytes}")
        if size + n > max_bytes:
            pieces.append((i, j))
            i, size = j, 0
        size += n
    pieces.append((i, len(lines)))
    return pieces
