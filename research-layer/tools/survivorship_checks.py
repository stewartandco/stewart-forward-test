"""Data checks for the crypto survivorship study (P1 parity, P3 splice, P4 hidden splices)."""
from __future__ import annotations

import math
from datetime import date as _date

GAP_DAYS = 7
JUMP_RATIO = 10.0
SPLICE_TOL = 0.05

_FIELDS = ("open", "high", "low", "close", "volume")


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
    if not (set(a) & set(p)):
        out.append("no shared dates between archive and pipeline")
    for d in sorted(set(a) & set(p)):
        for f in _FIELDS:
            if a[d][f] != p[d][f]:
                out.append(f"{d} {f}: archive={a[d][f]!r} pipeline={p[d][f]!r}")
    return out


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


def hidden_splices(bars: list[dict]) -> list[str]:
    flags: list[str] = []
    for prev, cur in zip(bars, bars[1:]):
        if _day(cur) < _day(prev):
            raise ValueError(f"bars not sorted: {_day(prev)} then {_day(cur)}")
    for b in bars:
        if not math.isfinite(b["close"]) or b["close"] <= 0:
            flags.append(f"invalid close {b['close']} on {_day(b)}")
    for prev, cur in zip(bars, bars[1:]):
        gap = (_date.fromisoformat(_day(cur)) - _date.fromisoformat(_day(prev))).days
        if gap > GAP_DAYS:
            flags.append(f"gap of {gap} days before {_day(cur)} (after {_day(prev)})")
        pc, cc = prev["close"], cur["close"]
        if math.isfinite(pc) and math.isfinite(cc) and pc > 0 and cc > 0:
            r = cc / pc
            if r > JUMP_RATIO or r < 1.0 / JUMP_RATIO:
                flags.append(f"close jump x{r:.4g} on {_day(cur)} ({pc} -> {cc})")
    return flags


def apply_cut(bars: list[dict], first_date: str) -> list[dict]:
    return [b for b in bars if _day(b) >= first_date]
