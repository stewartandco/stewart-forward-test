"""Per-cycle deadline shared by the loop and its chain-writing stages.

Phase 3 step 1 (2026-09-03). A stage that receives --deadline-utc stops
BEFORE starting work it cannot finish by then, and leaves the remainder in a
state the next cycle resumes from. It never abandons work mid-flight: the
unit of decision is a chunk it is about to submit, judged against a rate it
has measured on the chunks already done (a conservative prior before the
first one).

Why this exists: on 2026-09-01 the 21:30 loop cycle was hard-killed by Task
Scheduler at exactly the PT4H ExecutionTimeLimit. Everything it had done was
discarded, its composer spend was not, and a hard kill leaves no terminator
in any log. TRIAGE_LIMIT bounds triage only; the gauntlet was ~150 of that
cycle's 237 minutes and had no bound at all.

The clock is injectable so tests are exact rather than sleepy. The ISO
deadline is converted to a monotonic offset once at construction, so a wall
clock adjustment mid-cycle cannot move the deadline.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

RESULT_SUFFIX = "_result.json"


def _parse_iso(s: str) -> datetime:
    """Aware datetime from an ISO-8601 string; a trailing Z means UTC."""
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _wall_now_utc() -> datetime:
    """Split out so tests can pin the wall clock without touching time.time."""
    return datetime.now(timezone.utc)


class DeadlineBudget:
    """Answers one question, repeatedly: can I start this many more items?

    Inactive (deadline None) means every answer is yes and no rate is kept --
    a stage without --deadline-utc behaves exactly as it did before this
    module existed.
    """

    def __init__(self, deadline_utc: str | None, *, reserve_s: float = 0.0,
                 clock=time.monotonic):
        self._clock = clock
        self.reserve_s = float(reserve_s)
        self.deadline_utc = deadline_utc
        self._done_n = 0
        self._done_s = 0.0
        if deadline_utc is None:
            self._deadline_mono: float | None = None
        else:
            ahead_s = (_parse_iso(deadline_utc) - _wall_now_utc()).total_seconds()
            self._deadline_mono = clock() + ahead_s

    @property
    def active(self) -> bool:
        return self._deadline_mono is not None

    def remaining_s(self) -> float | None:
        if self._deadline_mono is None:
            return None
        return self._deadline_mono - self._clock()

    def rate_s(self, prior_s: float) -> float:
        """Measured seconds per item, or the prior before anything ran."""
        if self._done_n <= 0:
            return float(prior_s)
        return self._done_s / self._done_n

    def fits(self, n: int, rate_s: float) -> bool:
        """True if n more items at rate_s each, plus the reserve, end before
        the deadline. Always True when inactive."""
        rem = self.remaining_s()
        if rem is None:
            return True
        return n * float(rate_s) + self.reserve_s <= rem

    def record(self, n: int, elapsed_s: float) -> None:
        if n > 0:
            self._done_n += int(n)
            self._done_s += float(elapsed_s)


def result_path(registry_path: Path, stage: str) -> Path:
    """logs/<stage>_result.json beside the registry -- the same place and
    convention as triage_result.json, which the loop already reads."""
    return Path(registry_path).resolve().parent / "logs" / f"{stage}{RESULT_SUFFIX}"


def write_result(registry_path: Path, stage: str, *, evaluated: int, deferred: int,
                 deadline_utc: str | None, stopped_at_deadline: bool) -> Path:
    """Atomic, written on every completed run including the no-deadline
    case, so an absent file means 'the stage did not report', never
    'nothing was deferred'."""
    p = result_path(registry_path, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"stage": stage, "evaluated": int(evaluated),
                               "deferred": int(deferred),
                               "deadline_utc": deadline_utc,
                               "stopped_at_deadline": bool(stopped_at_deadline)},
                              indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return p


def read_result(registry_path: Path, stage: str) -> dict | None:
    """The stage's report, or None if absent or unreadable."""
    try:
        d = json.loads(result_path(registry_path, stage).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("evaluated"), int):
        return None
    return d


def chunks(items: list, size: int) -> list[list]:
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]
