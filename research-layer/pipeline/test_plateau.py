"""Plateau qualification and neighbourhood selection."""
from pipeline.plateau import (annualized_sharpe, neighbours_of, plateau_members,
                              qualifies, select_survivor)

GRIDS = {"lookback": [20, 35, 55, 75, 100]}


def sib(sid, lookback, score, gauntlet_passed=True, tc_fail=False):
    return {"sid": sid, "axes": {"lookback": lookback}, "score": score,
            "screen_trade_count_fail": tc_fail,
            "gauntlet_passed": gauntlet_passed}


def test_annualized_sharpe_of_a_flat_curve_is_none():
    curve = [("2020-01-01", 1.0), ("2020-01-02", 1.0), ("2020-01-03", 1.0)]
    assert annualized_sharpe(curve) is None


def test_annualized_sharpe_uses_365_day_scaling():
    curve = [("2020-01-01", 1.0)]
    v = 1.0
    for i in range(400):
        v *= 1.001 if i % 2 == 0 else 0.9995
        curve.append((f"d{i}", v))
    sr = annualized_sharpe(curve)
    assert sr is not None and sr > 0


def test_neighbours_are_one_grid_step_on_exactly_one_axis():
    fam = [sib("a", 35, 1.0), sib("b", 55, 1.0), sib("c", 75, 1.0),
           sib("d", 100, 1.0)]
    got = {n["sid"] for n in neighbours_of(fam[1], fam, GRIDS)}
    assert got == {"a", "c"}


def test_absent_neighbours_are_simply_not_returned():
    fam = [sib("b", 55, 1.0), sib("d", 100, 1.0)]
    assert neighbours_of(fam[0], fam, GRIDS) == []


def test_plateau_is_ninety_percent_of_the_family_best():
    fam = [sib("a", 35, 1.00), sib("b", 55, 0.95), sib("c", 75, 0.80)]
    assert plateau_members(fam) == {"a", "b"}


def test_a_trade_count_neighbour_is_a_cliff():
    fam = [sib("a", 35, 1.0, gauntlet_passed=False, tc_fail=True),
           sib("b", 55, 1.0), sib("c", 75, 1.0)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "cliff_trade_count"


def test_a_neighbour_below_plateau_disqualifies():
    fam = [sib("a", 35, 0.50), sib("b", 55, 1.00), sib("c", 75, 1.00)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "neighbour_below_plateau"


def test_a_candidate_below_plateau_disqualifies_itself():
    fam = [sib("a", 35, 1.00), sib("b", 55, 0.50), sib("c", 75, 1.00)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "below_plateau"


def test_selection_prefers_the_best_worst_neighbour_not_the_point_winner():
    """'b' is the point winner but sits beside a weak neighbour; 'c' scores
    lower yet its whole neighbourhood is strong. Plateau selection takes 'c'.

    Floors: a=0.92, b=0.92, c=0.985, d=0.93, e=0.93 -- 'c' wins outright, so
    the test does not lean on any tie-break. An earlier version of this
    fixture scored d and e at 0.98, which produced a three-way c/d/e tie at
    0.98 and asserted 'd', a value no floor-based rule can return.
    """
    fam = [sib("a", 20, 0.92), sib("b", 35, 1.00), sib("c", 55, 0.99),
           sib("d", 75, 0.985), sib("e", 100, 0.93)]
    winner, detail = select_survivor(fam, GRIDS)
    assert winner == "c", detail
    assert max(fam, key=lambda s: s["score"])["sid"] == "b"


def test_only_gauntlet_passers_can_be_selected():
    fam = [sib("a", 35, 1.00, gauntlet_passed=False),
           sib("b", 55, 1.00, gauntlet_passed=False),
           sib("c", 75, 1.00, gauntlet_passed=False)]
    winner, detail = select_survivor(fam, GRIDS)
    assert winner is None


def test_ties_break_lexicographically_on_sid():
    fam = [sib("zz", 35, 1.0), sib("aa", 55, 1.0), sib("mm", 75, 1.0)]
    winner, _ = select_survivor(fam, GRIDS)
    assert winner == "aa"


def test_selection_is_order_independent():
    fam = [sib("a", 20, 0.92), sib("b", 35, 1.00), sib("c", 55, 0.99),
           sib("d", 75, 0.98), sib("e", 100, 0.98)]
    assert select_survivor(fam, GRIDS)[0] == select_survivor(list(reversed(fam)), GRIDS)[0]


def test_a_scoreless_sibling_is_below_plateau_but_not_a_cliff():
    fam = [sib("a", 35, None), sib("b", 55, 1.0), sib("c", 75, 1.0)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "neighbour_below_plateau"


def test_a_family_with_no_swept_axis_fails_rather_than_passing_vacuously():
    """The bypass this guard exists to close: empty grids give no neighbours,
    so every other clause would pass and an unswept family would clear a
    robustness gate on no evidence at all."""
    fam = [sib("solo", 55, 1.0)]
    ok, reason = qualifies(fam[0], fam, {})
    assert ok is False and reason == "no_swept_axis"


def test_an_unswept_family_selects_nobody():
    fam = [sib("solo", 55, 1.0)]
    winner, detail = select_survivor(fam, {})
    assert winner is None
    assert detail["solo"]["reason"] == "no_swept_axis"


def test_tie_break_picks_the_smallest_sid_even_at_differing_lengths():
    """Regression: the original tie-break key `[-ord(c) for c in sid]` compared
    LISTS, so "aa" -> [-97,-97] sorts below "aab" -> [-97,-97,-98] and max()
    returned the LONGER sid rather than the smallest. Every other fixture uses
    one-character sids, which is why nothing caught it; real strategy ids are
    16 hex characters.
    """
    fam = [sib("aab", 35, 1.0), sib("aa", 55, 1.0), sib("b", 75, 1.0)]
    winner, _ = select_survivor(fam, GRIDS)
    assert winner == "aa"
