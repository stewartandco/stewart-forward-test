"""Effectively-independent trial counting for gauntlet protocol-v3.

Bailey & Lopez de Prado's deflation needs the number of INDEPENDENT trials
and the dispersion of their Sharpe ratios. Counting every registered
strategy over-counts: a sibling sweep is one idea tested at several
settings, and pooling structurally different families inflates the variance
term with genuine edge differences rather than noise.

This module clusters strategies by the correlation distance of their daily
returns and reports the effective trial count. Everything here is
DETERMINISTIC and ORDER-INDEPENDENT: no randomness, no initialization, ties
broken lexicographically. An auditor re-implementing it must get identical
output, or the gate is not reproducible.
"""
from __future__ import annotations

import math


def correlation(a: list[float], b: list[float]) -> float:
    """Pearson correlation over the overlapping prefix. Zero-variance input
    correlates with nothing: returns 0.0 rather than raising."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def distance(rho: float) -> float:
    """Lopez de Prado's correlation distance: rho=1 -> 0, rho=-1 -> 1."""
    return math.sqrt(0.5 * (1 - rho))


def distance_matrix(returns_by_id: dict[str, list[float]]) -> dict:
    """{(id_i, id_j): distance} for every ordered pair, ids sorted."""
    ids = sorted(returns_by_id)
    dmat = {}
    for i, a in enumerate(ids):
        dmat[(a, a)] = 0.0
        for b in ids[i + 1:]:
            d = distance(correlation(returns_by_id[a], returns_by_id[b]))
            dmat[(a, b)] = d
            dmat[(b, a)] = d
    return dmat
