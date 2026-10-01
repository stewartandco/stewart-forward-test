"""Gauntlet battery: six-gate robustness validation of gauntlet-state
strategies on the 2024+ holdout. Every gate is STANDALONE.

protocol-v6 (chained at registry entry 2514) encodes one principle: each
individual edge is tested and judged on its own evidence, regardless of how
similar it is to another. It removed the three mechanisms that decided a
strategy's fate on something else -- one-winner-per-group selection, the PBO
gate and its family kill, and the plateau gate -- and kept all three as
RECORDED numbers. Every gate passer now proceeds to quarantine, and the
sibling_not_selected transition is retired. No gate reads a sibling, a group, a
neighbour, a grid position or a family statistic; reintroducing any of those
contradicts v6 and needs its own pre-declared chained note. The evidence behind
the removals is chained at entries 2503, 2511 and 2513.

protocol-v4 adds a train-window Sharpe floor, a CSCV overfitting gate and a
plateau gate, and replaces point-winner sibling selection (highest deflated
Sharpe) with neighbourhood-floor selection. See pipeline/plateau.py.

protocol-v5 amends ONE of those, the PBO gate, and leaves every other gate at
v4's threshold and FAIL_ORDER position. v4's fixed 0.20 / 0.50 lines assumed a
no-skill null of about 0.5; that is false in this implementation at small ODD
family sizes, where the median rank lands exactly on the omega <= 0.5 boundary
and pushes the null to 0.600 at five configs -- above v4's own kill line, on
which every one of generation 4's six families sat. v5 counts that boundary
tie as a half event, counts DISTINCT configurations rather than registered
siblings and fails closed below four of them, and replaces the fixed lines
with a test against each family's own permutation null. Evidence is chained at
registry entry 2511, the protocol at 2512.

Usage:
    python -m pipeline.gauntlet [--registry registry_log.jsonl]
        [--data-dir data] [--artifacts-dir artifacts]
        [--cutoff 2023-12-31] [--pbo-null-draws 50] [--no-perturb]
        [--dry-run]

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

import os
import sys
import math
import json
import time
import hashlib
import argparse
from pathlib import Path
import numpy as np
import gc
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import cells
from . import deadline as _deadline
from . import simcache
from .simcache import Series
from .registry import Registry
from .engine import run_spec, ENGINE_REV, exit_reason_counts
from .stats import (moments, sharpe, percentile, psr, expected_max_sharpe,
                    bootstrap_paths, harvey_liu_haircut)
from .cluster import effective_trials
from .pbo import (cscv_pbo, distinct_configs, permutation_null,
                   percentile_of)
# select_survivor is deliberately NOT imported: protocol-v6 retired
# selection, and `qualifies` is kept only to RECORD the outcome.
from .plateau import annualized_sharpe, qualifies, TRADING_DAYS
from .perturb import sensitivity
from .walkforward import walkforward_report
from .regime import regime_by_date, regime_split
from .gauntlet_core import (FAIL_ORDER, SR_FLOOR, DECAY_MIN_PCT, MC_PATHS,
                            MC_P05_MIN, RUIN_LEVEL, P_RUIN_MAX, DEFAULT_CUTOFF,
                            PURGE_BARS, _date_le, split_trades, contributions,
                            compound, window_vol, _spec_bars,
                            daily_returns_with_dates,
                            _annualized_sharpe_from_returns, era_summary,
                            stressed, _benchmark_relative,
                            write_gauntlet_artifacts, evaluate_gates,
                            # pool sizing lives in the core so the worker's
                            # verdict path never imports this module
                            GAUNTLET_PRIOR_S_PER_CANDIDATE,
                            GAUNTLET_CHUNK_PER_WORKER, WORKER_COMMIT_MB,
                            WORKER_COMMIT_HEADROOM_MB, WORKER_BLAS_ENV,
                            worker_count, available_commit_mb, worker_env)

PROTOCOL = "gauntlet-protocol-v6"
# protocol-v3: DSR_MIN no longer gates THIS stage. It is retained verbatim as
# the threshold for the quarantine -> live gate, computed on the quarantine
# forward record. See docs/2026-08-16-gen3-design.md rev 2.
# SCHEMA.md's gauntlet criterion (d) still lists the deflated Sharpe as a
# gauntlet gate; it is amended by the chained protocol-v3 note, and the SCHEMA
# text itself is updated in this plan's verifier task.
DSR_MIN = 0.95

# protocol-v5 withdraws v4's fixed 0.20 / 0.50 lines. They presupposed that a
# family with no persistent skill differences scores about 0.5, which is false
# in this implementation at small odd family sizes and only approximate
# anywhere. The observed PBO is now tested against a null computed for THAT
# family by permutation, so the reference is calibrated to its size, parity,
# sibling correlation and series length instead of assumed. Evidence: registry
# entry 2511. Argument: entry 2512.
PBO_MIN_DISTINCT = 4     # below this the gate cannot see; it FAILS, never passes
PBO_PASS_PCTILE = 0.05   # <= this percentile of its own null passes
PBO_KILL_PCTILE = 0.95   # >= this kills the whole sibling group
# SP4 Task P3: 200 -> 50. The null is a RECORDED-NOT-GATED statistic (chain
# entries 2513-2515: v6 kept it as evidence, not a decision), and 200 draws of
# CSCV per sibling group was measured at ~10h at eq-gen1 scale -- the single
# largest remaining cost after P1/P2/P4. 50 draws is still enough resolution
# for the 5%/95% percentile bands this reads (PBO_PASS_PCTILE/PBO_KILL_PCTILE)
# and a deliberate deep run can still ask for 200 via --pbo-null-draws.
PBO_NULL_DRAWS = 50
CSCV_SPLITS = 16

# Below this many shared calendar days, an "intersection" is not a common
# history worth clustering on -- it is noise dressed as a trial count. A real
# dry-run generation hit this: 12 fx pairs with genuinely different real
# inception dates (Bretton-Woods-era pairs from 1971 through EUR from its
# 1999 launch) produced ragged calendars that would otherwise cluster on
# whatever few days happen to overlap the shortest-lived pair.
MIN_TRIALS_COMMON_DAYS = 100



# PBO (after the candidates): one permutation null per LIVE family, pure
# Python, ~50 draws -- the 2026-09-03 15:30 cycle spent 108 min on 325 of 366
# families and was killed by the PT4H wall at group 325 with every verdict
# unwritten. The loop below asks the budget before EACH null (prior below
# until the first null has been timed, the measured mean after) and defers
# the CANDIDATES of a family whose null does not fit: they stay in 'gauntlet'
# state with no verdict, exactly like a candidate the chunk loop never
# started, and the next run picks them up. Families already measured keep
# their verdicts. A family is judged in one pass or not at all.
PBO_NULL_PRIOR_S = 60.0
def private_commit_mb() -> int | None:
    """This process's private commit (Windows PROCESS_MEMORY_COUNTERS_EX
    PrivateUsage -- the figure the Resource-Exhaustion-Detector reports).
    None where unknowable. Diagnostic only."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32)] + [
                (n, ctypes.c_size_t) for n in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                    "PrivateUsage")]
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC),
                                               ctypes.c_uint32]
        m = _PMC()
        m.cb = ctypes.sizeof(m)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb):
            return None
        return int(m.PrivateUsage // 2**20)
    except Exception:
        return None


GAUNTLET_RESERVE_FRAC = 0.25           # of the budget left when candidates begin
GAUNTLET_RESERVE_MIN_S = 60.0          # ...never less than this, for PBO + writes


def _pbo_metrics_fields(pbo_status: dict | None) -> dict:
    """The seven verdict-metrics keys derived from a PBO group status dict.

    Factored out (SP4 Task P2/P3) so evaluate_spec's inline computation and
    run()'s post-hoc patch (candidates are evaluated in a worker process
    BEFORE this pass's own gate-passing groups are known -- see the P3
    dead-group note at the PBO loop below) read the SAME seven keys from the
    SAME dict shape and can never drift apart. `None` (no status supplied,
    or a bare `{}`) records every field as None, exactly as evaluate_spec
    always has for a caller that omits pbo_status.

    Batch review rider (SP4): `pbo_verdict` (one of "not_measured_dead_group",
    "underpowered", "kill", "pass", "fail") used to exist ONLY in the printed
    PBO line -- a chained verdict recorded every number the label was derived
    from but never the label itself, so a reader of the chain alone (not the
    run's own stdout) could not tell "not_measured_dead_group" from
    "underpowered" even though they mean different things (see run()'s own
    comment at the PBO loop: one means the family was never even asked the
    question, the other means the null was attempted or considered but the
    family cannot support one). Adding it here is additive-only -- every
    existing consumer of this dict's other six keys is unaffected."""
    s = pbo_status or {}
    return {"pbo": s.get("pbo"),
            "pbo_n_distinct": s.get("n_distinct"),
            "pbo_percentile": s.get("percentile"),
            "pbo_null_p05": s.get("null_p05"),
            "pbo_null_p95": s.get("null_p95"),
            "pbo_null_draws": s.get("null_draws"),
            "pbo_verdict": s.get("verdict")}


def evaluate_spec(is_trades: list[dict], oos_trades: list[dict],
                  stress_oos_trades: list[dict], daily_returns: list[float],
                  is_vol: float, oos_vol: float,
                  trials_n: int, trials_sr_var: float, seed: int,
                  group_n: int | None = None, registered_n: int | None = None,
                  train_sharpe: float | None = None,
                  pbo_status: dict | None = None,
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
    passed, reason, metrics, mc_summary = evaluate_gates(
        is_trades, oos_trades, stress_oos_trades, is_vol, oos_vol,
        seed, train_sharpe)
    sr_hat = sharpe(daily_returns)
    _, _, skew, kurt = moments(daily_returns)
    sr_star = expected_max_sharpe(trials_n, trials_sr_var)
    metrics.update({
        "deflated_sharpe": psr(sr_hat, sr_star, len(daily_returns), skew, kurt),
        "sibling_group_n": group_n if group_n is not None else trials_n,
        "trials_n": trials_n,
        "registered_n": registered_n,
        "trials_sr_var": trials_sr_var,
        "expected_max_sharpe": sr_star,
        "protocol": PROTOCOL,
        **_pbo_metrics_fields(pbo_status),
        "plateau_ok": plateau_ok,
    })
    return passed, reason, metrics, mc_summary


def select_survivors(rows: list[dict], grids_by_group: dict,
                     family_by_group: dict) -> tuple[set[str], set[str]]:
    """protocol-v6: there is no selection. Every strategy that passed the gate
    battery proceeds to quarantine.

    Under v3 this took the highest deflated Sharpe in each sibling group; under
    v4 and v5 it took the strongest neighbourhood floor. Both discarded
    strategies that had passed every gate on their own evidence because a
    SIMILAR SIBLING scored higher -- 7 of them, all in generation 3, recorded on
    the chain as sibling_not_selected. v6's principle is that each individual
    edge is tested and judged standalone, and a sibling's score is not evidence
    about this strategy, so the second element of the returned pair is now
    always empty and the sibling_not_selected transition is retired.

    grids_by_group and family_by_group are still accepted so callers and their
    tests are unchanged, and they are deliberately UNUSED: a future reader
    should see that the group state is available here and consulted by nothing.
    """
    del grids_by_group, family_by_group        # v6: group state cannot decide
    return {r["sid"] for r in rows if r["passed"]}, set()


def daily_returns_from_curve(equity: list[tuple[str, float]]) -> list[float]:
    return [equity[i][1] / equity[i - 1][1] - 1
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]


def intersect_returns(dated_by_id: dict[str, list[tuple[str, float]]]
                      ) -> tuple[dict[str, list[float]], list[str]]:
    """Align every series onto the dates common to ALL of them, sorted.

    Returns (returns_by_id, common_dates). Every output series has the same
    length by construction, so check_aligned passes trivially and
    effective_trials clusters on dates every strategy actually shares --
    the INTERSECTION calendar (spec s10.6), not a native one that would
    silently correlate different dates index-by-index."""
    # Arrays since 2026-09-03 (docs/plans/2026-09-03-simcache-arrays.md): the
    # same intersection over int32 day ordinals, the same gather. The legacy
    # gather was `dict(rows)[d]`, where a date repeated within one series
    # (an intraday spec's per-bar steps share a date) resolved to the LAST
    # row -- searchsorted(side="right") - 1 over a stable sort keeps that.
    series = {sid: (rows if isinstance(rows, Series) else Series.from_pairs(rows))
              for sid, rows in dated_by_id.items()}
    common = None
    for ser in series.values():
        dates = np.unique(ser.dates)
        common = dates if common is None else np.intersect1d(common, dates,
                                                            assume_unique=True)
    if common is None:
        common = np.zeros(0, dtype=np.int32)
    out = {}
    for sid, ser in series.items():
        order = np.argsort(ser.dates, kind="stable")
        sorted_dates = ser.dates[order]
        idx = np.searchsorted(sorted_dates, common, side="right") - 1
        out[sid] = ser.rets[order][idx]
    common_sorted = [simcache._iso(o) for o in common.tolist()]
    return out, common_sorted


def _calendar_range_overlap_days(dated_by_id: dict[str, list[tuple[str, float]]]) -> int:
    """Naive overlap, in days, of every series' own [min, max] date SPAN
    (not its actual trading days -- a full calendar-day count between the
    earliest shared start and the latest shared end).

    This is a DIAGNOSTIC heuristic only, not a clustering input: a large
    positive value here alongside a near-zero actual intersection is the
    signature of a key-format mismatch (string date keys that never compare
    equal despite covering the same calendar), the exact shape of the
    2026-08-24 real-run defect. Genuinely disjoint calendars (a strategy
    retired in 2010, another that only starts in 2020) score at or below
    zero here too, so this alone never decides anything -- it only tells
    _raise_too_short_intersection whether to add a breadcrumb."""
    spans = []
    for rows in dated_by_id.values():
        if not rows:
            return 0
        dates = [str(d)[:10] for d, _ in rows]
        spans.append((min(dates), max(dates)))
    latest_start = max(s for s, _ in spans)
    earliest_end = min(e for _, e in spans)
    if latest_start > earliest_end:
        return 0
    from datetime import date as _date
    return (_date.fromisoformat(earliest_end)
            - _date.fromisoformat(latest_start)).days + 1


def _raise_too_short_intersection(
        dated_by_id: dict[str, list[tuple[str, float]]],
        common_dates: list[str]) -> None:
    """Refuse to cluster on an intersection shorter than
    MIN_TRIALS_COMMON_DAYS, naming every series' own date span so the reader
    does not have to re-derive what went wrong from a bare day count.

    If every series' own [min, max] span overlaps generously
    (_calendar_range_overlap_days >= MIN_TRIALS_COMMON_DAYS) even though the
    actual intersection came up empty or nearly so, that combination is the
    signature of a key-format mismatch rather than a genuine data gap --
    named as a question, not a diagnosis, since daily_returns_with_dates
    already normalises every key it builds to date-only and should make this
    unreachable in practice. Kept as a cheap breadcrumb for whoever hits the
    next one of these."""
    def _span(rows):
        return (f"{min(d for d, _ in rows)}..{max(d for d, _ in rows)} "
               f"({len(rows)}d)") if rows else "(no returns)"
    detail = "; ".join(f"{sid}={_span(rows)}"
                       for sid, rows in sorted(dated_by_id.items()))
    hint = ""
    range_overlap = _calendar_range_overlap_days(dated_by_id)
    if range_overlap >= MIN_TRIALS_COMMON_DAYS:
        hint = (f" Each series' own date span overlaps by ~{range_overlap} "
               f"calendar days even though the actual intersection is only "
               f"{len(common_dates)} -- key-format mismatch? (one builder's "
               f"date strings may carry a time suffix or other formatting "
               f"the other's does not; daily_returns_with_dates normalises "
               f"every key it builds to date-only, so this should not "
               f"happen unless something bypassed it).")
    raise ValueError(
        f"the intersection calendar across {len(dated_by_id)} "
        f"registered strategies is only {len(common_dates)} day(s) "
        f"-- too short to cluster on (minimum "
        f"{MIN_TRIALS_COMMON_DAYS}). Ragged calendars or a mismatched "
        f"class pairing left almost no shared history: {detail}.{hint}")


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


BENCHMARK_BASIS = {
    "fx": "price returns, carry excluded on both sides",
    # crypto is DORMANT until SP5 Phase 3 flips CLASSES["crypto"]["benchmark"];
    # declared now so the flip is a one-line cells.py change later.
    "crypto": "price returns, staking/funding yield excluded on both sides",
}
_DEFAULT_BASIS = "price returns, dividends excluded on both sides"


def _as_list(rets) -> list[float]:
    """A worker payload carries plain data only (see _evaluate_candidate):
    an array row from the clustering pass becomes the list it stands for --
    the same float64s, so the worker's sums are unchanged."""
    return rets.tolist() if hasattr(rets, "tolist") else list(rets)


def _candidate_payload(s: dict, spec_bars: dict, res: dict,
                       rets: list[float], group_n_val: int, registered_n: int,
                       train_sharpe_val: float | None, trials_n: int,
                       trials_var: float, sibling: dict, family: list[dict],
                       grids: dict, cutoff: str, perturb: bool,
                       trials_alignment: str, trials_common_days: int | None,
                       sim_cache_hits: int, sim_cache_misses: int) -> dict:
    """Build ONE candidate's worker payload: everything `_evaluate_candidate`
    needs, and nothing it does not (Registry, Path, open file handles never
    belong here -- see that function's own docstring on why).

    `res` is this candidate's OWN already-simulated (trades, equity) --
    computed once, serially, in run()'s registry-wide clustering pass (SP4
    Task P1) -- passed through rather than re-run inside the worker, so P2
    does not pay for the base simulation twice. `spec_bars` (this spec's own
    cell's bars) is still needed in the worker regardless, for the stress
    re-run and for perturbation's re-runs of nudged neighbours."""
    sid = s["strategy_id"]
    return {
        "spec": s, "spec_bars": spec_bars,
        "res_trades": res["trades"], "res_equity": res["equity"],
        # D15: the engine's own open_at_end for this candidate (run_spec
        # metrics). The full-sample run ends inside OOS, so "the book ended
        # open" and "the OOS book ended open" are one fact. RECORDED only.
        "res_open_at_end": bool(res["metrics"]["open_at_end"]),
        "rets": rets, "group_n": group_n_val, "registered_n": registered_n,
        "train_sharpe": train_sharpe_val, "trials_n": trials_n,
        "trials_var": trials_var, "seed": int(sid, 16) % (2 ** 31),
        "sibling": sibling, "family": family, "grids": grids,
        "cutoff": cutoff, "perturb": perturb,
        "trials_alignment": trials_alignment,
        "trials_common_days": trials_common_days,
        "sim_cache_hits": sim_cache_hits, "sim_cache_misses": sim_cache_misses,
    }


def _evaluate_candidate(payload: dict) -> dict:
    """Evaluate ONE candidate's full gate battery + every corroborating
    metric, standalone (protocol-v6's own founding principle -- see the
    module docstring -- made this split possible: nothing here reads a
    sibling, a group, or another candidate).

    SP4 Task P2: this is the ProcessPoolExecutor worker. Windows uses
    'spawn', which re-imports this module fresh in every worker process and
    pickles the CALL rather than sharing memory, so this function must be a
    plain module-level callable taking only picklable plain data (dicts,
    lists, str, float, int -- exactly what `_candidate_payload` builds) and
    returning the same. No Registry, no Path-backed file handle, no closure
    over run()'s locals crosses that boundary.

    PBO is deliberately NOT threaded through here. Task P3 gates the
    (expensive) permutation null on whether at least one candidate in a
    sibling group passed ITS OWN gate battery, which is only knowable once
    every candidate's base verdict exists -- a fact this function, evaluating
    one candidate in isolation, cannot see. It therefore always evaluates
    with `pbo_status=None` (every pbo_* metric records None, exactly as
    evaluate_spec already does for any caller that omits it), and run()
    patches the real pbo_status and the pbo_family_kill override into the
    returned metrics dict afterwards, once every candidate's base result is
    in and the live/dead groups are known. See run()'s PBO section.

    Self-perturbation (`sensitivity`) needs a `score_fn` closure over this
    candidate's OWN `spec_bars` and `cutoff` -- for a pre-P2 in-process loop
    that closure lived in run() itself; here it is defined and consumed
    entirely INSIDE this one worker call, so it never has to be pickled.
    """
    s = payload["spec"]
    spec_bars = payload["spec_bars"]
    cutoff = payload["cutoff"]
    sid = s["strategy_id"]
    g = s["provenance"]["sibling_group_id"]

    stress_res = run_spec(stressed(s), spec_bars)
    is_t, oos_t = split_trades(payload["res_trades"], cutoff)
    _, stress_oos = split_trades(stress_res["trades"], cutoff)
    assets = s["universe"]["assets"]
    is_vol = window_vol(spec_bars, assets, "", cutoff)
    oos_vol = window_vol(spec_bars, assets, cutoff, "9999-12-31")
    ok_plateau, _reason = qualifies(payload["sibling"], payload["family"],
                                    payload["grids"])
    passed, reason, metrics, mc_summary = evaluate_spec(
        is_t, oos_t, stress_oos, payload["rets"], is_vol, oos_vol,
        payload["trials_n"], payload["trials_var"], seed=payload["seed"],
        group_n=payload["group_n"], registered_n=payload["registered_n"],
        train_sharpe=payload["train_sharpe"],
        pbo_status=None,      # patched by run() after the whole pool returns
        plateau_ok=ok_plateau)

    # Clustering alignment + sim-cache counters (spec s10.6 / SP4 Task P1):
    # run-level facts, identical for every candidate of this pass, computed
    # once in run() before the pool was even submitted.
    metrics["trials_alignment"] = payload["trials_alignment"]
    metrics["trials_common_days"] = payload["trials_common_days"]
    metrics["sim_cache"] = {"hits": payload["sim_cache_hits"],
                            "misses": payload["sim_cache_misses"]}

    spec_class = s["universe"].get("asset_class", "crypto")
    eras = cells.CLASSES.get(spec_class, {}).get("eras", ())
    if eras:
        metrics["era_summary"] = era_summary(payload["res_trades"], eras)

    # D15: RECORDED, NOT GATED (design s4; same doctrine as benchmark_relative
    # below) -- why the IS and OOS trades closed, and whether the book ended
    # with a position still open (marked to market in equity, never a closed
    # trade). The payload carries ONE trade list (res_trades); is_t / oos_t
    # are its split at the cutoff, made above, and these counts are over
    # exactly those two lists. exit_reason_counts is the engine's own helper,
    # so a verdict's figures and run_spec's `exit_reasons` are one formula.
    metrics["exit_reasons_is"] = exit_reason_counts(is_t)
    metrics["exit_reasons_oos"] = exit_reason_counts(oos_t)
    metrics["open_at_end"] = bool(payload["res_open_at_end"])

    # B1: RECORDED, NOT GATED (see _benchmark_relative's own docstring and
    # the addendum's pre-registration). strategy_net is the SAME figure the
    # oos_negative gate above just read -- evaluate_spec computes it as
    # compound(contributions(oos_trades)) and never returns it, so it is
    # reproduced here with the exact same two functions on the exact same
    # oos_t list, never a different formula. None (a class that does not
    # declare benchmark: "self") writes no key at all.
    benchmark_relative = _benchmark_relative(
        s, spec_bars, compound(contributions(oos_t)), cutoff)
    if benchmark_relative is not None:
        metrics["benchmark_relative"] = benchmark_relative

    train_dates = [d for d, _ in payload["res_equity"] if _date_le(d, cutoff)]
    metrics["haircut"] = dict(
        harvey_liu_haircut(payload["train_sharpe"] or 0.0,
                           t_years=len(train_dates) / 365.0,
                           n_trials=payload["trials_n"]),
        window="train")
    metrics["walkforward"] = dict(
        walkforward_report(
            [t for t in payload["res_trades"] if _date_le(t["entry_date"], cutoff)],
            train_dates, n_folds=3, purge_bars=PURGE_BARS),
        window="train")

    if payload["perturb"]:
        def _perturbed_score(pspec):
            r = run_spec(pspec, spec_bars)
            return annualized_sharpe([(d, v) for d, v in r["equity"]
                                      if _date_le(d, cutoff)])
        metrics["perturbation"] = sensitivity(
            s, payload["train_sharpe"], _perturbed_score, dense_only=True)
    else:
        metrics["perturbation"] = None

    btc = spec_bars.get("BTCUSD") or spec_bars[sorted(spec_bars)[0]]
    metrics["regime"] = {"window": "oos",
                         "buckets": regime_split(oos_t, regime_by_date(btc))}

    return {"sid": sid, "group": g, "passed": passed, "reason": reason,
            "metrics": metrics, "mc_summary": mc_summary, "oos_trades": oos_t}


def _run_candidates(payloads: list[dict], max_workers: int,
                    progress_every: int = 25, *, offset: int = 0,
                    total: int | None = None) -> dict[str, dict]:
    """Evaluate every candidate (SP4 Task P2), returning {sid: result}.

    Each candidate is evaluated STANDALONE, so nothing here depends on the
    order payloads are submitted in or the order results complete in --- but
    that also means the CALLER, not this function, owns merge order: run()
    always walks its own `candidates` list (registry order) and looks results
    up here by sid, never iterates this dict directly, so a chained verdict's
    position never depends on which worker happened to finish first.

    max_workers <= 1 (or a single candidate) runs every payload in THIS
    process, in list order -- the serial reference path P2's same-answer
    test compares the pool path against. Above that, ProcessPoolExecutor
    spawns worker processes (Windows: 'spawn', re-importing this module in
    each) and `_evaluate_candidate` is the only thing sent across that
    boundary, by design (see its own docstring).
    """
    n = len(payloads)
    results: dict[str, dict] = {}
    done = 0
    # Progress is reported against the WHOLE run, not this chunk: run() feeds
    # chunks of workers x GAUNTLET_CHUNK_PER_WORKER, and "evaluated 24/24"
    # twenty times over (2026-09-03) hid that ~974 candidates were in flight.
    grand_total = n if total is None else total

    def _tick():
        nonlocal done
        done += 1
        if (offset + done) % progress_every == 0 or done == n:
            print(f"[gauntlet] evaluated {offset + done}/{grand_total} candidates",
                  flush=True)

    if max_workers <= 1 or n <= 1:
        for p in payloads:
            r = _evaluate_candidate(p)
            results[r["sid"]] = r
            _tick()
        return results

    with worker_env(), ProcessPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_evaluate_candidate, p) for p in payloads]
        for fut in as_completed(futures):
            r = fut.result()
            results[r["sid"]] = r
            _tick()
    return results


