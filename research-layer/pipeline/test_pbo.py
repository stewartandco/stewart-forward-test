"""CSCV / PBO tests."""
import math
import random

import pytest

from pipeline.pbo import block_stats, cscv_pbo


def test_block_stats_drops_the_remainder_so_blocks_are_equal():
    stats = block_stats(list(range(10)), s=3)
    assert len(stats) == 3
    assert [n for n, _, _ in stats] == [3, 3, 3]
    assert stats[0] == (3, 0 + 1 + 2, 0 + 1 + 4)


def test_dominant_config_gives_low_pbo():
    """A config that is genuinely better everywhere should rarely rank in the
    bottom half out of sample."""
    rng = random.Random(7)
    series = {f"noise{i}": [rng.gauss(0, 0.01) for _ in range(320)]
              for i in range(7)}
    series["real"] = [rng.gauss(0.004, 0.01) for _ in range(320)]
    out = cscv_pbo(series, s=8)
    assert out["pbo"] < 0.2, out


def test_pure_noise_averages_to_pbo_one_half():
    """With no real edge the in-sample winner is a coin flip out of sample.

    Asserted on the MEAN over 30 independent draws rather than a single one.
    At 8 configs and s=8 a single PBO estimate has sd ~0.23 -- a third of seeds
    fall outside (0.25, 0.75) -- so a single-seed assertion is either flaky or
    pinned to a hand-picked seed and proves nothing about the estimator. The
    mean of 30 draws has sd ~0.042, which does test unbiasedness.
    """
    vals = []
    for seed in range(30):
        rng = random.Random(seed)
        series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(320)]
                  for i in range(8)}
        vals.append(cscv_pbo(series, s=8)["pbo"])
    mean = sum(vals) / len(vals)
    assert 0.40 < mean < 0.60, f"mean PBO {mean:.3f} over {len(vals)} draws"


def test_is_deterministic():
    rng = random.Random(3)
    series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(160)]
              for i in range(5)}
    assert cscv_pbo(series, s=6) == cscv_pbo(series, s=6)


def test_order_of_ids_does_not_change_the_answer():
    rng = random.Random(5)
    series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(160)]
              for i in range(5)}
    reversed_series = dict(reversed(list(series.items())))
    assert cscv_pbo(series, s=6)["pbo"] == cscv_pbo(reversed_series, s=6)["pbo"]


def test_ragged_series_refuse_rather_than_silently_misalign():
    with pytest.raises(ValueError, match="ragged"):
        cscv_pbo({"a": [0.1] * 100, "b": [0.1] * 99}, s=4)


def test_fewer_than_two_configs_is_uncomputable_not_zero():
    out = cscv_pbo({"only": [0.01] * 100}, s=4)
    assert out["pbo"] is None
    assert out["reason"] == "needs at least 2 configs"


def test_reports_its_own_shape():
    rng = random.Random(1)
    series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(160)]
              for i in range(4)}
    out = cscv_pbo(series, s=8)
    assert out["n_configs"] == 4
    assert out["s"] == 8
    assert out["n_combinations"] == 70   # C(8,4)
    assert out["block_size"] == 20
