"""Self-perturbation sensitivity: what happens to THIS strategy if you nudge it.

RECORDED, NOT GATING. Decision of Coen's, 2026-08-21, taken when protocol-v6
retired the plateau gate. The SOP's underlying concern is real -- a
configuration that works at exactly one parameter value, with performance
collapsing either side, is showing an overfitting signature rather than an edge
-- but the plateau rule answered it by reading a candidate's NEIGHBOURS'
registered scores, which v6's standalone principle forbids.

This asks the same question in a form that is standalone by construction. It
does not consult a sibling, does not require a neighbouring grid point to have
been registered, and does not care where the candidate sits in anyone's sweep.
It takes the strategy's own parameters, steps one of them one place along its
own declared grid, RE-RUNS the strategy, and records what happened to it. The
old rule asked "how did my neighbours score?"; this asks "what happens to ME if
you nudge me?".

That also repairs a real defect. protocol-v4's edge_of_grid disqualified a
candidate when a neighbouring grid point simply had not been registered as a
sibling, which is a fact about the Composer's sweep choices rather than about
the strategy. Here the perturbed configuration is run whether or not anyone
ever registered it.

NOT GATED, deliberately. protocol-v4 gated a plateau ratio of 0.9 that had
never touched real data, and protocol-v5 gated a PBO threshold whose null it
had never measured; both were wrong in ways only measurement revealed. Nothing
is known yet about what perturbation sensitivity real strategies show, so this
records a number and decides nothing. Gating it later is a protocol change and
needs its own pre-declared chained note.
"""
from __future__ import annotations

import copy

from .blocks import BLOCK_TYPES

# Only NUMERIC grids are stepped. A categorical axis such as direction
# ["long", "both"] has no notion of "one step": moving along it is not a small
# perturbation of the same strategy, it is a different strategy, and recording
# it as sensitivity would be misleading rather than conservative.
NUMERIC = {"int", "float"}


def gridded_axes(spec: dict) -> list[dict]:
    """Every numeric parameter of this spec that sits on a declared grid.

    Records the grid the step is taken along, because a step on a COARSE grid
    (channel_breakout.lookback 20 -> 55) is a far bigger change than a step on
    its dense twin (20 -> 35), and a reader comparing two sensitivity numbers
    has to be able to see which kind of step produced them.
    """
    out = []
    for i, block in enumerate(spec["blocks"]):
        schema = BLOCK_TYPES.get((block["role"], block["type"]), {})
        for param, value in sorted(block["params"].items()):
            decl = schema.get(param, {})
            grid = decl.get("grid")
            if decl.get("type") not in NUMERIC or not isinstance(grid, list):
                continue
            if value not in grid:
                continue
            out.append({"block": i, "role": block["role"], "type": block["type"],
                        "param": param, "value": value, "grid": list(grid),
                        "index": grid.index(value), "dense": "_dense" in block["type"]})
    return out


def perturbations(spec: dict, dense_only: bool = False) -> list[dict]:
    """One perturbed spec per available single step, in both directions.

    A step off the end of a grid does not exist and is simply absent -- unlike
    protocol-v4's edge_of_grid, its absence disqualifies nothing. The returned
    specs carry no strategy_id: they are throwaway configurations to be run,
    never registered, and giving them an id would invite exactly that.
    """
    out = []
    for axis in gridded_axes(spec):
        if dense_only and not axis["dense"]:
            # A step on a COARSE grid is not a nudge. channel_breakout.lookback
            # 20 -> 55 is a different strategy, which is precisely the argument
            # protocol-v4 made when it introduced the dense twins. Callers
            # measuring SENSITIVITY want the small step or none.
            continue
        for direction, step in (("down", -1), ("up", +1)):
            j = axis["index"] + step
            if not 0 <= j < len(axis["grid"]):
                continue
            moved = copy.deepcopy(spec)
            moved.pop("strategy_id", None)
            moved["blocks"][axis["block"]]["params"][axis["param"]] = axis["grid"][j]
            out.append({"axis": f"{axis['type']}.{axis['param']}",
                        "direction": direction,
                        "from": axis["value"], "to": axis["grid"][j],
                        "dense": axis["dense"], "spec": moved})
    return out


def sensitivity(spec: dict, base_score: float | None, score_fn,
                dense_only: bool = False) -> dict:
    """Score every one-step perturbation of `spec` and summarise the movement.

    `score_fn(perturbed_spec) -> float | None` runs the strategy and returns
    its objective; the caller owns the data and the window, so this module
    stays free of both. Ratios are to `base_score`, so 1.0 means the nudge
    changed nothing and 0.5 means it halved the objective.

    A non-positive or missing base score makes ratios meaningless rather than
    infinite, so they are reported as None and the summary says why. That is
    the same fail-quiet-and-say-so posture plateau_members takes.
    """
    results = []
    for p in perturbations(spec, dense_only=dense_only):
        score = score_fn(p["spec"])
        ratio = (None if base_score is None or base_score <= 0 or score is None
                 else score / base_score)
        results.append({k: p[k] for k in ("axis", "direction", "from", "to", "dense")}
                       | {"score": score, "ratio": ratio})
    ratios = [r["ratio"] for r in results if r["ratio"] is not None]
    return {
        "base_score": base_score,
        "n_perturbations": len(results),
        "dense_only": dense_only,
        "results": results,
        # the WORST one-step outcome is the number the SOP's concern is about:
        # a lone peak is a configuration with a bad neighbour in some direction
        "worst_ratio": min(ratios) if ratios else None,
        "mean_ratio": sum(ratios) / len(ratios) if ratios else None,
        "reason": None if ratios else (
            "no scorable one-step perturbation: either no numeric gridded axis, "
            "or a non-positive base score"),
    }
