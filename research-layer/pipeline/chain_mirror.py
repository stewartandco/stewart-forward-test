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

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import degraded
from .common import GENESIS_HASH, entry_hash
from .lock import FileLock, FileLockTimeout

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


LEDGER = "degraded_commit.json"
WRITER = "commit"
SOURCE = "chain_mirror"
KEY = "registry_log.d"
EXIT_OK = 0
EXIT_REFUSED = 3
LOCK_TIMEOUT_S = 120.0     # a sync takes about a second; the loop and worker can overlap at 20:00
LOCK_STALE_S = 600.0

LAYER = Path(__file__).resolve().parent.parent


@dataclass
class SyncResult:
    ok: bool
    reason: str | None = None
    sealed_new: list[str] = field(default_factory=list)
    active: str | None = None
    active_bytes: int = 0
    lines_total: int = 0


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _entries(lines: list[bytes]) -> list[bytes]:
    return [ln for ln in lines if ln.strip()]


def _first_prev(lines: list[bytes]) -> str | None:
    e = _entries(lines)
    return json.loads(e[0]).get("prev_entry_hash") if e else None


def _last_hash(lines: list[bytes]) -> str | None:
    e = _entries(lines)
    return entry_hash(json.loads(e[-1])) if e else None


def load_manifest(mirror_dir, max_bytes: int = SEGMENT_MAX_BYTES) -> dict:
    """The manifest, or a fresh one (recording max_bytes) when none exists.
    An existing manifest's segment_max_bytes always wins: sealed sizes are
    fixed once the mirror starts."""
    p = Path(mirror_dir) / MANIFEST
    if not p.exists():
        return {"format": FORMAT, "segment_max_bytes": max_bytes, "line_terminator": "LF",
                "source": "research-layer/registry_log.jsonl (CRLF on disk)", "sealed": []}
    m = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise MirrorRefused(f"{MANIFEST} format is {m.get('format') if isinstance(m, dict) else m!r}, "
                            f"expected {FORMAT}")
    return m


