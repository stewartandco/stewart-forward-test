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
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import cells
from . import simcache
from .registry import Registry
from .engine import run_spec, ENGINE_REV
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

PROTOCOL = "gauntlet-protocol-v6"
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
PURGE_BARS = 200     # >= the grammar's longest lookback (ma_cross.slow = 200)

# Below this many shared calendar days, an "intersection" is not a common
# history worth clustering on -- it is noise dressed as a trial count. A real
# dry-run generation hit this: 12 fx pairs with genuinely different real
# inception dates (Bretton-Woods-era pairs from 1971 through EUR from its
# 1999 launch) produced ragged calendars that would otherwise cluster on
# whatever few days happen to overlap the shortest-lived pair.
MIN_TRIALS_COMMON_DAYS = 100

# The fixed order in which gates are evaluated and reported. 'dsr' is
# DELIBERATELY ABSENT: protocol-v4 still computes and records the deflated
# Sharpe, but it does not gate entry to paper trading and — new in v4 — it no
# longer ranks siblings either; neighbourhood floor does. Adding it back here
# is a protocol change and needs its own pre-declared chained note.
# protocol-v6: SIX gates, and every input to every one of them is a property
# of the STRATEGY ALONE -- its own trades, its own returns, its own train
# Sharpe, its own trades re-run at doubled slippage. No gate reads a sibling, a
# group, a neighbour, a grid position or a family statistic. 'pbo',
# 'pbo_underpowered' and 'plateau' were removed because each decided a
# strategy's fate on something other than its own performance; all three are
# still COMPUTED and RECORDED. 'dsr' remains deliberately absent, as it has
# been since v3. Reintroducing any group-level input here contradicts v6's
# founding principle and needs its own pre-declared chained note saying so.
FAIL_ORDER = ("sharpe_floor", "oos_negative", "edge_decay", "mc_p05",
              "p_ruin", "cost_stress")


def _date_le(a: str, b: str) -> bool:
    """Date-only `a <= b`, ignoring any time-of-day suffix either string may
    carry (`YYYY-MM-DD HH:MM:SS` vs bare `YYYY-MM-DD` -- see
    daily_returns_with_dates' own docstring on why this repo's CSVs disagree
    on format: legacy crypto is bare-dated, the fx snapshot adapter and the
    modern ...USDT grid are timestamped).

    Batch review rider (SP4): every cutoff-boundary comparison in this module
    now goes through this ONE helper, so a suffixed bar compares the same way
    everywhere instead of date-only in some call sites (train_returns, the
    PBO family matrix -- both slice from daily_returns_with_dates' already-
    normalised series) and raw-string in others (split_trades, window_vol,
    _benchmark_relative used to compare `entry_date`/`date` to `cutoff`
    directly). A bare-dated string is unaffected by the `[:10]` slice, so
    every production crypto comparison this repo has ever recorded a verdict
    against is byte-identical before and after; only a time-suffixed bar
    landing exactly on the cutoff date can change side."""
    return a[:10] <= b[:10]


def split_trades(trades: list[dict], cutoff: str) -> tuple[list, list]:
    is_t = [t for t in trades if _date_le(t["entry_date"], cutoff)]
    oos_t = [t for t in trades if not _date_le(t["entry_date"], cutoff)]
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
        closes = [b["close"] for b in bars_by_asset[a]
                  if not _date_le(b["date"], lo) and _date_le(b["date"], hi)]
        if len(closes) < 3:
            continue
        rets = [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))]
        m = sum(rets) / len(rets)
        vols.append(math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
                    * math.sqrt(365))
    return sum(vols) / len(vols) if vols else 0.0


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
        # protocol-v4: the train-window annualized Sharpe the floor gate read.
        # protocol-v5: the sibling group's CSCV result is now a STATUS, not a
        # bare number -- the observed value alone cannot be read without the
        # null it was judged against, and a chained verdict must not require a
        # reader to recompute one. Both are None when the caller did not
        # supply them.
        "train_sharpe": train_sharpe,
        **_pbo_metrics_fields(pbo_status),
        # protocol-v6: neighbourhood qualification is still computed and still
        # recorded; it stopped gating and stopped selecting. Kept because the
        # next protocol argument will need the evidence, and a verdict that
        # quietly dropped it would destroy that evidence generation by
        # generation.
        "plateau_ok": plateau_ok,
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
              }
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


