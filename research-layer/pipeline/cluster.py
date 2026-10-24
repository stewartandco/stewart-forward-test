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

A numpy fast path (SP4 clustering-perf, 2026-08-28) mirrors the reference
implementation exactly: `effective_trials` dispatches to it for rectangular
input and falls back to the hand-written functions otherwise. The reference
functions below are the CONTRACT; test_cluster_np.py holds the two paths
identical, and the tie-break key round(d, 12) is what absorbs BLAS-vs-loop
float summation differences. The DOMINANT divergence class was not that
rounding tail but the rho = 1 clamp discontinuity on exact-duplicate rows
(BLAS landing 1ulp under the clamp where the reference lands on or over
it: 0.0 vs ~1e-8, unabsorbable at 12dp); it is closed by the duplicate pin
in _distance_matrix_np, which reproduces the reference-computed distance
for byte-identical positive-variance rows. What remains is the rounding
tail, and that absorption is probabilistic, not absolute: two summation
orders can land within ulps of a half-1e-12 rounding boundary and still
round apart, so the real ship bar is the recorded-data identity proof in
tools/verify_cluster_identity.py, not the rounding alone. The same tool
proves each later performance change (step 7, 2026-10-03: the array
bookkeeping in _agglomerate_np and the in-place distance arithmetic)
bit-identical, on real cached series, to the revision before it.
"""
from __future__ import annotations

import math

import numpy as np


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
    """Lopez de Prado's correlation distance: rho=1 -> 0, rho=-1 -> 1.

    rho is clamped to [-1, 1]: correlation() on near-identical series can
    overshoot 1.0 by a few ulp (first seen 2026-08-24, fx sibling specs on an
    intersection window: rho = 1 + 1.1e-16 made sqrt() raise), and the clamp
    is the honest fix -- a float artifact, not a data question.
    """
    rho = max(-1.0, min(1.0, rho))
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


def _returns_matrix(returns_by_id: dict[str, list[float]]):
    """(sorted ids, n x L float64 matrix), or (ids, None) when the series
    lengths differ. The fast path needs rectangular input; gauntlet's
    check_aligned guarantees it in production, and ragged direct callers
    fall back to the reference implementation.

    Constant NONZERO rows also force the reference path: whether their
    variance rounds to exactly zero depends on summation order, so the two
    paths can classify them differently. Exact-zero rows are safe (mean and
    residuals are exactly 0.0 either way) and stay on the fast path. The
    check only applies when L >= 2: below that neither path computes a
    variance (correlation is 0.0 by the n < 2 early return), so no
    divergence is possible."""
    ids = sorted(returns_by_id)
    if not ids:
        return ids, None
    lengths = {len(returns_by_id[i]) for i in ids}
    if len(lengths) != 1:
        return ids, None
    X = np.asarray([returns_by_id[i] for i in ids], dtype=np.float64)
    if X.shape[1] >= 2:
        row_max = X.max(axis=1)
        row_min = X.min(axis=1)
        constant = row_max == row_min
        if np.any(constant & (row_max != 0.0)):
            return ids, None
    return ids, X


# Row block for the chunked n x n passes below. Chunking changes no value:
# every element is the same IEEE operation on the same operands, only the
# temporaries are (block x n) instead of (n x n).
_ROWS = 512


def _mirror_upper(D: "np.ndarray") -> None:
    """In place: D becomes triu(D, 1) + triu(D, 1).T, the original
    symmetrisation, without its two n x n temporaries. Off the diagonal the
    original computes x + 0.0 (upper) and 0.0 + x (lower), which equal x
    bit for bit for every x >= +0.0, and every entry here is the sqrt of a
    non-negative value. Diagonal blocks use the original expression
    verbatim; the diagonal itself is 0.0 + 0.0."""
    n = D.shape[0]
    for a in range(0, n, _ROWS):
        b = min(n, a + _ROWS)
        D[a:b, :a] = D[:a, a:b].T
        up = np.triu(D[a:b, a:b], 1)
        D[a:b, a:b] = up + up.T


def _distance_matrix_np(X: "np.ndarray") -> "np.ndarray":
    """Correlation-distance matrix over the rows of X, numpy form of
    distance_matrix(). Semantics matched to correlation()/distance():
    zero-variance rows correlate 0.0 with everything (distance sqrt(0.5)),
    rho clamped to [-1, 1], diagonal forced to 0.0, result exactly
    symmetric (the reference assigns (i, j) and (j, i) from one number;
    BLAS output is mirrored from the upper triangle to match).

    Memory (step 7, 2026-10-03): ONE n x n buffer. The arithmetic runs in
    place on the Gram matrix, the normalisation in row blocks, and the
    mirror by _mirror_upper. Each element goes through exactly the IEEE
    operations of the original expression
    sqrt(0.5 * (1 - clip((M @ M.T) / sqrt(outer(ss, ss))))), in the same
    order, so the result is bit-identical (tools/verify_cluster_identity.py
    proves it on real data).

    Duplicate-row pin: byte-identical positive-variance rows hit rho =
    1 +/- 1ulp differently under BLAS than under the reference's
    pow/multiply mix, a discontinuity at the clamp (0.0 vs ~1e-8, far
    beyond round(d, 12) absorption) that merge-order ties and the -inf
    silhouette disqualification both amplify. Every within-group pair of
    identical rows is therefore pinned to the REFERENCE-computed value,
    distance(correlation(s, s)) evaluated once per group in pure Python:
    usually exactly 0.0, but rho lands at 1 - 1ulp for ~0.09% of duplicate
    lists (pow-vs-multiply term drift) and the pinned value is then
    7.45e-9, exactly as the reference reports it. Zero-variance duplicate
    rows are excluded: the good-mask above already reproduces the
    reference's 0.0-correlation semantics (distance sqrt(0.5))."""
    n, L = X.shape
    if L < 2:
        D = np.zeros((n, n))
    else:
        M = X - X.mean(axis=1, keepdims=True)
        ss = np.einsum("ij,ij->i", M, M)
        good = ss > 0.0
        D = M @ M.T
        del M
        with np.errstate(invalid="ignore", divide="ignore"):
            for a in range(0, n, _ROWS):
                b = min(n, a + _ROWS)
                D[a:b] /= np.sqrt(np.outer(ss[a:b], ss))
        D[~good, :] = 0.0
        D[:, ~good] = 0.0
    np.clip(D, -1.0, 1.0, out=D)
    np.subtract(1.0, D, out=D)
    np.multiply(0.5, D, out=D)
    np.sqrt(D, out=D)
    _mirror_upper(D)
    if L >= 2 and n >= 2:
        _, inverse = np.unique(X, axis=0, return_inverse=True)
        inverse = np.asarray(inverse).ravel()
        for g in np.unique(inverse):
            members = np.flatnonzero(inverse == g)
            if len(members) < 2 or not good[members[0]]:
                continue
            s = X[members[0]].tolist()
            d = distance(correlation(s, s))
            D[np.ix_(members, members)] = d
            D[members, members] = 0.0
    return D


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


