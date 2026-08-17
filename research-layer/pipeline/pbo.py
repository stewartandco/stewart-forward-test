"""CSCV: the probability of backtest overfitting.

Bailey, Borwein, Lopez de Prado & Zhu, J. Computational Finance 2017
(SSRN 2326253). The trials x time performance matrix is split into S equal
contiguous blocks; every way of choosing S/2 blocks as in-sample gives one
observation of where the in-sample winner ranks out of sample. PBO is the
share of those observations where the winner lands in the bottom half.

Blocks are treated as exchangeable -- that is the method, not an oversight.

Performance note: subset Sharpes are reconstructed from per-block
(count, sum, sum of squares) so a combination costs S/2 additions per config
instead of a full pass over the observations. Without this, C(16,8) = 12,870
combinations is unusably slow in pure Python.
"""
from __future__ import annotations

import math
from itertools import combinations


def block_stats(series: list[float], s: int) -> list[tuple[int, float, float]]:
    """Per-block (count, sum, sum of squares). The trailing remainder is
    dropped so every block is the same length, as CSCV requires."""
    size = len(series) // s
    out = []
    for b in range(s):
        chunk = series[b * size:(b + 1) * size]
        out.append((len(chunk), sum(chunk), sum(x * x for x in chunk)))
    return out


def _sharpe_from(n: int, total: float, total_sq: float) -> float:
    """Sharpe of a block subset from its pooled moments. Degenerate subsets
    (fewer than 2 points, or zero variance) sort last rather than raising: a
    flat curve is a real outcome for a config that never traded."""
    if n < 2:
        return -math.inf
    mean = total / n
    var = (total_sq - total * total / n) / (n - 1)
    if var <= 0:
        return -math.inf
    return mean / math.sqrt(var)


def cscv_pbo(perf_by_id: dict[str, list[float]], s: int = 16) -> dict:
    """PBO over a {config_id: return series} matrix.

    Every series must share one calendar; ragged input is refused rather than
    silently misaligned (the same failure mode gauntlet.check_aligned guards).
    """
    ids = sorted(perf_by_id)
    lengths = {i: len(perf_by_id[i]) for i in ids}
    if len(set(lengths.values())) > 1:
        raise ValueError(
            "cannot compute PBO on ragged series: every config must share one "
            "calendar, got " + ", ".join(f"{i}={n}" for i, n in sorted(lengths.items())))
    n_configs = len(ids)
    size = (lengths[ids[0]] // s) if ids else 0
    shape = {"n_configs": n_configs, "s": s, "block_size": size,
             "n_combinations": 0}
    if n_configs < 2:
        return {"pbo": None, "reason": "needs at least 2 configs", **shape}
    if size < 2:
        return {"pbo": None, "reason": f"blocks of {size} are too short", **shape}

    stats = {i: block_stats(perf_by_id[i], s) for i in ids}
    blocks = range(s)
    half = s // 2
    below = 0
    total = 0
    for is_blocks in combinations(blocks, half):
        oos_blocks = [b for b in blocks if b not in is_blocks]
        is_sr, oos_sr = {}, {}
        for i in ids:
            st = stats[i]
            n = sum(st[b][0] for b in is_blocks)
            t = sum(st[b][1] for b in is_blocks)
            q = sum(st[b][2] for b in is_blocks)
            is_sr[i] = _sharpe_from(n, t, q)
            n = sum(st[b][0] for b in oos_blocks)
            t = sum(st[b][1] for b in oos_blocks)
            q = sum(st[b][2] for b in oos_blocks)
            oos_sr[i] = _sharpe_from(n, t, q)
        # in-sample winner; ties break on id so the result is reproducible
        winner = max(ids, key=lambda i: (is_sr[i], i))
        # ascending rank out of sample: 1 = worst, n_configs = best
        ranked = sorted(ids, key=lambda i: (oos_sr[i], i))
        rank = ranked.index(winner) + 1
        omega = rank / (n_configs + 1)
        # lambda = logit(omega); lambda <= 0 iff omega <= 0.5 (BBLdP's own
        # convention -- the median OOS rank counts as overfit, not as a pass)
        if omega <= 0.5:
            below += 1
        total += 1
    shape["n_combinations"] = total
    return {"pbo": below / total, "reason": None, **shape}
