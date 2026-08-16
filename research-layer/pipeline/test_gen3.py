"""Offline tests for Composer gen-3 + gauntlet protocol-v3 (no network/API).

Run: python -m pytest pipeline/test_gen3.py -q
"""
from __future__ import annotations

import math

import pytest

from .cluster import correlation, distance, distance_matrix
from .cluster import agglomerate, labels_for_k, silhouette


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


def two_group_series():
    """Two tight, well-separated groups: {a,b,c} rise together, {d,e} fall."""
    up = [0.01, 0.02, -0.005, 0.015, 0.01, -0.002, 0.02, 0.008]
    dn = [-x for x in up]
    jitter = [0.0005, -0.0004, 0.0003, -0.0002, 0.0004, -0.0003, 0.0002, -0.0001]
    return {
        "a" * 16: up,
        "b" * 16: [x + j for x, j in zip(up, jitter)],
        "c" * 16: [x - j for x, j in zip(up, jitter)],
        "d" * 16: dn,
        "e" * 16: [x + j for x, j in zip(dn, jitter)],
    }


# ---------------- agglomerative clustering ----------------

def test_agglomerate_recovers_two_groups():
    series = two_group_series()
    dmat = distance_matrix(series)
    hist = agglomerate(sorted(series), dmat)
    labels = labels_for_k(hist, sorted(series), 2)
    up_group = {labels["a" * 16], labels["b" * 16], labels["c" * 16]}
    dn_group = {labels["d" * 16], labels["e" * 16]}
    assert len(up_group) == 1 and len(dn_group) == 1
    assert up_group != dn_group


def test_clustering_is_order_independent():
    series = two_group_series()
    dmat = distance_matrix(series)
    ids = sorted(series)
    a = labels_for_k(agglomerate(ids, dmat), ids, 2)
    # feed the ids in reverse; sorted() inside must normalise it
    b = labels_for_k(agglomerate(list(reversed(ids)), dmat), ids, 2)
    same = [[i for i in ids if a[i] == k] for k in sorted(set(a.values()))]
    other = [[i for i in ids if b[i] == k] for k in sorted(set(b.values()))]
    assert sorted(same) == sorted(other)


def test_labels_for_k_endpoints():
    series = two_group_series()
    ids = sorted(series)
    hist = agglomerate(ids, distance_matrix(series))
    one = labels_for_k(hist, ids, 1)
    assert len(set(one.values())) == 1
    allsep = labels_for_k(hist, ids, len(ids))
    assert len(set(allsep.values())) == len(ids)


def test_identical_series_merge_first():
    ids = ["a" * 16, "b" * 16, "c" * 16]
    series = {ids[0]: [0.01, -0.02, 0.03],
              ids[1]: [0.01, -0.02, 0.03],
              ids[2]: [-0.01, 0.03, -0.02]}
    hist = agglomerate(ids, distance_matrix(series))
    labels = labels_for_k(hist, ids, 2)
    assert labels[ids[0]] == labels[ids[1]] != labels[ids[2]]


# ---------------- silhouette ----------------

def test_silhouette_two_clean_clusters_is_high():
    series = two_group_series()
    ids = sorted(series)
    dmat = distance_matrix(series)
    labels = labels_for_k(agglomerate(ids, dmat), ids, 2)
    vals = silhouette(labels, dmat)
    assert len(vals) == len(ids)
    assert sum(vals) / len(vals) > 0.8


def test_tie_break_is_deterministic_and_lexicographic():
    """Two candidate merges at exactly equal distance must resolve the same
    way every run — the (distance, min_a, min_b) key is what guarantees it."""
    ids = ["a" * 16, "b" * 16, "c" * 16]
    s = [0.01, -0.02, 0.03, 0.01]
    # a, b, c mutually identical -> every pairwise distance is exactly 0
    series = {i: list(s) for i in ids}
    dmat = distance_matrix(series)
    assert all(dmat[(i, j)] == pytest.approx(0.0) for i in ids for j in ids)
    first = agglomerate(ids, dmat)[0]
    for _ in range(5):
        assert agglomerate(ids, dmat)[0] == first
    # lowest-id pair merges first
    assert first == (frozenset({ids[0]}), frozenset({ids[1]}))


def test_silhouette_singleton_scores_zero():
    ids = ["a" * 16, "b" * 16]
    series = {ids[0]: [0.01, -0.02, 0.03], ids[1]: [-0.01, 0.03, -0.02]}
    dmat = distance_matrix(series)
    vals = silhouette({ids[0]: 0, ids[1]: 1}, dmat)
    assert vals == [0.0, 0.0]
