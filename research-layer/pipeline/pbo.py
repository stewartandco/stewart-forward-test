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

protocol-v5 amends two things here. The boundary tie counts as a HALF event
(see overfit_weight), and the fixed thresholds that used to be applied to the
returned number are withdrawn in favour of a per-family permutation null (see
permutation_null). The evidence for both is chained at registry entry 2511 and
the argument at 2512.
"""
from __future__ import annotations

import math
import random
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


def overfit_weight(rank: int, n_configs: int) -> float:
    """How much one split contributes to the overfit count (protocol-v5).

    omega = rank/(n_configs + 1) and BBLdP score lambda = logit(omega) <= 0 as
    overfit. At the family sizes their work assumes, omega == 0.5 exactly has
    vanishing measure. At small ODD n_configs it does not: the median rank
    lands precisely on the boundary, and counting it as a WHOLE overfit event
    made the uniform-rank null (n_configs + 1) / (2 * n_configs) -- 0.600 at
    five configs, ABOVE protocol-v4's own 0.50 family-kill line, so a family
    with no persistent skill differences at all was killed by construction.
    Generation 4 registered every one of its six families at exactly five.

    Counting the exact tie as a HALF event makes the null exactly 0.5 at every
    family size. The comparison is done in integers because the tie must be
    decided exactly and never by float equality.
    """
    doubled = 2 * rank
    if doubled < n_configs + 1:
        return 1.0
    if doubled == n_configs + 1:
        return 0.5
    return 0.0


def distinct_configs(perf_by_id: dict[str, list[float]]) -> int:
    """How many genuinely different configurations a family contains.

    Siblings with identical series are the same configuration observed more
    than once: the swept axis did not bind between them, so there is nothing
    for CSCV to select among and a low PBO records only that a tiny difference
    was persistent. protocol-v5 fails a family closed below four of these.
    """
    return len({tuple(v) for v in perf_by_id.values()})


def permute_labels(series: dict[str, list[float]],
                   rng: random.Random) -> dict[str, list[float]]:
    """Randomly reassign which sibling owns which return, day by day.

    Each day's cross-section is preserved EXACTLY, so the family's real return
    distribution, its real sibling correlation and the real common market
    factor all survive; only PERSISTENT per-sibling skill is destroyed. That
    is precisely the null the PBO thresholds assumed and never verified.
    """
    ids = sorted(series)
    n_obs = min(len(series[i]) for i in ids)
    out: dict[str, list[float]] = {i: [] for i in ids}
    for t in range(n_obs):
        order = ids[:]
        rng.shuffle(order)
        for src, dst in zip(ids, order):
            out[dst].append(series[src][t])
    return out


def permutation_null(perf_by_id: dict[str, list[float]], s: int = 16,
                     draws: int = 50, seed: int = 0) -> list[float]:
    """The distribution of PBO for THIS family under no persistent skill.

    Refuses a family whose siblings are all identical: permuting labels there
    cannot change anything, so the result would be one point pretending to be
    a distribution. Callers get an error rather than a false null.

    SP4 Task P3: this default tracks gauntlet.PBO_NULL_DRAWS (200 -> 50,
    2026-08-26) so a caller that omits `draws` gets the same declared
    protocol default gauntlet.py's own CLI now does; gauntlet.py's real
    call site always passes `draws` explicitly (args.pbo_null_draws), so
    this default only matters to a caller reaching for it directly (a
    diagnose_*.py script, a REPL, a future test).
    """
    if distinct_configs(perf_by_id) < 2:
        raise ValueError(
            "cannot build a permutation null from a family with fewer than 2 "
            "distinct series: permuting labels would change nothing")
    rng = random.Random(seed)
    out = []
    for _ in range(draws):
        v = cscv_pbo(permute_labels(perf_by_id, rng), s=s)["pbo"]
        if v is not None:
            out.append(v)
    return out


def percentile_of(values: list[float], observed: float) -> float | None:
    """Where `observed` sits in `values`, counting ties as half.

    Ties are split rather than counted wholly on either side for the same
    reason overfit_weight splits its boundary: PBO is discrete at small family
    sizes, so exact ties are common and assigning them entirely to one side
    biases the answer in that direction.
    """
    if not values:
        return None
    below = sum(1 for v in values if v < observed)
    equal = sum(1 for v in values if v == observed)
    return (below + 0.5 * equal) / len(values)


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
        # protocol-v5: the exact median rank contributes a HALF event rather
        # than a whole one. See overfit_weight for why that is not a detail.
        below += overfit_weight(rank, n_configs)
        total += 1
    shape["n_combinations"] = total
    return {"pbo": below / total, "reason": None, **shape}
