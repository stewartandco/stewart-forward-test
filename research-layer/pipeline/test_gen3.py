"""Offline tests for Composer gen-3 + gauntlet protocol-v3 (no network/API).

Run: python -m pytest pipeline/test_gen3.py -q
"""
from __future__ import annotations

import math

import pytest

from .cluster import correlation, distance, distance_matrix


# ---------------- correlation + distance ----------------

def test_correlation_perfect_positive_and_negative():
    a = [0.01, -0.02, 0.03, 0.00, -0.01]
    b = [2 * x for x in a]
    c = [-x for x in a]
    assert correlation(a, b) == pytest.approx(1.0)
    assert correlation(a, c) == pytest.approx(-1.0)


def test_correlation_zero_variance_returns_zero():
    flat = [0.0] * 5
    assert correlation(flat, [0.01, -0.02, 0.03, 0.0, -0.01]) == 0.0
    assert correlation(flat, flat) == 0.0


def test_correlation_hand_case():
    # devs a=(-1,0,1) b=(-1,1,0): cov=1, va=2, vb=2 -> 1/sqrt(4) = 0.5
    a, b = [1.0, 2.0, 3.0], [1.0, 3.0, 2.0]
    expected = 0.5
    assert correlation(a, b) == pytest.approx(expected, abs=1e-9)


def test_distance_endpoints():
    assert distance(1.0) == pytest.approx(0.0)
    assert distance(0.0) == pytest.approx(math.sqrt(0.5))
    assert distance(-1.0) == pytest.approx(1.0)


def test_distance_matrix_is_symmetric_and_sorted():
    series = {"b" * 16: [0.01, 0.02, -0.01],
              "a" * 16: [0.01, 0.02, -0.01],
              "c" * 16: [-0.01, -0.02, 0.01]}
    dmat = distance_matrix(series)
    ids = sorted(series)
    for i in ids:
        for j in ids:
            assert dmat[(i, j)] == pytest.approx(dmat[(j, i)])
        assert dmat[(i, i)] == pytest.approx(0.0)
    # a and b are identical series -> distance 0; c is inverted -> distance 1
    assert dmat[("a" * 16, "b" * 16)] == pytest.approx(0.0)
    assert dmat[("a" * 16, "c" * 16)] == pytest.approx(1.0)
