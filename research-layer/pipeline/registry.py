"""Append-only, hash-chained registry writer for the research layer.

Every write re-walks nothing: the writer keeps the running head hash by reading
the last line of the log. Verification is verify_registry.py's job.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Callable, Mapping

from .common import GENESIS_HASH, canonical_json, entry_hash
from .lock import FileLock

VALID_TRANSITIONS = {
    "proposed":   {"screened", "graveyard"},
    "screened":   {"gauntlet", "graveyard"},
    "gauntlet":   {"quarantine", "graveyard"},
    "quarantine": {"live", "graveyard"},
    "live":       {"retired", "graveyard"},
}

# Exact payload shape of a quarantine_decision entry (one paper-trading day
# per asset), and the closed action vocabulary, which is defined nowhere else
# in the codebase.
#
# These are belt-and-braces, not the only enforcement: verify_registry.py
# invariant 7 re-checks the state and the (strategy_id, date, asset) key from
# the chain itself, so a row that got past this writer is still caught by
# anyone walking the log.
QUARANTINE_DECISION_KEYS = ("strategy_id", "date", "asset", "action", "price",
                            "position_frac", "equity")
QUARANTINE_ACTIONS = frozenset({"enter_long", "enter_short", "exit", "hold"})

# The price data a day's decisions were computed from, hashed two ways:
# `data_sha256` is the whole file (what screen.py and gauntlet.py record for
# the same CSVs) and `bars_sha256` covers only the bars up to and including
# that date. The runner compares bars_sha256, never data_sha256, because
# appending a later bar changes the file while leaving the earlier date's bars
# identical -- guarding on the file hash would refuse every backfill.
# verify_registry.py invariant 9 re-checks uniqueness on `date` and that every
# decision has an EARLIER snapshot naming its asset in both maps.
QUARANTINE_SNAPSHOT_KEYS = ("date", "data_sha256", "bars_sha256")
QUARANTINE_SNAPSHOT_DIGEST_KEYS = ("data_sha256", "bars_sha256")
HEX_DIGITS = frozenset("0123456789abcdef")


class DuplicateQuarantineDecision(ValueError):
    """That (strategy_id, date, asset) is already on the chain.

    A ValueError subclass, so callers that catch ValueError keep working, but
    distinguishable on purpose: a routine race (a scheduler retry overlapping
    the daily job) can be absorbed as a no-op, while a malformed payload --
    every other ValueError from this writer -- must stay fatal.
    """


class DuplicateQuarantineSnapshot(ValueError):
    """A quarantine_data_snapshot for that date is already chained.

    Same shape and same reason as DuplicateQuarantineDecision, but the stakes
    are higher: two processes that both read "no snapshot for this date yet"
    would chain two, which invariant 9 rejects -- and the chain is append-only,
    so that failure could never be repaired. The check therefore runs under the
    same lock as the append, and `.chained` carries the payload that won so the
    caller can compare hashes rather than guess.
    """


class OverlappingQuarantineSupplement(ValueError):
    """A quarantine_data_snapshot_supplement names an asset the date already
    covers (via the base snapshot or a prior supplement).

    Introduced by the 2026-08-27 per-class-calendars addendum. Same contract
    as DuplicateQuarantineSnapshot: checked under the append lock because a
    conflicting double-coverage on an append-only chain could never be
    repaired, and `.chained` carries the date's merged coverage
    ({"data_sha256": ..., "bars_sha256": ...}) so a losing concurrent writer
    can compare hashes and reconcile instead of guessing.
    """


def parse_iso_date(value: object, field: str = "date") -> datetime:
    """Strict YYYY-MM-DD.

    strptime alone is NOT strict enough: it accepts '2023-1-22' for
    '%Y-%m-%d', so the round trip is what actually pins the format.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a YYYY-MM-DD string, got {type(value).__name__}")
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field} {value!r} is not a valid date: {exc}") from exc
    if dt.strftime("%Y-%m-%d") != value:
        raise ValueError(f"{field} {value!r} is not zero-padded YYYY-MM-DD")
    return dt


