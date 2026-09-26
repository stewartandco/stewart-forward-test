"""Fan-out for screen runs. The engine is pure and deterministic, so parallel
results must be IDENTICAL to serial ones - that is what keeps the run
comparable under screen-protocol-v1."""
from pipeline import parallel


def _square(x):
    return x * x


def _boom(x):
    if x == 3:
        raise ValueError("cell 3 is broken")
    return x * x


def test_parallel_map_matches_serial_exactly():
    items = list(range(20))
    assert parallel.run_all(_square, items, workers=4) == [_square(i) for i in items]


def test_results_keep_input_order_regardless_of_completion_order():
    """Manifests and denominators are indexed by position; out-of-order results
    would silently mis-attribute a cell's verdict to another cell."""
    assert parallel.run_all(_square, [5, 1, 4, 2], workers=4) == [25, 1, 16, 4]


def test_one_failing_cell_does_not_kill_the_run():
    out = parallel.run_all(_boom, [1, 2, 3, 4], workers=2)
    assert out[0] == 1 and out[1] == 4 and out[3] == 16
    assert isinstance(out[2], parallel.CellError)
    assert "cell 3 is broken" in str(out[2])


def test_a_single_worker_is_a_plain_serial_run():
    """Debuggability: workers=1 must not spawn processes, so a traceback is
    readable and a breakpoint works."""
    assert parallel.run_all(_square, [1, 2, 3], workers=1) == [1, 4, 9]
