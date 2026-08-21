"""The quarantine -> live gate, per quarantine-live-protocol-v1 (entry 2515).

Judgment lives here; OBSERVATION lives in quarantine.py. That separation is
deliberate and mirrors screen/gauntlet: the daily runner records what the book
did without deciding anything, and this module decides without recording
anything new.

THE FOUNDING ASYMMETRY. Sixty days can disprove but cannot confirm. Clearing
PSR 0.95 on a 60-day record needs an annualized Sharpe near 4.14 even against a
zero benchmark, while the strategies here carry train Sharpes near 1.0 to 1.3.
So the gate has two arms with different horizons:

  KILL      from MIN_TRADING_DAYS onward, bury a book behaving nothing like the
            one that was modelled: forward terminal equity below the FIRST
            percentile of a cone REBUILT at the matched forward trade count.
            One percent, not five, because burial is terminal on this chain.
  GRADUATE  plain PSR of the forward daily returns against a benchmark of ZERO
            -- no deflation by search burden, which was already paid at the
            gauntlet -- with the threshold set by Benjamini-Hochberg over the
            cohort under simultaneous assessment.

This module WRITES NOTHING. It returns a report; chaining any state change from
it is a separate, Coen-gated step.
"""
from __future__ import annotations

import random
from collections import defaultdict

from .stats import moments, sharpe, percentile, psr

KILL_PCTILE = 0.01       # burial line on the rebuilt cone; 1 percent, not 5
BH_Q = 0.05              # Benjamini-Hochberg false discovery rate
CONE_PATHS = 2000        # bootstrap paths for the rebuilt cone
TRADING_DAYS = 365       # 24x7 crypto

# Below this many CLOSED forward trades the cone is not built and no burial
# occurs. protocol-v4 held that a gate passing on absence of evidence is not a
# gate; this gate BURIES, so the same reasoning runs the other way and silence
# must not condemn.
#
# MEASURED CONSEQUENCE, recorded rather than discovered later: the three
# strategies in quarantine close roughly 2 to 3.6 trades per 60 days at their
# train-window rates, so this floor means the kill arm typically cannot fire on
# them until around 150 days. That is the honest cost of a conservative burial
# rule meeting a low trade frequency, and it is the direction to err in.
MIN_FORWARD_TRADES = 5


def forward_series(rows: list[dict]) -> list[tuple[str, float]]:
    """The strategy's forward equity curve: the equal-weight mean across assets
    of the rebased per-asset equity quarantine.observe_day already records.

    That is engine.run_spec's own combination rule. A date missing any asset is
    DROPPED rather than averaged over whichever assets reported, which would
    silently reweight the book toward one leg.
    """
    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    assets = set()
    for r in rows:
        by_date[r["date"]][r["asset"]] = r["equity"]
        assets.add(r["asset"])
    out = []
    for date in sorted(by_date):
        got = by_date[date]
        if set(got) != assets:
            continue
        out.append((date, sum(got.values()) / len(got)))
    return out


def daily_returns(series: list[tuple[str, float]]) -> list[float]:
    return [series[i][1] / series[i - 1][1] - 1
            for i in range(1, len(series)) if series[i - 1][1] > 0]


def closed_forward_trades(rows: list[dict]) -> int:
    """Closed trades across every asset. observe_day writes action='exit' on
    the bar a position closes, and never enters on the bar it exits."""
    return sum(1 for r in rows if r.get("action") == "exit")


def rebuilt_cone(train_contributions: list[float], n_trades: int,
                 seed: int = 0, paths: int = CONE_PATHS) -> dict | None:
    """Terminal-equity distribution over exactly `n_trades` draws.

    Rebuilt at the MATCHED forward trade count rather than reusing the cone in
    the gauntlet verdict, which spans the strategy's full train trade count and
    is not on the same footing as a short forward record -- quarantine.py's
    CONE_CAVEAT says exactly this. Returns None below MIN_FORWARD_TRADES, which
    the kill arm treats as "cannot judge", never as "fails".
    """
    if n_trades < MIN_FORWARD_TRADES or not train_contributions:
        return None
    rng = random.Random(seed)
    terminals = []
    for _ in range(paths):
        eq = 1.0
        for _ in range(n_trades):
            eq *= 1 + rng.choice(train_contributions)
        terminals.append(eq)
    terminals.sort()
    return {"n_trades": n_trades, "paths": paths, "terminals": terminals,
            "p01": percentile(terminals, KILL_PCTILE),
            "p50": percentile(terminals, 0.50)}