def _spec_bars(bars_by_cell: dict, spec: dict) -> dict:
    """The bars of the spec's OWN cell, keyed by asset for run_spec.

    A spec with no declared timeframe is a legacy daily, matching the screen.
    """
    tf = spec["universe"].get("timeframe", "1d")
    return {a: bars_by_cell[(a, tf)] for a in spec["universe"]["assets"]}


def daily_returns_from_curve(equity: list[tuple[str, float]]) -> list[float]:
    return [equity[i][1] / equity[i - 1][1] - 1
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]


def daily_returns_with_dates(equity: list[tuple[str, float]]
                             ) -> list[tuple[str, float]]:
    """Same values as daily_returns_from_curve, paired with the DATE each
    return is attributed to (equity[i]'s date, for the step from i-1 to i).

    A run whose specs are ALREADY on one shared calendar never needs the
    dates. Two things break that: a mixed-class run pools a 24x7 calendar
    with a 5-day fx calendar, and a same-class run can still be ragged --
    registered specs on assets with genuinely different history starts
    (fx pairs each have their own real inception date; the crypto grid's
    five assets were listed on different days too). cluster.correlation
    compares series BY INDEX, so the dates are the only way to find what
    those series actually share (spec s10.6).

    FAILURE THIS GUARDS AGAINST (real run, 2026-08-24): a bar's `date` string
    is carried through verbatim from its CSV, and this repo's CSVs do not
    all agree on format -- the legacy BTCUSD_1d.csv (what real crypto specs
    actually register against, spec s10.9) is bare `YYYY-MM-DD`, while the
    fx snapshot adapter and the modern ...USDT grid write `YYYY-MM-DD
    HH:MM:SS`. Two overlapping calendars (1999-2026, both sides) whose keys
    never compare equal intersect to an empty set -- "intersection ... is
    only 0 day(s)" across 455 real strategies -- not because the calendars
    disagreed, but because the STRINGS did. Normalising every key to
    date-only HERE, in the one place every dated return series is built,
    closes it by construction: every downstream consumer (intersect_returns,
    era_summary's date-string comparisons) sees one format no matter which
    CSV convention produced the bar."""
    return [(str(equity[i][0])[:10], equity[i][1] / equity[i - 1][1] - 1)
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]


def _annualized_sharpe_from_returns(rets: list[float]) -> float | None:
    """Same math as plateau.annualized_sharpe (sample mean / sample stdev,
    annualized by TRADING_DAYS), taking an already-computed return series
    instead of an equity curve.

    SP4 Task P1: this lets train_sharpe and the PBO family matrix be derived
    straight from a spec's dated-returns series -- real or served from
    simcache -- without ever needing that spec's equity curve. When `rets`
    is exactly the per-step series plateau.annualized_sharpe would itself
    derive from an equity curve (as daily_returns_with_dates/
    daily_returns_from_curve already do throughout this file), the output is
    identical. The one behavioural nuance: callers here always slice the
    DATE-NORMALISED series (daily_returns_with_dates strips any time suffix
    to date-only -- see its docstring), so a source date carrying a time
    suffix now compares against `cutoff` the same way the clustering
    intersection already does elsewhere in this module, rather than by raw
    string. Production crypto data is bare-dated (spec s10.9) and every
    pinned fixture's cutoff falls outside its data range, so this is a
    no-op in every case this repo currently exercises; it would only differ
    from the pre-P1 behaviour for a time-suffixed source with a bar landing
    exactly on the cutoff date."""
    if len(rets) < 30:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)


