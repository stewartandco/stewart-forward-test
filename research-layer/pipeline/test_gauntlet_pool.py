"""The gauntlet's worker pool must fit the box it runs on (2026-09-03).

A worker died with "OpenBLAS error: Memory allocation still failed after 10
retries" and the pool reported BrokenProcessPool: the parent held ~9.6 GB of
clustering series across the spawn, each worker committed ~280 MB at import
for an 8-thread BLAS pool it never uses, and the machine was already near
its commit limit. Three defences, each pinned here: workers get one BLAS
thread, the worker count is bounded by available commit, and the parent
releases the series before spawning (that last one is exercised by the live
run's own "[gauntlet] clustering inputs released" line, not by a unit test).
"""
import os

from . import gauntlet as g


def test_worker_count_is_cores_minus_two_when_commit_is_unknowable():
    assert g.worker_count(8, None) == 6
    assert g.worker_count(3, None) == 1
    assert g.worker_count(1, None) == 1


def test_worker_count_is_bounded_by_available_commit():
    # 8 cores would give 6; 4 GB available - 2 GB headroom = 2 GB / 512 MB = 4.
    assert g.worker_count(8, 4096) == 4
    # Plenty of commit: cores decide.
    assert g.worker_count(8, 64 * 1024) == 6
    # Less than the headroom: never below the serial reference path.
    assert g.worker_count(8, 1024) == 1
    assert g.worker_count(8, 0) == 1


def test_worker_env_caps_blas_threads_for_the_pool_and_restores_after(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "8")
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    with g.worker_env():
        assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
        assert os.environ["OMP_NUM_THREADS"] == "1"
        assert os.environ["MKL_NUM_THREADS"] == "1"
    assert os.environ["OPENBLAS_NUM_THREADS"] == "8"
    assert "OMP_NUM_THREADS" not in os.environ


def test_pool_path_runs_workers_under_the_env(monkeypatch):
    """_run_candidates enters worker_env() around the executor: pin that the
    context is actually used on the pool path (a fake executor observes
    the environment at submit time)."""
    seen = {}

    class FakeFuture:
        def __init__(self, r):
            self._r = r

        def result(self):
            return self._r

    class FakeExecutor:
        def __init__(self, max_workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, p):
            seen["OPENBLAS_NUM_THREADS"] = os.environ.get("OPENBLAS_NUM_THREADS")
            return FakeFuture({"sid": p["sid"]})

    monkeypatch.setattr(g, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(g, "as_completed", lambda fs: iter(fs))
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    out = g._run_candidates([{"sid": "a"}, {"sid": "b"}], max_workers=2)
    assert set(out) == {"a", "b"}
    assert seen["OPENBLAS_NUM_THREADS"] == "1"
    assert "OPENBLAS_NUM_THREADS" not in os.environ


def test_commit_probes_return_an_int_or_none_never_raise():
    a, p = g.available_commit_mb(), g.private_commit_mb()
    assert a is None or (isinstance(a, int) and a >= 0)
    assert p is None or (isinstance(p, int) and p > 0)


def test_progress_line_reports_the_whole_run_not_the_chunk(monkeypatch, capsys):
    class FakeFuture:
        def __init__(self, r):
            self._r = r

        def result(self):
            return self._r

    class FakeExecutor:
        def __init__(self, max_workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, p):
            return FakeFuture({"sid": p["sid"]})

    monkeypatch.setattr(g, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(g, "as_completed", lambda fs: iter(fs))
    g._run_candidates([{"sid": "x"}, {"sid": "y"}], max_workers=2, offset=100, total=124)
    out = capsys.readouterr().out
    assert "evaluated 102/124 candidates" in out
    assert "2/2" not in out
