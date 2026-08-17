"""Gauntlet battery: eight-gate robustness validation of gauntlet-state
strategies on the 2024+ holdout, with pre-declared sibling selection.

protocol-v4 adds a train-window Sharpe floor, a CSCV overfitting gate and a
plateau gate, and replaces point-winner sibling selection (highest deflated
Sharpe) with neighbourhood-floor selection. See pipeline/plateau.py.

Usage:
    python -m pipeline.gauntlet [--registry registry_log.jsonl]
        [--data-dir data] [--artifacts-dir artifacts]
        [--cutoff 2023-12-31] [--dry-run]

Real runs HARD-REFUSE unless a note starting with PROTOCOL is chained.
Current gates and amendments per docs/2026-08-17-gate-standard-design.md,
the protocol-v4 spec. docs/2026-08-16-gen3-design.md (rev 2) is the
HISTORICAL protocol-v3 record — it retired the deflated-Sharpe gate from
this stage and moved it to quarantine -> live, and describes the
point-winner sibling selection that protocol-v4 itself retires (see
pipeline/plateau.py). docs/2026-08-14-gauntlet-design.md is the HISTORICAL
v1 spec and still describes the deflated-Sharpe gate as gating here, which
has not been true since protocol-v3.
"""
from __future__ import annotations

import sys
import math
import json
import argparse
from pathlib import Path

from .registry import Registry
from .engine import run_spec
from .stats import (moments, sharpe, percentile, psr, expected_max_sharpe,
                    bootstrap_paths, harvey_liu_haircut)
from .cluster import effective_trials
from .pbo import cscv_pbo
from .plateau import annualized_sharpe, select_survivor, qualifies
from .walkforward import walkforward_report
from .regime import regime_by_date, regime_split

PROTOCOL = "gauntlet-protocol-v4"
DECAY_MIN_PCT = -25.0
MC_PATHS = 2000
MC_P05_MIN = 1.0
RUIN_LEVEL = 0.5
P_RUIN_MAX = 0.05
# protocol-v3: DSR_MIN no longer gates THIS stage. It is retained verbatim as
# the threshold for the quarantine -> live gate, computed on the quarantine
# forward record. See docs/2026-08-16-gen3-design.md rev 2.
# SCHEMA.md's gauntlet criterion (d) still lists the deflated Sharpe as a
# gauntlet gate; it is amended by the chained protocol-v3 note, and the SCHEMA
# text itself is updated in this plan's verifier task.
DSR_MIN = 0.95
DEFAULT_CUTOFF = "2023-12-31"

# protocol-v4 additions. SR_FLOOR is knowingly non-binding today — every one of
# the 43 strategies that has ever reached this stage scored at least 0.577 on
# the train window, and all 24 sub-0.4 specs died at the screen. It is adopted
# so the two pipelines read identically, and it will bite if the screen is ever
# loosened.
SR_FLOOR = 0.4
PBO_PASS = 0.20      # < this passes
PBO_KILL = 0.50      # > this kills the whole sibling group
CSCV_SPLITS = 16
PURGE_BARS = 200     # >= the grammar's longest lookback (ma_cross.slow = 200)

# The fixed order in which gates are evaluated and reported. 'dsr' is
# DELIBERATELY ABSENT: protocol-v4 still computes and records the deflated
# Sharpe, but it does not gate entry to paper trading and — new in v4 — it no
# longer ranks siblings either; neighbourhood floor does. Adding it back here
# is a protocol change and needs its own pre-declared chained note.
FAIL_ORDER = ("sharpe_floor", "oos_negative", "edge_decay", "mc_p05",
              "p_ruin", "cost_stress", "pbo", "plateau")


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


def window_vol(bars_by_asset: dict, assets: list[str], lo: str, hi: str) -> float:
    """Equal-weight mean annualized realized volatility across assets, over
    bars with lo < date <= hi. Returns 0.0 when no window has enough bars."""
    vols = []
    for a in assets:
        closes = [b["close"] for b in bars_by_asset[a] if lo < b["date"] <= hi]
        if len(closes) < 3:
            continue
        rets = [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))]
        m = sum(rets) / len(rets)
        vols.append(math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
                    * math.sqrt(365))
    return sum(vols) / len(vols) if vols else 0.0


