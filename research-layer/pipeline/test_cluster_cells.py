"""Tests for the per-partition average-linkage cut (cluster_cells.py).

Run: python -m pytest pipeline/test_cluster_cells.py -q
"""
from __future__ import annotations

import statistics

import numpy as np
import pytest

from . import cluster, cluster_cells
from .cluster_cells import (CUT_DISTANCE, CUT_RHO, ConstantNonzeroRow,
                            cut_partition, effective_trials_by_cell,
                            merge_heights, partition_key)

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


# ---- registry-wide aggregation (Task 2) ----

def _blocks(prefix: str, k: int, per: int, seed: int) -> dict:
    """k well separated groups of `per` near-duplicate rows."""
    rng = np.random.default_rng(seed)
    out = {}
    for g in range(k):
        base = rng.standard_normal(L)
        for j in range(per):
            out[f"{prefix}{g}_{j}"] = (base + 0.01 * rng.standard_normal(L)).tolist()
    return out


def _three_partitions():
    rows, key_of = {}, {}
    for prefix, k, seed, key in (("a", 2, 1, "A"), ("b", 1, 2, "B"), ("c", 3, 3, "C")):
        part = _blocks(prefix, k, 2, seed)
        rows.update(part)
        key_of.update({sid: key for sid in part})
    return rows, key_of


def test_trials_n_is_the_sum_of_partition_k():
    rows, key_of = _three_partitions()
    r = effective_trials_by_cell(rows, key_of)
    assert [r["by_partition"][k]["k"] for k in "ABC"] == [2, 1, 3]
    assert r["trials_n"] == 6
    assert r["partitions"] == 3


def test_labels_offset_in_sorted_key_order():
    rows, key_of = _three_partitions()
    r = effective_trials_by_cell(rows, key_of)
    lab = r["cluster_labels"]
    assert {lab[s] for s in rows if key_of[s] == "A"} == {0, 1}
    assert {lab[s] for s in rows if key_of[s] == "B"} == {2}
    assert {lab[s] for s in rows if key_of[s] == "C"} == {3, 4, 5}


def test_pooled_variance_matches_hand_computation():
    rows, key_of = _three_partitions()
    r = effective_trials_by_cell(rows, key_of)
    groups = {}
    for sid, lab in r["cluster_labels"].items():
        groups.setdefault(lab, []).append(sid)
    reps = [sum(cluster._sharpe(rows[s]) for s in g) / len(g)
            for g in groups.values()]
    assert r["trials_var"] == pytest.approx(statistics.variance(reps), rel=1e-12)


def test_never_traded_representative_enters_once():
    base = _blocks("a", 2, 2, 7)
    key_one = {s: "A" for s in base}
    key_one["z0"] = "Z"
    one = dict(base, z0=[0.0] * L)
    three = dict(one, z1=[0.0] * L, z2=[0.0] * L)
    key_three = dict(key_one, z1="Z", z2="Z")
    r1 = effective_trials_by_cell(one, key_one)
    r3 = effective_trials_by_cell(three, key_three)
    assert r1["trials_var"] == r3["trials_var"]
    assert r1["trials_n"] == r3["trials_n"] == 3
    assert r3["by_partition"]["Z"] == {"n": 3, "k": 1, "never_traded": 3}


def test_order_independence():
    rows, key_of = _three_partitions()
    base = effective_trials_by_cell(rows, key_of)
    rng = np.random.default_rng(0)
    sids = list(rows)
    rng.shuffle(sids)
    rows2 = {s: rows[s] for s in sids}
    key2 = {s: key_of[s] for s in reversed(sids)}
    assert effective_trials_by_cell(rows2, key2) == base


def test_other_partitions_do_not_change_a_partition():
    rows, key_of = _three_partitions()
    full = effective_trials_by_cell(rows, key_of)
    keep = {s for s in rows if key_of[s] != "B"}
    sub = effective_trials_by_cell({s: rows[s] for s in keep},
                                   {s: key_of[s] for s in keep})
    assert sub["by_partition"]["A"] == full["by_partition"]["A"]
    assert "B" not in sub["by_partition"]


def test_sum_n2_and_largest():
    rows, key_of = {}, {}
    for key, n, seed in (("A", 3, 1), ("B", 1, 2), ("C", 5, 3)):
        part = random_rows(n, seed=seed)
        for sid, v in part.items():
            rows[f"{key}{sid}"] = v
            key_of[f"{key}{sid}"] = key
    r = effective_trials_by_cell(rows, key_of)
    assert r["sum_n2"] == 35
    assert r["largest_partition_n"] == 5
    assert r["late_merges"] == 0
