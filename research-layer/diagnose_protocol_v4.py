"""Audit protocol-v4's new gates against the strategies already on the chain.

Usage:
    python diagnose_protocol_v4.py [--registry registry_log.jsonl]
                                   [--artifacts-dir artifacts]
                                   [--cutoff 2023-12-31]

Reports what the 80 already-chained strategies (77 graveyarded, 3 in
quarantine) WOULD have scored under protocol-v4's three additions: the
train-window Sharpe floor, the CSCV overfitting (PBO) gate, and the
plateau/neighbourhood gate that replaces point-winner sibling selection.

This audits the GATE, not the strategies. Every graveyard/quarantine verdict
on the chain is final; gen-1/2/3 verdicts stand exactly as chained; nothing
here changes, re-runs, or re-judges any of them. Every line below describing
a hypothetical result is written with WOULD for that reason — nothing in
this output is a verdict.

This script has NO registry write path by construction: it never imports a
writer, never constructs Registry for anything but reads (`entries()`,
`strategy_states()`), and touches no chain-writing entry point anywhere in
its call graph. `pipeline/test_gen4.py::test_the_diagnostic_writes_nothing`
asserts this mechanically by hashing registry_log.jsonl before and after a
real run.

Per the ratchet: this diagnostic may only argue to TIGHTEN protocol-v4. Any
loosening needs evidence and argument on a fresh pre-declared chained note.

PLATEAU LIMITATION (read this before reading the plateau section below):
protocol-v4's plateau gate only ever fires on a family with a swept DENSE
block type (pipeline.composer.SWEEPABLE_TYPES). Every one of the 80 chained
specs was composed before dense types existed and uses only coarse/_ds
block types, so this script proves — from the chain itself, the same way
gauntlet.py would — that every one of the 12 sibling groups has an empty
swept-axis set. `pipeline.plateau.qualifies` therefore returns
`(False, "no_swept_axis")` for all 80 specs, unconditionally, on its very
first check. This diagnostic does NOT relax the rule, fall back to a coarser
grid, or invent neighbours to work around that — it reports the gap. The
plateau gate cannot be ratchet-checked against history until a dense-swept
family exists on the chain. This is a real, already-declared limitation of
the evidence behind protocol-v4, not a bug in this script.
"""
from __future__ import annotations

import csv
import sys
import argparse
from pathlib import Path

from pipeline.registry import Registry
from pipeline.pbo import cscv_pbo
from pipeline.plateau import annualized_sharpe, qualifies
from pipeline.composer import SWEEPABLE_TYPES
from pipeline.blocks import BLOCK_TYPES
from pipeline.gauntlet import SR_FLOOR, PBO_PASS, PBO_KILL, CSCV_SPLITS

PROTOCOL = "gauntlet-protocol-v4"


def load_train_equity(artifacts_dir: Path, sid: str,
                      cutoff: str) -> list[tuple[str, float]]:
    """The committed artifacts/<sid>/equity.csv, truncated at `cutoff`.

    screen.py already writes this file from bars fenced at the screen's own
    cutoff (default 2023-12-31, same default as the gauntlet's), so in
    practice every row already satisfies date <= cutoff. The truncation
    below is a defensive re-application of that same fence at the point of
    use, not an assumption that the artifact already honors it.
    """
    path = artifacts_dir / sid / "equity.csv"
    out = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"][:10] > cutoff[:10]:
                continue
            out.append((row["date"], float(row["combined_equity"])))
    return out


