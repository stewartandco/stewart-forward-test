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


class BudgetMeter:
    def __init__(self, ledger_path: str | Path, monthly_cap_usd: float = 50.0,
                 warn_frac: float = 0.8):
        self.ledger_path = Path(ledger_path)
        self.monthly_cap_usd = monthly_cap_usd
        self.warn_frac = warn_frac
        self._rows: list[dict] = []
        if self.ledger_path.exists():
            with self.ledger_path.open("r", encoding="utf-8") as f:
                self._rows = [json.loads(l) for l in f if l.strip()]

    def record_call(self, model: str, usage, purpose: str,
                    ts_utc: str | None = None, extra_usd: float = 0.0) -> float:
        """Append one ledger row from an API response's usage object.
        extra_usd covers non-token charges (e.g. server-side web searches)."""
        row = {
            "ts_utc": ts_utc or _now_utc(),
            "model": model,
            "purpose": purpose,
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

    def month_spend(self, month: str | None = None) -> float:
        month = month or _now_utc()[:7]
        return sum(r["usd"] for r in self._rows if r["ts_utc"][:7] == month)

    def state(self) -> str:
        spend = self.month_spend()
        if spend >= self.monthly_cap_usd:
            return "CAP"
        if spend >= self.warn_frac * self.monthly_cap_usd:
            return "WARN"
        return "OK"

    def can_spend(self) -> bool:
        return self.state() != "CAP"
