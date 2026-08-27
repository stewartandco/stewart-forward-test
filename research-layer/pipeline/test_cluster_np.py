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
