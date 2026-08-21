"""The Intelligence pipeline's budget line.

Two limits, deliberately different in kind:

* CAP (USD 20/month) is a hard stop. Nothing spends past it. The 2026-08-15
  scanner runaway - billing errors retried forever, 105,565 logged decisions in
  two hours - is why this is a stop and not an alert.
* BATCH STOP (80% of cap) refuses to START new work while allowing work in
  flight to finish, so a batch is never left half-done and half-chained. This
  is the pattern D21 pre-approved for the Composer campaign.

Metering itself lives in pipeline/budget.py and is not reimplemented here.

Note the screen and gauntlet make NO LLM calls - they are pure local compute -
so this line covers Composer generation and any future metered stage only.
"""
from __future__ import annotations

MONTHLY_USD = 20.0
BATCH_STOP_FRACTION = 0.80


def may_spend(spent: float) -> bool:
    """False at or past the hard cap."""
    return spent < MONTHLY_USD


def may_start_batch(spent: float) -> bool:
    """False at or past 80% - refuse to begin new work."""
    return spent < MONTHLY_USD * BATCH_STOP_FRACTION


def state(spent: float) -> str:
    if not may_spend(spent):
        return "CAP"
    if not may_start_batch(spent):
        return "BATCH_STOP"
    return "OK"
