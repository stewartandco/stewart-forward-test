"""
verify_registry.py — chain integrity + invariant walker for registry_log.jsonl.

Mirrors the root verify.py hash-chain walk, then checks research-layer
invariants:

  1. chain integrity (prev_entry_hash links, genesis = 64 zeros)
  2. verdicts / state_changes reference a previously registered strategy_id
  3. strategies cite >= 1 previously registered card_id
  4. strategy_registered payloads carry no results fields
  5. lifecycle transitions follow the state machine

Usage:
    python verify_registry.py [path/to/registry_log.jsonl]
"""
from __future__ import annotations

import sys
import json
import hashlib
import argparse
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GENESIS_HASH = "0" * 64

FORBIDDEN_RESULT_KEYS = {"net_pnl", "sharpe", "win_rate", "results", "backtest",
                         "pnl", "equity_curve", "max_dd"}

VALID_TRANSITIONS = {
    "proposed":   {"screened", "graveyard"},
    "screened":   {"gauntlet", "graveyard"},
    "gauntlet":   {"quarantine", "graveyard"},
    "quarantine": {"live", "graveyard"},
    "live":       {"retired", "graveyard"},
}
TERMINAL_STATES = {"retired", "graveyard"}


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical_json(entry).encode("utf-8")).hexdigest()


def verify(log_path: Path) -> int:
    if not log_path.exists():
        print(f"ERROR: log file not found: {log_path}")
        return 2

    prev_hash = GENESIS_HASH
    n_ok = n_bad = 0
    cards: set[str] = set()
    strategies: set[str] = set()
    state: dict[str, str] = {}
    by_type: dict[str, int] = {}

    def fail(lineno: int, msg: str) -> None:
        nonlocal n_bad
        print(f"  line {lineno}: {msg}")
        n_bad += 1

    with log_path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                fail(lineno, f"PARSE ERROR — {exc}")
                continue

            ok = True
            if entry.get("prev_entry_hash") != prev_hash:
                fail(lineno, "BROKEN CHAIN")
                ok = False
            prev_hash = _entry_hash(entry)

            etype = entry.get("entry_type", "?")
            by_type[etype] = by_type.get(etype, 0) + 1
            payload = entry.get("payload", {})

            if etype == "card_registered":
                cid = payload.get("card_id")
                if not cid:
                    fail(lineno, "card_registered with no card_id"); ok = False
                else:
                    cards.add(cid)

            elif etype == "strategy_registered":
                sid = payload.get("strategy_id")
                if not sid:
                    fail(lineno, "strategy_registered with no strategy_id"); ok = False
                else:
                    strategies.add(sid)
                    state[sid] = "proposed"
                cited = set(payload.get("provenance", {}).get("card_ids", []))
                if not cited:
                    fail(lineno, f"strategy {sid}: cites no research cards"); ok = False
                elif not cited <= cards:
                    fail(lineno, f"strategy {sid}: cites unregistered cards {sorted(cited - cards)}"); ok = False
                leaked = FORBIDDEN_RESULT_KEYS & set(payload.keys())
                if leaked:
                    fail(lineno, f"strategy {sid}: results fields in spec {sorted(leaked)}"); ok = False

            elif etype in ("verdict", "state_change"):
                sid = payload.get("strategy_id")
                if sid not in strategies:
                    fail(lineno, f"{etype} for unregistered strategy {sid!r}"); ok = False
                if etype == "state_change" and sid in strategies:
                    frm, to = payload.get("from"), payload.get("to")
                    cur = state.get(sid)
                    if cur in TERMINAL_STATES:
                        fail(lineno, f"strategy {sid}: transition out of terminal state {cur!r}"); ok = False
                    elif frm != cur:
                        fail(lineno, f"strategy {sid}: 'from' is {frm!r} but recorded state is {cur!r}"); ok = False
                    elif to not in VALID_TRANSITIONS.get(frm, set()):
                        fail(lineno, f"strategy {sid}: illegal transition {frm!r} -> {to!r}"); ok = False
                    else:
                        state[sid] = to

            if ok:
                n_ok += 1

    total = n_ok + n_bad
    print()
    print(f"  Entries           : {total}")
    print(f"  By type           : "
          + ", ".join(f"{t}={n}" for t, n in sorted(by_type.items())))
    print(f"  Cards registered  : {len(cards)}")
    print(f"  Strategies        : {len(strategies)}")
    if strategies:
        funnel: dict[str, int] = {}
        for s in state.values():
            funnel[s] = funnel.get(s, 0) + 1
        print(f"  Funnel            : "
              + ", ".join(f"{s}={n}" for s, n in sorted(funnel.items())))
    print()
    if n_bad == 0 and total > 0:
        print(f"  REGISTRY VALID — all {total} entries link and satisfy invariants.")
        return 0
    elif total == 0:
        print("  Empty log.")
        return 1
    else:
        print(f"  REGISTRY INVALID — {n_bad}/{total} entries fail.")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify research-layer registry log")
    ap.add_argument("path", nargs="?",
                    default="registry_log.jsonl",
                    help="Path to registry_log.jsonl")
    args = ap.parse_args()
    log_path = Path(args.path)
    print(f"Verifying registry at {log_path}")
    print("=" * 70)
    return verify(log_path)


if __name__ == "__main__":
    sys.exit(main())
