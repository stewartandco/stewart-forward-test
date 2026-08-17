"""Neighbourhood/plateau selection — protocol-v4.

Replaces point-winner selection. The SOP forbids choosing the best single
configuration and requires selection by neighbourhood quality: a candidate is
only eligible if it AND every one of its one-step neighbours sit on the
plateau, and among eligible candidates the winner is the one whose worst
neighbour is strongest.

A neighbour that died at the screen on trade_count is a hard cliff. Turnover is
a structural property of a configuration, not a noisy metric — a 24-trade
sibling can post a flattering per-trade Sharpe while being untradeable.

Every function here is pure. The registry and the artifacts are read by the
caller (gauntlet.py / diagnose_protocol_v4.py), never here.
"""
from __future__ import annotations

import math

PLATEAU_RATIO = 0.9
TRADING_DAYS = 365   # crypto trades every day; matches the rest of the pipeline


def annualized_sharpe(equity: list[tuple[str, float]]) -> float | None:
    """Annualized Sharpe of a daily equity curve. None when it cannot be
    computed (too few steps, or no variance) — never a fabricated 0.0."""
    rets = [equity[i][1] / equity[i - 1][1] - 1
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]
    if len(rets) < 30:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)


def neighbours_of(sibling: dict, family: list[dict],
                   grids: dict[str, list]) -> list[dict]:
    """Siblings differing from this one by exactly one grid step on exactly one
    axis. Absent grid points simply have no neighbour there."""
    out = []
    for axis, values in grids.items():
        if axis not in sibling["axes"]:
            continue
        here = values.index(sibling["axes"][axis])
        for step in (-1, 1):
            j = here + step
            if not 0 <= j < len(values):
                continue
            want = values[j]
            for other in family:
                if other["sid"] == sibling["sid"]:
                    continue
                if other["axes"].get(axis) != want:
                    continue
                if all(other["axes"].get(a) == v
                       for a, v in sibling["axes"].items() if a != axis):
                    out.append(other)
    return sorted(out, key=lambda s: s["sid"])


def plateau_members(family: list[dict],
                     ratio: float = PLATEAU_RATIO) -> set[str]:
    """Every sibling scoring at least `ratio` of the family's best score.
    A sibling with no computable score is never on the plateau."""
    scored = [s["score"] for s in family if s["score"] is not None]
    if not scored:
        return set()
    best = max(scored)
    if best <= 0:
        return set()
    return {s["sid"] for s in family
            if s["score"] is not None and s["score"] >= ratio * best}


def qualifies(sibling: dict, family: list[dict], grids: dict[str, list],
              ratio: float = PLATEAU_RATIO) -> tuple[bool, str | None]:
    """Plateau qualification. Returns (ok, reason_when_not).

    A family with no swept dense axis FAILS. Without a neighbourhood there is
    no robustness evidence, and every other clause here would pass vacuously:
    empty grids give no neighbours, no neighbours give nothing to fail on, and
    a lone sibling is trivially >= 0.9 of its own score. A gate that passes on
    the absence of evidence is not a gate.
    """
    if not grids:
        return False, "no_swept_axis"
    plat = plateau_members(family, ratio)
    if sibling["sid"] not in plat:
        return False, "below_plateau"
    nbrs = neighbours_of(sibling, family, grids)
    if any(n["screen_trade_count_fail"] for n in nbrs):
        return False, "cliff_trade_count"
    if any(n["sid"] not in plat for n in nbrs):
        return False, "neighbour_below_plateau"
    return True, None


def neighbourhood_floor(sibling: dict, family: list[dict],
                         grids: dict[str, list]) -> float:
    """The worst score across the candidate and its neighbours. This is the
    selection currency — never the candidate's own score alone."""
    scores = [sibling["score"]]
    scores += [n["score"] for n in neighbours_of(sibling, family, grids)]
    return min((s for s in scores if s is not None), default=-math.inf)


def select_survivor(family: list[dict], grids: dict[str, list],
                     ratio: float = PLATEAU_RATIO) -> tuple[str | None, dict]:
    """Pick the sibling with the strongest neighbourhood floor among
    gauntlet-passing, plateau-qualifying candidates. Ties break on sid
    (lexicographically smallest wins, matching gauntlet.select_survivors)."""
    detail = {}
    eligible = []
    for s in sorted(family, key=lambda x: x["sid"]):
        ok, reason = qualifies(s, family, grids, ratio)
        floor = neighbourhood_floor(s, family, grids)
        detail[s["sid"]] = {"qualifies": ok, "reason": reason, "floor": floor,
                             "gauntlet_passed": s["gauntlet_passed"]}
        if ok and s["gauntlet_passed"]:
            eligible.append((floor, s["sid"]))
    if not eligible:
        return None, detail
    # Descending floor, then ascending sid. NOT max() with an ord-based key:
    # that compares lists, so "aa" -> [-97,-97] ranks below "aab" ->
    # [-97,-97,-98] and the LONGER sid wins. Strategy ids are 16 hex chars.
    ranked = sorted(eligible, key=lambda pair: (-pair[0], pair[1]))
    return ranked[0][1], detail