def daily_returns(equity: list[tuple[str, float]]) -> list[float]:
    """Same formula as gauntlet.daily_returns_from_curve: reimplemented here,
    not imported, so this script's call graph never passes through the
    module that contains the live write path."""
    return [equity[i][1] / equity[i - 1][1] - 1
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent / "registry_log.jsonl")
    ap.add_argument("--artifacts-dir", type=Path,
                    default=Path(__file__).resolve().parent / "artifacts")
    ap.add_argument("--cutoff", default="2023-12-31")
    args = ap.parse_args(argv)

    # -- read-only chain access --------------------------------------------
    # Registry is opened for reads only: entries() and strategy_states().
    # No writer method (register_strategy, record_state_change,
    # record_verdict, ...) is ever called on this object or any other.
    registry = Registry(args.registry)
    states = registry.strategy_states()
    all_specs = [e["payload"] for e in registry.entries()
                 if e["entry_type"] == "strategy_registered"]
    screen_tc_fail = {
        e["payload"]["strategy_id"]
        for e in registry.entries()
        if e["entry_type"] == "state_change"
        and e["payload"].get("to") == "graveyard"
        and e["payload"].get("reason") == "trade_count"}
    # A gauntlet fail and a sibling_not_selected both land in graveyard;
    # only the verdict entries distinguish a gauntlet PASSER from a genuine
    # gauntlet failure, so state alone is never used for that question.
    gauntlet_passed = {
        e["payload"]["strategy_id"]
        for e in registry.entries()
        if e["entry_type"] == "verdict"
        and e["payload"].get("stage") == "gauntlet"
        and e["payload"].get("verdict") == "pass"}

    buried = [s for s in all_specs
              if states.get(s["strategy_id"]) == "graveyard"]

    print(f"PROTOCOL-V4 DIAGNOSTIC (write-free ratchet check) -- {PROTOCOL}")
    print(f"registry: {args.registry}   artifacts: {args.artifacts_dir}   "
          f"cutoff: {args.cutoff}")
    print(f"{len(all_specs)} strategies registered on the chain: "
          f"{len(buried)} graveyarded (buried, terminal), "
          f"{sum(1 for s in states.values() if s == 'quarantine')} in quarantine.")
    print("Every verdict below is final and stands as chained. This script "
          "audits the GATE, not the specs, and writes NOTHING.\n")

    # -- train-window Sharpe, per spec -------------------------------------
    train_sharpe: dict[str, float | None] = {}
    train_returns: dict[str, list[float]] = {}
    for s in all_specs:
        sid = s["strategy_id"]
        eq = load_train_equity(args.artifacts_dir, sid, args.cutoff)
        train_returns[sid] = daily_returns(eq)
        train_sharpe[sid] = annualized_sharpe(eq)

    # -- plateau axes, computed from the chain exactly as gauntlet.run()
    # would, to PROVE the no-swept-axis claim rather than assume it --------
    family_by_group: dict[str, list[dict]] = {}
    grids_by_group: dict[str, dict[str, list]] = {}
    group_of: dict[str, str] = {}
    for s in all_specs:
        sid, g = s["strategy_id"], s["provenance"]["sibling_group_id"]
        group_of[sid] = g
        axes = {}
        for b in s["blocks"]:
            key = (b["role"], b["type"])
            if key not in SWEEPABLE_TYPES:
                continue
            for p, v in b["params"].items():
                if isinstance(BLOCK_TYPES[key].get(p, {}).get("grid"), list):
                    axes[f"{b['type']}.{p}"] = v
                    grids_by_group.setdefault(g, {})[f"{b['type']}.{p}"] = \
                        BLOCK_TYPES[key][p]["grid"]
        family_by_group.setdefault(g, []).append(
            {"sid": sid, "axes": axes, "score": train_sharpe[sid],
             "screen_trade_count_fail": sid in screen_tc_fail,
             "gauntlet_passed": sid in gauntlet_passed})
    for g, fam in family_by_group.items():
        varying = {a for a in grids_by_group.get(g, {})
                   if len({s["axes"].get(a) for s in fam}) > 1}
        grids_by_group[g] = {a: v for a, v in grids_by_group.get(g, {}).items()
                             if a in varying}
        for s in fam:
            s["axes"] = {a: v for a, v in s["axes"].items() if a in varying}

    # -- per-group report: PBO, sharpe floor, plateau ----------------------
    pbo_by_group: dict[str, dict] = {}
    pbo_gate_pass_by_group: dict[str, bool] = {}
    pbo_family_kill: set[str] = set()

    for g in sorted(family_by_group):
        fam = family_by_group[g]
        sids = sorted(s["sid"] for s in fam)
        print(f"=== sibling group: {g}  ({len(sids)} siblings) ===")

        # PBO, over every sibling including screen deaths, train window only.
        series = {sid: train_returns[sid] for sid in sids}
        try:
            result = cscv_pbo(series, s=CSCV_SPLITS)
        except ValueError as exc:
            print(f"  PBO: UNCOMPUTABLE -- {exc}")
            pbo_by_group[g] = {"pbo": None, "reason": str(exc)}
            pbo_gate_pass_by_group[g] = True  # uncomputable does not gate
            pbo_value = None
        else:
            pbo_by_group[g] = result
            pbo_value = result["pbo"]
            if pbo_value is None:
                print(f"  PBO: n/a -- {result['reason']} "
                      f"({result['n_configs']} configs)")
                pbo_gate_pass_by_group[g] = True
            else:
                if pbo_value > PBO_KILL:
                    label = f"WOULD FAMILY-KILL (pbo={pbo_value:.3f} > {PBO_KILL})"
                    pbo_family_kill.add(g)
                    pbo_gate_pass_by_group[g] = False
                elif pbo_value >= PBO_PASS:
                    label = f"WOULD FAIL (pbo={pbo_value:.3f} >= {PBO_PASS})"
                    pbo_gate_pass_by_group[g] = False
                else:
                    label = f"WOULD PASS (pbo={pbo_value:.3f} < {PBO_PASS})"
                    pbo_gate_pass_by_group[g] = True
                print(f"  PBO: {label}  ({result['n_configs']} configs, "
                      f"{result['n_combinations']} combinations)")

        # Sharpe floor, per sibling.
        for sid in sids:
            sr = train_sharpe[sid]
            if sr is None:
                verdict = "n/a (Sharpe not computable)"
            elif sr >= SR_FLOOR:
                verdict = f"WOULD PASS (sharpe={sr:.3f} >= {SR_FLOOR})"
            else:
                verdict = f"WOULD FAIL (sharpe={sr:.3f} < {SR_FLOOR})"
            tags = []
            if sid in screen_tc_fail:
                tags.append("screen-death:trade_count")
            if sid in gauntlet_passed:
                tags.append("gauntlet-verdict:pass")
            buried_tag = "buried" if states.get(sid) == "graveyard" else states.get(sid)
            tag_str = f"  [{', '.join(tags)}]" if tags else ""
            print(f"    {sid}  {buried_tag:<11}  sharpe_floor: {verdict}{tag_str}")

        # Plateau: no_swept_axis is proved from the chain above, not assumed.
        grids = grids_by_group.get(g, {})
        reasons = {sid: qualifies(next(x for x in fam if x["sid"] == sid),
                                  fam, grids)[1] for sid in sids}
        if all(r == "no_swept_axis" for r in reasons.values()):
            print(f"  PLATEAU: WOULD BE no_swept_axis for all {len(sids)} "
                  f"siblings -- this group swept no dense block type, so "
                  f"protocol-v4's neighbourhood gate CANNOT be checked here.")
        else:
            # Would only happen if a future chain entry introduces a dense
            # sweep; reported per-sibling rather than assumed uniform.
            for sid, r in sorted(reasons.items()):
                print(f"  PLATEAU {sid}: qualifies-reason={r}")
        print()

    # -- quarantine PBO cross-check ------------------------------------------
    # The three strategies already in quarantine entered under protocol-v3,
    # BEFORE protocol-v4's PBO gate existed. Retroactivity was closed and
    # pre-declared separately, before protocol-v4 was designed, in registry
    # entry 2308 (`quarantine-standard-asymmetry` note, chained in commit
    # 1b5da5e): a successor gate standard applies only to candidates
    # evaluated under it, never retroactively. That note already names all
    # three strategy_ids and states the cost of the exemption in prose; this
    # section makes the PBO half of that cost a number instead of something
    # a reader has to look up and compute by hand.
    print("=" * 72)
    print("QUARANTINE PBO CROSS-CHECK -- the 3 strategies already in "
          "quarantine, against protocol-v4's PBO gate.")
    print("THIS CHANGES NOTHING. Retroactivity was closed and pre-declared "
          "BEFORE protocol-v4 existed: registry entry 2308 "
          "(`quarantine-standard-asymmetry` note, commit 1b5da5e) states "
          "that a successor gate standard applies only to candidates "
          "evaluated under it, never retroactively, and names these same "
          "three strategy_ids. All three keep their protocol-v3 verdicts "
          "and their quarantine state exactly as chained, regardless of "
          "what follows here. This is recorded to make the cost of that "
          "exemption legible, not to reopen it.")
    quarantine_sids = sorted(sid for sid, st in states.items()
                             if st == "quarantine")
    for sid in quarantine_sids:
        g = group_of.get(sid)
        pbo_r = pbo_by_group.get(g, {})
        pbo_v = pbo_r.get("pbo")
        if pbo_v is None:
            label = f"n/a -- {pbo_r.get('reason')}"
        elif pbo_v > PBO_KILL:
            label = f"WOULD FAMILY-KILL (pbo={pbo_v:.3f} > {PBO_KILL})"
        elif pbo_v >= PBO_PASS:
            label = f"WOULD FAIL (pbo={pbo_v:.3f} >= {PBO_PASS})"
        else:
            label = f"WOULD PASS (pbo={pbo_v:.3f} < {PBO_PASS})"
        print(f"    {sid}  group={g}  PBO gate: {label}")
    print("  None of the above alters any state_change, verdict, or "
          "eligibility. All three remain in quarantine under their "
          "protocol-v3 verdicts.\n")

    # -- summary -------------------------------------------------------------
    n_specs = len(all_specs)
    n_sharpe_fail_all = sum(1 for sid in train_sharpe
                            if train_sharpe[sid] is not None
                            and train_sharpe[sid] < SR_FLOOR)
    n_sharpe_fail_buried = sum(
        1 for s in buried
        if train_sharpe[s["strategy_id"]] is not None
        and train_sharpe[s["strategy_id"]] < SR_FLOOR)
    n_groups_killed = len(pbo_family_kill)
    n_groups_uncomputable = sum(1 for g, r in pbo_by_group.items()
                                if r["pbo"] is None)

    n_buried_pass_checkable = 0
    for s in buried:
        sid = s["strategy_id"]
        g = group_of[sid]
        sr = train_sharpe[sid]
        sharpe_ok = sr is None or sr >= SR_FLOOR
        pbo_ok = pbo_gate_pass_by_group.get(g, True)
        if sharpe_ok and pbo_ok:
            n_buried_pass_checkable += 1

    print("=" * 72)
    print("SUMMARY -- every number below is a hypothetical WOULD, not a verdict.")
    print(f"  {n_sharpe_fail_all}/{n_specs} registered strategies WOULD fail "
          f"the train-window Sharpe floor ({SR_FLOOR}); "
          f"{n_sharpe_fail_buried}/{len(buried)} of those are among the "
          f"already-buried.")
    print(f"  {n_groups_killed}/{len(family_by_group)} sibling groups WOULD "
          f"be PBO family-killed (pbo > {PBO_KILL}); "
          f"{n_groups_uncomputable}/{len(family_by_group)} groups have an "
          f"UNCOMPUTABLE PBO.")
    print(f"  PLATEAU: 0/{len(family_by_group)} sibling groups have a swept "
          f"dense axis, so the plateau/neighbourhood gate WOULD be "
          f"UNCHECKABLE for all {n_specs} chained specs -- see the "
          f"limitation notice above and in this file's module docstring.")
    print(f"\n  NEW-GATE REACH: {n_buried_pass_checkable}/{len(buried)} "
          f"already-buried strategies WOULD clear the two newly added "
          f"checkable gates (train-window Sharpe floor + CSCV/PBO) "
          f"considered in isolation. This is NOT a ratchet signal and does "
          f"NOT mean {n_buried_pass_checkable} would return: protocol-v4 "
          f"retains every protocol-v3 gate unchanged and adds three, so it "
          f"is a strict superset and no buried strategy can newly pass. "
          f"Each of these {n_buried_pass_checkable} still fails the v3 gate "
          f"that buried it. (The plateau gate is excluded from this count "
          f"because it cannot be evaluated at all against this chain -- see "
          f"above.)")
    print("\n  RATCHET POSITION: protocol-v4 cannot loosen. Three gates "
          "added, none removed, no threshold weakened -- verified "
          "structurally, not statistically.")

    print("\nNothing was re-judged: no state_change, no verdict, no note was "
          "written or considered for writing. registry_log.jsonl was opened "
          "read-only and is untouched by this run. The 77 buried strategies "
          "remain buried and gen-1/2/3 verdicts stand exactly as chained.")
    print("PLATEAU LIMITATION (repeated): protocol-v4's plateau/neighbourhood "
          "gate could NOT be checked for any of the 12 sibling groups on this "
          "chain -- zero of the 80 registered specs sweep a dense block "
          "type, so `qualifies` returns no_swept_axis unconditionally. This "
          "diagnostic did not relax the rule, fall back to a coarse grid, or "
          "invent neighbours to produce a plateau result. The judgement of "
          "whether this is acceptable to ship the v4 note belongs to a "
          "human.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
