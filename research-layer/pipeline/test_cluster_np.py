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
