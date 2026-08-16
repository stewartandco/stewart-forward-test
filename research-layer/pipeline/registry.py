"""Append-only, hash-chained registry writer for the research layer.

Every write re-walks nothing: the writer keeps the running head hash by reading
the last line of the log. Verification is verify_registry.py's job.
"""
from __future__ import annotations

import json
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

# exact payload shape of a quarantine_decision entry (one paper-trading day
# per asset); verify_registry.py enforces the same set on the chain
QUARANTINE_DECISION_KEYS = ("strategy_id", "date", "asset", "action", "price",
                            "position_frac", "equity")


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
        """One paper-trading decision. Guarded so a strategy that is not in
        quarantine can never accrue a forward record — the same invariant
        verify_registry.py enforces on the chain."""
        missing = [k for k in QUARANTINE_DECISION_KEYS if k not in payload]
        if missing:
            raise ValueError(f"quarantine decision missing {missing}")
        extra = sorted(set(payload) - set(QUARANTINE_DECISION_KEYS))
        if extra:
            raise ValueError(f"quarantine decision has unknown keys {extra}")
        sid = payload["strategy_id"]
        if self.strategy_states().get(sid) != "quarantine":
            raise ValueError(f"strategy {sid!r} is not in quarantine")
        return self.append("quarantine_decision", dict(payload))
