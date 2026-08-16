"""
verify_registry.py — chain integrity + invariant walker for registry_log.jsonl.

Mirrors the root verify.py hash-chain walk, then checks research-layer
invariants:

  1. chain integrity (prev_entry_hash links, genesis = 64 zeros)
  2. verdicts / state_changes reference a previously registered strategy_id
  3. strategies cite >= 1 card_id previously registered AND accepted
  4. strategy_registered payloads carry no results fields
  5. lifecycle transitions follow the state machine
  6. strategy blocks reference previously registered block types
  7. quarantine_decision entries reference a strategy CURRENTLY in quarantine,
     and (strategy_id, date, asset) is unique
  8. no two strategy_registered entries share a composition_fingerprint —
     rule 7 (a buried composition never returns) verified from the chain
     itself, not merely trusted to the composer's in-process guard
  9. quarantine_data_snapshot dates are unique, and every quarantine_decision
     is covered by an EARLIER snapshot for its date naming its asset in both
     data_sha256 and bars_sha256 — so no forward record exists without the
     provenance of the bars behind it

Usage:
    python verify_registry.py [path/to/registry_log.jsonl]
"""
from __future__ import annotations

import sys
import json
import hashlib
import argparse
from pathlib import Path

# Invariant 8 needs the SAME fingerprint the composer computes; a second
# implementation here would be two hashes that must stay byte-identical
# forever. pipeline/__init__.py is empty and composer.py keeps jsonschema and
# anthropic function-local, so this pulls in no third-party dependency. The
# explicit sys.path entry keeps the script runnable from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.composer import composition_fingerprint          # noqa: E402

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

