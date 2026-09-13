"""Scanner observability artifacts per stewartandco-agents/AGENT_STATUS_CONVENTION.md:
logs/status.json (atomic), the daily digest, and the hash-chained action log.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from datetime import datetime, timezone

from .common import GENESIS_HASH, entry_hash
from .registry import Registry

AGENT = "reader"
DOMAIN = "intelligence"
# Mirrors the Reader's CONTRACT.md in stewartandco-agents, which is the source
# of truth. Bump this in the SAME change as the contract: a version recorded
# only in the contract has not shipped, and this field is what the dashboard
# reads to say which rules the agent is running under.
# 1.8 = D27 case 3 (2026-08-24). Runtime had reported 1.6 since D36 (contract
# 1.7, 2026-08-18) without a bump: a ratified contract that the runtime does
# not report has not shipped.
CONTRACT_VERSION = "1.8"


class ActionLog:
    """Hash-chained append-only scanner event log (same chain format as the
    registry, so tampering is detectable the same way)."""

    def __init__(self, path: str | Path):
        self._registry = Registry(path)
        self.path = Path(path)

    def event(self, event_type: str, payload: dict) -> dict:
        return self._registry.append(event_type, payload)


def verify_chain(path: str | Path) -> bool:
    prev = GENESIS_HASH
    p = Path(path)
    if not p.exists():
        return False
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return False
            if entry.get("prev_entry_hash") != prev:
                return False
            prev = entry_hash(entry)
    return True


def write_status(path: str | Path, *, overall: str, summary: str, items: dict,
                 pending_tier3: int, digest_file: str | None,
                 next_run: str | None = None,
                 contract_version: str = CONTRACT_VERSION) -> None:
    status = {
        "agent": AGENT,
        "domain": DOMAIN,
        "contract_version": contract_version,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "summary": summary,
        "items": items,
        "digest_file": digest_file,
        "next_run": next_run,
        "pending_tier3": pending_tier3,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def write_digest(dir_path: str | Path, *, date: str, new_by_source: dict,
                 rejections: dict, discoveries: list, paywalled: list,
                 spend_usd: float, cards_registered: int,
                 budget_state: str = "OK", probation: dict | None = None) -> str:
    name = f"digest_{date}.txt"
    lines = [f"Reader scanner digest — {date}", "=" * 40, ""]
    lines.append("New items by source:")
    if new_by_source:
        for src, n in sorted(new_by_source.items()):
            lines.append(f"  {src}: {n}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("Screen rejections (reason: count):")
    if rejections:
        for reason, n in sorted(rejections.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {reason}: {n}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Cards registered by scanner (cumulative): {cards_registered}")
    lines.append("")
    if probation is not None:
        lines.append(
            "Source probation (D27 case 3): "
            f"on probation {probation['on_probation']} | "
            f"admitted {probation['admitted']} | "
            f"promoted {probation['promoted']} | "
            f"revoked {probation['revoked']} | "
            f"timed out {probation['timed_out']} | "
            f"blocked {probation['blocked']}")
        lines.append("")
    lines.append("Off-list sources queued today (Tier 3; admitted by D27 rules, never by default):")
    if discoveries:
        lines.extend(f"  {u}" for u in discoveries)
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("Paywalled items flagged (retrieve legitimately if wanted):")
    if paywalled:
        lines.extend(f"  {u}" for u in paywalled)
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Month-to-date spend: USD {spend_usd:.2f}")
    if budget_state != "OK":
        lines.append(f"BUDGET ALERT: {budget_state} — spend at or past 80% of "
                     "the monthly cap; extraction pauses at the cap, polling continues.")
    p = Path(dir_path) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return name