def validated_quarantine_decision(payload: dict) -> dict:
    """The payload shape guard, split out so it can be exercised alone.

    Values matter as much as keys here: canonical_json serializes with
    default=str and allow_nan, so a stray object would be silently stringified
    and a NaN would be written as bare `NaN`, which is not valid strict JSON.
    """
    missing = [k for k in QUARANTINE_DECISION_KEYS if k not in payload]
    if missing:
        raise ValueError(f"quarantine decision missing {missing}")
    extra = sorted(set(payload) - set(QUARANTINE_DECISION_KEYS))
    if extra:
        raise ValueError(f"quarantine decision has unknown keys {extra}")
    for k in ("strategy_id", "asset"):
        if not isinstance(payload[k], str) or not payload[k]:
            raise ValueError(f"quarantine decision {k} must be a non-empty string")
    # isinstance first: an unhashable action would make `in` raise TypeError
    # out of a validator whose whole contract is to raise ValueError
    if (not isinstance(payload["action"], str)
            or payload["action"] not in QUARANTINE_ACTIONS):
        raise ValueError(
            f"quarantine decision action {payload['action']!r} is not one of "
            f"{sorted(QUARANTINE_ACTIONS)}")
    parse_iso_date(payload["date"], "quarantine decision date")
    for k in ("price", "position_frac", "equity"):
        v = payload[k]
        # bool is an int subclass, so it would otherwise pass as a number
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(
                f"quarantine decision {k} must be a number, got "
                f"{type(v).__name__}")
        if not math.isfinite(v):
            raise ValueError(f"quarantine decision {k} must be finite, got {v!r}")
    return dict(payload)


def is_sha256_hex(value: object) -> bool:
    """64 LOWERCASE HEX characters, checked as a digest and not merely for
    length: this value is what an auditor compares against their own
    `sha256sum`, so anything that is not a digest is a permanent,
    un-amendable lie about provenance.

    Public because verify_registry.py applies the SAME test when it walks the
    chain -- a verifier that accepted digests this writer rejects would leave
    an outsider unable to tell a real provenance record from a fabricated one,
    which is the whole job of the entry type. One implementation, so the two
    cannot drift.
    """
    return (isinstance(value, str) and len(value) == 64
            and not set(value) - HEX_DIGITS)


def _validated_digest_map(value: object, field: str) -> dict[str, str]:
    """A non-empty {asset: sha256} map."""
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a non-empty {{asset: hex}} map")
    for asset, hexd in value.items():
        # a non-string key would be silently stringified by json.dumps, so
        # 'BTCUSD' and any other spelling of it must be rejected here
        if not isinstance(asset, str) or not asset:
            raise ValueError(f"{field} asset keys must be non-empty strings")
        if not is_sha256_hex(hexd):
            raise ValueError(
                f"{field} {asset}: sha256 must be 64 lowercase hex chars")
    return dict(value)