def intersect_returns(dated_by_id: dict[str, list[tuple[str, float]]]
                      ) -> tuple[dict[str, list[float]], list[str]]:
    """Align every series onto the dates common to ALL of them, sorted.

    Returns (returns_by_id, common_dates). Every output series has the same
    length by construction, so check_aligned passes trivially and
    effective_trials clusters on dates every strategy actually shares --
    the INTERSECTION calendar (spec s10.6), not a native one that would
    silently correlate different dates index-by-index."""
    common: set[str] | None = None
    for rows in dated_by_id.values():
        dates = {d for d, _ in rows}
        common = dates if common is None else (common & dates)
    common_sorted = sorted(common or set())
    # TRAP: dict(rows) is O(days) to build. Building it INSIDE the per-date
    # comprehension below (once per common date, not once per series) makes
    # this whole function O(specs x days x len) instead of O(specs x days) --
    # silent at fixture scale, an hour-long stall at the first real mixed run
    # (155 registered specs, ~7000 fx common days). Hoist it: one dict build
    # per series, not one per (series, date) pair.
    out = {}
    for sid, rows in dated_by_id.items():
        m = dict(rows)
        out[sid] = [m[d] for d in common_sorted]
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


def era_summary(trades: list[dict], eras: tuple[tuple[str, str, str], ...]
                ) -> dict[str, dict]:
    """Per-era {n_trades, net_pnl}, bucketed by entry_date, for a class whose
    CLASSES entry declares eras (fx first, spec §6). RECORDED, never gated:
    protocol-v6 has no era gate, and this replaces no FAIL_ORDER member."""
    out = {}
    for name, start, end in eras:
        bucket = [t for t in trades if start <= t["entry_date"] <= end]
        out[name] = {"n_trades": len(bucket),
                     "net_pnl": compound(contributions(bucket))}
    return out


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


BENCHMARK_BASIS = {
    "fx": "price returns, carry excluded on both sides",
    # crypto is DORMANT until SP5 Phase 3 flips CLASSES["crypto"]["benchmark"];
    # declared now so the flip is a one-line cells.py change later.
    "crypto": "price returns, staking/funding yield excluded on both sides",
}
_DEFAULT_BASIS = "price returns, dividends excluded on both sides"


