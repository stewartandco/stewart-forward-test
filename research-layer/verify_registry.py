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
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:                    # idempotent under re-import
    sys.path.insert(0, _HERE)
from pipeline.composer import composition_fingerprint          # noqa: E402
from pipeline.registry import (is_sha256_hex,                  # noqa: E402
                               QUARANTINE_SNAPSHOT_DIGEST_KEYS)

# Invariant 8 recomputes fingerprints for FROZEN entries against the LIVE
# block grammar, so it is fair to ask whether a future grammar edit could
# retroactively flip a sound chain to INVALID. It cannot:
#
#   * composition_fingerprint snaps a param to a grid value only where
#     `g == v`, i.e. it substitutes an EQUAL value. Snapping can therefore
#     normalize representation (2 vs 2.0) but can never merge two values that
#     differ, which is the only way it could manufacture a duplicate.
#   * Manufacturing one would additionally require an existing type's grid to
#     gain a value. composer.preflight_block_types aborts the run when
#     blocks.py mutates a chained params_schema, and Registry.register_block_type
#     refuses to re-register a type with a conflicting schema. The grammar is
#     additive-only by construction.
#   * preflight compares against BLOCK_TYPES, so it catches a CHANGED chained
#     type but not a DELETED one. Deletion turns snapping OFF, which can only
#     split fingerprints apart, never merge them, so it cannot produce a false
#     duplicate either.
#
# Checked against the live chain 2026-08-16: 56 registered strategies stay 56
# distinct fingerprints under the live grammar, under a wholly EMPTY grammar
# (worst-case deletion), and under a numeric-type-insensitive comparison
# (worst-case snapping). No grid holds two entries that are `==` but distinct.

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
    n_entries = 0
    # The LINES that carry at least one problem, not a count of problems: an
    # entry can trip several invariants at once (a decision with no snapshot
    # AND a duplicate key), and counting those separately reported more
    # failures than the log has lines. Recording the line inside fail() is
    # what makes "prints a problem but exits VALID" unrepresentable -- there
    # is no second flag a future call site could forget to set.
    bad_lines: set[int] = set()
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
        bad_lines.add(lineno)

    def as_key(lineno: int, value: object, what: str) -> str | None:
        """A payload field that becomes a set element or a dict key must be a
        non-empty string, or it raises TypeError out of the walk -- the same
        one-bad-entry-kills-every-later-entry failure the shape guards above
        exist to prevent. Returns None when the caller must skip it."""
        if isinstance(value, str) and value:
            return value
        fail(lineno, f"{what} must be a non-empty string, got {value!r}")
        return None

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
                continue

            # Shape first, and ONCE, so no branch below dereferences something
            # that is not a mapping. A hand-appended entry whose payload is a
            # string used to raise AttributeError out of the walk and leave
            # every LATER entry unverified -- the worst failure mode an
            # append-only public chain has.
            if not isinstance(entry, dict):
                fail(lineno, f"entry is not a JSON object, got "
                             f"{type(entry).__name__}")
                # still advance the head so the REST of the chain stays
                # checkable rather than cascading into false BROKEN CHAINs
                prev_hash = _entry_hash(entry)
                continue

            if entry.get("prev_entry_hash") != prev_hash:
                fail(lineno, "BROKEN CHAIN")
            prev_hash = _entry_hash(entry)

            etype = entry.get("entry_type", "?")
            if not isinstance(etype, str):
                etype = repr(etype)
            by_type[etype] = by_type.get(etype, 0) + 1
            payload = entry.get("payload", {})
            if not isinstance(payload, dict):
                fail(lineno, f"payload is not a JSON object, got "
                             f"{type(payload).__name__}")
                payload = {}

            if etype == "card_registered":
                cid = as_key(lineno, payload.get("card_id"),
                             "card_registered card_id")
                if cid:
                    cards.add(cid)

            elif etype == "card_reviewed":
                cid = as_key(lineno, payload.get("card_id"),
                             "card_reviewed card_id")
                if cid:
                    if payload.get("status") == "accepted":
                        accepted.add(cid)
                    else:
                        accepted.discard(cid)

            elif etype == "block_type_registered":
                role = as_key(lineno, payload.get("role"),
                              "block_type_registered role")
                btype = as_key(lineno, payload.get("type"),
                               "block_type_registered type")
                if role and btype:
                    block_types.add((role, btype))

            elif etype == "strategy_registered":
                sid = as_key(lineno, payload.get("strategy_id"),
                             "strategy_registered strategy_id")
                if sid:
                    strategies.add(sid)
                    state[sid] = "proposed"
                prov = payload.get("provenance", {})
                cards_cited = prov.get("card_ids") if isinstance(prov, dict) else None
                if not isinstance(prov, dict):
                    fail(lineno, f"strategy {sid}: provenance is not a JSON "
                                 f"object, got {type(prov).__name__}")
                elif not isinstance(cards_cited, list) or not all(
                        isinstance(c, str) for c in cards_cited):
                    # set() of a non-iterable, or of an unhashable item, would
                    # raise out of the walk; report and skip the citation
                    # checks rather than adding a misleading second reason
                    fail(lineno, f"strategy {sid}: provenance.card_ids must be "
                                 f"a list of strings")
                else:
                    cited = set(cards_cited)
                    if not cited:
                        fail(lineno, f"strategy {sid}: cites no research cards")
                    elif not cited <= cards:
                        fail(lineno, f"strategy {sid}: cites unregistered cards {sorted(cited - cards)}")
                    not_accepted = sorted(cited & cards - accepted)
                    if cited <= cards and not_accepted:
                        fail(lineno, f"strategy {sid}: cites cards not accepted {not_accepted}")
                blocks = payload.get("blocks", [])
                if not isinstance(blocks, list):
                    fail(lineno, f"strategy {sid}: blocks is not a list, got "
                                 f"{type(blocks).__name__}")
                    blocks = []
                for b in blocks:
                    # a non-mapping block, or an unhashable role/type, would
                    # raise on the membership test below
                    if not isinstance(b, dict) or not all(
                            isinstance(b.get(k), str) for k in ("role", "type")):
                        fail(lineno, f"strategy {sid}: block is not an object "
                                     f"with string role/type ({b!r})")
                        continue
                    if (b["role"], b["type"]) not in block_types:
                        fail(lineno, f"strategy {sid}: unregistered block type "
                                     f"{b['role']}/{b['type']}")
                leaked = FORBIDDEN_RESULT_KEYS & set(payload.keys())
                if leaked:
                    fail(lineno, f"strategy {sid}: results fields in spec {sorted(leaked)}")
                # Wrapped because the verifier must report a malformed payload
                # as malformed rather than crashing the whole walk — the chain
                # is append-only and a single bad entry must not make the
                # remaining thousands unverifiable.
                try:
                    fp = composition_fingerprint(payload)
                except Exception as exc:            # malformed spec, not a dupe
                    fail(lineno, f"strategy {sid}: cannot fingerprint ({exc})")
                else:
                    if fp in fingerprints:
                        fail(lineno, f"strategy {sid}: duplicate composition "
                                     f"already registered as {fingerprints[fp]}")
                    elif sid:
                        # only a NAMED strategy may become the first holder, or
                        # a later duplicate reports "already registered as
                        # None" and the real culprit goes unnamed
                        fingerprints[fp] = sid
                    # an unnamed spec is already reported by as_key above

            elif etype == "quarantine_data_snapshot":
                d = payload.get("date")
                # `named` is what the entry CLAIMS, `valid` is what survives
                # the same digest test the writer applies. Checking the format
                # here is the point of invariant 9: a verifier that accepted
                # digests the writer rejects would leave an outsider unable to
                # tell a real provenance record from a fabricated one.
                named, valid, bad_map = [], [], False
                for field in QUARANTINE_SNAPSHOT_DIGEST_KEYS:
                    m = payload.get(field)
                    if not isinstance(m, dict):
                        fail(lineno, f"quarantine_data_snapshot {field} is "
                                     f"not an {{asset: sha256}} map")
                        bad_map = True
                        named.append(set())
                        valid.append(set())
                        continue
                    named.append(set(m))
                    good = {a for a, h in m.items()
                            if isinstance(a, str) and is_sha256_hex(h)}
                    if set(m) - good:
                        fail(lineno, f"quarantine_data_snapshot {field}: not a "
                                     f"sha256 digest for "
                                     f"{sorted(repr(a) for a in set(m) - good)}")
                    valid.append(good)
                # skipped when a map was unusable: it would report a second,
                # misleading reason for the same defect
                if not bad_map and named[0] != named[1]:
                    fail(lineno, f"quarantine_data_snapshot names different "
                                 f"assets in "
                                 f"{QUARANTINE_SNAPSHOT_DIGEST_KEYS[0]} and "
                                 f"{QUARANTINE_SNAPSHOT_DIGEST_KEYS[1]}")
                # coverage is the INTERSECTION of the VALID sets: an asset
                # hashed only one way, or carrying something that is not a
                # digest, is not provenanced and must not license a decision
                covered = valid[0] & valid[1]
                if not isinstance(d, str):
                    # a list date would raise on the dict lookup below
                    fail(lineno, f"quarantine_data_snapshot date {d!r} is not "
                                 f"a string")
                elif d in snapshots:
                    fail(lineno, f"duplicate quarantine_data_snapshot for {d}")
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
                else:
                    if sid not in strategies:
                        fail(lineno, f"quarantine_decision for unregistered "
                                     f"strategy {sid!r}")
                    elif state.get(sid) != "quarantine":
                        fail(lineno, f"quarantine_decision for strategy {sid} "
                                     f"in state {state.get(sid)!r}, not "
                                     f"'quarantine'")
                    key = (sid, date, asset)
                    if key in quarantine_seen:
                        fail(lineno, f"duplicate quarantine_decision for {key}")
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

            elif etype in ("verdict", "state_change"):
                sid = as_key(lineno, payload.get("strategy_id"),
                             f"{etype} strategy_id")
                if sid is not None and sid not in strategies:
                    fail(lineno, f"{etype} for unregistered strategy {sid!r}")
                if etype == "state_change" and sid in strategies:
                    frm, to = payload.get("from"), payload.get("to")
                    cur = state.get(sid)
                    if cur in TERMINAL_STATES:
                        fail(lineno, f"strategy {sid}: transition out of terminal state {cur!r}")
                    elif frm != cur:
                        fail(lineno, f"strategy {sid}: 'from' is {frm!r} but recorded state is {cur!r}")
                    elif to not in VALID_TRANSITIONS.get(frm, set()):
                        fail(lineno, f"strategy {sid}: illegal transition {frm!r} -> {to!r}")
                    else:
                        state[sid] = to

    n_bad = len(bad_lines)
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
