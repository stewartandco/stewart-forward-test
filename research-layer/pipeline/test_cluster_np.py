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


def test_returns_matrix_constant_nonzero_row_returns_none():
    # constant nonzero rows make zero-variance classification depend on
    # summation order, so they must force the reference path
    series = {"a" * 16: [0.1] * 7,
              "b" * 16: [0.01, -0.02, 0.03, 0.0, 0.01, -0.01, 0.02],
              "c" * 16: [-0.01, 0.02, -0.03, 0.0, -0.01, 0.01, -0.02]}
    ids, X = _returns_matrix(series)
    assert ids == sorted(series)
    assert X is None


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
    # an all-exact-zero row is safe on the fast path: no fallback
    assert X is not None
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
    # correlated single-group data (max off-diag rho well below 1): checks
    # the output is finite and non-negative on structured input. The actual
    # clamp exercise (rho overshooting 1.0 by ulps) is
    # test_distance_matrix_np_bit_identical_rows_exact_zero below.
    series = seeded_series(6, 50, groups=1)
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert np.all(np.isfinite(D)) and np.all(D >= 0.0)


def test_distance_matrix_np_bit_identical_rows_exact_zero():
    # bit-identical NON-trivial rows over a long series: BLAS rho can
    # overshoot 1.0 by a few ulp, and the clamp must bring the distance to
    # exactly 0.0 (sqrt of a negative would raise a warning / go nan)
    vals = next(iter(seeded_series(1, 500).values()))
    series = {"a" * 16: vals,
              "b" * 16: list(vals),
              "c" * 16: [-v for v in vals]}
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert D[0, 1] == 0.0
    assert D[0, 2] == pytest.approx(1.0, abs=1e-12)
    assert np.all(np.isfinite(D)) and np.all(D >= 0.0)


def test_distance_matrix_np_short_series():
    # L < 2: correlation() returns 0.0, so every off-diagonal distance is
    # sqrt(0.5) and the diagonal stays 0
    series = {"a" * 16: [0.01], "b" * 16: [0.02], "c" * 16: [0.0]}
    _assert_matrix_matches_reference(series)
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    for i in range(3):
        for j in range(3):
            expected = 0.0 if i == j else 0.5 ** 0.5
            assert D[i, j] == pytest.approx(expected)


def test_distance_matrix_np_single_series():
    series = {"a" * 16: [0.01, 0.02, -0.01]}
    _assert_matrix_matches_reference(series)
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert D.shape == (1, 1)
    assert D[0, 0] == 0.0


# ---------------- _agglomerate_np ----------------

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


def test_agglomerate_np_quantized_tie_sweep():
    """Randomized equivalence sweep on tie-heavy coarse-grid matrices
    (values integers(1,6)/10, so exact ties are frequent and bit-identical).
    Pins the tie machinery of _agglomerate_np: the candidate window in
    row_best, the lexicographic min inside the window, and the round(d, 12)
    row key.

    A fine-grid variant (0.5 + k*1e-12, integer k) was tried and DIVERGES
    by construction: cluster averages of integer-k leaves land exactly on
    half-1e-12 rounding boundaries, where the fast path's S-accumulation
    and the reference's flat sums round apart (1 ulp across the boundary).
    That is the documented quantization limitation in the module docstring,
    not a tie-machinery bug; real correlation distances are continuous and
    never sit on those boundaries, and the recorded-data identity proof
    (plan Tasks 4/5) is the ship bar for the real chain.

    Known incomplete: byte-identical duplicate rows DO sit exactly on the
    rho = 1 clamp boundary; that class is closed by the duplicate-row pin
    in _distance_matrix_np (see its docstring), not by this sweep.
    """
    rng = np.random.default_rng(20260828)
    for case in range(20):
        n = int(rng.integers(8, 13))
        ids = sorted(f"{k:02d}" + "q" * 14 for k in range(n))
        vals = rng.integers(1, 6, size=(n, n)) / 10.0
        D = np.triu(vals, 1)
        D = D + D.T
        dmat = {}
        for i, a in enumerate(ids):
            for j, b in enumerate(ids):
                dmat[(a, b)] = float(D[i, j])
        ref = agglomerate(ids, dmat)
        new = _agglomerate_np(ids, D)
        assert _hist_as_sets(new) == _hist_as_sets(ref), f"case {case}"


