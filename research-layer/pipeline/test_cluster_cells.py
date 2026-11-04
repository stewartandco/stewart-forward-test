"""Tests for the per-partition average-linkage cut (cluster_cells.py).

Run: python -m pytest pipeline/test_cluster_cells.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from . import cluster, cluster_cells
from .cluster_cells import (CUT_DISTANCE, CUT_RHO, ConstantNonzeroRow,
                            cut_partition, merge_heights, partition_key)

L = 60


def rho_pair(rho: float, seed: int = 0, length: int = L):
    """Two series with sample correlation exactly rho."""
    rng = np.random.default_rng(seed)
    a = rng.standard_normal(length)
    a = (a - a.mean()) / a.std()
    z = rng.standard_normal(length)
    z = z - z.mean()
    z -= (z @ a) / (a @ a) * a
    z = z / z.std()
    b = rho * a + np.sqrt(1 - rho ** 2) * z
    return a.tolist(), b.tolist()


def random_rows(n: int, seed: int = 1, length: int = L) -> dict:
    rng = np.random.default_rng(seed)
    return {f"s{i:02d}": rng.standard_normal(length).tolist() for i in range(n)}


def spec(assets, tf):
    return {"universe": {"assets": assets, "timeframe": tf}}


def test_partition_key_sorts_cells_and_keeps_timeframe():
    assert partition_key(spec(["ETHUSD", "BTCUSD"], "1d")) == "BTCUSD|ETHUSD"
    assert partition_key(spec(["BTCUSDT"], "1h")) == "BTCUSDT@1h"


def test_constants():
    assert CUT_RHO == 0.9
    assert CUT_DISTANCE == pytest.approx(0.2236067977499790)
    assert cluster_cells.METHOD == (
        "effective_trials/v4 per-cell average-linkage cut rho>=0.9")


def test_merge_heights_equal_brute_force_average_linkage():
    rows = random_rows(12, seed=3)
    ids, X = cluster._returns_matrix(rows)
    D = cluster._distance_matrix_np(X)
    history = cluster._agglomerate_np(ids, D, overwrite=False)
    heights = merge_heights(X, history)
    idx = {i: j for j, i in enumerate(ids)}
    assert len(heights) == len(history) == 11
    for h, (ca, cb) in zip(heights, history):
        brute = np.mean([D[idx[a], idx[b]] for a in ca for b in cb])
        assert h == pytest.approx(brute, abs=1e-12)
    assert all(b >= a - 1e-15 for a, b in zip(heights, heights[1:]))


def test_history_is_clusters_own_agglomerate(monkeypatch):
    rows = random_rows(8, seed=5)
    calls = []
    real = cluster._agglomerate_np

    def spy(ids, D, **kw):
        calls.append(list(ids))
        return real(ids, D, **kw)

    monkeypatch.setattr(cluster_cells, "_agglomerate_np", spy)
    seen = {}
    real_mh = cluster_cells.merge_heights

    def grab(X, history):
        seen["history"] = history
        return real_mh(X, history)

    monkeypatch.setattr(cluster_cells, "merge_heights", grab)
    cut_partition(rows)
    assert calls == [sorted(rows)]
    ids, X = cluster._returns_matrix(rows)
    expect = real(ids, cluster._distance_matrix_np(X), overwrite=True)
    assert seen["history"] == expect


def test_pair_above_cut_merges_and_pair_below_does_not():
    a, b = rho_pair(0.95)
    assert cut_partition({"a": a, "b": b}).k == 1
    a, b = rho_pair(0.85)
    assert cut_partition({"a": a, "b": b}).k == 2


def test_cut_boundary_is_inclusive_at_12_decimals(monkeypatch):
    a, b = rho_pair(0.5)
    rows = {"a": a, "b": b}
    monkeypatch.setattr(cluster_cells, "merge_heights",
                        lambda X, h: [CUT_DISTANCE + 4e-13])
    assert cut_partition(rows).k == 1
    monkeypatch.setattr(cluster_cells, "merge_heights",
                        lambda X, h: [CUT_DISTANCE + 6e-13])
    assert cut_partition(rows).k == 2


def test_one_member_partition_is_one():
    r = cut_partition({"only": random_rows(1)["s00"]})
    assert r.k == 1 and r.labels == {"only": 0}
    assert r.never_traded == 0 and r.late_merges == 0


def test_one_member_intraday_partition_is_one():
    # the first intraday registration (BTCUSDT@1h) arrives alone
    assert partition_key(spec(["BTCUSDT"], "1h")) == "BTCUSDT@1h"
    r = cut_partition({"intraday1": random_rows(1, seed=9)["s00"]})
    assert r.k == 1 and r.labels == {"intraday1": 0}


def test_duplicate_rows_collapse_to_one():
    row = random_rows(1, seed=11)["s00"]
    r = cut_partition({f"d{i}": list(row) for i in range(5)})
    assert r.k == 1
    assert set(r.labels.values()) == {0}


def test_never_traded_rows_form_one_cluster():
    a, b = rho_pair(0.95)
    rows = {"a": a, "b": b, "z1": [0.0] * L, "z2": [0.0] * L, "z3": [0.0] * L}
    r = cut_partition(rows)
    assert r.k == 2
    assert r.never_traded == 3
    assert r.labels["a"] == r.labels["b"] == 0
    assert r.labels["z1"] == r.labels["z2"] == r.labels["z3"] == 1


def test_all_never_traded_partition_is_one():
    r = cut_partition({f"z{i}": [0.0] * L for i in range(4)})
    assert r.k == 1 and r.never_traded == 4
    assert set(r.labels.values()) == {0}


def test_constant_nonzero_row_raises():
    rows = random_rows(3, seed=2)
    rows["c"] = [0.01] * L
    with pytest.raises(ConstantNonzeroRow):
        cut_partition(rows)


def test_late_merge_not_counted_and_reported(monkeypatch):
    rows = random_rows(4, seed=4)
    monkeypatch.setattr(cluster_cells, "merge_heights",
                        lambda X, h: [0.10, 0.30, 0.20])
    r = cut_partition(rows)
    assert r.k == 3
    assert r.late_merges == 1


def test_matches_scipy_fcluster_on_block_fixture():
    sp = pytest.importorskip("scipy.cluster.hierarchy")
    from scipy.spatial.distance import squareform
    rng = np.random.default_rng(21)
    rows = {}
    for blk in range(6):
        factor = rng.standard_normal(L)
        for j in range(10):
            rows[f"b{blk}_{j:02d}"] = (factor + 0.35 * rng.standard_normal(L)).tolist()
    ids, X = cluster._returns_matrix(rows)
    D = cluster._distance_matrix_np(X)
    Z = sp.linkage(squareform(D, checks=False), "average")
    expect = len(set(sp.fcluster(Z, CUT_DISTANCE, "distance")))
    r = cut_partition(rows)
    assert r.k == expect
    assert 1 < r.k < 60
