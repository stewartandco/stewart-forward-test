import numpy as np
import pytest

from pipeline import cluster
from tools.verify_cluster_identity import (DEFAULT_OLD_REV, ReadOnlyCache,
                                           load_old_cluster, run_module)


def _fixture(n=40, L=60, seed=3):
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, L)) * 0.01
    X[[2, 11, 30]] = 0.0                       # all-zero rows, as on the live registry
    X[[5, 6]] = X[20]                          # identical rows
    return {f"{i:04d}" + "v" * 12: X[i] for i in range(n)}


def test_the_old_revision_loads_and_matches_on_a_tie_heavy_fixture():
    """The proof's two arms agree on everything it compares, and the old arm
    really is the pre-step-7 code (no array cache in its _agglomerate_np)."""
    old = load_old_cluster(DEFAULT_OLD_REV)
    assert "overwrite" not in old._agglomerate_np.__code__.co_varnames
    rbi = _fixture()
    a, b = run_module(old, rbi), run_module(cluster, rbi)
    assert a["history"] is not None and a["history"] == b["history"]
    assert (a["k"], a["labels"], a["var"].hex()) == (b["k"], b["labels"], b["var"].hex())


def test_run_module_restores_the_module_it_wrapped():
    real = cluster._agglomerate_np
    run_module(cluster, _fixture())
    assert cluster._agglomerate_np is real


def test_the_sim_cache_is_read_only(tmp_path):
    c = ReadOnlyCache(tmp_path)
    with pytest.raises(RuntimeError):
        c.put("k", [("2026-01-01", 0.0)], 1)
    with pytest.raises(RuntimeError):
        c.migrate()
    bad = tmp_path / "x.npz"
    bad.write_bytes(b"not an npz")
    assert c.get("x") is None and bad.exists()   # reported, never deleted