def _agglomerate_np(ids: list[str], D: "np.ndarray", *, overwrite: bool = False,
                    _after_merge=None) -> list[tuple]:
    """Average-linkage merge history on a numpy distance matrix; numpy form
    of agglomerate() with the identical (round(d, 12), lo, hi) tie-break.

    Slot invariant: a cluster lives at the slot of its smallest member's
    sorted-id index, so `keep` below is always the smaller-min-id side.
    _effective_trials_np replays history under the same invariant. It also
    means the reference key (round(d, 12), lo_id, hi_id) orders exactly as
    (round(d, 12), min(i, j), max(i, j)) on SLOT numbers, which is what the
    cache below holds.

    Pair order inside a history step is not part of the reference contract
    (labels_for_k unions the pair either way); steps here list the cluster
    containing the smaller min id first.

    Bookkeeping (step 7, 2026-10-03; cluster-profile-report.md s3A "V2"):
    the method is unchanged, only how the per-row best key is kept.
    - The per-row cache is held in arrays: cd (the rounded distance), clo /
      chi (the slot pair) and cj (the partner); `valid` marks a row with a
      partner.
    - row_best resolves ties with arrays. Python's round(v, 12) is applied
      to the DISTINCT values in the 2e-12 window only; among the candidates
      whose rounded distance is smallest, the smallest slot j is the
      smallest (lo, hi) pair (j < i gives lo = j < i; otherwise lo = i for
      every candidate and hi = j decides), so it is the per-element loop's
      answer.
    - The global best is the minimum key over the arrays.
    - Stale rows are the original's two tests as masks: the cached partner
      was keep or drop (full re-scan), or the row's cached rounded distance
      is within 2e-12 of its average distance to the merged cluster (a tie).
    - A row stale ONLY through such a tie gets an O(1) update, new key =
      min(cached key, key(i, keep)). That equals a full re-scan: its cached
      partner is neither keep nor drop and only column `keep` changed, and
      the cached key is the minimum key over ALL active j, because a value
      more than 2e-12 above the raw minimum always rounds strictly higher
      at 12 decimals. The invariant "every active row's cached key equals a
      fresh re-scan" is asserted after every merge by test_cluster_np.py
      (test_agglomerate_np_cache_matches_a_fresh_rescan_after_every_merge).
    Before this the all-zero rows of the live registry (distance exactly
    sqrt(0.5) to everything; 79 of them at 13,730 strategies) were re-keyed
    through a Python loop over nearly every active cluster on every merge:
    4.1 h of clustering at 13,730 strategies.

    `overwrite=True` lets the merge sums consume D in place (S is D), saving
    one n x n copy; the caller must not read D afterwards. `_after_merge`,
    for tests only, is called after every merge's cache update with
    (S, sizes, active, cd, clo, chi, cj).
    """
    ids = sorted(ids)
    n = len(ids)
    if n <= 1:
        return []
    if overwrite:
        S = np.asarray(D, dtype=np.float64)
    else:
        S = D.astype(np.float64, copy=True)
    sizes = np.ones(n, dtype=np.float64)
    active = np.ones(n, dtype=bool)
    members: list = [frozenset([i]) for i in ids]
    INF = float("inf")
    cd = np.full(n, INF)                       # cached round(d, 12)
    clo = np.full(n, n, dtype=np.int64)        # cached pair, low slot
    chi = np.full(n, n, dtype=np.int64)        # cached pair, high slot
    cj = np.full(n, -1, dtype=np.int64)        # cached partner slot
    valid = np.zeros(n, dtype=bool)

    def row_best(i: int) -> None:
        """Cache slot i's best merge partner under the reference key."""
        avg = S[i] / (sizes[i] * sizes)
        avg[~active] = INF
        avg[i] = INF
        raw = avg.min()
        if raw == INF:
            valid[i] = False
            cd[i] = INF
            cj[i] = -1
            return
        tie = np.flatnonzero(avg <= raw + 2e-12)
        if len(tie) == 1:
            j = int(tie[0])
            d = round(float(avg[j]), 12)
        else:
            vals = avg[tie]
            uniq = np.unique(vals)
            rounded = [round(float(u), 12) for u in uniq]
            d = min(rounded)
            ok = np.zeros(len(tie), dtype=bool)
            for u, r in zip(uniq, rounded):
                if r == d:
                    ok |= vals == u
            j = int(tie[ok].min())
        valid[i] = True
        cd[i] = d
        cj[i] = j
        clo[i] = min(i, j)
        chi[i] = max(i, j)

    for i in range(n):
        row_best(i)

    history = []
    for _ in range(n - 1):
        dd = np.where(valid & active, cd, INF)
        cand = np.flatnonzero(dd == dd.min())
        if len(cand) > 1:
            lo = clo[cand]
            cand = cand[lo == lo.min()]
            if len(cand) > 1:
                hi = chi[cand]
                cand = cand[hi == hi.min()]
        x = int(cand[0])
        y = int(cj[x])
        # keep = the slot whose cluster holds the smaller min id
        keep, drop = (x, y) if x < y else (y, x)
        history.append((members[keep], members[drop]))
        S[keep, :] += S[drop, :]
        S[:, keep] += S[:, drop]
        sizes[keep] = sizes[keep] + sizes[drop]
        active[drop] = False
        members[keep] = members[keep] | members[drop]
        members[drop] = None
        valid[drop] = False
        if active.sum() <= 1:
            break
        # stale: rows whose cached partner was one of the merged pair
        others = active & valid
        others[keep] = False
        part = others & ((cj == keep) | (cj == drop))
        # ties: the merged cluster cannot BEAT any surviving cached key on
        # distance (average linkage is reducible), but it can tie on the
        # rounded distance and win on (lo, hi)
        avg_new = S[keep] / (sizes[keep] * sizes)
        avg_new[~active] = INF
        avg_new[keep] = INF
        tie = np.isfinite(avg_new) & valid & ~part
        tie[keep] = False
        tie &= np.abs(avg_new - cd) <= 2e-12
        row_best(keep)
        for i in np.flatnonzero(part):
            row_best(int(i))
        for i in np.flatnonzero(tie):
            i = int(i)
            d = round(float(avg_new[i]), 12)
            lo, hi = (i, keep) if i < keep else (keep, i)
            if (d, lo, hi) < (cd[i], clo[i], chi[i]):
                cd[i] = d
                clo[i] = lo
                chi[i] = hi
                cj[i] = keep
        if _after_merge is not None:
            _after_merge(S, sizes, active, cd, clo, chi, cj)
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