def kill_verdict(forward_terminal: float, cone: dict | None) -> bool:
    """True when the forward record is grossly outside what was modelled."""
    if cone is None:
        return False
    return forward_terminal < cone["p01"]


def forward_psr(returns: list[float], sr_star: float = 0.0) -> float:
    """PSR of the forward record against `sr_star`, default ZERO.

    The deflation protocol-v3 placed here is removed by
    quarantine-live-protocol-v1: the search burden was paid at the gauntlet,
    and a quarantine record is a single pre-registered hypothesis rather than a
    selected maximum, so deflating it again counts the same search twice.
    """
    if len(returns) < 2:
        return 0.0
    _, _, skew, kurt = moments(returns)
    return psr(sharpe(returns), sr_star, len(returns), skew, kurt)


def benjamini_hochberg(pvalues: list[float], q: float = BH_Q) -> set[int]:
    """Indices rejected by Benjamini-Hochberg at false discovery rate `q`.

    Chosen over Bonferroni because this is a discovery pipeline expecting
    several genuine survivors, which cares about the PROPORTION of false
    graduates among graduates rather than about avoiding any single false
    positive. It adapts: the weakest member of a uniformly strong cohort still
    clears at q, while a lone strong strategy among m faces q/m, Bonferroni's
    bar.
    """
    m = len(pvalues)
    if m == 0:
        return set()
    order = sorted(range(m), key=lambda i: pvalues[i])
    largest_k = 0
    for k, i in enumerate(order, start=1):
        if pvalues[i] <= k * q / m:
            largest_k = k
    return set(order[:largest_k])


def assess(cases: list[dict], min_days: int, q: float = BH_Q) -> dict:
    """Judge every quarantined strategy. Returns a report; writes nothing.

    `cases` are {sid, rows, train_contributions}. A strategy is eligible once
    its forward record reaches `min_days` observations. The kill arm runs per
    strategy; graduation runs over the ELIGIBLE COHORT, because the
    concurrent-confirmation burden is a property of the cohort rather than of
    any strategy in it.
    """
    report: dict[str, dict] = {}
    cones: dict[str, dict | None] = {}
    for case in cases:
        sid = case["sid"]
        series = forward_series(case["rows"])
        rets = daily_returns(series)
        n_trades = closed_forward_trades(case["rows"])
        # computed ONCE and reused by the kill arm below: rebuilding it there
        # would give two cones that agree only as long as the two seed
        # expressions stay in sync, which is a silent-divergence waiting to
        # happen on a gate that buries strategies.
        cone = rebuilt_cone(case["train_contributions"], n_trades,
                            seed=int(sid, 16) % (2 ** 31) if _is_hex(sid) else 0)
        cones[sid] = cone
        terminal = series[-1][1] if series else 1.0
        report[sid] = {
            "eligible": len(series) >= min_days,
            "forward_days": len(series),
            "forward_trades": n_trades,
            "terminal": terminal,
            "cone_p01": None if cone is None else cone["p01"],
            "psr": forward_psr(rets),
            "verdict": "hold",
        }

    eligible = [s for s, r in report.items() if r["eligible"]]

    # Kill first: a strategy the record has already disproved must not also be
    # counted in the cohort that sets everyone else's graduation bar.
    for sid in eligible:
        r = report[sid]
        if kill_verdict(r["terminal"], cones[sid]):
            r["verdict"] = "graveyard"

    survivors = [s for s in eligible if report[s]["verdict"] == "hold"]
    if survivors:
        pvals = [1.0 - report[s]["psr"] for s in survivors]
        for idx in benjamini_hochberg(pvals, q):
            report[survivors[idx]]["verdict"] = "live"
        for s in survivors:
            report[s]["cohort_size"] = len(survivors)
    return report


