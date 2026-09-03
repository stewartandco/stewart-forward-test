"""Per-cycle spend allowance (Phase 3 steps 4-5, 2026-09-03).

TRIAGE_LIMIT was a card count standing in for two things it never measured:
money and clock. The clock half is pipeline/deadline.py. This is the money
half. Before the loop spends, it asks one question -- how much may THIS
cycle cost? -- and derives how many triage cards that buys.

    expected_cycles     = max(CYCLES_FLOOR, cycles completed in the trailing 30 days)
    cycle_allowance     = cap x (1 - RESERVE) / expected_cycles
    triage_count        = clamp(floor((allowance - composer_pair_usd) / usd_per_card), 1, ceiling)

Inputs Coen set (2026-09-03): the cap (D39, budget.PIPELINE_CAP_USD = 40) and
RESERVE = 0.15. Everything else is measured: the cycle history from
loop_state.json, and the two unit costs from the loop's own spend deltas
around triage and the composer pair (Calibration), with the 2026-09-01/02
measurements as priors until the first samples exist. Nothing here is
hand-typed twice.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

CYCLES_FLOOR = 10            # a quiet month must not inflate one cycle's allowance
RESERVE = 0.15               # headroom for the quarantine daily and hand runs (Coen)
TRAILING_DAYS = 30
CALIBRATION_WINDOW = 10      # trailing samples per unit cost
PRIOR_USD_PER_CARD = 0.018          # measured 2026-09-01/02: $0.0059/reviewer call x 3
PRIOR_COMPOSER_PAIR_USD = 0.64      # measured: dry-run + real composer call, per cycle


def _parse(s: str) -> datetime:
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def expected_cycles(state: dict, *, now: datetime | None = None,
                    floor: int = CYCLES_FLOOR, trailing_days: int = TRAILING_DAYS) -> int:
    """Cycles completed in the trailing window (state["cycles"], appended on
    every cycle_complete), never below `floor`."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=trailing_days)
    n = 0
    for c in state.get("cycles", []) or []:
        try:
            if _parse(str(c.get("ts_utc", ""))) >= cutoff:
                n += 1
        except ValueError:
            continue
    return max(int(floor), n)


def cycle_allowance(cap: float, reserve: float, expected_cycles: int) -> float:
    return float(cap) * (1.0 - float(reserve)) / max(1, int(expected_cycles))


def triage_count(allowance: float, composer_pair_usd: float, usd_per_card: float,
                 ceiling: int) -> int:
    """How many cards the allowance buys after the composer pair is paid for.
    Never below 1 (a cycle that fires must review something or it is a
    no-op that re-fires) and never above the window-fit ceiling."""
    ceiling = max(1, int(ceiling))
    if usd_per_card <= 0:
        return ceiling
    n = math.floor((float(allowance) - float(composer_pair_usd)) / float(usd_per_card))
    return max(1, min(ceiling, n))


class Calibration:
    """Trailing means of the two unit costs, measured by the loop from its own
    spend deltas; the priors rule until a sample exists. Persisted in
    state["calibration"]."""

    def __init__(self, card_samples: list[float] | None = None,
                 composer_samples: list[float] | None = None):
        self._cards = list(card_samples or [])[-CALIBRATION_WINDOW:]
        self._composer = list(composer_samples or [])[-CALIBRATION_WINDOW:]

    @classmethod
    def from_state(cls, state: dict) -> "Calibration":
        c = (state or {}).get("calibration") or {}
        return cls(c.get("usd_per_card_samples"), c.get("composer_pair_samples"))

    def save(self, state: dict) -> None:
        state["calibration"] = {"usd_per_card_samples": list(self._cards),
                                "composer_pair_samples": list(self._composer)}

    @property
    def samples(self) -> int:
        return len(self._cards) + len(self._composer)

    @property
    def usd_per_card(self) -> float:
        return sum(self._cards) / len(self._cards) if self._cards else PRIOR_USD_PER_CARD

    @property
    def composer_pair_usd(self) -> float:
        return (sum(self._composer) / len(self._composer)
                if self._composer else PRIOR_COMPOSER_PAIR_USD)

    def record_triage(self, *, spent_delta: float, reviewed: int) -> None:
        """One sample = this cycle's triage spend over the cards it reviewed.
        Nothing reviewed is not a sample (no division by nothing); a negative
        delta is a misordered read, never a refund."""
        if reviewed <= 0 or spent_delta < 0:
            return
        self._cards = (self._cards + [float(spent_delta) / int(reviewed)])[-CALIBRATION_WINDOW:]

    def record_composer(self, *, spent_delta: float) -> None:
        if spent_delta < 0:
            return
        self._composer = (self._composer + [float(spent_delta)])[-CALIBRATION_WINDOW:]