def _write(path: Path, data: bytes) -> None:
    """Atomic replace via a .tmp beside the target (gitignored; removed at
    the start of every sync if a kill left one)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _sync_locked(registry_path: Path, mdir: Path, max_bytes: int) -> SyncResult:
    for t in mdir.glob("*.tmp"):
        t.unlink()
    man = load_manifest(mdir, max_bytes)
    max_bytes = man["segment_max_bytes"]
    sealed = man["sealed"]
    buf = read_live(registry_path)
    lines = split_lines(buf)
    off = 0
    for k, rec in enumerate(sealed, start=1):
        if rec["file"] != segment_name(k):
            raise MirrorRefused(f"manifest record {k} names {rec['file']}")
        seg = buf[off: off + rec["bytes"]]
        if len(seg) != rec["bytes"] or _sha(seg) != rec["sha256"]:
            raise MirrorRefused(f"prefix mismatch at segment {rec['file']}: the live "
                                f"file no longer holds its sealed bytes")
        f = mdir / rec["file"]
        if not f.is_file() or _sha(f.read_bytes()) != rec["sha256"]:
            raise MirrorRefused(f"sealed segment {rec['file']} on disk is missing or "
                                f"differs from the manifest")
        off += rec["bytes"]
    start = sealed[-1]["last_line"] if sealed else 0
    remainder = buf[off:]
    active_path = mdir / segment_name(len(sealed) + 1)
    if active_path.exists():
        cur = active_path.read_bytes()
        if not remainder.startswith(cur):
            raise MirrorRefused(
                f"active segment {active_path.name} ({len(cur)} bytes) is not a prefix "
                f"of the live file's tail ({len(remainder)} bytes): the live file was "
                f"truncated or rewritten")
    pieces = plan_pieces(lines, start, max_bytes)
    new_sealed = []
    for n, (i, j) in enumerate(pieces[:-1], start=len(sealed) + 1):
        data = b"".join(lines[i:j])
        _write(mdir / segment_name(n), data)
        new_sealed.append({"file": segment_name(n), "first_line": i + 1, "last_line": j,
                           "bytes": len(data), "sha256": _sha(data),
                           "first_prev_entry_hash": _first_prev(lines[i:j]),
                           "last_entry_hash": _last_hash(lines[i:j])})
    i, j = pieces[-1]
    active_no = len(sealed) + len(new_sealed) + 1
    active = b"".join(lines[i:j])
    _write(mdir / segment_name(active_no), active)
    for stale in mdir.glob("*.jsonl"):
        if stale.stem.isdigit() and int(stale.stem) > active_no:
            stale.unlink()
    if new_sealed or not (mdir / MANIFEST).exists():
        man["sealed"] = sealed + new_sealed
        _write(mdir / MANIFEST, (json.dumps(man, indent=2) + "\n").encode("utf-8"))
    return SyncResult(ok=True, sealed_new=[r["file"] for r in new_sealed],
                      active=segment_name(active_no), active_bytes=len(active),
                      lines_total=len(lines))


def _record(logs_dir, res: SyncResult) -> None:
    seen = [] if res.ok else [{"source": SOURCE, "key": KEY, "reason": res.reason}]
    try:
        degraded.record(Path(logs_dir) / LEDGER, WRITER, seen, remove_unseen=True)
    except Exception as exc:                     # noqa: BLE001 -- never changes the result
        print(f"chain_mirror: WARNING ledger not written ({type(exc).__name__}: {exc})",
              flush=True)


def sync(registry_path, mirror_dir, *, logs_dir=None,
         max_bytes: int = SEGMENT_MAX_BYTES) -> SyncResult:
    """Bring registry_log.d up to date with the live file. Never raises: a
    refusal or any error is SyncResult(ok=False, reason=...), recorded in
    the degraded ledger when logs_dir is given."""
    mdir = Path(mirror_dir)
    try:
        mdir.mkdir(parents=True, exist_ok=True)
        with FileLock(mdir, timeout=LOCK_TIMEOUT_S, stale_after=LOCK_STALE_S):
            res = _sync_locked(Path(registry_path), mdir, max_bytes)
    except MirrorRefused as exc:
        res = SyncResult(ok=False, reason=str(exc))
    except FileLockTimeout as exc:
        res = SyncResult(ok=False, reason=f"mirror lock busy ({exc})")
    except Exception as exc:                     # noqa: BLE001 -- bookkeeping, see docstring
        res = SyncResult(ok=False, reason=f"{type(exc).__name__}: {exc}")
    if logs_dir is not None:
        _record(logs_dir, res)
    return res


def joined(mirror_dir) -> bytes:
    """Every segment the manifest implies (sealed + the active one), joined."""
    mdir = Path(mirror_dir)
    n = len(load_manifest(mdir)["sealed"]) + 1
    return b"".join((mdir / segment_name(k)).read_bytes()
                    for k in range(1, n + 1) if (mdir / segment_name(k)).is_file())


def check(registry_path, mirror_dir) -> str:
    """MATCH: the mirror equals the live file now. BEHIND: it is a strict
    prefix (a commit has not caught up). MISMATCH: anything else."""
    try:
        j = joined(mirror_dir)
    except (OSError, ValueError, MirrorRefused):
        return "MISMATCH"
    live = read_live(registry_path)
    if j == live:
        return "MATCH"
    return "BEHIND" if live.startswith(j) else "MISMATCH"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.chain_mirror",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["sync", "check"])
    ap.add_argument("--registry", type=Path, default=LAYER / "registry_log.jsonl")
    ap.add_argument("--mirror-dir", type=Path, default=LAYER / KEY)
    ap.add_argument("--logs-dir", type=Path, default=LAYER / "logs")
    return ap


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    if ns.command == "check":
        status = check(ns.registry, ns.mirror_dir)
        print(f"chain_mirror: {status}", flush=True)
        return 0 if status == "MATCH" else 1
    res = sync(ns.registry, ns.mirror_dir, logs_dir=ns.logs_dir)
    if res.ok:
        print(f"chain_mirror: synced {res.lines_total} lines; active {res.active} "
              f"{res.active_bytes:,} bytes; sealed now {res.sealed_new or 'none'}", flush=True)
        return EXIT_OK
    print(f"chain_mirror: WARNING sync refused ({res.reason}); registry_log.d NOT "
          f"committed this run", flush=True)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
