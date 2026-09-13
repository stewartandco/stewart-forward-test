"""Neighbourhood/plateau selection — protocol-v4.

Replaces point-winner selection. The SOP forbids choosing the best single
configuration and requires selection by neighbourhood quality: a candidate is
only eligible if it AND every one of its one-step neighbours sit on the
plateau, and among eligible candidates the winner is the one whose worst
neighbour is strongest.

A candidate qualifies only if EVERY swept axis has a registered sibling one
step below AND one step above it (edge_of_grid otherwise). This covers two
failure shapes identically: sitting at grid index 0 or len-1, and sitting
mid-grid next to a value that was never expanded into a sibling — both mean
the configuration has never been perturbed in that direction, and neither
lets you claim a plateau from the boundary. In a real fixture, an edge
candidate with one strong neighbour tied a two-sided interior candidate on
floor and won the tie-break, advantaging the config with LESS evidence —
exactly the point-winner bias this gate exists to remove. One consequence:
a two-value sweep can never produce a survivor, since both of its points are
edges; pipeline/composer.py:validate_family enforces a minimum of three
values per swept axis so a family cannot be composed into that trap.

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


def _matches_at(other: dict, sibling: dict, axis: str, want: object) -> bool:
    """True if `other` is a registered sibling sitting at `want` on `axis`
    and identical to `sibling` on every other axis — i.e. it IS the one-step
    neighbour in that direction, not merely some other family member."""
    return (other["sid"] != sibling["sid"]
            and other["axes"].get(axis) == want
            and all(other["axes"].get(a) == v
                    for a, v in sibling["axes"].items() if a != axis))


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
            out.extend(o for o in family if _matches_at(o, sibling, axis, want))
    return sorted(out, key=lambda s: s["sid"])


def is_edge_of_grid(sibling: dict, family: list[dict],
                     grids: dict[str, list]) -> bool:
    """True if any swept axis is missing a registered sibling on either
    side — the candidate sits at a grid boundary, or next to a grid value
    that was never expanded into a sibling. Either way, that direction has
    never been perturbed, so no plateau claim can be made through it."""
    for axis, values in grids.items():
        if axis not in sibling["axes"]:
            continue
        here = values.index(sibling["axes"][axis])
        for step in (-1, 1):
            j = here + step
            if not 0 <= j < len(values):
                return True
            want = values[j]
            if not any(_matches_at(o, sibling, axis, want) for o in family):
                return True
    return False


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

    Checks run in order: no_swept_axis, edge_of_grid, then the plateau and
    cliff clauses.

    A family with no swept dense axis FAILS. Without a neighbourhood there is
    no robustness evidence, and every other clause here would pass vacuously:
    empty grids give no neighbours, no neighbours give nothing to fail on, and
    a lone sibling is trivially >= 0.9 of its own score. A gate that passes on
    the absence of evidence is not a gate.

    A candidate missing a registered neighbour on either side of any swept
    axis FAILS with edge_of_grid, before the plateau/cliff checks even run.
    You cannot claim a plateau from the boundary: a one-sided candidate has
    strictly less evidence than a two-sided one, and comparing their floors
    as if they were equally supported let an edge point with a single strong
    neighbour outrank an interior point with two adequate ones.
    """
    if not grids:
        return False, "no_swept_axis"
    if is_edge_of_grid(sibling, family, grids):
        return False, "edge_of_grid"
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
