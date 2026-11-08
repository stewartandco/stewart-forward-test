"""Data checks for the crypto survivorship study (P1 parity, P3 splice, P4 hidden splices)."""
from __future__ import annotations

import math
from datetime import date as _date

GAP_DAYS = 7
JUMP_RATIO = 10.0
# Coen 2026-11-08 (R4): 0.05 -> 0.25. Every Binance rename halted trading for
# 1-3 days, and the price moved 4-15% across the halt (MATIC->POL +8.0%,
# FTM->S +11.1%, KLAY->KAIA +12.8%, FXS->FRAX +14.7%): real returns, not a
# wrong ratio. The check exists to catch a wrong conversion ratio (a 30%+,
# usually 2x+, jump); |ln| < 0.25 still stops any ratio error over ~28%.
SPLICE_TOL = 0.25

# P1 fails on OHLC only. Volume is report-only (volume_diffs): Binance revised
# some archive volumes after the fact (~10 dates, e.g. 2021-12-24 in 60 pairs)
# while open/high/low/close match the pipeline bar for bar, and no strategy
# block reads volume. Coen 2026-11-08.
_FIELDS = ("open", "high", "low", "close")


class SpliceBreak(ValueError):
    """Raised when a ratio splice is not continuous at the swap date."""


def _day(b: dict) -> str:
    return b["date"][:10]


def _index(bars: list[dict], label: str, out: list[str]) -> dict:
    idx: dict = {}
    for b in bars:
        d = b["date"]
        if d in idx:
            out.append(f"duplicate date {d} in {label}")
        else:
            idx[d] = b
    return idx


def parity(archive: list[dict], pipeline: list[dict]) -> list[str]:
    out: list[str] = []
    a = _index(archive, "archive", out)
    p = _index(pipeline, "pipeline", out)
    shared = set(a) & set(p)
    if not shared:
        out.append("no shared dates between archive and pipeline")
    else:
        # inside the span both inputs cover, a date in only one is a finding
        lo, hi = max(min(a), min(p)), min(max(a), max(p))
        for d in sorted(x for x in set(a) ^ set(p) if lo <= x <= hi):
            where = ("archive", "pipeline") if d in a else ("pipeline", "archive")
            out.append(f"{d} in the {where[0]}, missing from the {where[1]} "
                       f"(inside the shared span {lo} .. {hi})")
    for d in sorted(shared):
        for f in _FIELDS:
            if a[d][f] != p[d][f]:
                out.append(f"{d} {f}: archive={a[d][f]!r} pipeline={p[d][f]!r}")
    return out


def volume_diffs(archive: list[dict], pipeline: list[dict]) -> list[str]:
    """Report-only P1 volume differences on dates both inputs hold (never a
    failure; see _FIELDS). Duplicate dates keep their first bar here: parity
    is the check that reports them."""
    a, p = {}, {}
    for bars, idx in ((archive, a), (pipeline, p)):
        for b in bars:
            idx.setdefault(b["date"], b)
    return [f"{d} volume: archive={a[d]['volume']!r} pipeline={p[d]['volume']!r}"
            for d in sorted(set(a) & set(p)) if a[d]["volume"] != p[d]["volume"]]


def dedup_exact(bars: list[dict]) -> tuple[list[dict], list[str]]:
    """Drops a bar identical in EVERY field (date, open, high, low, close,
    volume) to an already-kept bar of the same date; returns (kept, notes).
    A same-date bar whose values differ is kept, so it still fails P1/P4.
    Coen 2026-11-08 (AXSUSDT-1d-2026-02.zip holds 2026-02-10 twice)."""
    kept: list[dict] = []
    seen: set = set()
    notes: list[str] = []
    for b in bars:
        key = tuple(b[f] for f in ("date",) + _FIELDS + ("volume",))
        if key in seen:
            notes.append(f"dropped exact duplicate {b['date']} (open={b['open']!r} "
                         f"high={b['high']!r} low={b['low']!r} close={b['close']!r} "
                         f"volume={b['volume']!r})")
            continue
        seen.add(key)
        kept.append(b)
    return kept, notes


def first_bar(archive_first: str, pipeline_first: str | None) -> str:
    if pipeline_first is None:
        return archive_first
    return max(archive_first, pipeline_first)


def splice(pred: list[dict], succ: list[dict], swap_date: str, ratio: float) -> list[dict]:
    head = [dict(b) for b in pred if _day(b) < swap_date]
    tail = []
    for b in succ:
        if _day(b) >= swap_date:
            nb = dict(b)
            for f in ("open", "high", "low", "close"):
                nb[f] = b[f] * ratio
            tail.append(nb)
    if not head:
        raise SpliceBreak(f"no predecessor bars dated before swap_date {swap_date}")
    if not tail:
        raise SpliceBreak(f"no successor bars dated on or after swap_date {swap_date}")
    pc, sc = head[-1]["close"], tail[0]["close"]
    if (not (math.isfinite(pc) and math.isfinite(sc)) or pc <= 0 or sc <= 0
            or abs(math.log(sc / pc)) >= SPLICE_TOL):
        raise SpliceBreak(
            f"splice break at {swap_date}: predecessor last close {pc} "
            f"({head[-1]['date']}) vs successor first close x ratio {sc} "
            f"({tail[0]['date']}), ratio {ratio}")
    return head + tail


def hidden_splices(bars: list[dict], halts=()) -> list[str]:
    """`halts` = declared (last bar date before, first bar date after) pairs
    (survivorship_universe.HALTS). A gap over GAP_DAYS is skipped ONLY when its
    two bar dates equal a declared pair exactly; the x10 jump check still runs
    across it. A declared halt that matches no gap in the data is itself a flag
    (never a silent skip)."""
    flags: list[str] = []
    halts = {tuple(h) for h in halts}
    used: set = set()
    for prev, cur in zip(bars, bars[1:]):
        if _day(cur) < _day(prev):
            raise ValueError(f"bars not sorted: {_day(prev)} then {_day(cur)}")
    for b in bars:
        if not math.isfinite(b["close"]) or b["close"] <= 0:
            flags.append(f"invalid close {b['close']} on {_day(b)}")
    for prev, cur in zip(bars, bars[1:]):
        gap = (_date.fromisoformat(_day(cur)) - _date.fromisoformat(_day(prev))).days
        if gap > GAP_DAYS and (_day(prev), _day(cur)) in halts:
            used.add((_day(prev), _day(cur)))
        elif gap > GAP_DAYS:
            flags.append(f"gap of {gap} days before {_day(cur)} (after {_day(prev)})")
        pc, cc = prev["close"], cur["close"]
        if math.isfinite(pc) and math.isfinite(cc) and pc > 0 and cc > 0:
            r = cc / pc
            if r > JUMP_RATIO or r < 1.0 / JUMP_RATIO:
                flags.append(f"close jump x{r:.4g} on {_day(cur)} ({pc} -> {cc})")
    flags += [f"declared halt {a} .. {b} matches no gap in the data"
              for a, b in sorted(halts - used)]
    return flags


def apply_cut(bars: list[dict], first_date: str) -> list[dict]:
    return [b for b in bars if _day(b) >= first_date]
