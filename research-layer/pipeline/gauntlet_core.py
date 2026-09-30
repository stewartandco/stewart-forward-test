"""The gauntlet's STANDALONE battery: everything a verdict depends on, and
nothing registry-wide (Build 2a, docs/2026-09-30-gauntlet-at-scale-design.md).

protocol-v6 made every gate a property of the strategy alone. This module is
that sentence as an import graph: it must never import pipeline.cluster or
pipeline.pbo (test_gauntlet_core pins it), so the verdict path CANNOT apply a
group statistic. gauntlet.py re-exports these names unchanged; its
registry-wide pass stays there and in gauntlet_stats.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import cells
from .engine import run_spec, exit_reason_counts
from .stats import bootstrap_paths, percentile
from .plateau import annualized_sharpe, TRADING_DAYS
from .perturb import sensitivity
from .walkforward import walkforward_report
from .regime import regime_by_date, regime_split

PROTOCOL_V61 = "gauntlet-protocol-v6.1"
DECAY_MIN_PCT = -25.0
MC_PATHS = 2000
MC_P05_MIN = 1.0
RUIN_LEVEL = 0.5
P_RUIN_MAX = 0.05
DEFAULT_CUTOFF = "2023-12-31"
# protocol-v4 additions. SR_FLOOR is knowingly non-binding today — every one of
# the 43 strategies that has ever reached this stage scored at least 0.577 on
# the train window, and all 24 sub-0.4 specs died at the screen. It is adopted
# so the two pipelines read identically, and it will bite if the screen is ever
# loosened.
SR_FLOOR = 0.4
PURGE_BARS = 200     # >= the grammar's longest lookback (ma_cross.slow = 200)
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


def _spec_bars(bars_by_cell: dict, spec: dict) -> dict:
    """The bars of the spec's OWN cell, keyed by asset for run_spec.

    A spec with no declared timeframe is a legacy daily, matching the screen.
    """
    tf = spec["universe"].get("timeframe", "1d")
    return {a: bars_by_cell[(a, tf)] for a in spec["universe"]["assets"]}


def truncate_bars(bars_by_cell: dict, data_end: dict) -> dict:
    """Cut each cell's bars at the last date a past gauntlet run recorded.

    `bars_by_cell` is {(asset, timeframe): [bar, ...]} as load_cell_data
    returns it; `data_end` is a bundle's config.json field, {cell_id: last
    bar date string} with cell_id = cells.cell_id(asset, timeframe)
    ("BTCUSD_1d") and the date string carried verbatim from the CSV (bare
    `YYYY-MM-DD` for legacy crypto, `YYYY-MM-DD HH:MM:SS` for fx/etf). The
    comparison is date-only, like every other boundary in this module. A cell
    with no recorded end is returned whole: the caller decides whether that
    is acceptable.
    """
    out = {}
    for cell, bars in bars_by_cell.items():
        end = data_end.get(cells.cell_id(cell[0], cell[1]))
        out[cell] = bars if end is None else [
            b for b in bars if _date_le(str(b["date"]), end)]
    return out


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


def write_gauntlet_artifacts(art_dir: Path, spec: dict, oos_trades: list[dict],
                             mc_summary: dict, metrics: dict, cutoff: str,
                             data_hashes: dict, data_end: dict,
                             group_context: dict, *, protocol: str) -> Path:
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
        {"protocol": protocol, "cutoff": cutoff, "metrics": metrics,
         "data_sha256": data_hashes, "data_end": data_end,
         "group_context": group_context,
         "spec": spec}, indent=1, sort_keys=True), encoding="utf-8")
    return bundle



def evaluate_gates(is_trades, oos_trades, stress_oos_trades, is_vol: float,
                   oos_vol: float, seed: int, train_sharpe: float | None):
    """The six gates in FAIL_ORDER over this strategy's OWN evidence.
    Returns (passed, fail_reason|None, metrics, mc_summary). The group
    statistics evaluate_spec used to add (deflated Sharpe, trials, PBO,
    plateau) are NOT here: under v6.1 they follow in a gauntlet_stats entry."""
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
    stress_net = compound(contributions(stress_oos_trades))
    metrics = {
        "is_edge_per_trade": is_edge, "oos_edge_per_trade": oos_edge,
        "edge_decay_pct": decay, "mc_p05_equity": mc_p05,
        "p_ruin": mc["p_ruin"], "cost_stress_net_pnl": stress_net,
        "protocol": PROTOCOL_V61, "is_edge_raw": is_raw,
        "oos_edge_raw": oos_raw, "is_vol": is_vol, "oos_vol": oos_vol,
        "train_sharpe": train_sharpe,
    }
    mc_summary = {"seed": seed, "paths": MC_PATHS, "p05": mc_p05,
                  "p25": percentile(mc["terminals"], 0.25),
                  "p50": percentile(mc["terminals"], 0.50),
                  "p75": percentile(mc["terminals"], 0.75),
                  "p_ruin": mc["p_ruin"], "ruin_level": RUIN_LEVEL}
    checks = {"sharpe_floor": train_sharpe is None or train_sharpe >= SR_FLOOR,
              "oos_negative": oos_net > 0,
              "edge_decay": decay is not None and decay > DECAY_MIN_PCT,
              "mc_p05": mc_p05 > MC_P05_MIN,
              "p_ruin": mc["p_ruin"] < P_RUIN_MAX,
              "cost_stress": stress_net > 0}
    assert checks.keys() == set(FAIL_ORDER), (
        f"gate battery and FAIL_ORDER disagree: "
        f"computed-not-declared={sorted(checks.keys() - set(FAIL_ORDER))}, "
        f"declared-not-computed={sorted(set(FAIL_ORDER) - checks.keys())}")
    for name in FAIL_ORDER:
        if not checks[name]:
            return False, name, metrics, mc_summary
    return True, None, metrics, mc_summary


def evaluate_standalone(spec: dict, spec_bars: dict, cutoff: str,
                        perturb: bool) -> dict:
    """One candidate, end to end, from its own bars only: the verdict and
    every per-strategy metric _evaluate_candidate records, minus the group
    ones (plateau_ok, haircut, trials, sibling_group_n, sim_cache,
    trials_alignment). Same formulas, same call order as gauntlet.py's
    registry pass, so chain parity (tools/gauntlet_parity.py) holds."""
    sid = spec["strategy_id"]
    res = run_spec(spec, spec_bars)
    stress_res = run_spec(stressed(spec), spec_bars)
    is_t, oos_t = split_trades(res["trades"], cutoff)
    _, stress_oos = split_trades(stress_res["trades"], cutoff)
    assets = spec["universe"]["assets"]
    is_vol = window_vol(spec_bars, assets, "", cutoff)
    oos_vol = window_vol(spec_bars, assets, cutoff, "9999-12-31")
    dated = daily_returns_with_dates(res["equity"])
    train_rets = [r for d, r in dated if _date_le(d, cutoff)]
    train_sharpe = _annualized_sharpe_from_returns(train_rets)
    passed, reason, metrics, mc_summary = evaluate_gates(
        is_t, oos_t, stress_oos, is_vol, oos_vol,
        seed=int(sid, 16) % (2 ** 31), train_sharpe=train_sharpe)
    spec_class = spec["universe"].get("asset_class", "crypto")
    eras = cells.CLASSES.get(spec_class, {}).get("eras", ())
    if eras:
        metrics["era_summary"] = era_summary(res["trades"], eras)
    metrics["exit_reasons_is"] = exit_reason_counts(is_t)
    metrics["exit_reasons_oos"] = exit_reason_counts(oos_t)
    metrics["open_at_end"] = bool(res["metrics"]["open_at_end"])
    br = _benchmark_relative(spec, spec_bars, compound(contributions(oos_t)), cutoff)
    if br is not None:
        metrics["benchmark_relative"] = br
    train_dates = [d for d, _ in res["equity"] if _date_le(d, cutoff)]
    metrics["walkforward"] = dict(
        walkforward_report([t for t in res["trades"]
                            if _date_le(t["entry_date"], cutoff)],
                           train_dates, n_folds=3, purge_bars=PURGE_BARS),
        window="train")
    if perturb:
        def _score(pspec):
            r = run_spec(pspec, spec_bars)
            return annualized_sharpe([(d, v) for d, v in r["equity"]
                                      if _date_le(d, cutoff)])
        metrics["perturbation"] = sensitivity(spec, train_sharpe, _score,
                                              dense_only=True)
    else:
        metrics["perturbation"] = None
    btc = spec_bars.get("BTCUSD") or spec_bars[sorted(spec_bars)[0]]
    metrics["regime"] = {"window": "oos",
                         "buckets": regime_split(oos_t, regime_by_date(btc))}
    return {"sid": sid, "passed": passed, "reason": reason, "metrics": metrics,
            "mc_summary": mc_summary, "oos_trades": oos_t}
