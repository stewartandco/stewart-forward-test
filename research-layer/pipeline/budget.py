"""Budget metering for the scanner: per-call token ledger, monthly USD totals,
80% alert, hard cap (D23: USD 25/month; at cap extraction stops, polling
continues).

Prices are Anthropic sticker rates per MTok (2026-08). Sonnet 5 runs an intro
discount through 2026-08-31, so sticker metering slightly OVERSTATES spend -
conservative by design for a hard cap.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

# USD per 1M tokens: input, output, cache read (0.1x in), cache write (1.25x in)
PRICES = {
    "claude-sonnet-5": {"input": 3.00, "output": 15.00,
                        "cache_read": 0.30, "cache_write": 3.75},
    "claude-opus-5":   {"input": 5.00, "output": 25.00,
                        "cache_read": 0.50, "cache_write": 6.25},
}


def usd_for_usage(model: str, input_tokens: int = 0, output_tokens: int = 0,
                  cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    p = PRICES[model]  # unknown model: fail loudly, never meter at a guess
    return (input_tokens * p["input"] + output_tokens * p["output"]
            + cache_read_tokens * p["cache_read"]
            + cache_write_tokens * p["cache_write"]) / 1_000_000


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# D36 / D33: which agent a ledger row belongs to.
#
# Every row written before 2026-08-18 predates the `agent` field. Coen chose to
# DERIVE attribution from `purpose` rather than split the ledger, because
# splitting a spend record retroactively is the same shape as re-judging
# history, which this project refuses. The mapping is read out of data already
# in each row; nothing is invented.
#
# A purpose that is neither agent's work (the 2026-08-17 honesty-guard
# investigation) resolves to `unattributed`: it stays visible in the total and
# is charged to no cap. Only HISTORY can land there -- record_call requires an
# agent, so a new row cannot escape a cap by omission.
PURPOSE_AGENT = {
    "screen": "reader", "extract": "reader", "scout": "reader",
    "inbox_extract": "reader",
    "triage": "pipeline", "composer": "pipeline",
}
UNATTRIBUTED = "unattributed"

# D33 gave the pipeline agent USD 20 of the D28 Intelligence band; D39
# (2026-09-03) re-split the same band on measured burn to Reader 20 / pipeline 40, and D36 made
# it enforceable by scoping a meter to that agent's own attributed rows. It
# lives HERE rather than in one of the two agents that spend against it,
# because two copies of a number that must agree will eventually disagree.
PIPELINE_CAP_USD = 40.0     # D39 (2026-09-03): Intelligence band re-split, Reader 20 / pipeline 40


def agent_of(row: dict) -> str:
    """The agent a row belongs to: its own field, else derived from purpose."""
    return row.get("agent") or PURPOSE_AGENT.get(row.get("purpose"), UNATTRIBUTED)


class BudgetMeter:
    def __init__(self, ledger_path: str | Path, monthly_cap_usd: float = 20.0,     # Reader's line per D39 (was 35 under D33)
                 warn_frac: float = 0.8, agent: str | None = None):
        """`agent` scopes this meter's cap to one agent's spend.

        Unscoped (None) means "every row", which is the old behaviour and is
        what a reporting caller wants. A RUNNING agent should always scope
        itself, or it stops on someone else's bill -- the failure D33's split
        exists to prevent."""
        self.agent = agent
        self.ledger_path = Path(ledger_path)
        self.monthly_cap_usd = monthly_cap_usd
        self.warn_frac = warn_frac
        self._rows: list[dict] = []
        if self.ledger_path.exists():
            with self.ledger_path.open("r", encoding="utf-8") as f:
                self._rows = [json.loads(l) for l in f if l.strip()]

    def record_call(self, model: str, usage, purpose: str, *, agent: str,
                    ts_utc: str | None = None, extra_usd: float = 0.0) -> float:
        """Append one ledger row from an API response's usage object.
        extra_usd covers non-token charges (e.g. server-side web searches)."""
        row = {
            "ts_utc": ts_utc or _now_utc(),
            "model": model,
            "purpose": purpose,
            "agent": agent,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        row["usd"] = extra_usd + usd_for_usage(
            model, row["input_tokens"], row["output_tokens"],
            row["cache_read_tokens"], row["cache_write_tokens"])
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self._rows.append(row)
        return row["usd"]

    def month_spend(self, month: str | None = None,
                    agent: str | None = None) -> float:
        """Spend for a month, optionally scoped to one agent.

        An explicit `agent` argument wins; otherwise this meter's own scope
        applies. Passing neither totals every row, so the parts always
        reconcile against the whole."""
        month = month or _now_utc()[:7]
        scope = agent if agent is not None else self.agent
        return sum(r["usd"] for r in self._rows
                   if r["ts_utc"][:7] == month
                   and (scope is None or agent_of(r) == scope))

    def state(self) -> str:
        spend = self.month_spend()
        if spend >= self.monthly_cap_usd:
            return "CAP"
        if spend >= self.warn_frac * self.monthly_cap_usd:
            return "WARN"
        return "OK"

    def can_spend(self) -> bool:
        return self.state() != "CAP"
