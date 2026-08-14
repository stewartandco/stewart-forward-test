"""Gauntlet battery: five-gate validation of gauntlet-state strategies on
the 2024+ holdout, with pre-declared sibling selection.

Usage:
    python -m pipeline.gauntlet [--registry registry_log.jsonl]
        [--data-dir data] [--artifacts-dir artifacts]
        [--cutoff 2023-12-31] [--dry-run]

Real runs HARD-REFUSE unless a note starting with PROTOCOL is chained.
Gates and amendments per docs/2026-08-14-gauntlet-design.md.
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path

from .registry import Registry
from .engine import run_spec
from .stats import (moments, sharpe, percentile, psr, expected_max_sharpe,
                    bootstrap_paths)

PROTOCOL = "gauntlet-protocol-v1"
DECAY_MIN_PCT = -25.0
MC_PATHS = 2000
MC_P05_MIN = 1.0
RUIN_LEVEL = 0.5
P_RUIN_MAX = 0.05
DSR_MIN = 0.95
DEFAULT_CUTOFF = "2023-12-31"


def split_trades(trades: list[dict], cutoff: str) -> tuple[list, list]:
    is_t = [t for t in trades if t["entry_date"] <= cutoff]
    oos_t = [t for t in trades if t["entry_date"] > cutoff]
    return is_t, oos_t


def contributions(trades: list[dict]) -> list[float]:
    """Per-trade portfolio contribution: return_net x notional_frac."""
    return [t["return_net"] * t["notional_frac"] for t in trades]


def compound(contribs: list[float]) -> float:
    eq = 1.0
    for c in contribs:
        eq *= 1 + c
    return eq - 1


def evaluate_spec(is_trades: list[dict], oos_trades: list[dict],
                  stress_oos_trades: list[dict], daily_returns: list[float],
                  group_n: int, group_sr_var: float, seed: int):
    """Run the six checks in fixed order. Returns
    (passed, fail_reason|None, metrics, mc_summary)."""
    is_c = contributions(is_trades)
    oos_c = contributions(oos_trades)
    is_edge = sum(is_c) / len(is_c) if is_c else 0.0
    oos_edge = sum(oos_c) / len(oos_c) if oos_c else 0.0
    oos_net = compound(oos_c)
    decay = ((oos_edge - is_edge) / abs(is_edge) * 100
             if is_edge > 0 else None)

    mc = bootstrap_paths(is_c + oos_c, MC_PATHS, seed, RUIN_LEVEL)
    mc_p05 = percentile(mc["terminals"], 0.05)

    sr_hat = sharpe(daily_returns)
    _, _, skew, kurt = moments(daily_returns)
    sr_star = expected_max_sharpe(group_n, group_sr_var)
    dsr = psr(sr_hat, sr_star, len(daily_returns), skew, kurt)

    stress_net = compound(contributions(stress_oos_trades))

    metrics = {
        "is_edge_per_trade": is_edge,
        "oos_edge_per_trade": oos_edge,
        "edge_decay_pct": decay,
        "mc_p05_equity": mc_p05,
        "p_ruin": mc["p_ruin"],
        "deflated_sharpe": dsr,
        "sibling_group_n": group_n,
        "cost_stress_net_pnl": stress_net,
    }
    mc_summary = {"seed": seed, "paths": MC_PATHS,
                  "p05": mc_p05,
                  "p25": percentile(mc["terminals"], 0.25),
                  "p50": percentile(mc["terminals"], 0.50),
                  "p_ruin": mc["p_ruin"], "ruin_level": RUIN_LEVEL}

    if not oos_net > 0:
        return False, "oos_negative", metrics, mc_summary
    if decay is None or not decay > DECAY_MIN_PCT:
        return False, "edge_decay", metrics, mc_summary
    if not mc_p05 > MC_P05_MIN:
        return False, "mc_p05", metrics, mc_summary
    if not mc["p_ruin"] < P_RUIN_MAX:
        return False, "p_ruin", metrics, mc_summary
    if not dsr >= DSR_MIN:
        return False, "dsr", metrics, mc_summary
    if not stress_net > 0:
        return False, "cost_stress", metrics, mc_summary
    return True, None, metrics, mc_summary


def select_survivors(rows: list[dict]) -> tuple[set[str], set[str]]:
    """Pre-declared selection: per sibling group, the passer with the highest
    DSR (tie: lexicographically smallest sid) -> quarantine; other passers ->
    sibling_not_selected. rows: [{sid, group, passed, dsr}]."""
    quarantine, not_selected = set(), set()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["passed"]:
            groups.setdefault(r["group"], []).append(r)
    for group_rows in groups.values():
        ranked = sorted(group_rows, key=lambda r: (-r["dsr"], r["sid"]))
        quarantine.add(ranked[0]["sid"])
        not_selected.update(r["sid"] for r in ranked[1:])
    return quarantine, not_selected


def daily_returns_from_curve(equity: list[tuple[str, float]]) -> list[float]:
    return [equity[i][1] / equity[i - 1][1] - 1
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]


def stressed(spec: dict) -> dict:
    s = json.loads(json.dumps(spec))
    s["cost_model"]["slippage_ticks"] *= 2
    return s


def write_gauntlet_artifacts(art_dir: Path, spec: dict, oos_trades: list[dict],
                             mc_summary: dict, metrics: dict, cutoff: str,
                             data_hashes: dict, group_context: dict) -> Path:
    import csv
    bundle = art_dir / spec["strategy_id"] / "gauntlet"
    bundle.mkdir(parents=True, exist_ok=True)
    with (bundle / "oos_trades.csv").open("w", newline="",
                                          encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asset", "side", "entry_date",
                                          "entry_px", "exit_date", "exit_px",
                                          "exit_reason", "return_net",
                                          "notional_frac"],
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(oos_trades)
    (bundle / "mc_summary.json").write_text(
        json.dumps(mc_summary, indent=1, sort_keys=True), encoding="utf-8")
    (bundle / "config.json").write_text(json.dumps(
        {"protocol": PROTOCOL, "cutoff": cutoff, "metrics": metrics,
         "data_sha256": data_hashes, "group_context": group_context,
         "spec": spec}, indent=1, sort_keys=True), encoding="utf-8")
    return bundle


def run(argv: list[str] | None = None) -> int:
    import hashlib
    from .screen import load_bars, bundle_hash

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data")
    ap.add_argument("--artifacts-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "artifacts")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    states = registry.strategy_states()

    gauntlet_verdicted = {e["payload"]["strategy_id"]
                          for e in registry.entries()
                          if e["entry_type"] == "verdict"
                          and e["payload"].get("stage") == "gauntlet"}
    orphans = [sid for sid, st in states.items()
               if st == "gauntlet" and sid in gauntlet_verdicted]
    if orphans:
        print("ORPHANED: gauntlet verdicts without state changes "
              "(mid-run crash?) — repair manually before proceeding:")
        for sid in orphans:
            print(f"  {sid}")
        return 1

    if not args.dry_run and not any(
            e["entry_type"] == "note"
            and str(e["payload"].get("text", "")).startswith(PROTOCOL)
            for e in registry.entries()):
        print(f"REFUSED: no '{PROTOCOL}' note on the chain. Chain the "
              f"gauntlet protocol note before running for real "
              f"(dry-run is allowed).")
        return 1

    all_specs = [e["payload"] for e in registry.entries()
                 if e["entry_type"] == "strategy_registered"]
    candidates = [s for s in all_specs
                  if states.get(s["strategy_id"]) == "gauntlet"]
    if not candidates:
        print("No strategies in 'gauntlet' state.")
        return 0

    assets = sorted({a for s in all_specs for a in s["universe"]["assets"]})
    bars_by_asset, data_hashes = {}, {}
    for a in assets:
        bars_by_asset[a] = load_bars(args.data_dir, a, "9999-12-31")  # full history
        data_hashes[a] = hashlib.sha256(
            (args.data_dir / f"{a}_1d.csv").read_bytes()).hexdigest()

    # group SR variance needs every sibling's full-run curve (incl. graveyarded)
    group_of = {s["strategy_id"]: s["provenance"]["sibling_group_id"]
                for s in all_specs}
    group_srs: dict[str, list[float]] = {}
    full_results: dict[str, dict] = {}
    for s in all_specs:
        res = run_spec(s, {a: bars_by_asset[a]
                           for a in s["universe"]["assets"]})
        full_results[s["strategy_id"]] = res
        rets = daily_returns_from_curve(res["equity"])
        group_srs.setdefault(group_of[s["strategy_id"]], []).append(
            sharpe(rets))
    group_var = {}
    for g, srs in group_srs.items():
        m = sum(srs) / len(srs)
        group_var[g] = (sum((x - m) ** 2 for x in srs) / (len(srs) - 1)
                       if len(srs) > 1 else 0.0)
    group_n = {g: len(srs) for g, srs in group_srs.items()}

    rows, payloads = [], []
    for s in candidates:
        sid = s["strategy_id"]
        res = full_results[sid]
        stress_res = run_spec(stressed(s),
                              {a: bars_by_asset[a]
                               for a in s["universe"]["assets"]})
        is_t, oos_t = split_trades(res["trades"], args.cutoff)
        _, stress_oos = split_trades(stress_res["trades"], args.cutoff)
        rets = daily_returns_from_curve(res["equity"])
        g = group_of[sid]
        passed, reason, metrics, mc_summary = evaluate_spec(
            is_t, oos_t, stress_oos, rets, group_n[g], group_var[g],
            seed=int(sid, 16) % (2 ** 31))
        rows.append({"sid": sid, "group": g, "passed": passed,
                     "dsr": metrics["deflated_sharpe"]})
        payloads.append((s, oos_t, passed, reason, metrics, mc_summary))
        d = metrics["edge_decay_pct"]
        print(f"{sid}  {'PASS' if passed else 'fail':<4} "
              f"oos_edge={metrics['oos_edge_per_trade']:+.5f}  "
              f"decay={'n/a' if d is None else f'{d:+.1f}%'}  "
              f"p05={metrics['mc_p05_equity']:.3f}  "
              f"ruin={metrics['p_ruin']:.3f}  "
              f"dsr={metrics['deflated_sharpe']:.3f}  "
              f"stress={metrics['cost_stress_net_pnl']:+.4f}"
              + (f"  [{reason}]" if reason else ""))

    quarantine, not_selected = select_survivors(rows)
    n_pass = sum(1 for r in rows if r["passed"])
    if args.dry_run:
        print(f"\nDRY RUN — {len(rows)} evaluated, {n_pass} pass; "
              f"{len(quarantine)} would quarantine, "
              f"{len(not_selected)} sibling_not_selected, "
              f"{len(rows) - n_pass} gate-fail; nothing written.")
        return 0

    n_written = 0
    try:
        # phase 1: all verdicts
        for s, oos_t, passed, reason, metrics, mc_summary in payloads:
            sid = s["strategy_id"]
            group_context = {
                "group": group_of[sid],
                "dsrs": {r["sid"]: r["dsr"] for r in rows
                         if r["group"] == group_of[sid]}}
            bundle = write_gauntlet_artifacts(
                args.artifacts_dir, s, oos_t, mc_summary, metrics,
                args.cutoff, data_hashes, group_context)
            registry.record_verdict(
                sid, "gauntlet", "pass" if passed else "fail", metrics,
                bundle_hash(bundle, names=("oos_trades.csv",
                                           "mc_summary.json",
                                           "config.json")))
            n_written += 1
        # phase 2: state changes
        for s, _, passed, reason, _, _ in payloads:
            sid = s["strategy_id"]
            if not passed:
                registry.record_state_change(sid, "graveyard", reason)
            elif sid in quarantine:
                registry.record_state_change(sid, "quarantine",
                                             "gauntlet pass, group-selected")
            else:
                registry.record_state_change(sid, "graveyard",
                                             "sibling_not_selected")
            n_written += 1
    except BaseException:
        print(f"\nPARTIAL WRITE: {n_written}/{2 * len(payloads)} entries "
              f"chained before failure — run again to see ORPHANED "
              f"diagnostics.", file=sys.stderr)
        raise

    print(f"\n{len(rows)} evaluated: {len(quarantine)} -> quarantine, "
          f"{len(not_selected)} sibling_not_selected, "
          f"{len(rows) - n_pass} gate-fail -> graveyard.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