def _is_hex(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# The runner. Everything above is pure; everything below touches the chain, and
# only ever behind the same refusal guard and --dry-run discipline the screen
# and the gauntlet use.
# ---------------------------------------------------------------------------

PROTOCOL = "quarantine-live-protocol-v1"


def quarantined_specs(registry) -> dict[str, dict]:
    """{sid: spec} for every strategy currently in the quarantine state."""
    specs, state = {}, {}
    for e in registry.entries():
        p = e.get("payload", {})
        if not isinstance(p, dict):
            continue
        if e["entry_type"] == "strategy_registered":
            specs[p["strategy_id"]] = p
        elif e["entry_type"] == "state_change" and p.get("strategy_id"):
            state[p["strategy_id"]] = p.get("to")
    return {s: specs[s] for s, st in state.items()
            if st == "quarantine" and s in specs}


def quarantine_rows(registry) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    for e in registry.entries():
        if e["entry_type"] == "quarantine_decision":
            rows[e["payload"]["strategy_id"]].append(e["payload"])
    return rows


def train_contributions(spec: dict, data_dir, cutoff: str) -> list[float]:
    """Per-trade portfolio contributions over the TRAIN window.

    Recomputed by re-running the spec rather than read from the screen's
    trades.csv, which deliberately omits notional_frac -- its byte format is
    pinned by a regression test -- so the contributions the cone needs are not
    recoverable from the artifact. The spec is content-addressed and the bars
    are hashed on the chain, so the re-run is reproducible.
    """
    from .screen import load_bars
    from .engine import run_spec
    from .gauntlet import contributions
    universe = spec["universe"]
    tf = universe.get("timeframe", "1d")
    bars = {a: load_bars(data_dir, a, cutoff, timeframe=tf)
            for a in universe["assets"]}
    res = run_spec(spec, bars)
    return contributions([t for t in res["trades"] if t["entry_date"] <= cutoff])


def run(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    from pathlib import Path
    from .registry import Registry
    from .quarantine import MIN_TRADING_DAYS

    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, default=here / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path, default=here / "data")
    ap.add_argument("--cutoff", default="2023-12-31")
    ap.add_argument("--min-days", type=int, default=MIN_TRADING_DAYS)
    ap.add_argument("--q", type=float, default=BH_Q)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    if not args.dry_run and not any(
            e["entry_type"] == "note"
            and str(e["payload"].get("text", "")).startswith(PROTOCOL)
            for e in registry.entries()):
        print(f"REFUSED: no '{PROTOCOL}' note on the chain. Chain the gate "
              f"before it may move a single strategy.")
        return 1

    specs = quarantined_specs(registry)
    rows = quarantine_rows(registry)
    if not specs:
        print("no strategies in quarantine; nothing to assess")
        return 0

    cases = [{"sid": sid,
              "rows": rows.get(sid, []),
              "train_contributions": train_contributions(spec, args.data_dir,
                                                         args.cutoff)}
             for sid, spec in sorted(specs.items())]
    report = assess(cases, min_days=args.min_days, q=args.q)

    for sid in sorted(report):
        r = report[sid]
        cone = "n/a" if r["cone_p01"] is None else f"{r['cone_p01']:.4f}"
        print(f"{sid}  days={r['forward_days']:>4} trades={r['forward_trades']:>3} "
              f"terminal={r['terminal']:.4f} cone_p01={cone:>7} "
              f"psr={r['psr']:.4f}  -> {r['verdict'].upper()}"
              + ("" if r["eligible"] else "  (not yet eligible)"))

    n_live = sum(1 for r in report.values() if r["verdict"] == "live")
    n_dead = sum(1 for r in report.values() if r["verdict"] == "graveyard")
    if args.dry_run:
        print(f"\nDRY RUN - {len(report)} assessed, {n_live} would go live, "
              f"{n_dead} would be buried; nothing written.")
        return 0

    written = 0
    try:
        for sid in sorted(report):
            r = report[sid]
            if r["verdict"] == "hold":
                continue
            registry.record_verdict(
                sid, "live_gate", "pass" if r["verdict"] == "live" else "fail",
                {k: r[k] for k in ("forward_days", "forward_trades", "terminal",
                                   "cone_p01", "psr", "eligible")}
                | {"protocol": PROTOCOL, "cohort_size": r.get("cohort_size")},
                "0" * 64)
            written += 1
        for sid in sorted(report):
            r = report[sid]
            if r["verdict"] == "live":
                registry.record_state_change(sid, "live", f"{PROTOCOL} pass")
                written += 1
            elif r["verdict"] == "graveyard":
                registry.record_state_change(
                    sid, "graveyard", "forward record below the rebuilt cone")
                written += 1
    except BaseException:
        print(f"\nPARTIAL WRITE: {written} entries chained before failure.",
              file=sys.stderr)
        raise

    print(f"\n{len(report)} assessed: {n_live} -> live, {n_dead} -> graveyard.")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(run())