def _dict_from_pairs(ids, default, pairs):
    dmat = {}
    for i in ids:
        for j in ids:
            dmat[(i, j)] = 0.0 if i == j else default
    for x, y, v in pairs:
        dmat[(x, y)] = v
        dmat[(y, x)] = v
    return dmat


def test_agglomerate_np_sub_rounding_ties():
    """Hand-built raw-vs-rounded tie cases the coarse sweep cannot reach:
    candidate distances that differ raw (by 1e-13 or 1.2e-12) but round
    together at 12dp, placed at least 1e-13 away from every half-1e-12
    rounding boundary so both paths round identically by construction. The
    decisive rounds involve only singleton clusters, so the fast path's S
    values are the leaf doubles exactly and no summation-order drift exists.

    Pins the two tie-machinery behaviors the coarse sweep leaves open:
    the 2e-12 candidate window in row_best (a raw argmin that is NOT the
    key winner on any row of the winning pair), and taking the
    lexicographic min INSIDE the window rather than the first candidate in
    slot order (an earlier-slot decoy in the window with a higher rounded
    distance on both rows of the winning pair).
    """
    a, b, c, d = (ch * 16 for ch in "abcd")

    # window case: every row's raw argmin misses the true key winner (a, b),
    # which sits 1e-13 above each row's raw minimum yet rounds equal
    ids = [a, b, c]
    dmat = _dict_from_pairs(ids, 0.2, [(a, b, 0.2 + 1e-13)])
    ref = agglomerate(ids, dmat)
    new = _agglomerate_np(ids, dmat_to_array(ids, dmat))
    assert _hist_as_sets(new) == _hist_as_sets(ref)
    assert new[0] == (frozenset({a}), frozenset({b}))

    # first-candidate case: winning pair (c, d) at raw 0.3; both of its rows
    # carry an earlier-slot decoy 1.2e-12 above (inside the window, rounding
    # one 12dp step higher), so slot order and key order disagree
    ids = [a, b, c, d]
    dmat = _dict_from_pairs(ids, 0.9,
                            [(c, d, 0.3),
                             (b, c, 0.3 + 1.2e-12),
                             (a, d, 0.3 + 1.2e-12)])
    ref = agglomerate(ids, dmat)
    new = _agglomerate_np(ids, dmat_to_array(ids, dmat))
    assert _hist_as_sets(new) == _hist_as_sets(ref)
    assert new[0] == (frozenset({c}), frozenset({d}))


# ---------------- _effective_trials_np ----------------

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


# ---------------- duplicate-row pinning (rho clamp identity break) ----------

# Byte-exact fixture where the REFERENCE correlation on a byte-identical
# duplicate lands at rho = 1 - 1ulp (pow-vs-multiply term drift), BELOW the
# clamp: reference distance is 7.45e-9, not 0.0. Found by fuzz 2026-08-28.
_TAIL_HEX = ["0x1.d2176496033f0p-7", "0x1.9cd277e8c6e09p+1",
             "-0x1.074a6cab8e68ap-8", "0x1.17ae88d344523p+1",
             "-0x1.62432b038b93ep-6", "0x1.3c68b10f8330fp+0",
             "-0x1.317b5eb4ee0a9p-7", "0x1.6201585308a7ap-7"]


def test_duplicate_rows_pinned_to_reference_value():
    # common case: identical positive-variance rows -> the reference rho is
    # exactly 1.0 (or 1 + 1ulp, clamped), so the pinned distance is 0.0
    s = [0.01, -0.02, 0.03, 0.01, -0.015, 0.02]
    series = {"a" * 16: list(s), "b" * 16: list(s),
              "c" * 16: [-0.01, 0.02, -0.03, 0.0, 0.01, -0.02]}
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert distance(correlation(s, s)) == 0.0
    assert D[0, 1] == 0.0 and D[1, 0] == 0.0
    assert D[0, 0] == 0.0 and D[1, 1] == 0.0


def test_duplicate_rows_tail_case_pins_nonzero_reference_value():
    # the pin must reproduce the reference VALUE, not force 0.0: on this
    # fixture the reference distance for the duplicate pair is nonzero
    s = [float.fromhex(h) for h in _TAIL_HEX]
    d_ref = distance(correlation(s, list(s)))
    assert d_ref != 0.0                      # this fixture IS the tail case
    series = {"a" * 16: list(s), "b" * 16: list(s)}
    _, X = _returns_matrix(series)
    D = _distance_matrix_np(X)
    assert D[0, 1] == d_ref and D[1, 0] == d_ref
    # end-to-end identity on a pool containing the tail pair
    series["c" * 16] = [v * 0.5 + 0.001 * i for i, v in enumerate(s)]
    series["d" * 16] = [-v for v in s]
    assert effective_trials(series) == _effective_trials_ref(series)


