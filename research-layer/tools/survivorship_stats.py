"""Gap statistic, stratified two-way bootstrap, pre-registered verdict and
report for the crypto survivorship study (design doc 2026-11-06, s5 and s6).

The decision rule is fixed before any study number exists; TAU, RESAMPLES,
SEED, ALPHA and the verdict boundaries must not be tuned.

Universe A = S + R columns; universe B = S + F + N + D columns. Every "gap"
is A minus B.
"""
from __future__ import annotations

import numpy as np

from tools.survivorship_universe import STRATA

TAU = 0.10
RESAMPLES = 10_000
SEED = 20261106
ALPHA = 0.05

A_STRATA = ("S", "R")
B_STRATA = ("S", "F", "N", "D")


def matrix(rows: list[dict], tkeys: list[str], pairs: list[str]) -> np.ndarray:
    """Templates x cells matrix of m. Raises ValueError on a missing cell."""
    cell = {}
    for r in rows:
        k = (r["tkey"], r["pair"])
        if k in cell:
            raise ValueError(f"duplicate cell: template {k[0]!r} pair {k[1]!r}")
        cell[k] = float(r["m"])
    out = np.empty((len(tkeys), len(pairs)), dtype=float)
    for i, t in enumerate(tkeys):
        for j, p in enumerate(pairs):
            if (t, p) not in cell:
                raise ValueError(f"missing cell: template {t!r} pair {p!r}")
            out[i, j] = cell[(t, p)]
    return out


def gap(m: np.ndarray, a_idx, b_idx) -> float:
    """G = mean over t of (mean of m[t, A] minus mean of m[t, B])."""
    a = np.asarray(a_idx, dtype=int)
    b = np.asarray(b_idx, dtype=int)
    return float(np.mean(m[:, a].mean(axis=1) - m[:, b].mean(axis=1)))


def bootstrap(m: np.ndarray, strata_idx: dict, resamples: int = RESAMPLES,
              seed: int = SEED) -> np.ndarray:
    """Resampled G values: templates drawn with replacement, then cells drawn
    with replacement within each stratum (STRATA order). The S draw is shared
    by both universes."""
    rng = np.random.default_rng(seed)
    T = m.shape[0]
    out = np.empty(resamples, dtype=float)
    empty = np.empty(0, dtype=int)
    for k in range(resamples):
        t = rng.integers(0, T, T)
        drawn = {}
        for s in STRATA:
            idx = np.asarray(strata_idx[s], dtype=int)
            n_s = len(idx)
            drawn[s] = idx[rng.integers(0, n_s, n_s)] if n_s else empty
        a = np.concatenate([drawn["S"], drawn["R"]])
        b = np.concatenate([drawn["S"], drawn["F"], drawn["N"], drawn["D"]])
        mt = m[t]
        out[k] = np.mean(mt[:, a].mean(axis=1) - mt[:, b].mean(axis=1))
    return out


def interval(gstar: np.ndarray) -> tuple[float, float]:
    """Percentile 95% interval [L, U] of the bootstrap G* values."""
    lo, hi = np.percentile(gstar, [100 * ALPHA / 2, 100 * (1 - ALPHA / 2)])
    return float(lo), float(hi)


def verdict(G: float, L: float, U: float, tau: float = TAU) -> str:
    """Pre-registered rule (design s5): the rows are applied top to bottom and
    the first that matches is the verdict: MATERIAL, then NOT MATERIAL
    (returned as "REVERSE" when also U < 0), then INCONCLUSIVE (anything
    else)."""
    if G >= tau and L > 0:
        return "MATERIAL"
    if U < tau:
        return "REVERSE" if U < 0 else "NOT MATERIAL"
    return "INCONCLUSIVE"


def verdict_text(v: str) -> str:
    """How a verdict is printed: REVERSE is NOT MATERIAL with a flag (s5)."""
    if v == "REVERSE":
        return "NOT MATERIAL, flagged REVERSE (U < 0: our universe is harder than PIT)"
    return v


def primary(m: np.ndarray, strata_idx: dict, resamples: int = RESAMPLES,
            seed: int = SEED, tau: float = TAU) -> dict:
    """G, [L, U] and the verdict for one arm."""
    a = np.concatenate([strata_idx[s] for s in A_STRATA])
    b = np.concatenate([strata_idx[s] for s in B_STRATA])
    G = gap(m, a, b)
    L, U = interval(bootstrap(m, strata_idx, resamples, seed))
    return {"G": G, "L": L, "U": U, "tau": tau, "verdict": verdict(G, L, U, tau)}


def _mean(xs) -> float:
    xs = list(xs)
    return float(np.mean(xs)) if xs else float("nan")


