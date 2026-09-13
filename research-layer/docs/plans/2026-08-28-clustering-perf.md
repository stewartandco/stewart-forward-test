# Clustering Performance Fix (numpy vectorisation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the gauntlet's clustering stage from 45,525s (96% of the 2026-08-27 run) to minutes by vectorising `pipeline/cluster.py` on numpy, with old-vs-new label/trials_n identity proven on real-scale data before anything ships.

**Architecture:** The existing pure-Python functions in `cluster.py` stay untouched as the reference implementation. Three new private numpy functions are added beside them: `_distance_matrix_np` (one BLAS call instead of O(n^2) hand loops), `_agglomerate_np` (sum-matrix average linkage with a per-row nearest-neighbour cache and the reference's exact `(round(d,12), lo, hi)` tie-break), and `_effective_trials_np` (single replay of the merge history with incremental silhouette sums instead of n-2 from-scratch sweeps). `effective_trials` becomes a dispatcher: rectangular input with n>=3 takes the numpy path, anything else falls back to the reference. `trials_n`, `cluster_labels` and `trials_sr_var` are RECORDED on every gauntlet verdict, so identity is the ship bar, not approximate equivalence.

**Tech Stack:** Python 3.14 (system PATH, no venv), numpy 2.4.2 (already a dependency via pandas), pytest. Repo: `E:\Users\Coen\Claude\stewart-forward-test`, all work under `research-layer/`, branch `claude/ai-agent-business-automation-0lzfd9`.

---

## Standing hazards (every task, non-negotiable)

- A RESIDENT scanner writes `sources/*.jsonl` in this tree continuously. NEVER `git add -A`, never `git add .`, never touch `sources/`, never `git reset --hard`, never `git stash`. Scoped explicit-path commits only; run `git show --stat HEAD` after every commit and confirm ONLY your files are in it (the shared index has swept files before).
- `registry_log.jsonl` (production chain, VALID) and `artifacts/` are production. Tests and probes touch tmp copies only. The sim cache `simcache/` may be READ by tools; anything that writes uses a copy.
- No em-dashes in any new prose (docs, comments, commit messages). Use "--".
- Foreground commands cap at ~600s. Run the full suite in two chunks (`python -m pytest pipeline/test_[a-h]*.py -q` then the rest) or in background. NEVER have a subagent block waiting on a background-task notification.
- Known allowed failure BEFORE Task 7: only `pipeline/test_scanner.py::test_committed_watchlist_loads_and_gate_tracks_verification` (live-data-coupled; Task 7 fixes it). Anything else red = your problem.
- `ENGINE_REV` ("e2") must NOT be bumped by this work: clustering consumes simulated series, it does not produce them. If you find yourself touching `engine.py`, stop and re-read the plan.
- All commands below run from `E:\Users\Coen\Claude\stewart-forward-test\research-layer` unless stated.

## The identity contract (read before Task 1)

`trials_n` feeds `deflated_sharpe` on every recorded verdict. The numpy path must produce, on the same input: identical `k`, an identical `labels` dict (same keys, same integer values), and `trials_sr_var` exactly equal (shared pure-Python representative code makes it bit-identical when labels match; 1e-9 is the outer tolerance if a platform surprise appears). Float summation order differs between BLAS and the hand loops, so ulp-level differences in distances are expected and are absorbed by the reference's own `round(d, 12)` tie-break key. If a regression fixture shows a tie genuinely flipping (different k or labels), STOP the build and report: that becomes a declared protocol note requiring Coen, never a silent change. Do not "fix" such a mismatch by loosening a test.

---

### Task 1: numpy distance matrix

**Files:**
- Modify: `pipeline/cluster.py` (add imports + two functions; touch nothing existing)
- Create: `pipeline/test_cluster_np.py`

- [ ] **Step 1: Write the failing tests**

Create `pipeline/test_cluster_np.py`:

```python
"""Equivalence tests: numpy clustering fast path vs the pure-Python
reference in cluster.py. The reference is the contract; these tests fail
whenever the fast path diverges from it.

Run: python -m pytest pipeline/test_cluster_np.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from .cluster import (correlation, distance, distance_matrix, agglomerate,
                      labels_for_k, silhouette, effective_trials,
                      _returns_matrix, _distance_matrix_np)
from .test_gen3 import two_group_series


def dmat_to_array(ids: list[str], dmat: dict) -> np.ndarray:
    n = len(ids)
    D = np.zeros((n, n))
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            if i != j:
                D[i, j] = dmat[(a, b)]
    return D


def seeded_series(n: int, length: int, groups: int = 3) -> dict[str, list[float]]:
    """Deterministic structured fixture: `groups` planted bases plus
    per-series noise, so clustering has real structure to find."""
    rng = np.random.default_rng(20260828)
    bases = rng.standard_normal((groups, length)) * 0.01
    out = {}
    for i in range(n):
        base = bases[i % groups]
        noise = rng.standard_normal(length) * 0.002
        out[f"{i:04d}" + "s" * 12] = [float(v) for v in base + noise]
    return out


# ---------------- _returns_matrix ----------------

def test_returns_matrix_rectangular():
    series = two_group_series()
    ids, X = _returns_matrix(series)
    assert ids == sorted(series)
    assert X.shape == (5, 8)
    assert X.dtype == np.float64
    for r, i in enumerate(ids):
        assert list(X[r]) == series[i]


def test_returns_matrix_ragged_returns_none():
    ids, X = _returns_matrix({"a" * 16: [0.1, 0.2], "b" * 16: [0.1, 0.2, 0.3]})
    assert X is None


def test_returns_matrix_empty():
    ids, X = _returns_matrix({})
    assert ids == [] and X is None


# ---------------- _distance_matrix_np ----------------

def _assert_matrix_matches_reference(series):
    ids = sorted(series)
    ref = distance_matrix(series)
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert D.shape == (len(ids), len(ids))
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            assert D[i, j] == pytest.approx(ref[(a, b)], abs=1e-12), (a, b)
    # exactly symmetric, exactly zero diagonal
    assert np.array_equal(D, D.T)
    assert np.all(np.diag(D) == 0.0)


def test_distance_matrix_np_matches_reference_two_groups():
    _assert_matrix_matches_reference(two_group_series())


def test_distance_matrix_np_matches_reference_seeded():
    _assert_matrix_matches_reference(seeded_series(40, 120))


def test_distance_matrix_np_zero_variance_series():
    series = {"a" * 16: [0.01, -0.02, 0.03, 0.0],
              "b" * 16: [0.0, 0.0, 0.0, 0.0],
              "c" * 16: [-0.01, 0.02, -0.03, 0.0]}
    _assert_matrix_matches_reference(series)
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    # zero-variance row: rho 0 vs everything -> distance sqrt(0.5); diag 0
    assert D[1, 0] == pytest.approx(0.5 ** 0.5)
    assert D[1, 1] == 0.0


def test_distance_matrix_np_identical_and_inverted():
    series = {"a" * 16: [0.01, 0.02, -0.01],
              "b" * 16: [0.01, 0.02, -0.01],
              "c" * 16: [-0.01, -0.02, 0.01]}
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert D[0, 1] == pytest.approx(0.0, abs=1e-12)   # identical -> rho 1
    assert D[0, 2] == pytest.approx(1.0, abs=1e-12)   # inverted -> rho -1


def test_distance_matrix_np_clamps_overshoot():
    # near-identical series can push BLAS rho a few ulp past 1.0; the clamp
    # plus the forced-zero diagonal must keep sqrt() finite and real
    series = seeded_series(6, 50, groups=1)
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert np.all(np.isfinite(D)) and np.all(D >= 0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest pipeline/test_cluster_np.py -q`
Expected: FAIL at import time with `ImportError: cannot import name '_returns_matrix'`

- [ ] **Step 3: Implement in `pipeline/cluster.py`**

Add below the module imports (`import math` stays):

```python
import numpy as np
```

Update the module docstring's "pure stdlib" implication: append this paragraph to the docstring:

```
A numpy fast path (SP4 clustering-perf, 2026-08-28) mirrors the reference
implementation exactly: `effective_trials` dispatches to it for rectangular
input and falls back to the hand-written functions otherwise. The reference
functions below are the CONTRACT; test_cluster_np.py holds the two paths
identical, and the tie-break key round(d, 12) is what absorbs BLAS-vs-loop
float summation differences.
```

Add after `distance_matrix`:

```python
def _returns_matrix(returns_by_id: dict[str, list[float]]):
    """(sorted ids, n x L float64 matrix), or (ids, None) when the series
    lengths differ. The fast path needs rectangular input; gauntlet's
    check_aligned guarantees it in production, and ragged direct callers
    fall back to the reference implementation."""
    ids = sorted(returns_by_id)
    if not ids:
        return ids, None
    lengths = {len(returns_by_id[i]) for i in ids}
    if len(lengths) != 1:
        return ids, None
    return ids, np.asarray([returns_by_id[i] for i in ids], dtype=np.float64)


def _distance_matrix_np(X: "np.ndarray") -> "np.ndarray":
    """Correlation-distance matrix over the rows of X, numpy form of
    distance_matrix(). Semantics matched to correlation()/distance():
    zero-variance rows correlate 0.0 with everything (distance sqrt(0.5)),
    rho clamped to [-1, 1], diagonal forced to 0.0, result exactly
    symmetric (the reference assigns (i, j) and (j, i) from one number;
    BLAS output is mirrored from the upper triangle to match)."""
    n, L = X.shape
    if L < 2:
        R = np.zeros((n, n))
    else:
        M = X - X.mean(axis=1, keepdims=True)
        ss = np.einsum("ij,ij->i", M, M)
        good = ss > 0.0
        with np.errstate(invalid="ignore", divide="ignore"):
            R = (M @ M.T) / np.sqrt(np.outer(ss, ss))
        R[~good, :] = 0.0
        R[:, ~good] = 0.0
    np.clip(R, -1.0, 1.0, out=R)
    D = np.sqrt(0.5 * (1.0 - R))
    D = np.triu(D, 1)
    return D + D.T
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest pipeline/test_cluster_np.py -q`
Expected: all PASS

Run: `python -m pytest pipeline/test_gen3.py -q`
Expected: all PASS (reference untouched)

- [ ] **Step 5: Commit (scoped paths only)**

```bash
git add research-layer/pipeline/cluster.py research-layer/pipeline/test_cluster_np.py
git commit -m "perf(cluster): numpy distance matrix beside the reference implementation"
git show --stat HEAD
```

Confirm the stat lists exactly the two files.

---

### Task 2: numpy agglomerate with the reference tie-break

**Files:**
- Modify: `pipeline/cluster.py`
- Modify: `pipeline/test_cluster_np.py`

**Design notes the implementer must follow:**
- Maintain a cluster-pair SUM matrix `S` (`S[a, b]` = sum of pairwise member distances) indexed by fixed slots; merging updates `S[keep] += S[drop]` row and column. Average distance = `S[a, b] / (size_a * size_b)`.
- SLOT INVARIANT: a cluster always lives at the slot of its smallest member's index in sorted-id order. When merging, `keep` is the slot whose cluster holds the smaller min id. Task 3's replay depends on this invariant.
- Tie-break must be the reference's exactly: key `(round(d, 12), lo, hi)` with `lo, hi = sorted((min_id_a, min_id_b))`, Python's `round` (NOT `np.round`, whose fast path is documented as sometimes inexact). Locate raw candidate minima with numpy, then key only the tiny candidate set with Python `round`: round-half-even to 12 dp is monotone, so every pair whose rounded distance can equal the minimal rounded distance lies within `raw_min + 2e-12`.
- A per-row nearest-neighbour cache keeps each merge near O(n): store each active row's best partner key; after a merge recompute only the merged row and rows whose cached partner was one of the merged pair; for every other row the merged cluster cannot beat the cached key on distance (average linkage is reducible), but it CAN tie on distance and win on `(lo, hi)`, so rows whose cached rounded distance is within 2e-12 of the merged cluster's new average must be re-keyed against it.
- History pairs are recorded with the cluster containing the smaller min id first. Pair ORDER inside a step is not part of the reference contract (`labels_for_k` treats the pair as a set); equivalence tests compare each step as a set.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_cluster_np.py`:

```python
from .cluster import _agglomerate_np


def _hist_as_sets(history):
    return [frozenset({ca, cb}) for ca, cb in history]


def _assert_agglomerate_matches_reference(ids, dmat):
    ref = agglomerate(ids, dmat)
    new = _agglomerate_np(sorted(ids), dmat_to_array(sorted(ids), dmat))
    assert _hist_as_sets(new) == _hist_as_sets(ref)


def test_agglomerate_np_two_groups():
    series = two_group_series()
    _assert_agglomerate_matches_reference(sorted(series), distance_matrix(series))


def test_agglomerate_np_seeded_40():
    series = seeded_series(40, 120)
    _assert_agglomerate_matches_reference(sorted(series), distance_matrix(series))


def test_agglomerate_np_exact_tie_first_round():
    # mirrors test_tie_break_is_deterministic_and_lexicographic
    ids = ["a" * 16, "b" * 16, "c" * 16]
    s = [0.01, -0.02, 0.03, 0.01]
    series = {i: list(s) for i in ids}
    _assert_agglomerate_matches_reference(ids, distance_matrix(series))
    new = _agglomerate_np(ids, dmat_to_array(ids, distance_matrix(series)))
    assert new[0] == (frozenset({ids[0]}), frozenset({ids[1]}))


def test_agglomerate_np_exact_tie_later_round():
    # mirrors test_tie_break_is_canonical_in_later_rounds: a tie AFTER a
    # merge must resolve by smallest member id
    a, b, c, d, e = (ch * 16 for ch in "abcde")
    ids = [a, b, c, d, e]
    D = {}
    for i in ids:
        D[(i, i)] = 0.0

    def put(x, y, v):
        D[(x, y)] = v
        D[(y, x)] = v

    put(a, b, 0.10)
    put(c, d, 0.41)
    put(a, e, 0.41)
    put(b, e, 0.41)
    for x, y in ((a, c), (a, d), (b, c), (b, d), (c, e), (d, e)):
        put(x, y, 0.90)
    _assert_agglomerate_matches_reference(ids, D)


def test_agglomerate_np_deterministic():
    series = seeded_series(30, 80)
    ids = sorted(series)
    D = dmat_to_array(ids, distance_matrix(series))
    first = _agglomerate_np(ids, D)
    for _ in range(3):
        assert _agglomerate_np(ids, D) == first


def test_agglomerate_np_trivial_sizes():
    assert _agglomerate_np([], np.zeros((0, 0))) == []
    assert _agglomerate_np(["a" * 16], np.zeros((1, 1))) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest pipeline/test_cluster_np.py -q`
Expected: FAIL with `ImportError: cannot import name '_agglomerate_np'`

- [ ] **Step 3: Implement `_agglomerate_np` in `pipeline/cluster.py`**

Add after `agglomerate`:

```python
def _agglomerate_np(ids: list[str], D: "np.ndarray") -> list[tuple]:
    """Average-linkage merge history on a numpy distance matrix; numpy form
    of agglomerate() with the identical (round(d, 12), lo, hi) tie-break.

    Slot invariant: a cluster lives at the slot of its smallest member's
    sorted-id index, so `keep` below is always the smaller-min-id side.
    _effective_trials_np replays history under the same invariant.

    Pair order inside a history step is not part of the reference contract
    (labels_for_k unions the pair either way); steps here list the cluster
    containing the smaller min id first.
    """
    ids = sorted(ids)
    n = len(ids)
    if n <= 1:
        return []
    S = D.astype(np.float64, copy=True)
    sizes = np.ones(n, dtype=np.float64)
    active = np.ones(n, dtype=bool)
    members: list = [frozenset([i]) for i in ids]
    min_id: list = list(ids)
    INF = float("inf")

    def row_best(i: int):
        """Best merge partner for slot i under the reference key, as
        (key, j). None when no active partner exists."""
        avg = S[i] / (sizes[i] * sizes)
        avg[~active] = INF
        avg[i] = INF
        raw = avg.min()
        if raw == INF:
            return None
        best = None
        for j in np.flatnonzero(avg <= raw + 2e-12):
            d = round(float(avg[j]), 12)
            lo, hi = sorted((min_id[i], min_id[int(j)]))
            key = (d, lo, hi)
            if best is None or key < best[0]:
                best = (key, int(j))
        return best

    cache: list = [None] * n
    for i in range(n):
        cache[i] = row_best(i)

    history = []
    for _ in range(n - 1):
        gx, gbest = None, None
        for i in range(n):
            if active[i] and cache[i] is not None:
                if gbest is None or cache[i][0] < gbest[0]:
                    gx, gbest = i, cache[i]
        x, y = gx, gbest[1]
        # keep = the slot whose cluster holds the smaller min id
        keep, drop = (x, y) if min_id[x] < min_id[y] else (y, x)
        history.append((members[keep], members[drop]))
        new_size = sizes[keep] + sizes[drop]
        S[keep, :] += S[drop, :]
        S[:, keep] += S[:, drop]
        sizes[keep] = new_size
        active[drop] = False
        members[keep] = members[keep] | members[drop]
        members[drop] = None
        min_id[drop] = None
        cache[drop] = None
        if not active.any() or active.sum() == 1:
            break
        # invalidate: the merged row, and any row whose cached partner was
        # one of the merged pair
        stale = {keep}
        for i in range(n):
            if active[i] and i != keep and cache[i] is not None \
                    and cache[i][1] in (keep, drop):
                stale.add(i)
        # ties: the merged cluster cannot BEAT any surviving cached key on
        # distance (average linkage is reducible), but it can tie on the
        # rounded distance and win on (lo, hi); re-key those rows too
        avg_new = S[keep] / (sizes[keep] * sizes)
        avg_new[~active] = INF
        avg_new[keep] = INF
        for i in np.flatnonzero(np.isfinite(avg_new)):
            i = int(i)
            if i in stale or cache[i] is None:
                continue
            if abs(avg_new[i] - cache[i][0][0]) <= 2e-12:
                stale.add(i)
        for i in stale:
            cache[i] = row_best(i)
    return history
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest pipeline/test_cluster_np.py -q`
Expected: all PASS

Run: `python -m pytest pipeline/test_gen3.py -q`
Expected: all PASS

- [ ] **Step 5: Commit (scoped paths only)**

```bash
git add research-layer/pipeline/cluster.py research-layer/pipeline/test_cluster_np.py
git commit -m "perf(cluster): numpy average-linkage agglomerate, reference tie-break exact"
git show --stat HEAD
```

---

### Task 3: single-replay silhouette sweep, dispatch, and the gauntlet timing line

**Files:**
- Modify: `pipeline/cluster.py`
- Modify: `pipeline/test_cluster_np.py`
- Modify: `pipeline/gauntlet.py` (one timing print around the `effective_trials` call, nothing else)

**Design notes the implementer must follow:**
- The reference sweeps k in range(2, n), each k replaying `labels_for_k` from scratch and computing `silhouette` from the dict matrix: O(n^3) total. The fast path replays the merge history ONCE (k = n-1 down to 2), maintaining `T` (n x slots matrix, `T[i, c]` = sum of distances from point i to members of cluster-slot c, initialised to `D`) with `T[:, keep] += T[:, drop]` per merge, under Task 2's slot invariant.
- Per k: `a_i = T[i, own_i] / (size_own - 1)` for points whose cluster has >1 member; `b_i = min over active c != own_i of T[i, c] / sizes[c]`; silhouette 0.0 for singletons and when `max(a, b) == 0`; quality = `mean_s / sd` (sd from the sample-variance formula, `-inf` when sd == 0); selection key `(quality, mean_s, -k)` maximised. Keys are injective in k (the -k term), so iteration direction cannot change the winner; strict `>` comparison as in the reference.
- b_i uses a per-row min cache over the ratio columns: after a merge only rows whose cached argmin column was `keep` or `drop` need a fresh masked scan; every other row updates in O(1) against the merged column (reducibility: the merged column's ratio is a weighted mean of the two old columns, so it cannot undercut a cached min that was not at those columns; on exact value ties the cached min VALUE is still correct, which is all silhouette needs).
- The reference's -inf fallback (every k had zero silhouette spread -> re-select by `(mean_s, -k)`) is reproduced by tracking both selection keys during the same replay.
- Labels for the winning k must equal `labels_for_k`'s dict exactly: cluster indices assigned by sorted order of each cluster's smallest member. Under the slot invariant, sorting active slots numerically IS that order. Snapshot `(own, active)` whenever the best key improves; build the dict once at the end.
- `trials_sr_var` must be computed by the SAME pure-Python code as the reference. Extract the representative-variance block at the bottom of `effective_trials` into a module-level helper `_reps_variance(returns_by_id, ids, labels)` and call it from both paths, so identical labels give a bit-identical float.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_cluster_np.py`:

```python
from .cluster import _effective_trials_np, _effective_trials_ref, _reps_variance


def _assert_effective_trials_identical(series):
    ref_k, ref_labels, ref_var = _effective_trials_ref(series)
    ids, X = _returns_matrix(series)
    new_k, new_labels, new_var = _effective_trials_np(series, ids, X)
    assert new_k == ref_k
    assert new_labels == ref_labels
    assert new_var == pytest.approx(ref_var, abs=1e-9)
    # shared reps code + identical labels should be bit-identical
    assert new_var == ref_var


def test_effective_trials_np_two_groups():
    _assert_effective_trials_identical(two_group_series())


def test_effective_trials_np_seeded_structured():
    _assert_effective_trials_identical(seeded_series(40, 120))
    _assert_effective_trials_identical(seeded_series(80, 300))


def test_effective_trials_np_identical_siblings():
    s = [0.01, -0.02, 0.03, 0.01, -0.015, 0.02]
    series = {chr(ord("a") + i) * 16: list(s) for i in range(5)}
    _assert_effective_trials_identical(series)


def test_effective_trials_np_with_zero_variance_member():
    series = seeded_series(10, 60)
    series["zzzz" + "z" * 12] = [0.0] * 60
    _assert_effective_trials_identical(series)


def test_effective_trials_np_smallest_n():
    _assert_effective_trials_identical(seeded_series(3, 40))
    _assert_effective_trials_identical(seeded_series(4, 40))


def test_effective_trials_dispatcher_agrees_with_reference():
    # the public entry point must give the numpy result for rectangular
    # input and the reference result for ragged input
    series = seeded_series(12, 60)
    assert effective_trials(series) == _effective_trials_ref(series)
    ragged = {"a" * 16: [0.01, -0.02, 0.03, 0.01],
              "b" * 16: [0.02, 0.01, -0.01],
              "c" * 16: [-0.01, 0.02]}
    assert effective_trials(ragged) == _effective_trials_ref(ragged)


def test_effective_trials_np_deterministic():
    series = seeded_series(25, 80)
    ids, X = _returns_matrix(series)
    assert (_effective_trials_np(series, ids, X)
            == _effective_trials_np(series, ids, X))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest pipeline/test_cluster_np.py -q`
Expected: FAIL with `ImportError: cannot import name '_effective_trials_np'`

- [ ] **Step 3: Restructure `effective_trials` in `pipeline/cluster.py`**

Rename the existing `effective_trials` body: the current function keeps its docstring and becomes the dispatcher; the reference body moves to `_effective_trials_ref`. Extract the representative block into `_reps_variance`:

```python
def _reps_variance(returns_by_id: dict[str, list[float]], ids: list[str],
                   labels: dict[str, int]) -> float:
    """Sample variance across cluster representatives (mean member daily
    Sharpe). Pure Python on purpose: both the reference and numpy paths call
    THIS, so identical labels give a bit-identical recorded float."""
    groups: dict[int, list[str]] = {}
    for i in ids:
        groups.setdefault(labels[i], []).append(i)
    reps = [sum(_sharpe(returns_by_id[i]) for i in g) / len(g)
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
        best = None
        for k in range(2, n):
            labels = labels_for_k(history, ids, k)
            mean_s = sum(silhouette(labels, dmat)) / n
            key = (mean_s, -k)
            if best is None or key > best[0]:
                best = (key, k, labels)
    _, k, labels = best
    return k, labels, _reps_variance(returns_by_id, ids, labels)


def effective_trials(returns_by_id: dict[str, list[float]]):
    """(k, labels, cross_cluster_sharpe_variance).

    [KEEP THE ORIGINAL DOCSTRING PARAGRAPHS HERE VERBATIM, then append:]

    Dispatch: rectangular input with n >= 3 takes the numpy fast path
    (identical output, held by test_cluster_np.py); ragged input and the
    n <= 2 special cases take the reference path.
    """
    ids = sorted(returns_by_id)
    n = len(ids)
    if n <= 2:
        return _effective_trials_ref(returns_by_id)
    ids2, X = _returns_matrix(returns_by_id)
    if X is None:
        return _effective_trials_ref(returns_by_id)
    return _effective_trials_np(returns_by_id, ids2, X)
```

- [ ] **Step 4: Implement `_effective_trials_np`**

```python
def _effective_trials_np(returns_by_id: dict[str, list[float]],
                         ids: list[str], X: "np.ndarray"):
    """Numpy fast path for effective_trials: one merge-history replay with
    incremental silhouette sums instead of n-2 from-scratch sweeps. Output
    is identical to _effective_trials_ref (see test_cluster_np.py); the
    recorded variance goes through the same _reps_variance code."""
    n = len(ids)
    D = _distance_matrix_np(X)
    history = _agglomerate_np(ids, D)
    idx_of = {i: j for j, i in enumerate(ids)}

    T = D.copy()                     # T[i, c] = sum dist from point i to slot c
    sizes = np.ones(n)
    active = np.ones(n, dtype=bool)
    own = np.arange(n)               # point -> cluster slot
    rows = np.arange(n)
    bmin_val = np.full(n, np.inf)
    bmin_idx = np.full(n, -1)

    def rescan(subset):
        """Fresh masked b-min for the given point rows."""
        if len(subset) == 0:
            return
        cols = np.flatnonzero(active)
        Q = T[np.ix_(subset, cols)] / sizes[cols]
        Q[cols[None, :] == own[subset][:, None]] = np.inf
        pos = Q.argmin(axis=1)
        bmin_val[subset] = Q[np.arange(len(subset)), pos]
        bmin_idx[subset] = cols[pos]

    rescan(rows)

    best_q = None                    # ((quality, mean_s, -k), own, active)
    best_m = None                    # ((mean_s, -k), own, active)
    for t, (ca, cb) in enumerate(history):
        keep = idx_of[min(min(ca), min(cb))]
        drop = idx_of[max(min(ca), min(cb))]
        pts = np.flatnonzero((own == keep) | (own == drop))
        T[:, keep] += T[:, drop]
        sizes[keep] += sizes[drop]
        active[drop] = False
        own[own == drop] = keep
        k = n - 1 - t
        if k < 2:
            break
        # b-min cache maintenance: only rows whose cached column was one of
        # the merged pair need a fresh scan (reducibility; see plan notes)
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest pipeline/test_cluster_np.py -q`
Expected: all PASS

Run: `python -m pytest pipeline/test_gen3.py -q`
Expected: all PASS (these now exercise the dispatcher's numpy path for n >= 3 and must be untouched -- if any fails, the fast path is wrong; fix the code, never the test)

- [ ] **Step 6: Add the pure-clustering timing line to `pipeline/gauntlet.py`**

Around the existing call at `pipeline/gauntlet.py:1047` (`trials_n, cluster_labels, trials_var = effective_trials(returns_by_id)`):

```python
    t_et0 = time.time()
    trials_n, cluster_labels, trials_var = effective_trials(returns_by_id)
    print(f"[gauntlet] effective_trials {time.time() - t_et0:.1f}s "
          f"(pure clustering, inside the clustering stage)", flush=True)
```

The existing `t_cluster` stage timer covers sim-cache reads plus clustering jointly; this line isolates the part this plan changes, so the Task 5 proof can attribute the win honestly.

- [ ] **Step 7: Run the gauntlet test files**

Run: `python -m pytest pipeline/test_gauntlet.py pipeline/test_gauntlet_classes.py -q`
Expected: all PASS

- [ ] **Step 8: Commit (scoped paths only)**

```bash
git add research-layer/pipeline/cluster.py research-layer/pipeline/test_cluster_np.py research-layer/pipeline/gauntlet.py
git commit -m "perf(cluster): numpy effective_trials fast path + dispatcher; pure-clustering timing line"
git show --stat HEAD
```

---

### Task 4: real-scale identity proof + timing tool

**Files:**
- Create: `tools_verify_cluster_identity.py` (research-layer root, beside `tools_dryrun_fx.py`)

This is the plan's decision gate: old-vs-new identity on a fixture built from a few hundred REAL cached series (the prompt-file requirement), plus the first full-scale timing of the new path. The simcache is opened READ-ONLY (plain reads, no SimCache.put, no deletes).

- [ ] **Step 1: Write the tool**

Create `tools_verify_cluster_identity.py`:

```python
"""Old-vs-new clustering identity proof on REAL cached return series.

Builds a fixture from the sim cache (read-only), intersection-aligns it the
same way the gauntlet does, then runs the pure-Python reference and the
numpy fast path side by side. Identity of (k, labels) and agreement of
trials_sr_var within 1e-9 is the ship bar; a mismatch exits nonzero and the
build STOPS (declared-protocol-note territory, never a silent change).

Usage (from research-layer/):
    python tools_verify_cluster_identity.py --n 400
    python tools_verify_cluster_identity.py --full        # new path only, all entries
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pipeline.cluster import (_effective_trials_ref, _effective_trials_np,
                              _returns_matrix)
from pipeline.gauntlet import intersect_returns, MIN_TRIALS_COMMON_DAYS


def load_series(cache_dir: Path, limit: int | None) -> dict[str, list[tuple[str, float]]]:
    out = {}
    for p in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            series = [(str(r[0]), float(r[1])) for r in payload["series"]]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
            continue
        if len(series) < 50:
            continue
        out[p.stem] = series
        if limit is not None and len(out) >= limit:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simcache-dir", type=Path,
                    default=Path(__file__).resolve().parent / "simcache")
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--full", action="store_true",
                    help="new path only, over every usable cache entry")
    args = ap.parse_args(argv)

    limit = None if args.full else args.n
    dated = load_series(args.simcache_dir, limit)
    if len(dated) < 3:
        print(f"FAIL: only {len(dated)} usable cache entries in "
              f"{args.simcache_dir}")
        return 2
    returns_by_id, common = intersect_returns(dated)
    print(f"{len(returns_by_id)} series, {len(common)} common days "
          f"(floor {MIN_TRIALS_COMMON_DAYS})")
    if len(common) < MIN_TRIALS_COMMON_DAYS:
        print("FAIL: intersection below the gauntlet floor; pick different "
              "entries")
        return 2

    ids, X = _returns_matrix(returns_by_id)
    t0 = time.time()
    new_k, new_labels, new_var = _effective_trials_np(returns_by_id, ids, X)
    t_new = time.time() - t0
    print(f"new path: k={new_k}, var={new_var!r}, {t_new:.1f}s")

    if args.full:
        print("PASS (timing-only mode; identity is proven by --n runs)")
        return 0

    t0 = time.time()
    ref_k, ref_labels, ref_var = _effective_trials_ref(returns_by_id)
    t_ref = time.time() - t0
    print(f"reference: k={ref_k}, var={ref_var!r}, {t_ref:.1f}s")

    if new_k != ref_k:
        print(f"FAIL: k differs (ref {ref_k} vs new {new_k}) -- STOP THE "
              f"BUILD and report (protocol-note territory)")
        return 1
    if new_labels != ref_labels:
        diff = [i for i in ref_labels if ref_labels[i] != new_labels.get(i)]
        print(f"FAIL: labels differ on {len(diff)} ids (first: {diff[:5]}) "
              f"-- STOP THE BUILD and report")
        return 1
    if abs(new_var - ref_var) > 1e-9:
        print(f"FAIL: trials_sr_var differs beyond 1e-9 "
              f"({ref_var!r} vs {new_var!r}) -- STOP THE BUILD and report")
        return 1
    exact = "bit-identical" if new_var == ref_var else "within 1e-9"
    print(f"PASS: k and labels identical, var {exact}; "
          f"speedup x{t_ref / max(t_new, 1e-9):.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the identity proof at n=400 real series**

Run: `python tools_verify_cluster_identity.py --n 400`
Expected: `PASS: k and labels identical, ...`. Reference wall is expected around 1 to 3 minutes at this n; if it exceeds ~8 minutes, rerun with `--n 300`.
On FAIL: STOP the build, report the exact output. Do not proceed to Task 5.

- [ ] **Step 3: Run the full-scale timing (new path only, background)**

Run in background: `python tools_verify_cluster_identity.py --full > /tmp/cluster_full_timing.log 2>&1` (any scratch path is fine; NOT inside the repo)
Expected: completes with the new-path wall printed. Target: under ~5 minutes over all ~2,470 cache entries. Record the number.

- [ ] **Step 4: Commit (scoped paths only)**

```bash
git add research-layer/tools_verify_cluster_identity.py
git commit -m "perf(cluster): real-series identity proof + timing tool"
git show --stat HEAD
```

---

### Task 5: timed gauntlet proof on a tmp copy of the real chain

The ship bar. Rerun the 2026-08-27 bond+metal gauntlet (303 candidates, registry-wide clustering over ~5,235 registered) on a THROWAWAY copy of the real chain and prove: (a) every one of the 303 verdicts reproduces exactly, and (b) clustering collapses from 45,524.8s to minutes.

**Background facts (verified 2026-08-28, do not rediscover):**
- The production chain `registry_log.jsonl` had 15,684 lines at planning time; the 2b gauntlet block starts at 0-based line index 15060 (first of 303 `verdict` entries with `payload.stage == "gauntlet"`, `ts_utc` 2026-08-27T18:06:51). Everything from there is 303 verdicts + 303 state_changes + interleaved scanner `card_registered` entries. Truncating to the first 15060 lines yields a VALID prefix chain in which the 303 candidates are back in `gauntlet` state and the protocol note is present.
- The resident scanner appends to the live file continuously; taking the FIRST 15060 lines is stable regardless.
- PBO permutation nulls are seeded (`pbo.permutation_null` seed=0) and parallel eval has a deterministic merge, so a rerun on identical inputs is verdict-deterministic.
- The 08-27 run used gauntlet CLI defaults (cutoff `2023-12-31`, perturb on, 50 PBO draws). If the comparison shows EVERY verdict differing wildly, suspect a flag mismatch first, not clustering.
- Verdict `metrics.sim_cache` (hit/miss counters) is warmth-dependent: exclude it from comparison. `artifacts_hash` may legitimately be null on graveyard verdicts; report if it differs but do not fail on it alone. Everything else compares EXACT; `trials_sr_var`, `deflated_sharpe`, `expected_max_sharpe`, `haircut` may fall back to 1e-9 tolerance ONLY if exact fails AND the verdict strings all match. Any `verdict` string, `trials_n`, or gate-outcome difference = HARD FAIL, stop and report.

- [ ] **Step 1: Build the proof workspace (tmp copies only)**

From `research-layer/` in Git Bash (pick a scratch root outside the repo; `$SCRATCH` below):

```bash
SCRATCH=<scratchpad>/cluster-proof
mkdir -p "$SCRATCH/artifacts"
head -n 15060 registry_log.jsonl > "$SCRATCH/registry_log.jsonl"
cp -r simcache "$SCRATCH/simcache"
wc -l "$SCRATCH/registry_log.jsonl"
```

Expected: 15060 lines. The REAL `registry_log.jsonl`, `artifacts/` and `simcache/` are not written by anything in this task.

- [ ] **Step 2: Verify the truncated copy is a valid chain with 303 waiting candidates**

```bash
python - <<'EOF'
import json, sys
sys.path.insert(0, ".")
from pipeline.registry import Registry
from pathlib import Path
reg = Registry(Path(r"<SCRATCH>/registry_log.jsonl"))
states = reg.strategy_states()
waiting = [s for s, st in states.items() if st == "gauntlet"]
print("entries:", sum(1 for _ in reg.entries()))
print("gauntlet-state candidates:", len(waiting))
EOF
```

Expected: 303 candidates. If `Registry` exposes a chain verification entry point (check `pipeline/registry.py` for a `verify`/`validate` function or CLI), run it on the copy and confirm VALID; if verification fails, the truncation point is wrong -- stop and re-derive it (first gauntlet verdict with ts_utc >= 2026-08-27).

- [ ] **Step 3: Run the gauntlet on the copy (background, ~30-60 min)**

Real mode, not dry-run, so verdicts are written to the TMP chain for field-by-field comparison:

```bash
python -m pipeline.gauntlet \
  --registry "$SCRATCH/registry_log.jsonl" \
  --artifacts-dir "$SCRATCH/artifacts" \
  --simcache-dir "$SCRATCH/simcache" \
  --data-dir data \
  > "$SCRATCH/gauntlet_rerun.log" 2>&1
```

Run in background; poll the log. Expected log lines when done:
- `effective trials: <N> clusters over 5235 registered strategies` (the registered count must match the 08-27 run's)
- `[gauntlet] effective_trials <SECONDS>s` -- THE number; target under ~600s
- `[gauntlet] stage timings: clustering <X>s, ...` (X includes sim-cache reads and any cache-miss re-simulations; attribute honestly)
- `303 evaluated: 0 -> quarantine, 303 gate-fail -> graveyard.`

- [ ] **Step 4: Compare all 303 verdicts field-by-field**

Write a throwaway comparison script in the scratch dir (NOT committed):

```python
import json
from pathlib import Path

REAL = Path("registry_log.jsonl")                  # run from research-layer/
PROOF = Path(r"<SCRATCH>/registry_log.jsonl")
SKIP_METRICS = {"sim_cache"}
FLOAT_TOL = {"trials_sr_var", "deflated_sharpe", "expected_max_sharpe",
             "haircut"}

def gauntlet_verdicts(path, after_line=0):
    out = {}
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < after_line:
                continue
            e = json.loads(line)
            p = e.get("payload", {})
            if e.get("entry_type") == "verdict" and p.get("stage") == "gauntlet":
                out[p["strategy_id"]] = p
    return out

real = gauntlet_verdicts(REAL, after_line=15060)
proof = gauntlet_verdicts(PROOF, after_line=15060)
assert len(real) == 303, len(real)
assert set(real) == set(proof), (len(real), len(proof))

hard, soft = [], []
for sid, rp in sorted(real.items()):
    pp = proof[sid]
    if rp["verdict"] != pp["verdict"]:
        hard.append((sid, "verdict", rp["verdict"], pp["verdict"]))
    rm, pm = rp["metrics"], pp["metrics"]
    for key in sorted(set(rm) | set(pm)):
        if key in SKIP_METRICS:
            continue
        a, b = rm.get(key), pm.get(key)
        if a == b:
            continue
        if key == "artifacts_hash":
            soft.append((sid, key, a, b))
            continue
        if (key in FLOAT_TOL and isinstance(a, float) and isinstance(b, float)
                and abs(a - b) <= 1e-9):
            soft.append((sid, key, a, b))
            continue
        hard.append((sid, key, a, b))

print(f"compared {len(real)} verdicts")
print(f"HARD differences: {len(hard)}")
for row in hard[:20]:
    print("  ", row)
print(f"soft (tolerated) differences: {len(soft)}")
for row in soft[:10]:
    print("  ", row)
raise SystemExit(1 if hard else 0)
```

Expected: `HARD differences: 0`. Ideally soft is 0 too (report whatever it is). On any hard difference: STOP, do not commit anything further, report the diff verbatim.

- [ ] **Step 5: Record the proof**

Create `docs/runs/2026-08-28-clustering-perf-proof.md` with: the truncation recipe, the rerun's full stage-timings line, the `effective_trials` seconds, the 08-27 baseline (`clustering 45524.8s` of 47455.6s total), the comparison script's summary output (counts, zero hard diffs), and the Task 4 tool outputs (n=400 identity PASS + full-scale timing).

```bash
git add research-layer/docs/runs/2026-08-28-clustering-perf-proof.md
git commit -m "docs: clustering perf proof -- verdict-identical rerun, timings recorded"
git show --stat HEAD
```

- [ ] **Step 6: Clean up the proof workspace**

Delete `$SCRATCH/cluster-proof` (it holds a ~450MB simcache copy). Confirm the real `registry_log.jsonl` was untouched: `git status --short research-layer/registry_log.jsonl` shows only the pre-existing scanner-driven modification, and `head -c 200 research-layer/registry_log.jsonl` still parses.

---

### Task 6: full suite green (minus the known scanner failure)

- [ ] **Step 1: Run the suite in two chunks (background or sequential foreground)**

```bash
python -m pytest pipeline/test_[a-h]*.py -q
python -m pytest pipeline/test_[i-z]*.py test_*.py -q 2>/dev/null || python -m pytest pipeline/test_[i-z]*.py -q
```

(Adjust the second glob to whatever captures the remaining test files; `ls pipeline/test_*.py` first and split roughly evenly. ~7 min each chunk is normal.)

Expected: everything green EXCEPT `test_scanner.py::test_committed_watchlist_loads_and_gate_tracks_verification` (fixed next task). Any other failure traces to this plan's commits: fix before proceeding.

---

### Task 7: make the scanner watchlist test hermetic (standing chip)

**Files:**
- Modify: `pipeline/test_scanner.py` (the one test at :64)
- Create: `pipeline/fixtures/verified_sources_snapshot_20260828.json`

**Verified failure mode (2026-08-28):** the test reads the LIVE `sources/verified_sources.json` and now fails at `pipeline/test_scanner.py:86`: `pollable(sources)` returns 6 ids that are not in the test's `stamped` set (`econompicdata.blogspot.com`, `mcoscillator.com`, `optimalmomentum.com`, `blog.fosstrading.com`, `edgealchemy.robotwealth.com`, `quant.stackexchange.com`). Root cause is almost certainly the 2026-08-23 source-probation filter (D27 case 3) admitting or stamping sources under rules the test never learned. The test's own docstring already states the lesson: a test reading a live, agent-mutated file has to encode the rules the agent actually runs under. The handoff decision: make it hermetic with a fixture copy instead.

- [ ] **Step 1: Diagnose precisely (do not skip)**

Read `pipeline/watchlist.py` `pollable()` and `load_watchlist()`, plus whatever the probation filter added (grep `probation` across `pipeline/`), and inspect the 6 offending entries in the live `sources/verified_sources.json` (READ ONLY -- never write to `sources/`). Establish exactly which field/path makes them pollable-but-unstamped under the test's current `ADMISSION_PATHS = {"coen", "auto-d27"}`.

- [ ] **Step 2: Snapshot a fixture**

Copy the CURRENT live file to `pipeline/fixtures/verified_sources_snapshot_20260828.json` (create `pipeline/fixtures/` if absent; check first -- other fixture conventions may already exist, follow them). This freezes a real, representative watchlist including probation-era entries.

- [ ] **Step 3: Rewrite the test to load the fixture and encode today's rules**

Point `load_watchlist` at the fixture instead of `LAYER / "sources" / "verified_sources.json"`. Update the admission/pollability assertions to the rules found in Step 1 (e.g. if probation adds a third recognised stamp, add it to `ADMISSION_PATHS` with a comment citing D27 case 3 and the 2026-08-23 session; if pollability now also depends on a probation state field, assert THAT rule). Keep the invariants the docstring declares must not weaken: no source pollable without a `verified_date`, and no admission path beyond the recognised set. Extend the docstring with a dated paragraph explaining the fixture snapshot and why (live-file coupling broke the suite for a week).

- [ ] **Step 4: Run the test and the file**

Run: `python -m pytest pipeline/test_scanner.py -q`
Expected: ALL scanner tests pass, including the previously-failing one.

- [ ] **Step 5: Commit (scoped paths only)**

```bash
git add research-layer/pipeline/test_scanner.py research-layer/pipeline/fixtures/verified_sources_snapshot_20260828.json
git commit -m "test(scanner): watchlist gate test hermetic via fixture snapshot (D27 probation-aware)"
git show --stat HEAD
```

- [ ] **Step 6: Full-suite confirmation**

Re-run the two suite chunks from Task 6. Expected: 100% green, first time in a week. Record the counts.

---

## After the plan completes (main session, not a subagent)

1. Update the vault: `project_market_expansion.md` new dated section (clustering fix shipped, effective_trials seconds, verdict-identical proof, suite fully green) + the MEMORY.md pointer line.
2. STOP. Track 3 futures is gated on Coen's Norgate readout (~2026-08-30). Do not start it.

## Self-review notes

- Spec coverage: measured problem (matrix + agglomerate + silhouette) -> Tasks 1-3; real-scale identity fixture from real cached series -> Task 4; timed gauntlet rerun on tmp chain, verdicts identical -> Task 5; follow-on queue items -> Tasks 6-7. Option B (population cap) deliberately absent: fallback only, needs pre-registration and Coen.
- The tie-flip escape hatch (STOP and report) is wired into Task 4's tool exit codes and Task 5's hard-diff rule.
- `trials_sr_var` bit-identity is engineered (shared `_reps_variance`), not hoped for.