def test_duplicate_all_zero_rows_keep_sqrt_half():
    # zero-variance duplicates are EXCLUDED from the pin: the reference
    # correlation returns 0.0 even for identical lists -> d = sqrt(0.5)
    series = {"a" * 16: [0.0] * 6, "b" * 16: [0.0] * 6,
              "c" * 16: [0.01, -0.02, 0.03, 0.0, 0.01, -0.01]}
    _, X = _returns_matrix(series)
    assert X is not None
    D = _distance_matrix_np(X)
    assert D[0, 1] == pytest.approx(0.5 ** 0.5)
    assert D[0, 1] == D[1, 0]
    assert np.array_equal(D, D.T)


def test_effective_trials_np_minimal_duplicate_repro():
    # reviewer's minimal repro: n=4, two exact-duplicate pairs from distinct
    # groups. Pre-pin, tied zero-distance merges ordered differently between
    # the paths and the -inf silhouette disqualification flipped k (ref k=3
    # vs np k=2, recorded-var difference up to 7x).
    base = seeded_series(2, 40)
    s1, s2 = (base[i] for i in sorted(base))
    series = {"a" * 16: list(s1), "b" * 16: list(s1),
              "c" * 16: list(s2), "d" * 16: list(s2)}
    _assert_effective_trials_identical(series)
    assert effective_trials(series) == _effective_trials_ref(series)


def test_effective_trials_duplicate_dense_sweep():
    # ~30 fixtures, n 8-20: 2-4 duplicate groups of size 2-4 drawn from
    # seeded structured rows, plus singletons; every third fixture also
    # carries one all-zero row. Tuple identity end-to-end through the
    # public dispatcher.
    rng = np.random.default_rng(20260829)
    for case in range(30):
        n_groups = int(rng.integers(2, 5))
        length = int(rng.integers(40, 90))
        pool = seeded_series(12, length)
        rows = [pool[i] for i in sorted(pool)]
        series = {}
        i = 0
        for g in range(n_groups):
            for _ in range(int(rng.integers(2, 5))):
                series[f"{i:04d}" + "d" * 12] = list(rows[g])
                i += 1
        s_idx = n_groups
        for _ in range(int(rng.integers(1, 4))):
            series[f"{i:04d}" + "d" * 12] = list(rows[s_idx])
            i += 1
            s_idx += 1
        while len(series) < 8:
            series[f"{i:04d}" + "d" * 12] = list(rows[s_idx])
            i += 1
            s_idx += 1
        if case % 3 == 0:
            series[f"{i:04d}" + "d" * 12] = [0.0] * length
            i += 1
        assert 8 <= len(series) <= 21
        assert effective_trials(series) == _effective_trials_ref(series), \
            f"case {case}"


# ---------------- step 7 (2026-10-03): V2 bookkeeping + memory ----------------
# The array bookkeeping in _agglomerate_np and the in-place distance arithmetic
# must change NO result (v6.1, chain entry 68486, fixes the method). The real-
# data proof is tools/verify_cluster_identity.py; these hold the contract in
# the suite.

from . import cluster as _cluster


def _fresh_key(S, sizes, active, i):
    """The ORIGINAL per-element re-scan of slot i: (round(d, 12), lo, hi) over
    every active j in the 2e-12 window, compared in a Python loop. Slot
    numbers stand in for min ids (the slot invariant)."""
    avg = S[i] / (sizes[i] * sizes)
    avg[~active] = np.inf
    avg[i] = np.inf
    raw = avg.min()
    if raw == np.inf:
        return None
    best = None
    for j in np.flatnonzero(avg <= raw + 2e-12):
        j = int(j)
        key = (round(float(avg[j]), 12), min(i, j), max(i, j))
        if best is None or key < best:
            best = key
    return best