def secondaries(merged: dict, strata: dict, family: dict) -> dict:
    """Secondary readouts (never decision-bearing). `merged` maps
    "tkey|pair|tf" -> union of the full-run row (m, m_none, hold_sharpe), the
    train row (screen_pass) and the gauntlet row (passed; absent for a
    non-passer, which counts as not passing). `strata` maps pair -> stratum,
    `family` maps tkey -> family name. All gaps are A minus B; rates are over
    all template-cells of each universe."""
    cells = []
    for key, row in merged.items():
        parts = key.split("|")
        cells.append((parts[0], parts[1], strata[parts[1]], row))
    univ = {"A": A_STRATA, "B": B_STRATA}

    screen_rate, gauntlet_rate, mh_mean, mh_excl = {}, {}, {}, {}
    hold_by_pair = {"A": {}, "B": {}}
    for u, members in univ.items():
        sel = [c for c in cells if c[2] in members]
        n = len(sel)
        nan = float("nan")
        screen_rate[u] = sum(bool(c[3].get("screen_pass")) for c in sel) / n if n else nan
        gauntlet_rate[u] = sum(bool(c[3].get("passed")) for c in sel) / n if n else nan
        diffs = [c[3]["m"] - c[3]["hold_sharpe"] for c in sel
                 if c[3].get("hold_sharpe") is not None]
        mh_mean[u] = _mean(diffs)
        mh_excl[u] = sum(c[3].get("hold_sharpe") is None for c in sel)
        for c in sel:
            if c[3].get("hold_sharpe") is not None:
                hold_by_pair[u].setdefault(c[1], []).append(c[3]["hold_sharpe"])
    hold_mean = {u: _mean(_mean(v) for v in hold_by_pair[u].values()) for u in univ}

    by_stratum: dict = {}
    none_counts = {s: 0 for s in STRATA}
    fam_m: dict = {"A": {}, "B": {}}
    for tkey, pair, s, row in cells:
        by_stratum.setdefault(s, []).append(row["m"])
        none_counts[s] += bool(row.get("m_none"))
        for u, members in univ.items():
            if s in members:
                fam_m[u].setdefault(family[tkey], []).append(row["m"])
    return {
        "screen_rate": screen_rate,
        "screen_gap": screen_rate["A"] - screen_rate["B"],
        "gauntlet_rate": gauntlet_rate,
        "gauntlet_gap": gauntlet_rate["A"] - gauntlet_rate["B"],
        "m_minus_hold_mean": mh_mean,
        "m_minus_hold_gap": mh_mean["A"] - mh_mean["B"],
        "m_minus_hold_excluded": mh_excl,
        "hold_mean": hold_mean,
        "hold_gap": hold_mean["A"] - hold_mean["B"],
        "stratum_mean_m": {s: _mean(by_stratum[s]) for s in STRATA if s in by_stratum},
        "family_mean_m": {u: {f: _mean(v) for f, v in sorted(d.items())}
                          for u, d in fam_m.items()},
        "none_counts": none_counts,
    }


def _f(x) -> str:
    return "n/a" if x is None or x != x else f"{x:.4f}"


def report(result: dict) -> str:
    """Markdown report. `result` keys: tf (str), verdict, G, L, U, tau,
    stratum_sizes {S,R,F,N,D: int}, stratum_means {stratum: mean m},
    secondaries (the dict from `secondaries`). Optional: n_templates,
    resamples, seed."""
    r = result
    sec = r["secondaries"]
    out = [f"# Survivorship study report: {r['tf']} arm", "",
           f"**Verdict: {verdict_text(r['verdict'])}**" + (
               "" if r["tf"] == "1d" else " (secondary, not decision-bearing)"), "",
           f"- G = {_f(r['G'])}",
           f"- 95% interval [L, U] = [{_f(r['L'])}, {_f(r['U'])}]",
           f"- tau = {r['tau']:.2f}"]
    if "n_templates" in r:
        out.append(f"- templates = {r['n_templates']}")
    if "resamples" in r:
        out.append(f"- resamples = {r['resamples']}, seed = {r.get('seed')}")
    out += ["", "## Strata", "", "| stratum | cells | mean m |", "|---|---|---|"]
    for s in STRATA:
        out.append(f"| {s} | {r['stratum_sizes'].get(s, 0)} | "
                   f"{_f(r['stratum_means'].get(s))} |")
    out += ["", "## Secondaries", "", "secondary, not decision-bearing", "",
            "| measure | A | B | gap (A minus B) |", "|---|---|---|---|"]
    out.append(f"| screen pass rate | {_f(sec['screen_rate']['A'])} | "
               f"{_f(sec['screen_rate']['B'])} | {_f(sec['screen_gap'])} |")
    out.append(f"| gauntlet pass rate | {_f(sec['gauntlet_rate']['A'])} | "
               f"{_f(sec['gauntlet_rate']['B'])} | {_f(sec['gauntlet_gap'])} |")
    out.append(f"| mean (m minus hold) | {_f(sec['m_minus_hold_mean']['A'])} | "
               f"{_f(sec['m_minus_hold_mean']['B'])} | {_f(sec['m_minus_hold_gap'])} |")
    out.append(f"| mean hold Sharpe | {_f(sec['hold_mean']['A'])} | "
               f"{_f(sec['hold_mean']['B'])} | {_f(sec['hold_gap'])} |")
    ex = sec["m_minus_hold_excluded"]
    out += ["", f"(m minus hold) excluded for a None hold Sharpe: A {ex['A']}, B {ex['B']}.",
            "", "### None counts (m_none, scored 0.0) per stratum", "",
            "| " + " | ".join(STRATA) + " |", "|" + "---|" * len(STRATA),
            "| " + " | ".join(str(sec["none_counts"].get(s, 0)) for s in STRATA) + " |",
            "", "### Per-family mean m", "", "| family | A | B |", "|---|---|---|"]
    fams = sorted(set(sec["family_mean_m"]["A"]) | set(sec["family_mean_m"]["B"]))
    for f in fams:
        out.append(f"| {f} | {_f(sec['family_mean_m']['A'].get(f))} | "
                   f"{_f(sec['family_mean_m']['B'].get(f))} |")
    out.append("")
    return "\n".join(out)