def _benchmark_relative(spec: dict, spec_bars: dict, strategy_net: float,
                        cutoff: str) -> dict | None:
    """B1 (SP4 Track 2a addendum, pre-registered 2026-08-26,
    `docs/2026-08-24-sp4-track2a-addendum.md`): RECORDED, NOT GATED
    same-OOS-window buy-and-hold control against the cell's own asset, for
    every class whose CLASSES entry declares `benchmark: "self"`. Returns
    None (no key written at all) for every other class -- absence means
    not applicable, never a null placeholder, per the addendum's own
    no-null-placeholder convention.

    `strategy_net` is the candidate's OOS net exactly as evaluate_spec's
    own `oos_net` (compound(contributions(oos_trades))) -- the caller must
    pass the SAME figure the oos_negative gate read, computed the same way,
    never recomputed by a different formula here.

    The control buys the cell's own single asset at the first OOS bar's
    open and sells at the last OOS bar's close -- the same `date > cutoff`
    fence split_trades applies to trades -- net of ONE round trip of the
    class's own cost model: `per_side = commission_per_side + slippage_ticks`
    charged on both sides, the exact formula engine.simulate_asset applies
    to every real trade (engine.py's `net = gross - 2 * per_side`). No
    financing: short_financing_per_year only ever accrues on a SHORT
    position, and a buy-and-hold control is definitionally long.

    SP5 D3: the recorded `basis` string is per-class (BENCHMARK_BASIS) --
    it names exactly what a price-only control cannot see for that class
    (dividends for the ETF classes, carry for fx), on every verdict.
    """
    asset_class = spec["universe"].get("asset_class", "crypto")
    class_spec = cells.CLASSES.get(asset_class, {})
    if class_spec.get("benchmark") != "self":
        return None
    assets = spec["universe"]["assets"]
    if len(assets) != 1:
        raise ValueError(
            f"{spec['strategy_id']}: benchmark-relative control needs "
            f"exactly one asset per cell for class {asset_class!r} "
            f"(benchmark: 'self'), got {assets!r}")
    bars = spec_bars[assets[0]]
    oos_bars = [b for b in bars if not _date_le(b["date"], cutoff)]
    if not oos_bars:
        raise ValueError(
            f"{spec['strategy_id']}: no OOS bars for {assets[0]!r} after "
            f"cutoff {cutoff} -- cannot compute the benchmark-relative "
            f"control")
    entry_px, exit_px = oos_bars[0]["open"], oos_bars[-1]["close"]
    cost_model = class_spec["cost_model"]
    per_side = cost_model["commission_per_side"] + cost_model["slippage_ticks"]
    buy_hold_net = (exit_px / entry_px - 1) - 2 * per_side
    return {"window": "oos", "strategy_net": strategy_net,
            "buy_hold_net": buy_hold_net,
            "excess": strategy_net - buy_hold_net,
            "basis": BENCHMARK_BASIS.get(asset_class, _DEFAULT_BASIS)}


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
                    progress_every: int = 25) -> dict[str, dict]:
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

    def _tick():
        nonlocal done
        done += 1
        if done % progress_every == 0 or done == n:
            print(f"[gauntlet] evaluated {done}/{n} candidates", flush=True)

    if max_workers <= 1 or n <= 1:
        for p in payloads:
            r = _evaluate_candidate(p)
            results[r["sid"]] = r
            _tick()
        return results

    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_evaluate_candidate, p) for p in payloads]
        for fut in as_completed(futures):
            r = fut.result()
            results[r["sid"]] = r
            _tick()
    return results


def write_gauntlet_artifacts(art_dir: Path, spec: dict, oos_trades: list[dict],
                             mc_summary: dict, metrics: dict, cutoff: str,
                             data_hashes: dict, data_end: dict,
                             group_context: dict) -> Path:
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
         "data_sha256": data_hashes, "data_end": data_end,
         "group_context": group_context,
         "spec": spec}, indent=1, sort_keys=True), encoding="utf-8")
    return bundle