def _cache_checker(seen):
    """An _after_merge hook asserting, for EVERY active row, that the cached
    key equals a fresh re-scan (and the cached partner is that key's other
    slot). Counts merges checked into seen['merges']."""
    def check(S, sizes, active, cd, clo, chi, cj):
        seen["merges"] = seen.get("merges", 0) + 1
        for i in np.flatnonzero(active):
            i = int(i)
            fresh = _fresh_key(S, sizes, active, i)
            cached = (float(cd[i]), int(clo[i]), int(chi[i]))
            assert cached == fresh, (
                f"merge {seen['merges']}: row {i} cached {cached} != fresh {fresh}")
            assert int(cj[i]) == (fresh[2] if fresh[1] == i else fresh[1])
    return check


def test_agglomerate_np_cache_matches_a_fresh_rescan_after_every_merge():
    """The O(1) tie-only update is invisible to every history comparison (a
    stale key there is only ever too HIGH, and the keep row holds the true
    minimum), so this asserts the invariant itself. Fixture: slots 0..3 with
    d(0,1) = 0.5 + 8e-13, d(0,2) = d(0,3) = 0.5 and (1, 3) merging first.
    Row 0 caches partner 2 (key (0.5, 0, 2)); after the merge its average
    to slot 1 is 0.5 + 4e-13, which rounds to 0.5 and wins on (lo, hi):
    the fresh key is (0.5, 0, 1). Only the tie update records that."""
    ids = ["a" * 16, "b" * 16, "c" * 16, "d" * 16]
    D = np.array([[0.0, 0.5 + 8e-13, 0.5, 0.5],
                  [0.5 + 8e-13, 0.0, 0.9, 0.01],
                  [0.5, 0.9, 0.0, 0.9],
                  [0.5, 0.01, 0.9, 0.0]])
    seen, rows0 = {}, []
    check = _cache_checker(seen)

    def hook(S, sizes, active, cd, clo, chi, cj):
        rows0.append((float(cd[0]), int(clo[0]), int(chi[0])))
        check(S, sizes, active, cd, clo, chi, cj)
    hist = _agglomerate_np(ids, D, _after_merge=hook)
    assert seen["merges"] == 2                       # the last merge ends the loop
    assert rows0[0] == (0.5, 0, 1), "the fixture must exercise the tie update"
    ref = agglomerate(ids, {(a, b): D[i, j] for i, a in enumerate(ids)
                            for j, b in enumerate(ids)})
    assert _hist_as_sets(hist) == _hist_as_sets(ref)


def test_agglomerate_np_cache_invariant_on_heavy_ties():
    """The same per-merge invariant on fixtures dominated by exact ties: blocks
    of all-zero rows (distance exactly sqrt(0.5) to everything, like the live
    registry's 79), identical rows, and distances quantised near the 1e-12
    rounding boundary."""
    rng = np.random.default_rng(20261003)
    merges = 0
    for case in range(60):
        n = int(rng.integers(6, 28))
        L = int(rng.integers(20, 60))
        X = rng.standard_normal((n, L)) * 0.01
        X[rng.random(n) < 0.35] = 0.0
        dup = rng.random(n) < 0.25
        X[dup] = X[int(rng.integers(0, n))]
        ids = [f"{i:04d}" + "t" * 12 for i in range(n)]
        D = _distance_matrix_np(X)
        if case % 3 == 1:
            D = np.round(D, 1) + (rng.integers(0, 3, D.shape) * 4e-13)
            D = np.triu(D, 1)
            D = D + D.T
        seen = {}
        _agglomerate_np(ids, D, _after_merge=_cache_checker(seen))
        merges += seen.get("merges", 0)
    assert merges > 500


def test_effective_trials_np_fuzz_heavy_exact_ties():
    """Fuzz, end to end against the pure-Python reference: many identical
    and all-zero rows (exact ties at every level of the merge), through the
    merge history and the public dispatcher."""
    rng = np.random.default_rng(31337)
    for case in range(40):
        n = int(rng.integers(5, 26))
        L = int(rng.integers(30, 70))
        base = rng.standard_normal((3, L)) * 0.01
        rows = []
        for i in range(n):
            r = rng.random()
            if r < 0.35:
                rows.append([0.0] * L)
            elif r < 0.7:
                rows.append([float(v) for v in base[int(rng.integers(0, 3))]])
            else:
                rows.append([float(v) for v in rng.standard_normal(L) * 0.01])
        series = {f"{i:04d}" + "z" * 12: rows[i] for i in range(n)}
        ids = sorted(series)
        ref_hist = agglomerate(ids, distance_matrix(series))
        new_hist = _agglomerate_np(ids, dmat_to_array(ids, distance_matrix(series)))
        assert _hist_as_sets(new_hist) == _hist_as_sets(ref_hist), f"case {case}"
        if _returns_matrix(series)[1] is not None:
            assert effective_trials(series) == _effective_trials_ref(series), \
                f"case {case}"