def _row(rets) -> list[float]:
    """A series as the Python list the pure-Python helpers were written
    for. Since 2026-09-03 the gauntlet hands this module numpy rows
    (simcache.Series, docs/plans/2026-09-03-simcache-arrays.md); the same
    float64s come back out, so every sum below is bit-identical."""
    return rets.tolist() if hasattr(rets, "tolist") else rets


def _as_lists(returns_by_id: dict) -> dict[str, list[float]]:
    return {k: _row(v) for k, v in returns_by_id.items()}


def _reps_variance(returns_by_id: dict[str, list[float]], ids: list[str],
                   labels: dict[str, int]) -> float:
    """Sample variance across cluster representatives (mean member daily
    Sharpe). Pure Python on purpose: both the reference and numpy paths call
    THIS, so identical labels give a bit-identical recorded float."""
    groups: dict[int, list[str]] = {}
    for i in ids:
        groups.setdefault(labels[i], []).append(i)
    reps = [sum(_sharpe(_row(returns_by_id[i])) for i in g) / len(g)
            for g in groups.values()]
    return _sample_variance(reps)


def _effective_trials_ref(returns_by_id: dict[str, list[float]]):
    """The original pure-Python implementation, kept verbatim as the
    comparison oracle and the ragged-input fallback."""
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
    return k, labels, _reps_variance(returns_by_id, ids, labels)