def registry_series(all_specs: list[dict], bars_by_cell: dict,
                    data_hashes: dict[str, str], cache,
                    candidate_sids: set[str],
                    full_results: dict[str, dict] | None = None):
    """The registry-wide re-simulation, moved out of run() unchanged
    (Build 2a task 7) so gauntlet_stats.py runs the SAME pass.

    Returns (dated_returns_by_sid, equity_len_by_sid, sim_cache_hits,
    sim_cache_misses). A candidate (sid in `candidate_sids`) is always
    simulated fresh and its full run_spec result is stored in
    `full_results` when the caller passes a dict (run() needs the
    trades); every other spec is served from `cache` when it can be."""
    if full_results is None:
        full_results = {}
    dated_returns_by_sid: dict[str, Series] = {}
    equity_len_by_sid: dict[str, int] = {}
    sim_cache_hits = sim_cache_misses = 0
    for s in all_specs:
        sid = s["strategy_id"]
        if sid in candidate_sids:
            res = run_spec(s, _spec_bars(bars_by_cell, s))
            full_results[sid] = res
            dated_returns_by_sid[sid] = Series.from_pairs(
                daily_returns_with_dates(res["equity"]))
            equity_len_by_sid[sid] = len(res["equity"])
            continue
        tf = s["universe"].get("timeframe", "1d")
        data_shas = {a: data_hashes[cells.cell_id(a, tf)]
                    for a in s["universe"]["assets"]}
        # SP4 batch review rider: the cached series also depends on the
        # RESOLVED periods_per_year (engine.run_spec derives it the same way
        # -- cells.SESSION_PERIODS.get(session, 365) -- and it feeds
        # vol_target's realized-vol sizing), so it must be part of the key or
        # a SESSION_PERIODS edit would silently serve a stale series instead
        # of missing. See simcache.cache_key's own docstring.
        periods_per_year = cells.SESSION_PERIODS.get(
            s["universe"].get("session"), 365)
        key = simcache.cache_key(sid, data_shas, ENGINE_REV, periods_per_year)
        hit = cache.get(key)
        if hit is not None:
            dated_returns_by_sid[sid] = hit["series"]
            equity_len_by_sid[sid] = hit["equity_len"]
            sim_cache_hits += 1
        else:
            res = run_spec(s, _spec_bars(bars_by_cell, s))
            series = Series.from_pairs(daily_returns_with_dates(res["equity"]))
            dated_returns_by_sid[sid] = series
            equity_len_by_sid[sid] = len(res["equity"])
            cache.put(key, series, len(res["equity"]))
            sim_cache_misses += 1
    return dated_returns_by_sid, equity_len_by_sid, sim_cache_hits, sim_cache_misses