def test_row_blocks_change_no_value(monkeypatch):
    """_ROWS only bounds temporaries. With tiny blocks (many boundaries,
    including a ragged last block) the distance matrix is byte-identical to
    the ORIGINAL whole-matrix expression, and effective_trials is unchanged."""
    rng = np.random.default_rng(7)
    n, L = 53, 40
    X = rng.standard_normal((n, L)) * 0.01
    X[[3, 17, 40]] = 0.0
    X[[5, 6, 7]] = X[9]
    # the pre-step-7 expression, verbatim
    M = X - X.mean(axis=1, keepdims=True)
    ss = np.einsum("ij,ij->i", M, M)
    good = ss > 0.0
    with np.errstate(invalid="ignore", divide="ignore"):
        R = (M @ M.T) / np.sqrt(np.outer(ss, ss))
    R[~good, :] = 0.0
    R[:, ~good] = 0.0
    np.clip(R, -1.0, 1.0, out=R)
    want = np.sqrt(0.5 * (1.0 - R))
    want = np.triu(want, 1)
    want = want + want.T
    series = {f"{i:04d}" + "r" * 12: X[i].tolist() for i in range(n)}
    before = effective_trials(series)
    # the duplicate pin rewrites the duplicate block; compare everywhere else
    mask = np.ones((n, n), dtype=bool)
    mask[np.ix_([5, 6, 7, 9], [5, 6, 7, 9])] = False
    for rows in (1, 4, 7, 52, 53, 1000):
        monkeypatch.setattr(_cluster, "_ROWS", rows)
        D = _distance_matrix_np(X)
        assert D[mask].tobytes() == want[mask].tobytes(), f"_ROWS={rows}"
        assert effective_trials(series) == before, f"_ROWS={rows}"


def test_effective_trials_np_holds_one_matrix_at_a_time(monkeypatch):
    """Memory: the merge sums consume D in place and the replay recomputes it,
    so the traced peak is about 1.6 x n^2 x 8 bytes (measured, n = 600, small
    row blocks), not the 4.3 x of three live n x n matrices plus full-size
    temporaries before step 7."""
    import tracemalloc
    monkeypatch.setattr(_cluster, "_ROWS", 16)
    rng = np.random.default_rng(0)
    n, L = 600, 64
    X = rng.standard_normal((n, L)) + rng.standard_normal((6, L))[rng.integers(0, 6, n)]
    X[:5] = 0.0
    series = {f"{i:05d}" + "m" * 11: X[i] for i in range(n)}
    ids, Xm = _returns_matrix(series)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        _effective_trials_np(series, ids, Xm)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2.2 * n * n * 8, f"peak {peak / (n * n * 8):.2f} x n^2 x 8"


def test_agglomerate_np_overwrite_is_opt_in():
    """Default callers keep their D (test helpers and the reference checks
    pass one D twice); overwrite=True consumes it and gives the same history."""
    series = seeded_series(30, 80)
    ids = sorted(series)
    D = _distance_matrix_np(_returns_matrix(series)[1])
    keep = D.copy()
    first = _agglomerate_np(ids, D)
    assert D.tobytes() == keep.tobytes()
    assert _agglomerate_np(ids, D, overwrite=True) == first
    assert D.tobytes() != keep.tobytes()


def test_mirror_upper_is_the_original_symmetrisation(monkeypatch):
    """_mirror_upper(D) == triu(D, 1) + triu(D, 1).T byte for byte, on an
    ASYMMETRIC non-negative matrix (BLAS usually hands back a symmetric Gram
    matrix, which would hide a mirror that does nothing), across block
    boundaries and a ragged last block."""
    rng = np.random.default_rng(11)
    for rows in (1, 3, 8, 512):
        monkeypatch.setattr(_cluster, "_ROWS", rows)
        A = rng.random((19, 19))
        want = np.triu(A, 1)
        want = want + want.T
        got = A.copy()
        _cluster._mirror_upper(got)
        assert got.tobytes() == want.tobytes(), f"_ROWS={rows}"
