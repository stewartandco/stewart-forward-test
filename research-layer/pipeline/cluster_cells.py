"""Per-partition average-linkage cut at correlation 0.9 (design s4).

The effective-trials count no longer clusters the whole registry. Each cell
partition gets cluster.py's own average-linkage tree, cut at a FIXED distance:
1. Never-traded rows (every value exactly 0.0) leave the tree and form ONE
   cluster of their own; any other constant row raises ConstantNonzeroRow.
2. The remaining rows get cluster._agglomerate_np's merge history on
   cluster._distance_matrix_np, unchanged; merge_heights replays it to read
   each merge's average-linkage height.
3. A merge counts when round(height, 12) <= round(CUT_DISTANCE, 12); the
   counted merges are the PREFIX up to the first merge over the cut. A later
   merge at or under it (float noise) does not count and is reported.
4. k = tree rows - counted merges, plus 1 when never-traded rows exist; labels
   come from cluster.labels_for_k, the never-traded cluster labelled last.
CUT_RHO is a protocol constant: changing it needs a new chained note.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import qcells
from .cluster import (_agglomerate_np, _distance_matrix_np,  # noqa: F401
                      _reps_variance, _returns_matrix, labels_for_k)

CUT_RHO = 0.9
CUT_DISTANCE = math.sqrt(0.5 * (1 - CUT_RHO))
METHOD = "effective_trials/v4 per-cell average-linkage cut rho>=0.9"
_CUT_ROUNDED = round(CUT_DISTANCE, 12)


class ConstantNonzeroRow(ValueError):
    """A partition row is constant but not all zero (variance classification
    would differ between the numpy and reference paths): the run refuses."""


def partition_key(spec: dict) -> str:
    return "|".join(sorted(qcells.spec_cells(spec)))


def merge_heights(X: np.ndarray, history: list[tuple]) -> list[float]:
    """Average-linkage height of each history step. X has rows in SORTED-id
    order (as cluster._returns_matrix returns them); history slots map to
    those rows, the cluster holding the smaller min id at the smaller slot.
    The distance matrix is recomputed (bit-identical to the one the history
    was built on) and replayed with the same sums as _agglomerate_np."""
    n = X.shape[0]
    S = _distance_matrix_np(X)
    sizes = np.ones(n, dtype=np.float64)
    # Slot of a cluster = sorted index of its smallest member; the sorted
    # leaves of the history are the sorted ids of X's rows.
    ids = sorted(set().union(*[ca | cb for ca, cb in history])) if history else []
    idx_of = {i: j for j, i in enumerate(ids)}
    heights = []
    for ca, cb in history:
        ma, mb = min(ca), min(cb)
        keep, drop = idx_of[min(ma, mb)], idx_of[max(ma, mb)]
        heights.append(float(S[keep, drop] / (sizes[keep] * sizes[drop])))
        S[keep, :] += S[drop, :]
        S[:, keep] += S[:, drop]
        sizes[keep] = sizes[keep] + sizes[drop]
    return heights


@dataclass(frozen=True)
class PartitionCut:
    k: int
    labels: dict
    never_traded: int
    late_merges: int


def cut_partition(returns_by_id: dict) -> PartitionCut:
    ids = sorted(returns_by_id)
    if not ids:
        return PartitionCut(0, {}, 0, 0)
    zero_ids, tree = [], {}
    for i in ids:
        row = np.asarray(returns_by_id[i], dtype=np.float64)
        if row.size and bool((row == 0.0).all()):
            zero_ids.append(i)
        elif row.size and float(row.max()) == float(row.min()):
            raise ConstantNonzeroRow(
                f"{i}: constant nonzero series ({float(row[0])!r})")
        else:
            tree[i] = returns_by_id[i]
    tree_ids = sorted(tree)
    counted = late = 0
    if not tree_ids:
        labels = {}
    elif len(tree_ids) == 1:
        labels = {tree_ids[0]: 0}
    else:
        ids2, X = _returns_matrix(tree)
        if X is None:
            raise ValueError("partition series are not rectangular")
        history = _agglomerate_np(ids2, _distance_matrix_np(X), overwrite=True)
        heights = merge_heights(X, history)
        over = None
        for t, h in enumerate(heights):
            if round(h, 12) > _CUT_ROUNDED:
                over = t
                break
        counted = len(heights) if over is None else over
        if over is not None:
            late = sum(1 for h in heights[over + 1:] if round(h, 12) <= _CUT_ROUNDED)
        labels = labels_for_k(history, ids2, len(ids2) - counted)
    k_tree = len(set(labels.values()))
    for i in zero_ids:
        labels[i] = k_tree
    k = k_tree + (1 if zero_ids else 0)
    return PartitionCut(k, labels, len(zero_ids), late)


def effective_trials_by_cell(returns_by_id: dict, key_of: dict) -> dict:
    """Registry-wide count: sum of per-partition cuts (design s4 steps 5-7).

    Partitions are cut in sorted key order and their labels offset by the k of
    the earlier ones. trials_var pools every cluster representative (mean member
    Sharpe) through cluster._reps_variance over all sorted ids; a never-traded
    cluster's representative is 0.0 and enters once, however many rows it holds.
    """
    groups: dict[str, dict] = {}
    for sid in sorted(returns_by_id):
        groups.setdefault(key_of[sid], {})[sid] = returns_by_id[sid]
    labels: dict[str, int] = {}
    by_partition: dict[str, dict] = {}
    offset = late = sum_n2 = largest = 0
    for key in sorted(groups):
        part = groups[key]
        cut = cut_partition(part)
        for sid, lab in cut.labels.items():
            labels[sid] = lab + offset
        offset += cut.k
        late += cut.late_merges
        n = len(part)
        sum_n2 += n * n
        largest = max(largest, n)
        by_partition[key] = {"n": n, "k": cut.k, "never_traded": cut.never_traded}
    ids = sorted(returns_by_id)
    var = _reps_variance(returns_by_id, ids, labels) if ids else 0.0
    return {"trials_n": offset, "cluster_labels": labels, "trials_var": var,
            "partitions": len(groups), "largest_partition_n": largest,
            "late_merges": late, "sum_n2": sum_n2, "by_partition": by_partition}