def cluster_registry(dated_returns_by_sid: dict, equity_len_by_sid: dict,
                     all_specs: list[dict]) -> dict:
    """Alignment + effective trials over the registry-wide series, moved
    out of run() unchanged (Build 2a task 7) so gauntlet_stats.py clusters
    by the SAME method. Returns trials_n, cluster_labels, trials_var,
    trials_alignment, trials_common_days, registered_n, and returns_by_id
    (the aligned series the clustering and the deflated Sharpe read)."""
    # A run whose registered specs are ALREADY on one shared calendar (today:
    # every real crypto chain, which has only ever registered the legacy
    # BTCUSD/ETHUSD pair -- same start date, same length) keeps the exact
    # prior arithmetic: native per-spec calendars, no intersection, so this
    # stays byte-identical and regression-covered by test_gauntlet.py.
    #
    # Two things make that assumption false and must intersect FIRST, before
    # check_aligned ever runs, not after it raises:
    #   - classes_present > 1: a 24x7 crypto calendar pooled with a 5-day fx
    #     calendar (spec s10.6);
    #   - ragged (real dry-run finding, 2026-08-24): registered specs on the
    #     SAME class can still have genuinely different calendars. 12 fx
    #     pairs each start on their own real inception date (most G10 pairs
    #     1971, EUR 1999, ZAR/SGD 1980/81, MXN 1993) with NO duplicate dates
    #     anywhere (verified: every pinned CSV's row count equals its unique
    #     date count) -- the raggedness is genuine history, not a bug. The
    #     same landmine exists latently for crypto too the moment a
    #     generation ever registers the full 5-asset ...USDT grid together
    #     (BTCUSDT/ETHUSDT: 3272 bars; SOLUSDT: 2182; XRPUSDT: 3012; BNBUSDT:
    #     3191 -- different listing dates), just never triggered because
    #     production has only ever used the same-length legacy pair. Gating
    #     on raggedness rather than on class alone closes that landmine too.
    # cluster.correlation compares series BY INDEX, so any of the above must
    # be trimmed to the dates every series actually shares before clustering,
    # else k and the recorded deflated Sharpe are silently wrong -- or, before
    # this fix, check_aligned simply refused the whole run.
    classes_present = {s["universe"].get("asset_class", "crypto")
                       for s in all_specs}
    # equity_len_by_sid carries the ORIGINAL equity curve length for every
    # sid regardless of source (fresh run or simcache hit -- simcache.put
    # records it alongside the returns series precisely so this check does
    # not need the equity itself), so the ragged/not-ragged decision is
    # unaffected by which specs happened to be cached this pass.
    raw_lengths = equity_len_by_sid
    ragged = len(set(raw_lengths.values())) > 1
    if len(classes_present) > 1 or ragged:
        dated_by_id = dated_returns_by_sid
        returns_by_id, common_dates = intersect_returns(dated_by_id)
        trials_alignment, trials_common_days = "intersection", len(common_dates)
        if len(common_dates) < MIN_TRIALS_COMMON_DAYS:
            _raise_too_short_intersection(dated_by_id, common_dates)
    else:
        # Same values daily_returns_from_curve(equity) would give: stripping
        # the (already date-normalised) date off each entry of the exact
        # series that function's own formula produces.
        returns_by_id = {sid: series.rets
                         for sid, series in dated_returns_by_sid.items()}
        trials_alignment, trials_common_days = "native", None
    registered_n = len(all_specs)

    # protocol-v3: DSR no longer gates this stage, but it still ranks siblings
    # and is still recorded, so it is computed against EFFECTIVELY INDEPENDENT
    # trials. A sibling sweep is one idea at several settings, and pooling
    # structurally different families put real edge dispersion into a term
    # meant to hold sampling noise.
    check_aligned(returns_by_id)
    t_et0 = time.time()
    trials_n, cluster_labels, trials_var = effective_trials(returns_by_id)
    print(f"[gauntlet] effective_trials {time.time() - t_et0:.1f}s "
          f"(pure clustering, inside the clustering stage)", flush=True)
    print(f"effective trials: {trials_n} clusters over {registered_n} "
          f"registered strategies")
    return {"trials_n": trials_n, "cluster_labels": cluster_labels,
            "trials_var": trials_var, "trials_alignment": trials_alignment,
            "trials_common_days": trials_common_days,
            "registered_n": registered_n, "returns_by_id": returns_by_id}