def run(argv: list[str] | None = None) -> int:
    import hashlib
    from .screen import assert_cells_comparable, bundle_hash, load_cell_data

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
        return 0

    # A CELL is (asset, timeframe), same as the screen. This used to load
    # `_1d` for every spec and hash by bare asset, so a 15m spec would have
    # been gauntleted on daily bars while the manifest named the cell, and two
    # cells of one asset would have overwritten each other's hash. Latent while
    # all 80 registered specs were 1d; live the moment gen-4 uses the grid.
    cells_needed = sorted({(a, s["universe"].get("timeframe", "1d"))
                           for s in all_specs for a in s["universe"]["assets"]})
    bars_by_cell, data_hashes, data_end = load_cell_data(
        args.data_dir, cells_needed, "9999-12-31")          # full history

    # cell_id -> asset class, read from each spec's OWN declared universe
    # rather than cells.class_of_asset: production crypto specs register
    # legacy BTCUSD/ETHUSD tickers that are not members of cells.CLASSES's
    # ...USDT grid (spec s10.9), so deriving class from the ticker would
    # raise on every one of them. asset_class already travels on every
    # registered spec's universe (composer stamps it; legacy fixtures declare
    # it explicitly too), so reading it back is both safe and exact.
    class_of: dict[str, str] = {}
    for s in all_specs:
        tf = s["universe"].get("timeframe", "1d")
        cls = s["universe"].get("asset_class", "crypto")
        for a in s["universe"]["assets"]:
            class_of[cells.cell_id(a, tf)] = cls

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
    dated_returns_by_sid: dict[str, list[tuple[str, float]]] = {}
    equity_len_by_sid: dict[str, int] = {}
    sim_cache_hits = sim_cache_misses = 0
    for s in all_specs:
        sid = s["strategy_id"]
        if sid in candidate_sids:
            res = run_spec(s, _spec_bars(bars_by_cell, s))
            full_results[sid] = res
            dated_returns_by_sid[sid] = daily_returns_with_dates(res["equity"])
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
            series = daily_returns_with_dates(res["equity"])
            dated_returns_by_sid[sid] = series
            equity_len_by_sid[sid] = len(res["equity"])
            cache.put(key, series, len(res["equity"]))
            sim_cache_misses += 1
    print(f"sim cache: {sim_cache_hits} hit(s), {sim_cache_misses} miss(es) "
          f"over {len(all_specs) - len(candidate_sids)} non-candidate "
          f"registered strategies")

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
        returns_by_id = {sid: [r for _, r in series]
                         for sid, series in dated_returns_by_sid.items()}
        trials_alignment, trials_common_days = "native", None
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
    t_et0 = time.time()
    trials_n, cluster_labels, trials_var = effective_trials(returns_by_id)
    print(f"[gauntlet] effective_trials {time.time() - t_et0:.1f}s "
          f"(pure clustering, inside the clustering stage)", flush=True)
    print(f"effective trials: {trials_n} clusters over {registered_n} "
          f"registered strategies")
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
        to the pre-P1 equity-curve computation."""
        return [r for d, r in dated_returns_by_sid[sid]
               if _date_le(d, args.cutoff)]

    train_sharpe = {s["strategy_id"]: _annualized_sharpe_from_returns(
        train_returns(s["strategy_id"])) for s in all_specs}

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
    max_workers = max(1, n_cpu - 2)
    payloads = [
        _candidate_payload(
            s, _spec_bars(bars_by_cell, s), full_results[s["strategy_id"]],
            returns_by_id[s["strategy_id"]], group_n[group_of[s["strategy_id"]]],
            registered_n, train_sharpe[s["strategy_id"]], trials_n, trials_var,
            next(x for x in family_by_group[group_of[s["strategy_id"]]]
                if x["sid"] == s["strategy_id"]),
            family_by_group[group_of[s["strategy_id"]]],
            grids_by_group.get(group_of[s["strategy_id"]], {}),
            args.cutoff, args.perturb, trials_alignment, trials_common_days,
            sim_cache_hits, sim_cache_misses)
        for s in candidates]
    t_eval0 = time.time()
    results_by_sid = _run_candidates(payloads, max_workers)
    t_eval = time.time() - t_eval0
    print(f"[gauntlet] candidate evaluation done in {t_eval:.1f}s "
          f"({len(candidates)} candidates, {max_workers} worker(s))",
          flush=True)

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
    n_groups = len(family_by_group)
    for gi, (g, fam) in enumerate(family_by_group.items(), start=1):
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
            null = permutation_null(
                series, s=CSCV_SPLITS, draws=args.pbo_null_draws,
                seed=int(hashlib.sha256(g.encode()).hexdigest()[:8], 16))
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
                args.cutoff, data_hashes, data_end, group_context)
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
    print(f"\n{len(rows)} evaluated: {len(quarantine)} -> quarantine, "
          f"{len(rows) - n_pass} gate-fail -> graveyard.")
    print(f"[gauntlet] stage timings: clustering {t_cluster:.1f}s, "
          f"candidate eval {t_eval:.1f}s ({max_workers} worker(s)), "
          f"pbo {t_pbo:.1f}s, artifacts {t_artifacts:.1f}s, "
          f"total {time.time() - t_run0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
