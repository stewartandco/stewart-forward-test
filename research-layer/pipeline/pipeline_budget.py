"""The Intelligence pipeline's budget line.

ONE limit: the CAP (pipeline/budget.py PIPELINE_CAP_USD -- USD 200/month since
D41) is a hard stop. Nothing spends past it. The 2026-08-15 scanner runaway -
billing errors retried forever, 105,565 logged decisions in two hours - is why
this is a stop and not an alert, and it is why the cap was raised rather than
removed: a continuously-running agent is unbounded spend by definition, and the
allocator in pipeline/allowance.py sizes every cycle's triage batch FROM this
number, so deleting it would leave the loop with no batch size at all.

REMOVED D41 (2026-09-18, Coen): the 80% BATCH STOP. It refused to START new work
once spend passed 80% of the cap, which meant a month's last fifth of budget was
never usable and good candidates were skipped for weeks at a time. Work now runs
right up to the hard cap. The in-flight half of that rule is unaffected: a batch
already running still finishes, because the gates are checked before a batch
starts, never mid-batch.

Metering itself lives in pipeline/budget.py and is not reimplemented here.

Note the screen and gauntlet make NO LLM calls - they are pure local compute -
so this line covers Composer generation and any future metered stage only.
"""
from __future__ import annotations

from .budget import PIPELINE_CAP_USD as MONTHLY_USD   # ONE constant (D41: 200); a second copy of this number sat here at 20 and would have disagreed


def may_spend(spent: float) -> bool:
    """False at or past the hard cap."""
    return spent < MONTHLY_USD


def may_start_batch(spent: float) -> bool:
    """False at or past the hard cap.

    Kept as a named call site so the INTENT stays legible: this is the
    proactive "may new work begin" question, asked before a batch starts.
    Since D41 it is the same line as may_spend -- there is no longer a
    separate, earlier threshold -- but collapsing it into may_spend at every
    call site would erase the distinction between "can this cycle start" and
    "did the thing that just failed fail because of budget", which loop.py
    relies on in two different places.
    """
    return may_spend(spent)


def state(spent: float) -> str:
    if not may_spend(spent):
        return "CAP"
    return "OK"
