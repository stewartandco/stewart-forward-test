"""D26 approvals consumer: Coen's source decisions from the Morpheus panel.

The dashboard never mutates agent state. It appends HMAC-signed records to
logs/approvals_queue.jsonl; this consumer (called from the scanner loop)
verifies each signature, admits approved sources to the watchlist stamped
from Coen's record, flips the discovery-queue proposal status, chain-logs
everything, and is idempotent via a processed-ids state file.

Record wire format (Morpheus writes the same shape):
    {"id": "<domain>-<ts>", "action": "source_decision", "domain", "url",
     "decision": "approve"|"block", "name", "source_class", "actor": "coen",
     "via": "morpheus-ops", "ts_utc", "sig": HMAC_SHA256(key, canonical
     JSON of the record without "sig")}
"""
from __future__ import annotations

import hmac
import json
import hashlib
from pathlib import Path

from .common import canonical_json
from .watchlist import (load_watchlist, load_discovery, discovery_domain,
                        VALID_CLASSES)
from .scanstatus import ActionLog

DEFAULT_POLL_MINUTES = 360


def sign_record(record: dict, key: str) -> str:
    unsigned = {k: v for k, v in record.items() if k != "sig"}
    return hmac.new(key.encode("utf-8"),
                    canonical_json(unsigned).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _verify(record: dict, key: str) -> bool:
    sig = record.get("sig", "")
    return bool(sig) and hmac.compare_digest(sig, sign_record(record, key))


def _watchlist_entry(record: dict) -> dict:
    source_class = record.get("source_class") or "blog"
    if source_class not in VALID_CLASSES:
        source_class = "blog"
    return {
        "id": record["domain"],
        "class": source_class,
        "name": record.get("name") or record["domain"],
        "url": record["url"],
        "feed": None,
        "poll_minutes": DEFAULT_POLL_MINUTES,
        "added_by": "coen",
        "verified_date": record["ts_utc"][:10],
        "notes": (f"approved via morpheus-ops {record['ts_utc']} (D26). "
                  "feed unset (HTML diff on url); refine feed URL when convenient."),
    }


def _flip_proposal(discovery_path: Path, domain: str, status: str) -> None:
    entries = load_discovery(discovery_path)
    changed = False
    for e in entries:
        if (e.get("domain") or discovery_domain(e["url"])) == domain \
                and e["status"] == "proposed":
            e["status"] = status
            changed = True
    if changed:
        Path(discovery_path).write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
            encoding="utf-8")


def process_approvals(*, queue_path: str | Path, watchlist_path: str | Path,
                      discovery_path: str | Path, actions: ActionLog,
                      state_path: str | Path, key: str) -> dict:
    queue_path, state_path = Path(queue_path), Path(state_path)
    result = {"approved": [], "blocked": 0, "invalid": 0}
    if not queue_path.exists() or not key:
        return result
    processed: set[str] = set()
    if state_path.exists():
        processed = set(json.loads(state_path.read_text(encoding="utf-8")))

    records = [json.loads(l) for l in
               queue_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    fresh = [r for r in records if r.get("id") not in processed]
    if not fresh:
        return result

    watchlist_doc = json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
    known_ids = {s["id"] for s in watchlist_doc["sources"]}
    watchlist_dirty = False

    for record in fresh:
        processed.add(record.get("id", canonical_json(record)))
        if not _verify(record, key) or record.get("action") != "source_decision":
            result["invalid"] += 1
            actions.event("approval_rejected", {
                "id": record.get("id"), "reason": "bad signature or action"})
            continue
        payload = {k: record[k] for k in
                   ("id", "domain", "url", "decision", "actor", "via", "ts_utc")}
        if record["decision"] == "approve":
            if record["domain"] not in known_ids:
                entry = _watchlist_entry(record)
                watchlist_doc["sources"].append(entry)
                known_ids.add(entry["id"])
                watchlist_dirty = True
                result["approved"].append(entry)
            _flip_proposal(Path(discovery_path), record["domain"], "approved")
            actions.event("source_approved", payload)
        elif record["decision"] == "block":
            _flip_proposal(Path(discovery_path), record["domain"], "blocked")
            actions.event("source_blocked", payload)
            result["blocked"] += 1

    if watchlist_dirty:
        Path(watchlist_path).write_text(
            json.dumps(watchlist_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(sorted(processed)), encoding="utf-8")
    return result