# A snapshot hashes each price file two ways; a decision is only provenanced
# if its asset is named in BOTH. See pipeline/quarantine.py for why the
# whole-file hash cannot be the one the runner guards on.
SNAPSHOT_DIGEST_KEYS = ("data_sha256", "bars_sha256")


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
    # entries walked, and entries with at least one problem. NOT a count of
    # problems: an entry can trip several invariants at once (a decision with
    # no snapshot AND a duplicate key), and counting those separately used to
    # report more failures than the log has lines.
    n_entries = n_bad = 0
    cards: set[str] = set()
    accepted: set[str] = set()
    block_types: set[tuple[str, str]] = set()
    strategies: set[str] = set()
    state: dict[str, str] = {}
    by_type: dict[str, int] = {}
    quarantine_seen: set[tuple] = set()
    fingerprints: dict[str, str] = {}       # composition -> first strategy_id
    snapshots: dict[str, set] = {}          # date -> assets fully provenanced

    def fail(lineno: int, msg: str) -> None:
        print(f"  line {lineno}: {msg}")

    with log_path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            n_entries += 1
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                fail(lineno, f"PARSE ERROR — {exc}")
                n_bad += 1
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

            elif etype == "card_reviewed":
                if payload.get("status") == "accepted":
                    accepted.add(payload.get("card_id"))
                else:
                    accepted.discard(payload.get("card_id"))

            elif etype == "block_type_registered":
                role, btype = payload.get("role"), payload.get("type")
                if not role or not btype:
                    fail(lineno, "block_type_registered missing role/type"); ok = False
                else:
                    block_types.add((role, btype))

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
                not_accepted = sorted(cited & cards - accepted)
                if cited <= cards and not_accepted:
                    fail(lineno, f"strategy {sid}: cites cards not accepted {not_accepted}"); ok = False
                for b in payload.get("blocks", []):
                    if (b.get("role"), b.get("type")) not in block_types:
                        fail(lineno, f"strategy {sid}: unregistered block type "
                                     f"{b.get('role')}/{b.get('type')}"); ok = False
                leaked = FORBIDDEN_RESULT_KEYS & set(payload.keys())
                if leaked:
                    fail(lineno, f"strategy {sid}: results fields in spec {sorted(leaked)}"); ok = False
                # Wrapped because the verifier must report a malformed payload
                # as malformed rather than crashing the whole walk — the chain
                # is append-only and a single bad entry must not make the
                # remaining thousands unverifiable.
                try:
                    fp = composition_fingerprint(payload)
                except Exception as exc:            # malformed spec, not a dupe
                    fail(lineno, f"strategy {sid}: cannot fingerprint ({exc})")
                    ok = False
                else:
                    if fp in fingerprints:
                        fail(lineno, f"strategy {sid}: duplicate composition "
                                     f"already registered as {fingerprints[fp]}")
                        ok = False
                    else:
                        fingerprints[fp] = sid

            elif etype == "quarantine_data_snapshot":
                d = payload.get("date")
                named = []
                for field in SNAPSHOT_DIGEST_KEYS:
                    m = payload.get(field)
                    if not isinstance(m, dict):
                        fail(lineno, f"quarantine_data_snapshot {field} is "
                                     f"not an {{asset: sha256}} map")
                        ok = False
                        m = {}
                    named.append(set(m))
                if named[0] != named[1]:
                    fail(lineno, f"quarantine_data_snapshot names different "
                                 f"assets in {SNAPSHOT_DIGEST_KEYS[0]} and "
                                 f"{SNAPSHOT_DIGEST_KEYS[1]}")
                    ok = False
                # coverage is the INTERSECTION: an asset hashed only one way
                # is not fully provenanced, so it must not license a decision
                covered = named[0] & named[1]
                if not isinstance(d, str):
                    fail(lineno, f"quarantine_data_snapshot date {d!r} is not "
                                 f"a string")
                    ok = False
                elif d in snapshots:
                    fail(lineno, f"duplicate quarantine_data_snapshot for {d}")
                    ok = False
                else:
                    snapshots[d] = covered

            elif etype == "quarantine_decision":
                sid = payload.get("strategy_id")
                date, asset = payload.get("date"), payload.get("asset")
                # The key goes into a set and the coverage lookup into a dict,
                # so a non-string field here would raise out of the walk and
                # leave every LATER entry unverified -- the worst failure mode
                # an append-only public chain has. Report and move on instead.
                if not all(isinstance(v, str) for v in (sid, date, asset)):
                    fail(lineno, f"quarantine_decision strategy_id/date/asset "
                                 f"must be strings, got "
                                 f"{type(sid).__name__}/{type(date).__name__}/"
                                 f"{type(asset).__name__}")
                    ok = False
                else:
                    if sid not in strategies:
                        fail(lineno, f"quarantine_decision for unregistered "
                                     f"strategy {sid!r}"); ok = False
                    elif state.get(sid) != "quarantine":
                        fail(lineno, f"quarantine_decision for strategy {sid} "
                                     f"in state {state.get(sid)!r}, not "
                                     f"'quarantine'")
                        ok = False
                    key = (sid, date, asset)
                    if key in quarantine_seen:
                        fail(lineno, f"duplicate quarantine_decision for {key}")
                        ok = False
                    else:
                        quarantine_seen.add(key)
                    # Because the walk is in chain order, `snapshots` holds
                    # only entries that appeared EARLIER, which is what makes
                    # "the provenance was recorded before the decision"
                    # checkable rather than assumed.
                    if asset not in snapshots.get(date, set()):
                        fail(lineno, f"quarantine_decision {key}: no earlier "
                                     f"quarantine_data_snapshot covers this "
                                     f"date/asset")
                        ok = False

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

            if not ok:
                n_bad += 1

    print()
    print(f"  Entries           : {n_entries}")
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
    if n_bad == 0 and n_entries > 0:
        print(f"  REGISTRY VALID — all {n_entries} entries link and satisfy invariants.")
        return 0
    elif n_entries == 0:
        print("  Empty log.")
        return 1
    else:
        print(f"  REGISTRY INVALID — {n_bad}/{n_entries} entries fail.")
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
