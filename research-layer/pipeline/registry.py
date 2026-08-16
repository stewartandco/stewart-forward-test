"""Append-only, hash-chained registry writer for the research layer.

Every write re-walks nothing: the writer keeps the running head hash by reading
the last line of the log. Verification is verify_registry.py's job.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from datetime import datetime, timezone

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


def _validated_digest_map(value: object, field: str) -> dict[str, str]:
    """A non-empty {asset: sha256} map.

    The digest is checked for 64 LOWERCASE HEX characters, not merely for
    length: this value is what an auditor compares against their own
    `sha256sum`, so anything that is not a digest is a permanent,
    un-amendable lie about provenance.
    """
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a non-empty {{asset: hex}} map")
    for asset, hexd in value.items():
        # a non-string key would be silently stringified by json.dumps, so
        # 'BTCUSD' and any other spelling of it must be rejected here
        if not isinstance(asset, str) or not asset:
            raise ValueError(f"{field} asset keys must be non-empty strings")
        if (not isinstance(hexd, str) or len(hexd) != 64
                or set(hexd) - HEX_DIGITS):
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
        entry = {
            "version": 1,
            "ts_utc": ts_utc or _now_utc(),
            "entry_type": entry_type,
            "prev_entry_hash": self._head_hash(),
            "payload": payload,
        }
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
        frm = states[strategy_id]
        if to not in VALID_TRANSITIONS.get(frm, set()):
            raise ValueError(f"illegal transition {frm!r} -> {to!r}")
        return self.append("state_change", {
            "strategy_id": strategy_id, "from": frm, "to": to,
            "reason": reason, "buried_at": frm if to == "graveyard" else None,
        }, ts_utc=ts_utc)

    def record_verdict(self, strategy_id: str, stage: str, verdict: str,
                       metrics: dict, artifacts_hash: str) -> dict:
        if strategy_id not in self.strategy_states():
            raise ValueError(f"unknown strategy {strategy_id!r}")
        return self.append("verdict", {
            "strategy_id": strategy_id, "stage": stage, "verdict": verdict,
            "metrics": metrics, "artifacts_hash": artifacts_hash,
        })

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