def sibling_families(all_specs: list[dict], train_sharpe: dict,
                     screen_tc_fail: set[str]) -> tuple[dict, dict]:
    """(family_by_group, grids_by_group): each sibling group's members with
    their swept-axis coordinates and train Sharpe, and the grid of every
    axis that actually varies within the group. Moved out of run()
    unchanged (Build 2a task 7); plateau.qualifies reads both."""
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
    return family_by_group, grids_by_group


def run(argv: list[str] | None = None) -> int:
    import hashlib
    from .screen import (assert_cells_comparable, bundle_hash, comparable_cells,
                         load_cell_data)

    t_run0 = time.time()          # SP4 Task P5: final stage-timings summary

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data")
    ap.add_argument("--artifacts-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "artifacts")
    # SP4 Task P1. Default derived from --registry (not a fixed path) so
    # every tmp-registry test gets its own isolated cache directory for
    # free, the same way it already gets its own artifacts dir -- a real
    # run's default registry lives in research-layer/, so its cache lands
    # in research-layer/simcache/ (gitignored) exactly as the plan names it.
    ap.add_argument("--simcache-dir", type=Path, default=None)
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    # protocol-v5's per-family null. SP4 Task P3 lowered the default 200 -> 50
    # (see PBO_NULL_DRAWS); a real run's verdict records whatever draw count
    # it actually used, and a deliberate deep run can still pass 200 here.
    ap.add_argument("--pbo-null-draws", type=int, default=PBO_NULL_DRAWS)
    # Self-perturbation costs two extra backtests per dense axis per strategy.
    # On by default because a metric nobody computes is a metric nobody has;
    # off in the fixtures that do not exercise it.
    ap.add_argument("--no-perturb", dest="perturb", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    # Phase 3 step 1: stop BEFORE starting candidates this run cannot finish
    # by the deadline; they stay in 'gauntlet' state with no verdict, which is
    # exactly 'not gauntleted yet', and the next run picks them up. Absent =
    # today's behaviour, byte for byte. See pipeline/deadline.py.
    ap.add_argument("--deadline-utc", default=None,
                    help="ISO-8601 instant; defer candidates that cannot finish by then")
    args = ap.parse_args(argv)
    simcache_dir = args.simcache_dir or (
        args.registry.resolve().parent / "simcache")

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
        if not args.dry_run:
            _deadline.write_result(args.registry, "gauntlet", evaluated=0, deferred=0,
                                   deadline_utc=args.deadline_utc, stopped_at_deadline=False)
        return 0

    budget = _deadline.DeadlineBudget(args.deadline_utc)
    n_candidates_total = len(candidates)
    # Cheapest possible refusal: if not even one candidate at the prior rate
    # fits, defer everything now -- before a single bar is loaded or a single
    # registry-wide simulation runs. Nothing is written, nothing is started.
    if budget.active and not budget.fits(1, GAUNTLET_PRIOR_S_PER_CANDIDATE):
        rem = budget.remaining_s()
        print(f"DEADLINE: {rem:.0f}s remain before {args.deadline_utc}; not even one "
              f"candidate fits -- deferring all {n_candidates_total} (they stay in "
              f"'gauntlet' state, no verdict, and the next run picks them up).")
        if not args.dry_run:
            _deadline.write_result(args.registry, "gauntlet", evaluated=0,
                                   deferred=n_candidates_total,
                                   deadline_utc=args.deadline_utc, stopped_at_deadline=True)
        return 0

    # A CELL is (asset, timeframe), same as the screen; the derivation and
    # the class-per-cell rule live in screen.comparable_cells (2026-09-12) so
    # the loop's pre-spend freshness preflight cannot drift from this check.
    cells_needed, class_of = comparable_cells(all_specs)
    bars_by_cell, data_hashes, data_end = load_cell_data(
        args.data_dir, cells_needed, "9999-12-31")          # full history

    # This stage COMPARES: clustering pools trials registry-wide, CSCV runs
    # over a sibling family, and plateau selection ranks neighbours. Comparing
    # cells whose bars stop on different days scores truncation as strategy
    # failure, so refuse before any of that runs. The screen needs no such gate
    # -- it judges each spec against a fixed threshold and compares nothing.
    # class_of makes the rule per-class (spec s10.6): the same-day rule still
    # applies within one class, but a crypto close and an fx fix may land up
    # to 3 calendar days apart without refusing the run.
    assert_cells_comparable(data_end, class_of=class_of)

    # clustering needs every sibling's full-run curve (incl. graveyarded).
    # SP4 Task P1: that registry-wide re-simulation set only grows with the
    # chain, and only its DATED RETURNS SERIES is ever consumed downstream
    # (clustering, train_sharpe, the PBO family matrix) -- never its trades.
    # A spec that is NOT one of this pass's own candidates is therefore
    # served from the content-addressed simcache when available, skipping
    # run_spec entirely; a cache miss falls back to a fresh simulation and
    # populates the entry for next time. Candidates are NEVER cache reads --
    # their full trades are needed regardless (below), so they gain nothing
    # from the cache and always run fresh, matching this pass's own
    # evaluation of them exactly.
    group_of = {s["strategy_id"]: s["provenance"]["sibling_group_id"]
                for s in all_specs}
    candidate_sids = {s["strategy_id"] for s in candidates}
    cache = simcache.SimCache(simcache_dir)
    t_cluster0 = time.time()      # SP4 Task P5: covers sim-cache + clustering
    full_results: dict[str, dict] = {}          # candidates only (need trades)
    (dated_returns_by_sid, equity_len_by_sid, sim_cache_hits,
     sim_cache_misses) = registry_series(all_specs, bars_by_cell, data_hashes,
                                         cache, candidate_sids,
                                         full_results=full_results)
    print(f"sim cache: {sim_cache_hits} hit(s), {sim_cache_misses} miss(es) "
          f"over {len(all_specs) - len(candidate_sids)} non-candidate "
          f"registered strategies")

    clustered = cluster_registry(dated_returns_by_sid, equity_len_by_sid,
                                 all_specs)
    returns_by_id = clustered["returns_by_id"]
    trials_alignment = clustered["trials_alignment"]
    trials_common_days = clustered["trials_common_days"]
    trials_n, cluster_labels, trials_var = (
        clustered["trials_n"], clustered["cluster_labels"],
        clustered["trials_var"])
    registered_n = clustered["registered_n"]
    group_n: dict[str, int] = {}
    for g in group_of.values():
        group_n[g] = group_n.get(g, 0) + 1
    t_cluster = time.time() - t_cluster0
    print(f"[gauntlet] clustering done in {t_cluster:.1f}s "
          f"(cache {sim_cache_hits} hits / {sim_cache_misses} misses)",
          flush=True)

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

    def train_returns(sid):
        """Train-window (date <= cutoff) daily returns, sliced from the
        per-spec dated-returns series -- real or simcache-served -- rather
        than from an equity curve a non-candidate spec may not have this
        pass. See _annualized_sharpe_from_returns for the exact equivalence
        to the pre-P1 equity-curve computation. Series.train applies the
        same date-only compare _date_le does, on the ordinal arrays."""
        return dated_returns_by_sid[sid].train(args.cutoff)

    train_sharpe = {s["strategy_id"]: _annualized_sharpe_from_returns(
        train_returns(s["strategy_id"])) for s in all_specs}

    family_by_group, grids_by_group = sibling_families(
        all_specs, train_sharpe, screen_tc_fail)

    # SP4 Task P2: evaluate every candidate's gate battery + corroborating
    # metrics in a worker pool, standalone (protocol-v6's own founding
    # principle -- see the module docstring -- is exactly what makes this
    # legal: nothing a candidate's verdict depends on reads a sibling, so
    # nothing is lost by evaluating siblings out of order or in different
    # processes). PBO is threaded through AFTERWARDS, once every candidate's
    # base result exists -- see the PBO section immediately below for why.
    #
    # Workers = cpu_count - 2, never fewer than 1: this machine also runs
    # Morpheus (hub :8100), gbp-dashboard (:8000/:5173) and the SDCA dash
    # (:8050) at the same time (workspace CLAUDE.md's standing hazard), so a
    # gauntlet pass must always leave two cores free for them rather than
    # claiming the whole box.
    n_cpu = os.cpu_count() or 3
    avail_commit = available_commit_mb()
    max_workers = worker_count(n_cpu, avail_commit)
    if max_workers < max(1, n_cpu - 2):
        print(f"[gauntlet] workers {max_workers} (not {max(1, n_cpu - 2)}): only "
              f"{avail_commit} MB of commit available on the box", flush=True)
    payloads = [
        _candidate_payload(
            s, _spec_bars(bars_by_cell, s), full_results[s["strategy_id"]],
            _as_list(returns_by_id[s["strategy_id"]]), group_n[group_of[s["strategy_id"]]],
            registered_n, train_sharpe[s["strategy_id"]], trials_n, trials_var,
            next(x for x in family_by_group[group_of[s["strategy_id"]]]
                if x["sid"] == s["strategy_id"]),
            family_by_group[group_of[s["strategy_id"]]],
            grids_by_group.get(group_of[s["strategy_id"]], {}),
            args.cutoff, args.perturb, trials_alignment, trials_common_days,
            sim_cache_hits, sim_cache_misses)
        for s in candidates]
    # Release what the pool phase no longer needs BEFORE the workers spawn.
    # Every payload above already carries what its candidate needs (its own
    # bars, trades and return list). The registry-wide series stay: PBO
    # reads their train slices afterwards, and as arrays (simcache.Series,
    # 2026-09-03) they are ~12 bytes a point rather than the ~150 the
    # [date, ret] pairs cost -- the representation that put this parent at
    # 9.6 GB of commit and starved the workers.
    returns_by_id.clear()
    equity_len_by_sid.clear()
    full_results.clear()
    bars_by_cell.clear()
    gc.collect()
    print(f"[gauntlet] clustering inputs released before the pool "
          f"(parent commit {private_commit_mb()} MB, "
          f"{available_commit_mb()} MB available on the box)", flush=True)
    # Evaluate in chunks, and ask the budget before EACH chunk whether it can
    # still finish. The first chunk is judged at a conservative prior; every
    # later one at the rate the finished chunks actually ran at. A reserve is
    # held back for PBO and the artifact/verdict writes, which run after the
    # candidates and scale with how many live groups they leave (see
    # deadline.py's module docstring for the honest limits of that).
    t_eval0 = time.time()
    if budget.active:
        rem0 = budget.remaining_s() or 0.0
        budget.reserve_s = max(GAUNTLET_RESERVE_MIN_S, GAUNTLET_RESERVE_FRAC * rem0)
    chunk_size = max(1, max_workers * GAUNTLET_CHUNK_PER_WORKER)
    results_by_sid: dict[str, dict] = {}
    n_started = 0
    stopped_at_deadline = False
    for chunk in _deadline.chunks(payloads, chunk_size):
        if budget.active and not budget.fits(
                len(chunk), budget.rate_s(GAUNTLET_PRIOR_S_PER_CANDIDATE)):
            stopped_at_deadline = True
            break
        t_c0 = time.time()
        results_by_sid.update(_run_candidates(chunk, max_workers,
                                              offset=n_started, total=len(payloads)))
        budget.record(len(chunk), time.time() - t_c0)
        n_started += len(chunk)
    deferred = candidates[n_started:]
    candidates = candidates[:n_started]
    payloads = payloads[:n_started]
    t_eval = time.time() - t_eval0
    print(f"[gauntlet] candidate evaluation done in {t_eval:.1f}s "
          f"({len(candidates)} candidates, {max_workers} worker(s))",
          flush=True)
    if stopped_at_deadline:
        print(f"DEADLINE: stopped before starting {len(deferred)} of "
              f"{n_candidates_total} candidates ({budget.remaining_s():.0f}s left of "
              f"the budget, measured {budget.rate_s(GAUNTLET_PRIOR_S_PER_CANDIDATE):.1f}s "
              f"per candidate); they stay in 'gauntlet' state with no verdict and "
              f"the next run picks them up.", flush=True)
    if not candidates:
        # Only reachable when the FIRST chunk did not fit after clustering ran.
        if not args.dry_run:
            _deadline.write_result(args.registry, "gauntlet", evaluated=0,
                                   deferred=len(deferred), deadline_utc=args.deadline_utc,
                                   stopped_at_deadline=True)
        return 0

    # PBO over the TRAIN window only — the 2024+ holdout has been consumed
    # three times already and protocol-v5 does not consume it a fourth. The
    # matrix includes EVERY sibling, screen deaths included; computing it over
    # passers only would filter on performance and understate overfitting.
    #
    # protocol-v5 judges the observed value against a null built for THAT
    # family rather than against a fixed line, and refuses to judge a family
    # with too few DISTINCT configurations at all.
    #
    # SP4 Task P3: the (expensive) permutation null is now built ONLY for a
    # group with >=1 candidate that passed its OWN gate battery THIS PASS
    # (before any PBO override -- see below). Everything else -- a group with
    # no candidate in it this run at all (already fully resolved, or not yet
    # advanced past screen), or one where every candidate died on its own
    # evidence -- gets the honest "not_measured_dead_group" label instead of
    # spending a null on a family with nothing left to test. This is distinct
    # from "underpowered" (which means the null WAS attempted or would have
    # been, but the family itself cannot support one): a dead group is never
    # even asked the question. live_groups is necessarily a subset of what
    # v5 would have computed a null for, so this can only ever REMOVE a null,
    # never add a "kill" or "pass"/"fail" that v5 would not also have reached.
    t_pbo0 = time.time()
    live_groups = {group_of[s["strategy_id"]] for s in candidates
                  if results_by_sid[s["strategy_id"]]["passed"]}
    pbo_by_group = {}
    # Only a family with a candidate THIS run can have its status read (the
    # rows loop below is the only reader, per candidate); the other ~300
    # registered families used to be walked for nothing.
    candidate_groups = {group_of[s["strategy_id"]] for s in candidates}
    deferred_groups: set[str] = set()
    null_times: list[float] = []
    n_groups = len(family_by_group)
    for gi, (g, fam) in enumerate(family_by_group.items(), start=1):
        if g not in candidate_groups:
            continue
        series = {s["sid"]: train_returns(s["sid"]) for s in fam}
        res = cscv_pbo(series, s=CSCV_SPLITS)
        n_distinct = distinct_configs(series)
        res["n_distinct"] = n_distinct
        res["null_draws"] = 0
        res["percentile"] = res["null_p05"] = res["null_p95"] = None
        res["member_pass"] = False        # fail closed until measured
        if g not in live_groups:
            res["verdict"] = "not_measured_dead_group"
        elif res["pbo"] is None:
            res["verdict"] = "underpowered"
        elif n_distinct < PBO_MIN_DISTINCT:
            # Not a lenient default: the swept axis did not bind, so there is
            # nothing to select among and no null worth building.
            res["verdict"] = "underpowered"
        else:
            # Seeded off the group id so a rerun of the same chain reproduces
            # the same null exactly, the way every other stochastic step here
            # is seeded off content rather than off the clock.
            null_rate = ((sum(null_times) / len(null_times)) if null_times
                         else PBO_NULL_PRIOR_S)
            if budget.active and not budget.fits(1, null_rate):
                res["verdict"] = "deferred_deadline"
                deferred_groups.add(g)
                pbo_by_group[g] = res
                print(f"  PBO {g}: null (~{null_rate:.0f}s) does not fit the "
                      f"deadline ({budget.remaining_s():.0f}s left) -> its "
                      f"candidates are deferred to the next run")
                if gi % 25 == 0 or gi == n_groups:
                    print(f"[gauntlet] pbo group {gi}/{n_groups}", flush=True)
                continue
            t_null0 = time.time()
            null = permutation_null(
                series, s=CSCV_SPLITS, draws=args.pbo_null_draws,
                seed=int(hashlib.sha256(g.encode()).hexdigest()[:8], 16))
            null_times.append(time.time() - t_null0)
            res["null_draws"] = len(null)
            if not null:                      # every draw was uncomputable
                res["verdict"] = "underpowered"
                pbo_by_group[g] = res
                print(f"  PBO {g}: null was uncomputable -> underpowered")
                if gi % 25 == 0 or gi == n_groups:
                    print(f"[gauntlet] pbo group {gi}/{n_groups}", flush=True)
                continue
            res["percentile"] = percentile_of(null, res["pbo"])
            res["null_p05"] = percentile(sorted(null), PBO_PASS_PCTILE)
            res["null_p95"] = percentile(sorted(null), PBO_KILL_PCTILE)
            # The MEMBER-level test and the GROUP kill are kept separate,
            # exactly as they were under v4's two thresholds. Collapsing them
            # would make 'pbo_family_kill' unreachable as a reason and bury the
            # distinction between a member that failed on its own standing and
            # one that had nothing wrong and died with its family -- in an
            # append-only chain, permanently.
            res["member_pass"] = res["percentile"] <= PBO_PASS_PCTILE
            if res["percentile"] >= PBO_KILL_PCTILE:
                res["verdict"] = "kill"
            elif res["member_pass"]:
                res["verdict"] = "pass"
            else:
                res["verdict"] = "fail"
        pbo_by_group[g] = res
        v, pct = res["pbo"], res["percentile"]
        print(f"  PBO {g}: "
              f"{'n/a - ' + str(res['reason']) if v is None else f'{v:.3f}'}"
              f"  ({res['n_configs']} configs, {n_distinct} distinct)"
              + ("" if pct is None else
                 f"  pctile={pct:.0%} of {res['null_draws']} draws")
              + f"  -> {res['verdict']}")
        if gi % 25 == 0 or gi == n_groups:
            print(f"[gauntlet] pbo group {gi}/{n_groups}", flush=True)
    killed_groups = {g for g, r in pbo_by_group.items()
                     if r["verdict"] == "kill"}
    for g in sorted(killed_groups):
        print(f"  PBO FAMILY KILL: {g} at {pbo_by_group[g]['pbo']:.3f}, "
              f"the {pbo_by_group[g]['percentile']:.0%} percentile of its own "
              f"no-skill null (kill at {PBO_KILL_PCTILE:.0%})")
    t_pbo = time.time() - t_pbo0
    if deferred_groups:
        deferred_pbo = [s for s in candidates
                        if group_of[s["strategy_id"]] in deferred_groups]
        candidates = [s for s in candidates
                      if group_of[s["strategy_id"]] not in deferred_groups]
        deferred = deferred + deferred_pbo
        stopped_at_deadline = True
        print(f"DEADLINE: {len(deferred_groups)} live family(ies) could not get "
              f"their PBO null before the deadline; their {len(deferred_pbo)} "
              f"candidate(s) stay in 'gauntlet' state with no verdict and the "
              f"next run picks them up. Measured families' verdicts are written "
              f"below.", flush=True)

    # Patch each candidate's worker-computed metrics with the pbo status that
    # was only knowable AFTER the whole pool returned (see the P2/P3 comments
    # above), and apply the same family-kill override run() always has.
    rows, payloads_out = [], []
    for s in candidates:
        sid = s["strategy_id"]
        r = results_by_sid[sid]
        g, passed, reason, metrics = r["group"], r["passed"], r["reason"], r["metrics"]
        metrics.update(_pbo_metrics_fields(pbo_by_group[g]))
        # A PBO family kill is recorded on EVERY member of the group, but it
        # only becomes the fail REASON for a strategy that had nothing else
        # wrong. Six gates precede 'pbo' in FAIL_ORDER, so overwriting `reason`
        # unconditionally would bury a strategy's own first failure — in an
        # append-only chain, permanently. The flag is written on the normal
        # path too, so the key is always present rather than sometimes-missing.
        metrics["pbo_family_kill"] = g in killed_groups
        if metrics["pbo_family_kill"] and passed:
            passed, reason = False, "pbo_family_kill"
        rows.append({"sid": sid, "group": g, "passed": passed,
                     "dsr": metrics["deflated_sharpe"]})
        payloads_out.append((s, r["oos_trades"], passed, reason, metrics,
                             r["mc_summary"]))
        d = metrics["edge_decay_pct"]
        print(f"{sid}  {'PASS' if passed else 'fail':<4} "
              f"oos_edge={metrics['oos_edge_per_trade']:+.5f}  "
              f"decay={'n/a' if d is None else f'{d:+.1f}%'}  "
              f"p05={metrics['mc_p05_equity']:.3f}  "
              f"ruin={metrics['p_ruin']:.3f}  "
              f"[info dsr={metrics['deflated_sharpe']:.3f}]  "
              f"stress={metrics['cost_stress_net_pnl']:+.4f}"
              + (f"  [{reason}]" if reason else ""))
    payloads = payloads_out

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
              f"{len(rows) - n_pass} gate-fail; nothing written.")
        print(f"[gauntlet] stage timings: clustering {t_cluster:.1f}s, "
              f"candidate eval {t_eval:.1f}s ({max_workers} worker(s)), "
              f"pbo {t_pbo:.1f}s, total {time.time() - t_run0:.1f}s",
              flush=True)
        return 0

    n_written = 0
    t_artifacts0 = time.time()
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
                args.cutoff, data_hashes, data_end, group_context,
                protocol=PROTOCOL)
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
                # protocol-v6: nothing is group-selected any more. Every gate
                # passer is here, on its own evidence.
                registry.record_state_change(sid, "quarantine", "gauntlet pass")
            else:
                # Unreachable under v6: select_survivors returns every passer,
                # and a non-passer always carries a reason, so this branch has
                # no members. It raises rather than falling back to the retired
                # sibling_not_selected transition, because a silent fallback
                # here would bury a strategy for a reason this protocol
                # abolished -- in an append-only chain, permanently.
                raise AssertionError(
                    f"{sid} passed the battery but was not promoted: v6 "
                    f"retired selection, so this cannot happen")
            n_written += 1
    except BaseException:
        print(f"\nPARTIAL WRITE: {n_written}/{2 * len(payloads)} entries "
              f"chained before failure — run again to see ORPHANED "
              f"diagnostics.", file=sys.stderr)
        raise

    t_artifacts = time.time() - t_artifacts0
    _deadline.write_result(args.registry, "gauntlet", evaluated=len(rows),
                           deferred=len(deferred), deadline_utc=args.deadline_utc,
                           stopped_at_deadline=stopped_at_deadline)
    print(f"\n{len(rows)} evaluated: {len(quarantine)} -> quarantine, "
          f"{len(rows) - n_pass} gate-fail -> graveyard"
          + (f", {len(deferred)} deferred to the next run." if deferred else "."))
    print(f"[gauntlet] stage timings: clustering {t_cluster:.1f}s, "
          f"candidate eval {t_eval:.1f}s ({max_workers} worker(s)), "
          f"pbo {t_pbo:.1f}s, artifacts {t_artifacts:.1f}s, "
          f"total {time.time() - t_run0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
