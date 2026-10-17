"""Merge-day guards for the crypto grid switch (2b-3, Switch 1).

    python -m tools.grid_switch preflight --registry PATH --loop-state PATH
    python -m tools.grid_switch chain-note --registry PATH --note PATH \
        --prefix PREFIX [--dry-run]

`preflight` is the drain check. Once crypto's benchmark is "self", a LEGACY
pooled crypto spec (a universe of 2+ assets, or the BTCUSD / ETHUSD assets)
still `proposed` / `screened`, in `gauntlet`, or waiting in the crypto sibling queue would
make the gauntlet worker raise every 30 minutes. It lists each one and exits 1,
or prints `preflight: no legacy pooled crypto spec in flight` and exits 0. It
reads the chain once and never writes anything.

`chain-note` chains one note file VERBATIM as `Registry.append("note",
{"text": <file text>})`, behind guards. It refuses (exit 2, nothing written)
when the note holds a CR byte, its first line does not start with --prefix,
--prefix does not end with ":", a note on the chain already starts with
--prefix, or logs/chain.lock exists (held by a live process, or left by a dead
one: this tool never breaks a lock). Otherwise it takes ChainLock for the
append, releases it, runs verify_registry.py on the registry it was given, and
exits 0 only if that says VALID (exit 1 otherwise, with the note ALREADY
chained, which is said loudly). --dry-run runs every guard and writes nothing.

Exit codes: 0 ok, 1 preflight found something / post-append verify not VALID,
2 refused.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

LAYER_ROOT = Path(__file__).resolve().parent.parent
if str(LAYER_ROOT) not in sys.path:
    sys.path.insert(0, str(LAYER_ROOT))

from pipeline import loop_state                                # noqa: E402
from pipeline.chainlock import ChainLock, ChainLockHeld        # noqa: E402
from pipeline.registry import Registry                         # noqa: E402

LEGACY_CRYPTO_ASSETS = ("BTCUSD", "ETHUSD")
# "screened" is beyond the brief's proposed/gauntlet: a screened pooled spec is
# on its way to `gauntlet` and would raise the worker there just the same.
IN_FLIGHT_STATES = ("proposed", "screened", "gauntlet")


def is_legacy_pooled(spec: dict) -> bool:
    """A crypto spec (asset_class absent means crypto) over 2+ assets, or over
    one of the legacy BTCUSD / ETHUSD assets."""
    uni = spec.get("universe") or {}
    if uni.get("asset_class", "crypto") != "crypto":
        return False
    assets = list(uni.get("assets") or [])
    return len(assets) != 1 or any(a in LEGACY_CRYPTO_ASSETS for a in assets)


def pooled_in_flight(registry_path: Path, loop_state_path: Path) -> list[dict]:
    """Every legacy pooled crypto spec still proposed / in gauntlet on the
    chain, plus every crypto sibling_queue entry that is one. One snapshot
    read of the chain; the queue is read through pipeline.loop_state on an
    in-memory copy that is never saved. Never writes."""
    specs: dict[str, dict] = {}

    def on_entry(entry: dict) -> None:
        if entry["entry_type"] == "strategy_registered":
            specs[entry["payload"]["strategy_id"]] = entry["payload"]

    snap = Registry(registry_path).snapshot(on_entry=on_entry)
    found: list[dict] = []
    for sid, state in snap.states.items():
        if state in IN_FLIGHT_STATES and is_legacy_pooled(specs[sid]):
            found.append({"where": state, "strategy_id": sid,
                          "assets": list((specs[sid].get("universe") or {})
                                         .get("assets") or [])})
    state_doc = loop_state.load(loop_state_path)
    queued = loop_state.dequeue_specs(
        state_doc, "crypto", loop_state.queue_depth(state_doc, "crypto"))
    for spec in queued:
        if is_legacy_pooled(spec):
            found.append({"where": "sibling_queue",
                          "strategy_id": spec.get("strategy_id"),
                          "assets": list((spec.get("universe") or {})
                                         .get("assets") or [])})
    return found


def preflight(registry_path: Path, loop_state_path: Path) -> int:
    for what, path in (("registry", registry_path),
                       ("loop state", loop_state_path)):
        if not Path(path).exists():
            print(f"preflight: refusing, {what} {path} does not exist "
                  f"(a wrong path would read as 'clear')", file=sys.stderr)
            return 2
    found = pooled_in_flight(registry_path, loop_state_path)
    if not found:
        print("preflight: no legacy pooled crypto spec in flight")
        return 0
    print(f"preflight: {len(found)} legacy pooled crypto spec(s) in flight "
          f"-- drain before the switch merges:")
    for f in found:
        print(f"  {f['where']:<13} {f['strategy_id']}  assets={f['assets']}")
    return 1


def _refuse(msg: str) -> int:
    print(f"chain-note: REFUSED, nothing written: {msg}", file=sys.stderr)
    return 2


def _verify(registry_path: Path) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, str(LAYER_ROOT / "verify_registry.py"),
         str(registry_path)],
        capture_output=True, text=True, cwd=str(LAYER_ROOT),
        encoding="utf-8", errors="replace")
    lines = (r.stdout + r.stderr).splitlines()
    verdict = next((ln.strip() for ln in lines if "REGISTRY " in ln),
                   "(no verdict line)")
    ok = r.returncode == 0 and "REGISTRY VALID" in verdict
    return (0 if ok else 1), verdict


def chain_note(registry_path: Path, note_path: Path, prefix: str, *,
               dry_run: bool) -> int:
    registry_path, note_path = Path(registry_path), Path(note_path)
    if not prefix.endswith(":"):
        return _refuse(f"prefix {prefix!r} must end with ':'")
    try:
        raw = note_path.read_bytes()
    except OSError as exc:
        return _refuse(f"cannot read {note_path}: {exc}")
    if b"\r" in raw:
        return _refuse(f"{note_path} contains a CR (\\r) byte; the note must "
                       f"be LF-only")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _refuse(f"{note_path} is not valid UTF-8: {exc}")
    if not text.split("\n", 1)[0].startswith(prefix):
        return _refuse(f"the first line of {note_path} does not start with "
                       f"{prefix!r}")
    reg = Registry(registry_path)
    for entry in reg.entries():
        if entry["entry_type"] == "note" and str(
                (entry.get("payload") or {}).get("text", "")).startswith(prefix):
            return _refuse(f"a note starting with {prefix!r} is already "
                           f"chained (prev_entry_hash "
                           f"{str(entry.get('prev_entry_hash'))[:12]}...)")
    lock = ChainLock(registry_path.parent / "logs", "grid_switch", "chain note")
    if lock.path.exists():
        alive = lock.holder_alive()
        return _refuse(
            f"{lock.path} exists, held by {lock.info()} "
            + ("(holder is alive)" if alive else
               "(holder is NOT alive: a stale lock; this tool never breaks "
               "locks, clear it by hand)"))
    if dry_run:
        print(f"chain-note: dry-run OK, would chain {len(raw)} bytes "
              f"({text.count(chr(10))} lines) from {note_path} to "
              f"{registry_path}; nothing written")
        return 0
    try:
        lock.acquire()
    except ChainLockHeld as exc:
        return _refuse(str(exc))
    try:
        entry = reg.append("note", {"text": text})
    finally:
        lock.release()
    print(f"chain-note: CHAINED {len(raw)} bytes as a note "
          f"(prev_entry_hash {str(entry.get('prev_entry_hash'))[:12]}...)")
    rc, verdict = _verify(registry_path)
    print(f"verify: {verdict}")
    if rc != 0:
        print("chain-note: THE NOTE IS ALREADY CHAINED (append-only) but the "
              "chain does NOT verify VALID. Investigate before anything else "
              "writes.", file=sys.stderr)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grid_switch", description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight", help="list legacy pooled crypto specs in flight")
    p.add_argument("--registry", required=True, type=Path)
    p.add_argument("--loop-state", required=True, type=Path)
    c = sub.add_parser("chain-note", help="chain a note file verbatim, guarded")
    c.add_argument("--registry", required=True, type=Path)
    c.add_argument("--note", required=True, type=Path)
    c.add_argument("--prefix", required=True)
    c.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "preflight":
        return preflight(args.registry, args.loop_state)
    return chain_note(args.registry, args.note, args.prefix,
                      dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
