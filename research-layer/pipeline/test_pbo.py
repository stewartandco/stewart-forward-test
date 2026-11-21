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


# --- protocol-v5: the boundary tie, distinct configs, permutation null -------

from pipeline.pbo import overfit_weight, distinct_configs, permutation_null


@pytest.mark.parametrize("n_configs", list(range(2, 26)))
def test_uniform_rank_null_is_one_half_at_every_family_size(n_configs):
    """protocol-v5 amendment 1, asserted exhaustively rather than simulated.

    omega = rank/(n+1) and BBLdP count lambda <= 0 as overfit, so at ODD n the
    median rank lands exactly on omega == 0.5 and was swept wholly into the
    overfit count. That made the uniform-rank null (n+1)/2n -- 0.600 at n=5,
    ABOVE protocol-v4's own 0.50 family-kill line, which is the defect the
    evidence note at registry entry 2511 measured. Counting the tie as a HALF
    event makes the null exactly 0.5 at every size, odd or even.
    """
    total = sum(overfit_weight(rank, n_configs)
                for rank in range(1, n_configs + 1))
    assert total / n_configs == pytest.approx(0.5)


def test_the_median_rank_is_the_only_half_and_only_at_odd_n():
    assert overfit_weight(3, 5) == 0.5          # omega == 3/6, exact tie
    assert overfit_weight(2, 5) == 1.0          # below the median
    assert overfit_weight(4, 5) == 0.0          # above it
    assert 0.5 not in {overfit_weight(r, 4) for r in range(1, 5)}


def test_pure_noise_at_an_odd_family_size_now_averages_one_half():
    """The n=5 case protocol-v4 actually ran. Under the old whole-event tie
    this mean sat near 0.6; the assertion below would have failed."""
    vals = []
    for seed in range(40):
        rng = random.Random(seed)
        series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(320)]
                  for i in range(5)}
        vals.append(cscv_pbo(series, s=8)["pbo"])
    mean = sum(vals) / len(vals)
    assert 0.40 < mean < 0.60, f"mean PBO {mean:.3f} over {len(vals)} draws"


def test_distinct_configs_collapses_identical_series():
    """protocol-v5 amendment 2. Generation 4's breakout_vol_state_filter
    registered five siblings and produced two distinct train curves: the
    swept filter admitted the same seven trades at four of its five values.
    A family like that has nothing for PBO to select between."""
    a, b = [0.01, -0.02, 0.03] * 5, [0.04, 0.05, -0.06] * 5
    assert distinct_configs({"s1": a, "s2": list(a), "s3": list(a),
                             "s4": list(a), "s5": b}) == 2
    assert distinct_configs({"s1": a, "s2": b}) == 2
    assert distinct_configs({"s1": a}) == 1
    assert distinct_configs({}) == 0


def test_permutation_null_is_deterministic_and_in_range():
    rng_series = random.Random(5)
    series = {f"n{i}": [rng_series.gauss(0, 0.01) for _ in range(160)]
              for i in range(4)}
    first = permutation_null(series, s=8, draws=6, seed=11)
    assert first == permutation_null(series, s=8, draws=6, seed=11)
    assert first != permutation_null(series, s=8, draws=6, seed=12)
    assert len(first) == 6
    assert all(0.0 <= v <= 1.0 for v in first)


def test_permutation_null_preserves_each_days_cross_section():
    """It permutes LABELS within a day, so the multiset of returns on any day
    is untouched. That is what keeps the real fat tails, the real sibling
    correlation and the real common market factor in the null while removing
    only PERSISTENT per-sibling skill."""
    from pipeline.pbo import permute_labels
    series = {"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0],
              "c": [100.0, 200.0, 300.0]}
    out = permute_labels(series, random.Random(3))
    assert sorted(out) == ["a", "b", "c"]
    for t in range(3):
        assert sorted(v[t] for v in out.values()) == \
               sorted(v[t] for v in series.values())


def test_permutation_null_refuses_a_degenerate_family_rather_than_guessing():
    """Every sibling identical: permuting labels cannot change anything, so a
    null built from it would be a single point masquerading as a distribution."""
    same = [0.01, -0.01, 0.02] * 20
    with pytest.raises(ValueError, match="distinct"):
        permutation_null({"a": same, "b": list(same)}, s=8, draws=4, seed=1)