def evaluate_spec(is_trades: list[dict], oos_trades: list[dict],
                  stress_oos_trades: list[dict], daily_returns: list[float],
                  is_vol: float, oos_vol: float,
                  trials_n: int, trials_sr_var: float, seed: int,
                  group_n: int | None = None, registered_n: int | None = None,
                  train_sharpe: float | None = None,
                  pbo_value: float | None = None,
                  plateau_ok: bool | None = None):
    """Run the robustness gates in FAIL_ORDER. Returns
    (passed, fail_reason|None, metrics, mc_summary).

    protocol-v4 adds three gates whose inputs are computed OUTSIDE this
    function, because two of them are properties of the whole sibling group
    rather than of one spec. `None` on any of the three means "not supplied by
    this caller" and passes, so direct callers written against v3 keep their
    meaning; main() always supplies all three.

    The deflated Sharpe is still computed and recorded, but under v3 it stopped
    gating this stage (it moved to the quarantine -> live gate, where fresh
    forward evidence exists to compute it on) and under v4 it stopped ranking
    siblings too. trials_n is the number of effectively
    independent trials (clusters); registered_n is the raw registration count.
    The edge-decay gate compares VOLATILITY-NORMALIZED per-trade edge, so a
    shrinking opportunity set is not scored as strategy decay."""
    is_c = contributions(is_trades)
    oos_c = contributions(oos_trades)
    is_raw = sum(is_c) / len(is_c) if is_c else 0.0
    oos_raw = sum(oos_c) / len(oos_c) if oos_c else 0.0
    oos_net = compound(oos_c)

    is_edge = is_raw / is_vol if is_vol > 0 else 0.0
    oos_edge = oos_raw / oos_vol if oos_vol > 0 else 0.0
    decay = ((oos_edge - is_edge) / abs(is_edge) * 100
             if is_edge > 0 and oos_vol > 0 else None)

    mc = bootstrap_paths(is_c + oos_c, MC_PATHS, seed, RUIN_LEVEL)
    mc_p05 = percentile(mc["terminals"], 0.05)

    sr_hat = sharpe(daily_returns)
    _, _, skew, kurt = moments(daily_returns)
    sr_star = expected_max_sharpe(trials_n, trials_sr_var)
    dsr = psr(sr_hat, sr_star, len(daily_returns), skew, kurt)

    stress_net = compound(contributions(stress_oos_trades))

    metrics = {
        "is_edge_per_trade": is_edge,
        "oos_edge_per_trade": oos_edge,
        "edge_decay_pct": decay,
        "mc_p05_equity": mc_p05,
        "p_ruin": mc["p_ruin"],
        "deflated_sharpe": dsr,
        "sibling_group_n": group_n if group_n is not None else trials_n,
        "cost_stress_net_pnl": stress_net,
        "trials_n": trials_n,
        # no fallback: registered_n exists to be the honest raw registration
        # count that trials_n no longer is (clusters <= registrations), so an
        # omitted value records null rather than a fabricated number.
        "registered_n": registered_n,
        # recorded so a recorded deflated_sharpe is reproducible from the
        # entry alone: under v3 the variance comes from cluster
        # representatives, which needs the clustering and every strategy's
        # daily returns to recompute.
        "trials_sr_var": trials_sr_var,
        "expected_max_sharpe": sr_star,
        # in-entry discriminator: trials_n means "registered strategies" under
        # v2 and "clusters" under v3, under the same key.
        "protocol": PROTOCOL,
        "is_edge_raw": is_raw,
        "oos_edge_raw": oos_raw,
        "is_vol": is_vol,
        "oos_vol": oos_vol,
        # protocol-v4: the train-window annualized Sharpe the floor gate read,
        # and the sibling group's CSCV overfitting probability. Both are None
        # when the caller did not supply them.
        "train_sharpe": train_sharpe,
        "pbo": pbo_value,
    }
    mc_summary = {"seed": seed, "paths": MC_PATHS,
                  "p05": mc_p05,
                  "p25": percentile(mc["terminals"], 0.25),
                  "p50": percentile(mc["terminals"], 0.50),
                  "p75": percentile(mc["terminals"], 0.75),
                  "p_ruin": mc["p_ruin"], "ruin_level": RUIN_LEVEL}

    # FAIL_ORDER drives the sequence rather than merely documenting it. Both
    # drift directions are closed, and only one of them fails loudly on its
    # own: a gate declared but not computed raises KeyError below, but a gate
    # computed and NOT declared would simply never be evaluated - silently
    # fail-open, the dangerous direction - so the assertion catches it. Every
    # value is an already-computed scalar, so eager evaluation has no cost or
    # side effect.
    checks = {"sharpe_floor": train_sharpe is None or train_sharpe >= SR_FLOOR,
              "oos_negative": oos_net > 0,
              "edge_decay": decay is not None and decay > DECAY_MIN_PCT,
              "mc_p05": mc_p05 > MC_P05_MIN,
              "p_ruin": mc["p_ruin"] < P_RUIN_MAX,
              "cost_stress": stress_net > 0,
              "pbo": pbo_value is None or pbo_value < PBO_PASS,
              "plateau": plateau_ok is not False}
    assert checks.keys() == set(FAIL_ORDER), (
        f"gate battery and FAIL_ORDER disagree: "
        f"computed-not-declared={sorted(checks.keys() - set(FAIL_ORDER))}, "
        f"declared-not-computed={sorted(set(FAIL_ORDER) - checks.keys())}")
    for name in FAIL_ORDER:
        if not checks[name]:
            return False, name, metrics, mc_summary
    return True, None, metrics, mc_summary


