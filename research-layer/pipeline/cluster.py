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


def agglomerate(ids: list[str], dmat: dict) -> list[tuple]:
    """Average-linkage agglomerative merge history.

    Returns [(cluster_a, cluster_b), ...] where each element is a pair of
    frozensets merged at that step, oldest first. Deterministic: `ids` is
    sorted internally, and ties on merge distance are broken by the
    lexicographically smallest (min(a), min(b)) pair, so the result cannot
    depend on the order ids were supplied in."""
    ids = sorted(ids)
    clusters = [frozenset([i]) for i in ids]
    history = []
    while len(clusters) > 1:
        best = None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                ca, cb = clusters[x], clusters[y]
                d = sum(dmat[(i, j)] for i in ca for j in cb) / (len(ca) * len(cb))
                # canonicalise: after round 1 the merged cluster is
                # appended to the END of `clusters`, so list position stops
                # tracking id order. Sorting the pair keeps the tie-break
                # genuinely lexicographic and re-implementable.
                lo, hi = sorted((min(ca), min(cb)))
                key = (round(d, 12), lo, hi)
                if best is None or key < best[0]:
                    best = (key, x, y)
        _, x, y = best
        ca, cb = clusters[x], clusters[y]
        history.append((ca, cb))
        clusters = ([c for k, c in enumerate(clusters) if k not in (x, y)]
                    + [ca | cb])
    return history


def labels_for_k(history: list[tuple], ids: list[str], k: int) -> dict[str, int]:
    """Cluster assignment {id: index} after replaying the merge history until
    exactly k clusters remain. Cluster indices are assigned by the sorted
    order of each cluster's smallest member, so labels are stable."""
    ids = sorted(ids)
    clusters = [frozenset([i]) for i in ids]
    merges = max(0, len(ids) - k)
    for ca, cb in history[:merges]:
        clusters = [c for c in clusters if c != ca and c != cb] + [ca | cb]
    clusters.sort(key=min)
    return {i: idx for idx, c in enumerate(clusters) for i in c}


def silhouette(labels: dict[str, int], dmat: dict) -> list[float]:
    """Silhouette value per id, in sorted-id order. A point alone in its
    cluster scores 0.0 by convention."""
    ids = sorted(labels)
    groups: dict[int, list[str]] = {}
    for i in ids:
        groups.setdefault(labels[i], []).append(i)
    out = []
    for i in ids:
        own = groups[labels[i]]
        if len(own) == 1:
            out.append(0.0)
            continue
        a = sum(dmat[(i, j)] for j in own if j != i) / (len(own) - 1)
        b = min(sum(dmat[(i, j)] for j in g) / len(g)
                for lbl, g in groups.items() if lbl != labels[i])
        out.append(0.0 if max(a, b) == 0 else (b - a) / max(a, b))
    return out


def _sharpe(series: list[float]) -> float:
    n = len(series)
    if n < 2:
        return 0.0
    m = sum(series) / n
    var = sum((x - m) ** 2 for x in series) / n
    return m / math.sqrt(var) if var > 0 else 0.0


def _sample_variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def effective_trials(returns_by_id: dict[str, list[float]]):
    """(k, labels, cross_cluster_sharpe_variance).

    k is the number of effectively independent trials: the cluster count
    maximising the silhouette QUALITY score mean(S)/stdev(S) over
    k in [2, n-1] (card L6's criterion). k=n is excluded because every
    silhouette is 0 when all clusters are singletons.

    The variance returned is the sample variance across CLUSTER
    representatives, each representative being the mean daily Sharpe of its
    members — not the variance across all strategies, which is what
    protocol-v2 used and what over-deflated the gate."""
    ids = sorted(returns_by_id)
    n = len(ids)
    if n == 0:
        return 0, {}, 0.0
    if n == 1:
        return 1, {ids[0]: 0}, 0.0
    if n == 2:
        return 2, {ids[0]: 0, ids[1]: 1}, _sample_variance(
            [_sharpe(returns_by_id[i]) for i in ids])

    dmat = distance_matrix(returns_by_id)
    history = agglomerate(ids, dmat)
    best = None            # (quality, mean_s, -k) maximised
    for k in range(2, n):
        labels = labels_for_k(history, ids, k)
        vals = silhouette(labels, dmat)
        mean_s = sum(vals) / len(vals)
        sd = math.sqrt(_sample_variance(vals))
        quality = mean_s / sd if sd > 0 else float("-inf")
        key = (quality, mean_s, -k)
        if best is None or key > best[0]:
            best = (key, k, labels)
    if best[0][0] == float("-inf"):
        # every candidate had zero silhouette spread: fall back to the k with
        # the highest mean silhouette, smallest k on ties
        best = None
        for k in range(2, n):
            labels = labels_for_k(history, ids, k)
            mean_s = sum(silhouette(labels, dmat)) / n
            key = (mean_s, -k)
            if best is None or key > best[0]:
                best = (key, k, labels)
    _, k, labels = best

    groups: dict[int, list[str]] = {}
    for i in ids:
        groups.setdefault(labels[i], []).append(i)
    reps = [sum(_sharpe(returns_by_id[i]) for i in g) / len(g)
            for g in groups.values()]
    return k, labels, _sample_variance(reps)