def _effective_trials_np(returns_by_id: dict[str, list[float]],
                         ids: list[str], X: "np.ndarray"):
    """Numpy fast path for effective_trials: one merge-history replay with
    incremental silhouette sums instead of n-2 from-scratch sweeps. Output
    is identical to _effective_trials_ref (see test_cluster_np.py); the
    recorded variance goes through the same _reps_variance code."""
    n = len(ids)
    # Memory (step 7): the merge sums consume D in place and the replay
    # recomputes it. The same X gives a bit-identical D (deterministic
    # arithmetic; tools/verify_cluster_identity.py proves the end result),
    # so one n x n matrix is held at a time instead of three.
    history = _agglomerate_np(ids, _distance_matrix_np(X), overwrite=True)
    idx_of = {i: j for j, i in enumerate(ids)}

    T = _distance_matrix_np(X)       # T[i, c] = sum dist from point i to slot c
    sizes = np.ones(n)
    active = np.ones(n, dtype=bool)
    own = np.arange(n)               # point -> cluster slot
    rows = np.arange(n)
    bmin_val = np.full(n, np.inf)
    bmin_idx = np.full(n, -1)

    def rescan(subset):
        """Fresh masked b-min for the given point rows, in row blocks (each
        row's minimum is its own; blocks only bound the temporaries)."""
        if len(subset) == 0:
            return
        cols = np.flatnonzero(active)
        for a in range(0, len(subset), _ROWS):
            sub = subset[a:a + _ROWS]
            Q = T[np.ix_(sub, cols)] / sizes[cols]
            Q[cols[None, :] == own[sub][:, None]] = np.inf
            pos = Q.argmin(axis=1)
            bmin_val[sub] = Q[np.arange(len(sub)), pos]
            bmin_idx[sub] = cols[pos]

    rescan(rows)

    best_q = None                    # ((quality, mean_s, -k), own, active)
    best_m = None                    # ((mean_s, -k), own, active)
    for t, (ca, cb) in enumerate(history):
        keep = idx_of[min(min(ca), min(cb))]
        drop = idx_of[max(min(ca), min(cb))]
        T[:, keep] += T[:, drop]
        sizes[keep] += sizes[drop]
        active[drop] = False
        own[own == drop] = keep
        k = n - 1 - t
        if k < 2:
            break
        # b-min cache maintenance: only rows whose cached column was one of
        # the merged pair need a fresh scan (reducibility; the merged
        # column's ratio is a weighted mean of the two old columns, so it
        # cannot undercut a min cached elsewhere)
        stale = np.flatnonzero((bmin_idx == keep) | (bmin_idx == drop))
        rescan(stale)

        own_sizes = sizes[own]
        multi = own_sizes > 1
        a = np.zeros(n)
        a[multi] = T[rows, own][multi] / (own_sizes[multi] - 1)
        b = bmin_val
        sil = np.zeros(n)
        mx = np.maximum(a, b)
        nz = multi & (mx > 0)
        sil[nz] = (b[nz] - a[nz]) / mx[nz]

        mean_s = float(sil.sum() / n)
        var = float(((sil - mean_s) ** 2).sum() / (n - 1)) if n > 1 else 0.0
        sd = math.sqrt(var)
        quality = mean_s / sd if sd > 0 else float("-inf")
        key_q = (quality, mean_s, -k)
        if best_q is None or key_q > best_q[0]:
            best_q = (key_q, own.copy(), active.copy())
        key_m = (mean_s, -k)
        if best_m is None or key_m > best_m[0]:
            best_m = (key_m, own.copy(), active.copy())

    chosen = best_m if best_q[0][0] == float("-inf") else best_q
    _, won, wactive = chosen
    slots = sorted(int(s) for s in np.flatnonzero(wactive))
    rank = {s: r for r, s in enumerate(slots)}
    labels = {ids[i]: rank[int(won[i])] for i in range(n)}
    k = len(slots)
    return k, labels, _reps_variance(returns_by_id, ids, labels)


def effective_trials(returns_by_id: dict[str, list[float]]):
    """(k, labels, cross_cluster_sharpe_variance).

    k is the number of effectively independent trials: the cluster count
    maximising the silhouette QUALITY score mean(S)/stdev(S) over
    k in [2, n-1] (card L6's criterion). k=n is excluded because every
    silhouette is 0 when all clusters are singletons.

    The variance returned is the sample variance across CLUSTER
    representatives, each representative being the mean daily Sharpe of its
    members — not the variance across all strategies, which is what
    protocol-v2 used and what over-deflated the gate.

    Dispatch: rectangular input with n >= 3 takes the numpy fast path
    (identical output, held by test_cluster_np.py); ragged input, input
    containing a constant NONZERO series (see _returns_matrix on why), and
    the n <= 2 special cases take the reference path.
    """
    ids = sorted(returns_by_id)
    n = len(ids)
    if n <= 2:
        return _effective_trials_ref(_as_lists(returns_by_id))
    ids2, X = _returns_matrix(returns_by_id)
    if X is None:
        return _effective_trials_ref(_as_lists(returns_by_id))
    return _effective_trials_np(returns_by_id, ids2, X)