def validated_quarantine_snapshot(payload: dict) -> dict:
    """The shape guard for a data snapshot, split out so it can be exercised
    alone, exactly like validated_quarantine_decision."""
    missing = [k for k in QUARANTINE_SNAPSHOT_KEYS if k not in payload]
    if missing:
        raise ValueError(f"quarantine snapshot missing {missing}")
    extra = sorted(set(payload) - set(QUARANTINE_SNAPSHOT_KEYS))
    if extra:
        raise ValueError(f"quarantine snapshot has unknown keys {extra}")
    parse_iso_date(payload["date"], "quarantine snapshot date")
    maps = {k: _validated_digest_map(payload[k], k)
            for k in QUARANTINE_SNAPSHOT_DIGEST_KEYS}
    # the two maps describe the SAME set of price files two ways; if they
    # disagree on which assets exist, neither can be trusted as provenance
    if set(maps["data_sha256"]) != set(maps["bars_sha256"]):
        raise ValueError(
            "data_sha256 and bars_sha256 must name the same assets, got "
            f"{sorted(maps['data_sha256'])} and {sorted(maps['bars_sha256'])}")
    return {"date": payload["date"], **maps}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- O(tail) chain views (Build 2a; the gauntlet worker's chain.lock hold) --
#
# Every typed writer below re-reads the whole log (strategy_states + the head
# hash) per append. On a 47 MB / 68k-entry chain that is ~3.6 s per gauntlet
# verdict, held under chain.lock, which starves the loop, the quarantine daily
# and the scanner (each probes chain.lock once and defers). A ChainSnapshot is
# ONE full read, taken outside the lock; advance() then parses only the bytes
# appended since, and record_gauntlet_outcome() appends the verdict and its
# state change in one shot against the snapshot's head.

class ChainMoved(RuntimeError):
    """The log is not where the snapshot left it: truncated, rewritten, a
    tail that does not link, or (at a write) grown since the snapshot. The
    caller must write nothing and re-read."""


@dataclass(frozen=True)
class ChainSnapshot:
    """The chain as of `byte_len` bytes: lifecycle states, the strategies
    holding any gauntlet-stage verdict, and the head entry's hash plus the
    byte offset where that entry's line starts (so advance() can re-hash it
    and notice a rewritten head)."""
    states: Mapping[str, str]
    gauntlet_judged: frozenset
    head_hash: str
    byte_len: int
    head_offset: int = 0


def _fold(states: dict, judged: set, entry: dict) -> None:
    """Apply one entry to a snapshot's state; the same rule as
    Registry.strategy_states()."""
    et, p = entry["entry_type"], entry["payload"]
    if et == "strategy_registered":
        states[p["strategy_id"]] = "proposed"
    elif et == "state_change":
        states[p["strategy_id"]] = p["to"]
    elif et == "verdict" and p.get("stage") == "gauntlet":
        judged.add(p["strategy_id"])


def _line_bytes(entry: dict) -> bytes:
    """Exactly the bytes _append_locked's text-mode write produces for an
    entry: canonical_json escapes every newline inside a value, so the only
    raw newline is the terminator, which text mode writes as os.linesep."""
    return (canonical_json(entry).encode("utf-8")
            + os.linesep.encode("ascii"))


def _make_entry(entry_type: str, payload: dict, prev_hash: str,
                ts_utc: str | None = None) -> dict:
    return {
        "version": 1,
        "ts_utc": ts_utc or _now_utc(),
        "entry_type": entry_type,
        "prev_entry_hash": prev_hash,
        "payload": payload,
    }


def _verdict_payload(strategy_id: str, stage: str, verdict: str,
                     metrics: dict, artifacts_hash: str) -> dict:
    return {"strategy_id": strategy_id, "stage": stage, "verdict": verdict,
            "metrics": metrics, "artifacts_hash": artifacts_hash}


def _state_change_payload(strategy_id: str, frm: str, to: str,
                          reason: str | None) -> dict:
    if to not in VALID_TRANSITIONS.get(frm, set()):
        raise ValueError(f"illegal transition {frm!r} -> {to!r}")
    return {"strategy_id": strategy_id, "from": frm, "to": to,
            "reason": reason, "buried_at": frm if to == "graveyard" else None}


def _complete_lines(f, start: int, size: int):
    """(line_start, raw_line) for every COMPLETE, non-blank line of the
    open binary file `f` from offset `start` up to `size` bytes. A line
    that crosses `size` or lacks its newline (a writer caught mid-append)
    is not yielded. The generator RETURNS the offset just past the last
    complete line (callers read it from StopIteration.value)."""
    pos = start
    for raw in f:
        if pos + len(raw) > size or not raw.endswith(b"\n"):
            break
        line_start, pos = pos, pos + len(raw)
        if raw.strip():
            yield line_start, raw
    return pos


class Registry:
    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path)

    # -- chain mechanics ---------------------------------------------------

    def _head_hash(self) -> str:
        if not self.log_path.exists():
            return GENESIS_HASH
        last = None
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return GENESIS_HASH
        return entry_hash(json.loads(last))

    def append(self, entry_type: str, payload: dict, ts_utc: str | None = None) -> dict:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Cross-process lock spans the head-read AND the write: two writers
        # that read the same head would fork the chain (bit twice 2026-08-14).
        with FileLock(self.log_path):
            return self._append_locked(entry_type, payload, ts_utc)

    def _append_locked(self, entry_type: str, payload: dict,
                       ts_utc: str | None = None) -> dict:
        """The append body, WITHOUT taking the lock.

        The caller must already hold FileLock(self.log_path). FileLock is not
        reentrant — a nested acquire blocks until it times out — so a writer
        that needs to read-then-append atomically (see
        record_quarantine_decision) must take the lock once and come through
        here, never through append().
        """
        entry = _make_entry(entry_type, payload, self._head_hash(), ts_utc)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(canonical_json(entry) + "\n")
        return entry

    # -- reads -------------------------------------------------------------

    def entries(self):
        if not self.log_path.exists():
            return
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def cards(self, status: str | None = None) -> dict[str, dict]:
        """card_id -> card, with review status folded in from card_reviewed entries."""
        cards: dict[str, dict] = {}
        for e in self.entries():
            if e["entry_type"] == "card_registered":
                cards[e["payload"]["card_id"]] = e["payload"]
            elif e["entry_type"] == "card_reviewed":
                cid = e["payload"]["card_id"]
                if cid in cards:
                    cards[cid]["review"]["status"] = e["payload"]["status"]
                    cards[cid]["review"]["reject_reason"] = e["payload"].get("reject_reason")
        if status is not None:
            cards = {k: v for k, v in cards.items() if v["review"]["status"] == status}
        return cards

    def strategy_states(self) -> dict[str, str]:
        state: dict[str, str] = {}
        for e in self.entries():
            if e["entry_type"] == "strategy_registered":
                state[e["payload"]["strategy_id"]] = "proposed"
            elif e["entry_type"] == "state_change":
                state[e["payload"]["strategy_id"]] = e["payload"]["to"]
        return state

    def block_types(self) -> set[tuple[str, str]]:
        """(role, type) pairs registered via block_type_registered entries."""
        out: set[tuple[str, str]] = set()
        for e in self.entries():
            if e["entry_type"] == "block_type_registered":
                out.add((e["payload"]["role"], e["payload"]["type"]))
        return out

    # -- typed writers -----------------------------------------------------

    def register_card(self, card: dict) -> dict:
        if card.get("review", {}).get("status") != "pending":
            raise ValueError("cards are registered with review.status='pending'")
        return self.append("card_registered", card)

    def review_card(self, card_id: str, status: str, reviewed_by: str,
                    reject_reason: str | None = None) -> dict:
        if status not in ("accepted", "rejected"):
            raise ValueError("status must be accepted|rejected")
        if status == "rejected" and reject_reason not in (
                "off_topic", "quote_not_found", "claim_not_supported", "duplicate"):
            raise ValueError("rejected cards need a valid reject_reason")
        if card_id not in self.cards():
            raise ValueError(f"unknown card {card_id!r}")
        return self.append("card_reviewed", {
            "card_id": card_id, "status": status,
            "reject_reason": reject_reason, "reviewed_by": reviewed_by,
        })

    def register_block_type(self, payload: dict) -> dict:
        for k in ("role", "type", "params_schema"):
            if k not in payload:
                raise ValueError(f"block type payload missing {k!r}")
        role, btype = payload["role"], payload["type"]
        for e in self.entries():
            if (e["entry_type"] == "block_type_registered"
                    and e["payload"]["role"] == role
                    and e["payload"]["type"] == btype
                    and e["payload"]["params_schema"] != payload["params_schema"]):
                raise ValueError(
                    f"block type {role}/{btype} already registered with a conflicting params_schema")
        return self.append("block_type_registered", payload)

    def register_strategy(self, spec: dict) -> dict:
        accepted = self.cards(status="accepted")
        cited = spec.get("provenance", {}).get("card_ids", [])
        if not cited:
            raise ValueError("strategy must cite at least one research card")
        missing = [c for c in cited if c not in accepted]
        if missing:
            raise ValueError(f"cited cards not registered+accepted: {missing}")
        registered_blocks = self.block_types()
        for b in spec.get("blocks", []):
            if (b["role"], b["type"]) not in registered_blocks:
                raise ValueError(f"block type {b['role']}/{b['type']} not registered")
        return self.append("strategy_registered", spec)

    def record_state_change(self, strategy_id: str, to: str,
                            reason: str | None = None,
                            ts_utc: str | None = None) -> dict:
        states = self.strategy_states()
        if strategy_id not in states:
            raise ValueError(f"unknown strategy {strategy_id!r}")
        return self.append("state_change", _state_change_payload(
            strategy_id, states[strategy_id], to, reason), ts_utc=ts_utc)

    def record_verdict(self, strategy_id: str, stage: str, verdict: str,
                       metrics: dict, artifacts_hash: str) -> dict:
        if strategy_id not in self.strategy_states():
            raise ValueError(f"unknown strategy {strategy_id!r}")
        return self.append("verdict", _verdict_payload(
            strategy_id, stage, verdict, metrics, artifacts_hash))

    # -- O(tail) snapshot path (see ChainSnapshot) -------------------------

    def snapshot(self, on_entry: Callable[[dict], None] | None = None
                 ) -> ChainSnapshot:
        """ONE full read. Only COMPLETE lines within the size seen at open
        count, so a concurrent append (or a writer caught mid-line) can
        never tear it. `on_entry` sees every counted entry in chain order,
        so a caller can build its own view from this same single read."""
        states: dict[str, str] = {}
        judged: set[str] = set()
        last, head_off = None, 0
        try:
            f = self.log_path.open("rb")
        except FileNotFoundError:
            return ChainSnapshot(MappingProxyType({}), frozenset(), GENESIS_HASH, 0, 0)
        with f:
            size = os.fstat(f.fileno()).st_size
            lines = _complete_lines(f, 0, size)
            while True:
                try:
                    head_off, raw = next(lines)
                except StopIteration as stop:
                    pos = stop.value
                    break
                last = json.loads(raw)
                _fold(states, judged, last)
                if on_entry is not None:
                    on_entry(last)
        head = entry_hash(last) if last is not None else GENESIS_HASH
        return ChainSnapshot(MappingProxyType(states), frozenset(judged), head,
                             pos, head_off)

    def advance(self, snap: ChainSnapshot) -> ChainSnapshot:
        """`snap` moved forward over whatever was appended since, parsing
        only that tail (plus the head line, re-hashed). Raises ChainMoved on
        a truncated or rewritten log or a tail that does not link."""
        try:
            f = self.log_path.open("rb")
        except FileNotFoundError:
            if snap.byte_len == 0:
                return snap
            raise ChainMoved(f"{self.log_path} is gone") from None
        states: dict[str, str] | None = None
        judged: set[str] | None = None
        head, head_off = snap.head_hash, snap.head_offset
        with f:
            size = os.fstat(f.fileno()).st_size
            if size < snap.byte_len:
                raise ChainMoved(f"log is {size} bytes, snapshot saw {snap.byte_len}")
            if snap.byte_len:
                f.seek(snap.head_offset)
                line = f.read(snap.byte_len - snap.head_offset)
                try:
                    same = (line.endswith(b"\n")
                            and entry_hash(json.loads(line)) == snap.head_hash)
                except ValueError:
                    same = False
                if not same:
                    raise ChainMoved("the snapshot's head entry was rewritten")
            lines = _complete_lines(f, snap.byte_len, size)
            while True:
                try:
                    start, raw = next(lines)
                except StopIteration as stop:
                    pos = stop.value
                    break
                entry = json.loads(raw)
                if entry.get("prev_entry_hash") != head:
                    raise ChainMoved(
                        f"entry at byte {start} links to "
                        f"{str(entry.get('prev_entry_hash'))[:12]}, head is {head[:12]}")
                if states is None:
                    states, judged = dict(snap.states), set(snap.gauntlet_judged)
                _fold(states, judged, entry)
                head, head_off = entry_hash(entry), start
        if states is None:
            if pos == snap.byte_len:
                return snap
            # only blank lines were appended: same state, longer file
            return ChainSnapshot(snap.states, snap.gauntlet_judged, head, pos, head_off)
        return ChainSnapshot(MappingProxyType(states), frozenset(judged), head,
                             pos, head_off)

    def _append_at(self, snap: ChainSnapshot,
                   items: list[tuple[str, dict]]) -> ChainSnapshot:
        """Append `items` chained onto snap's head, under the append
        FileLock, refusing (ChainMoved, nothing written) if the log is not
        exactly snap.byte_len bytes. Returns the advanced snapshot."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.log_path):
            size = self.log_path.stat().st_size if self.log_path.exists() else 0
            if size != snap.byte_len:
                raise ChainMoved(f"log is {size} bytes, snapshot saw "
                                 f"{snap.byte_len}: appended without chain.lock?")
            states, judged = dict(snap.states), set(snap.gauntlet_judged)
            head, pos, head_off = snap.head_hash, snap.byte_len, snap.head_offset
            out = b""
            for entry_type, payload in items:
                entry = _make_entry(entry_type, payload, head)
                line = _line_bytes(entry)
                head_off, pos = pos, pos + len(line)
                head = entry_hash(entry)
                out += line
                _fold(states, judged, entry)
            with self.log_path.open("ab") as f:
                f.write(out)
        return ChainSnapshot(MappingProxyType(states), frozenset(judged), head,
                             pos, head_off)

    def record_gauntlet_outcome(self, snap: ChainSnapshot, strategy_id: str,
                                verdict: str, metrics: dict, artifacts_hash: str,
                                to: str, reason: str | None) -> ChainSnapshot:
        """A gauntlet verdict and its state change, appended together against
        `snap` (which the caller has just advance()d under chain.lock): the
        same two entries, byte for byte, that record_verdict +
        record_state_change write, without either one's full-chain read."""
        if snap.states.get(strategy_id) != "gauntlet":
            raise ValueError(f"{strategy_id!r} is in state "
                             f"{snap.states.get(strategy_id)!r}, not 'gauntlet'")
        if strategy_id in snap.gauntlet_judged:
            raise ValueError(f"{strategy_id!r} already has a gauntlet verdict")
        sc = _state_change_payload(strategy_id, "gauntlet", to, reason)
        return self._append_at(snap, [
            ("verdict", _verdict_payload(strategy_id, "gauntlet", verdict,
                                         metrics, artifacts_hash)),
            ("state_change", sc)])

    def record_state_change_at(self, snap: ChainSnapshot, strategy_id: str,
                               to: str, reason: str | None) -> ChainSnapshot:
        """record_state_change against a snapshot (O(tail); same bytes)."""
        if strategy_id not in snap.states:
            raise ValueError(f"unknown strategy {strategy_id!r}")
        return self._append_at(snap, [("state_change", _state_change_payload(
            strategy_id, snap.states[strategy_id], to, reason))])

    def record_gauntlet_stats(self, strategy_id: str, verdict_entry_hash: str,
                              stats: dict) -> dict:
        """protocol-v6.1: the registry-wide recorded statistics for ONE
        gauntlet verdict, chained after it. Records only; never a state."""
        if strategy_id not in self.strategy_states():
            raise ValueError(f"unknown strategy {strategy_id!r}")
        return self.append("gauntlet_stats", {
            "strategy_id": strategy_id,
            "verdict_entry_hash": verdict_entry_hash, **stats})

    def record_quarantine_decision(self, payload: dict) -> dict:
        """One paper-trading decision, validated and de-duplicated atomically.

        Two guards, both of which must hold at write time rather than at
        check time:

        * the strategy must be in quarantine, so nothing else can accrue a
          forward record;
        * (strategy_id, date, asset) must not already be chained. A caller's
          in-process `seen` set cannot stop a second process — a scheduler
          retry overlapping the daily job — from chaining the same day twice,
          and the chain would still verify as valid.

        Both therefore run inside the same lock as the append. FileLock is not
        reentrant, so the body uses _append_locked.
        """
        row = validated_quarantine_decision(payload)
        key = (row["strategy_id"], row["date"], row["asset"])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.log_path):
            if self.strategy_states().get(row["strategy_id"]) != "quarantine":
                raise ValueError(
                    f"strategy {row['strategy_id']!r} is not in quarantine")
            for e in self.entries():
                if e["entry_type"] != "quarantine_decision":
                    continue
                p = e["payload"]
                if (p["strategy_id"], p["date"], p["asset"]) == key:
                    dup = DuplicateQuarantineDecision(
                        "quarantine decision already chained for "
                        f"{key[0]} {key[1]} {key[2]}")
                    # Carry the chained payload so a caller can tell a benign
                    # race (identical row, recomputed by a second process)
                    # from a genuine conflict (same key, DIFFERENT numbers —
                    # which means the data or the spec moved underneath us).
                    dup.chained = p
                    raise dup
            return self._append_locked("quarantine_decision", row)

    def record_quarantine_snapshot(self, payload: dict) -> dict:
        """The price files a day's decisions were computed from.

        Chained once per date, before that date's decision rows, so an auditor
        can tell whether a reproduction used the same bars. The runner
        recomputes each strategy's whole book from the first bar every day, so
        a re-fetch or vendor restatement would otherwise silently change what a
        reproduction yields for every historical day.

        Uniqueness on `date` is checked inside the lock, for the reason spelled
        out on DuplicateQuarantineSnapshot: unlike a duplicate decision, a
        duplicate snapshot would leave the public chain permanently invalid.
        """
        row = validated_quarantine_snapshot(payload)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.log_path):
            for e in self.entries():
                if e["entry_type"] != "quarantine_data_snapshot":
                    continue
                if e["payload"]["date"] == row["date"]:
                    dup = DuplicateQuarantineSnapshot(
                        "quarantine data snapshot already chained for "
                        f"{row['date']}")
                    dup.chained = e["payload"]
                    raise dup
            return self._append_locked("quarantine_data_snapshot", row)

    def record_quarantine_snapshot_supplement(self, payload: dict) -> dict:
        """Provenance for assets that became recordable AFTER the date's base
        snapshot was chained -- the backfill of a class whose source publishes
        late (2026-08-27 per-class-calendars addendum).

        Same payload shape and shape guard as the base snapshot. Rules,
        checked under the append lock for the reason spelled out on
        OverlappingQuarantineSupplement:
          * an EARLIER base snapshot for the date must exist -- a supplement
            with nothing to supplement is a fabricated provenance root;
          * the supplement's assets must be disjoint from everything the date
            already covers (base plus prior supplements).
        """
        row = validated_quarantine_snapshot(payload)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.log_path):
            base = None
            covered = {"data_sha256": {}, "bars_sha256": {}}
            for e in self.entries():
                if e["entry_type"] not in ("quarantine_data_snapshot",
                                           "quarantine_data_snapshot_supplement"):
                    continue
                p = e["payload"]
                if p.get("date") != row["date"]:
                    continue
                if e["entry_type"] == "quarantine_data_snapshot":
                    if base is not None:
                        continue          # defective duplicate: first wins
                    base = p
                for field in QUARANTINE_SNAPSHOT_DIGEST_KEYS:
                    m = p.get(field)
                    if isinstance(m, dict):
                        for a, h in m.items():
                            covered[field].setdefault(a, h)
            if base is None:
                raise ValueError(
                    f"no quarantine_data_snapshot for {row['date']}: a "
                    f"supplement extends a date's provenance, it cannot "
                    f"start it")
            overlap = sorted(set(row["bars_sha256"])
                             & set(covered["bars_sha256"]))
            if overlap:
                clash = OverlappingQuarantineSupplement(
                    f"assets already covered for {row['date']}: "
                    f"{overlap} -- the chain is append-only, so conflicting "
                    f"double-coverage could never be repaired")
                clash.chained = covered
                raise clash
            return self._append_locked(
                "quarantine_data_snapshot_supplement", row)


def edge_numbers(entries):
    """D11 (SP5, docs/2026-08-28-market-data-universe-design.md s7b): the
    chain's append-only order defines a stable sequential number per
    strategy; renumbering is impossible by construction. Display-layer
    only - never part of identity, provenance, or N accounting."""
    numbers = {}
    n = 0
    for entry in entries:
        if entry.get("entry_type") == "strategy_registered":
            n += 1
            numbers[entry["payload"]["strategy_id"]] = n
    return numbers


def edge_label(n):
    """Render an edge number as a display label, e.g. 7 -> '#0007'."""
    return f"#{n:04d}"
