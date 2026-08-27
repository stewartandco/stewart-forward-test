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
