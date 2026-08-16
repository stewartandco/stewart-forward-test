"""Status artifact for the Intelligence pipeline, per AGENT_STATUS_CONVENTION.

The escalation shortlist is deliberately four items. Pushing on every state
change trains Coen to ignore the channel, so the bar is: would waiting until
the morning digest cost something? A broken chain, an exhausted budget, a
quarantine day that never got recorded, and an aborted run all say yes.
Everything else - a finished run, a passing gauntlet, a new registration -
waits for the digest.
"""
from __future__ import annotations

from datetime import datetime, timezone

AGENT = "pipeline"
DOMAIN = "intelligence"
CONTRACT_VERSION = "1.0"

PUSH_TRIGGERS = ("chain_invalid", "budget_cap", "quarantine_gap", "run_aborted")

_SEVERITY = {"OK": 0, "SKIP": 0, "WARN": 1, "FAIL": 2}


def worst_of(statuses) -> str:
    """Most severe status; an UNREGISTERED status ranks as WARN, not OK, so a
    new stage status nobody added here surfaces instead of reading healthy."""
    worst, rank = "OK", 0
    for s in statuses:
        r = _SEVERITY.get(s, _SEVERITY["WARN"])
        if r > rank:
            worst, rank = s, r
    return worst


def build(stage_results: dict, spent: float,
          escalations: list[str] | None = None) -> dict:
    escalations = list(escalations or [])
    overall = worst_of(stage_results.values())
    return {
        "agent": AGENT,
        "domain": DOMAIN,
        "contract_version": CONTRACT_VERSION,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "summary": (f"{len(stage_results)} stage(s), overall {overall}; "
                    f"spend USD {spent:.2f}"),
        "items": dict(stage_results),
        "next_run": None,
        "spent_usd": spent,
        "escalations": escalations,
        "push": any(e in PUSH_TRIGGERS for e in escalations),
    }