def select_survivors(rows: list[dict], grids_by_group: dict,
                     family_by_group: dict) -> tuple[set[str], set[str]]:
    """protocol-v4 selection: per sibling group, the candidate with the
    strongest NEIGHBOURHOOD FLOOR among plateau-qualifying gauntlet passers.

    This function no longer reads any point metric. Under protocol-v3 it sorted
    on -dsr and took the winner, which is precisely the point-winner selection
    the SOP forbids.
    """
    quarantine, not_selected = set(), set()
    for group, family in sorted(family_by_group.items()):
        grids = grids_by_group.get(group, {})
        winner, _detail = select_survivor(family, grids)
        passers = {s["sid"] for s in family if s["gauntlet_passed"]}
        if winner is not None:
            quarantine.add(winner)
        not_selected.update(passers - {winner})
    return quarantine, not_selected


def daily_returns_from_curve(equity: list[tuple[str, float]]) -> list[float]:
    return [equity[i][1] / equity[i - 1][1] - 1
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]


def check_aligned(returns_by_id: dict[str, list[float]]) -> None:
    """Fail closed on ragged return series before clustering.

    cluster.correlation compares series BY INDEX and explicitly leaves
    alignment to the caller. Two things make length data-dependent:
    daily_returns_from_curve drops steps at non-positive equity (shortening
    AND shifting the series of a strategy that blows up), and run_spec builds
    each curve over the shortest common calendar of THAT spec's assets. A
    mismatch would silently correlate different dates, giving a wrong k and a
    wrong recorded deflated Sharpe with no error, so refuse instead."""
    lengths = {sid: len(r) for sid, r in returns_by_id.items()}
    if len(set(lengths.values())) > 1:
        raise ValueError(
            "cannot cluster ragged return series: every strategy must share "
            "one calendar, got "
            + ", ".join(f"{sid}={n}" for sid, n in sorted(lengths.items())))


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

    # clustering needs every sibling's full-run curve (incl. graveyarded)
    group_of = {s["strategy_id"]: s["provenance"]["sibling_group_id"]
                for s in all_specs}
    full_results: dict[str, dict] = {}
    returns_by_id: dict[str, list[float]] = {}
    for s in all_specs:
        sid = s["strategy_id"]
        res = run_spec(s, {a: bars_by_asset[a]
                           for a in s["universe"]["assets"]})
        full_results[sid] = res
        returns_by_id[sid] = daily_returns_from_curve(res["equity"])
    group_n: dict[str, int] = {}
    for g in group_of.values():
        group_n[g] = group_n.get(g, 0) + 1
    registered_n = len(all_specs)

    # protocol-v3: DSR no longer gates this stage, but it still ranks siblings
    # and is still recorded, so it is computed against EFFECTIVELY INDEPENDENT
    # trials. A sibling sweep is one idea at several settings, and pooling
    # structurally different families put real edge dispersion into a term
    # meant to hold sampling noise.
    check_aligned(returns_by_id)
    trials_n, cluster_labels, trials_var = effective_trials(returns_by_id)
    print(f"effective trials: {trials_n} clusters over {registered_n} "
          f"registered strategies")

    # protocol-v4: plateau selection needs every sibling's train-window score,
    # its dense-axis coordinates, and whether it died at the screen on
    # turnover. Screen deaths are read from the chain, not re-derived.
    # `gauntlet_passed` starts False and is filled in below, once this run's
    # verdicts exist.
    screen_tc_fail = {
        e["payload"]["strategy_id"]
        for e in registry.entries()
        if e["entry_type"] == "state_change"
        and e["payload"].get("to") == "graveyard"
        and e["payload"].get("reason") == "trade_count"}

    def train_curve(sid):
        return [(d, v) for d, v in full_results[sid]["equity"]
                if d <= args.cutoff]

    train_sharpe = {s["strategy_id"]: annualized_sharpe(
        train_curve(s["strategy_id"])) for s in all_specs}

    from .composer import SWEEPABLE_TYPES
    from .blocks import BLOCK_TYPES
    family_by_group, grids_by_group = {}, {}
    for s in all_specs:
        sid, g = s["strategy_id"], s["provenance"]["sibling_group_id"]
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
             "gauntlet_passed": False})

    # Prune axes that do not actually vary within a group, or a fixed parameter
    # generates phantom neighbours: a whole family sitting at atr_len=14 would
    # otherwise be read as a swept axis with every sibling its own island.
    for g, fam in family_by_group.items():
        varying = {a for a in grids_by_group.get(g, {})
                   if len({s["axes"].get(a) for s in fam}) > 1}
        grids_by_group[g] = {a: v for a, v in grids_by_group.get(g, {}).items()
                             if a in varying}
        for s in fam:
            s["axes"] = {a: v for a, v in s["axes"].items() if a in varying}

    # PBO over the TRAIN window only — the 2024+ holdout has been consumed
    # three times already and protocol-v4 does not consume it a fourth. The
    # matrix includes EVERY sibling, screen deaths included; computing it over
    # passers only would filter on performance and understate overfitting.
    pbo_by_group = {}
    for g, fam in family_by_group.items():
        series = {s["sid"]: daily_returns_from_curve(train_curve(s["sid"]))
                  for s in fam}
        pbo_by_group[g] = cscv_pbo(series, s=CSCV_SPLITS)
        v = pbo_by_group[g]["pbo"]
        print(f"  PBO {g}: "
              f"{'n/a — ' + pbo_by_group[g]['reason'] if v is None else f'{v:.3f}'}"
              f"  ({pbo_by_group[g]['n_configs']} configs)")
    killed_groups = {g for g, r in pbo_by_group.items()
                     if r["pbo"] is not None and r["pbo"] > PBO_KILL}
    for g in sorted(killed_groups):
        print(f"  PBO FAMILY KILL: {g} at {pbo_by_group[g]['pbo']:.3f} > {PBO_KILL}")

    rows, payloads = [], []
    for s in candidates:
        sid = s["strategy_id"]
        res = full_results[sid]
        stress_res = run_spec(stressed(s),
                              {a: bars_by_asset[a]
                               for a in s["universe"]["assets"]})
        is_t, oos_t = split_trades(res["trades"], args.cutoff)
        _, stress_oos = split_trades(stress_res["trades"], args.cutoff)
        rets = returns_by_id[sid]
        g = group_of[sid]
        assets = s["universe"]["assets"]
        is_vol = window_vol(bars_by_asset, assets, "", args.cutoff)
        oos_vol = window_vol(bars_by_asset, assets, args.cutoff, "9999-12-31")
        ok_plateau, _reason = qualifies(
            next(x for x in family_by_group[g] if x["sid"] == sid),
            family_by_group[g], grids_by_group.get(g, {}))
        passed, reason, metrics, mc_summary = evaluate_spec(
            is_t, oos_t, stress_oos, rets, is_vol, oos_vol,
            trials_n, trials_var, seed=int(sid, 16) % (2 ** 31),
            group_n=group_n[g], registered_n=registered_n,
            train_sharpe=train_sharpe[sid],
            pbo_value=pbo_by_group[g]["pbo"],
            plateau_ok=ok_plateau)
        # A PBO family kill is recorded on EVERY member of the group, but it
        # only becomes the fail REASON for a strategy that had nothing else
        # wrong. Six gates precede 'pbo' in FAIL_ORDER, so overwriting `reason`
        # unconditionally would bury a strategy's own first failure — in an
        # append-only chain, permanently. The flag is written on the normal
        # path too, so the key is always present rather than sometimes-missing.
        metrics["pbo_family_kill"] = g in killed_groups
        if metrics["pbo_family_kill"] and passed:
            passed, reason = False, "pbo_family_kill"

        # Corroborating numbers: RECORDED, never gating. Each carries the
        # WINDOW it was computed over, because a verdict is a public
        # append-only record and a reader must not have to infer that.
        train_dates = [d for d, _ in full_results[sid]["equity"]
                       if d <= args.cutoff]
        metrics["haircut"] = dict(
            harvey_liu_haircut(train_sharpe[sid] or 0.0,
                               t_years=len(train_dates) / 365.0,
                               n_trials=trials_n),
            window="train")
        metrics["walkforward"] = dict(
            walkforward_report(
                [t for t in res["trades"] if t["entry_date"] <= args.cutoff],
                train_dates, n_folds=3, purge_bars=PURGE_BARS),
            window="train")
        btc = bars_by_asset.get("BTCUSD") or bars_by_asset[sorted(bars_by_asset)[0]]
        # `buckets` is nested rather than flattened alongside `window`:
        # regime.BUCKETS is a closed vocabulary and the map's values are all
        # per-bucket stat dicts, so a bare string sibling would break anyone
        # iterating it.
        metrics["regime"] = {"window": "oos",
                             "buckets": regime_split(oos_t, regime_by_date(btc))}
        rows.append({"sid": sid, "group": g, "passed": passed,
                     "dsr": metrics["deflated_sharpe"]})
        payloads.append((s, oos_t, passed, reason, metrics, mc_summary))
        d = metrics["edge_decay_pct"]
        print(f"{sid}  {'PASS' if passed else 'fail':<4} "
              f"oos_edge={metrics['oos_edge_per_trade']:+.5f}  "
              f"decay={'n/a' if d is None else f'{d:+.1f}%'}  "
              f"p05={metrics['mc_p05_equity']:.3f}  "
              f"ruin={metrics['p_ruin']:.3f}  "
              f"[info dsr={metrics['deflated_sharpe']:.3f}]  "
              f"stress={metrics['cost_stress_net_pnl']:+.4f}"
              + (f"  [{reason}]" if reason else ""))

    passed_by_sid = {r["sid"]: r["passed"] for r in rows}
    for fam in family_by_group.values():
        for s in fam:
            s["gauntlet_passed"] = passed_by_sid.get(s["sid"], False)
    quarantine, not_selected = select_survivors(
        rows, grids_by_group, family_by_group)
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
                         if r["group"] == group_of[sid]},
                "effective_trials": trials_n,
                "registered_n": registered_n,
                "cluster_labels": cluster_labels}
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
