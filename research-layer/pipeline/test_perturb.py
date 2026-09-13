"""Self-perturbation sensitivity: recorded, not gating (Coen, 2026-08-21)."""
import pytest

from .blocks import BLOCK_TYPES
from .perturb import gridded_axes, perturbations, sensitivity


def spec(lookback=55, mult=2.5, direction="both"):
    return {
        "strategy_id": "abc123",
        "universe": {"assets": ["BTCUSD"], "asset_class": "crypto",
                     "timeframe": "1d", "session": "24x7"},
        "blocks": [
            {"role": "entry", "type": "channel_breakout_dense",
             "params": {"lookback": lookback, "direction": direction}},
            {"role": "stop", "type": "atr_stop_dense",
             "params": {"atr_len": 14, "mult": mult}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
    }


def test_only_numeric_gridded_axes_are_found():
    """direction ['long','both'] is categorical: moving along it is a different
    strategy, not a nudge, and recording it as sensitivity would mislead."""
    axes = {a["param"] for a in gridded_axes(spec())}
    assert axes == {"lookback", "atr_len", "mult", "f"}
    assert "direction" not in axes


def test_an_axis_records_which_grid_the_step_was_taken_along():
    """A step on a coarse grid is a far bigger change than on its dense twin;
    a reader comparing two numbers must be able to see which they got."""
    lb = next(a for a in gridded_axes(spec()) if a["param"] == "lookback")
    assert lb["grid"] == BLOCK_TYPES[("entry", "channel_breakout_dense")]["lookback"]["grid"]
    assert lb["dense"] is True and lb["index"] == 2


def test_a_value_off_its_own_grid_is_skipped_not_guessed():
    off = spec(lookback=37)          # not a declared grid value
    assert not [a for a in gridded_axes(off) if a["param"] == "lookback"]


def test_perturbations_step_one_place_each_way():
    ps = [p for p in perturbations(spec()) if p["axis"].endswith(".lookback")]
    assert {(p["direction"], p["from"], p["to"]) for p in ps} == {
        ("down", 55, 35), ("up", 55, 75)}


def test_a_grid_edge_simply_has_no_step_that_way():
    """Unlike protocol-v4's edge_of_grid, a missing neighbour disqualifies
    nothing here -- it is just one fewer measurement."""
    ps = [p for p in perturbations(spec(lookback=20)) if p["axis"].endswith(".lookback")]
    assert [p["direction"] for p in ps] == ["up"]


def test_a_perturbed_spec_is_a_throwaway_and_carries_no_strategy_id():
    """Giving it an id would invite registering a configuration that was run
    only to measure the original."""
    for p in perturbations(spec()):
        assert "strategy_id" not in p["spec"]
    assert spec()["strategy_id"] == "abc123", "the original must not be mutated"


def test_the_original_spec_is_never_mutated():
    s = spec()
    perturbations(s)
    assert s["blocks"][0]["params"]["lookback"] == 55
    assert s["blocks"][1]["params"]["mult"] == 2.5


# ---------------- sensitivity ----------------

def test_a_flat_strategy_scores_ratio_one_everywhere():
    out = sensitivity(spec(), base_score=1.0, score_fn=lambda s: 1.0)
    assert out["worst_ratio"] == pytest.approx(1.0)
    assert out["mean_ratio"] == pytest.approx(1.0)
    assert out["n_perturbations"] == len(perturbations(spec()))


def test_a_lone_peak_is_visible_in_the_worst_ratio():
    """The SOP's actual concern: it works at exactly one value and collapses
    either side. Nothing here ACTS on that -- it is recorded."""
    def score(s):
        return 1.0 if s["blocks"][0]["params"]["lookback"] == 55 else 0.1
    out = sensitivity(spec(), base_score=1.0, score_fn=score)
    assert out["worst_ratio"] == pytest.approx(0.1)
    worst = min(out["results"], key=lambda r: r["ratio"])
    assert worst["axis"] == "channel_breakout_dense.lookback"


def test_a_non_positive_base_score_reports_none_rather_than_infinity():
    out = sensitivity(spec(), base_score=0.0, score_fn=lambda s: 1.0)
    assert out["worst_ratio"] is None
    assert "non-positive base score" in out["reason"]
    assert all(r["ratio"] is None for r in out["results"])


def test_an_unscorable_perturbation_is_skipped_not_counted_as_zero():
    """score_fn returning None means 'could not be run', which is not the same
    as 'ran and was worthless'; conflating them would invent a collapse."""
    def score(s):
        return None if s["blocks"][0]["params"]["lookback"] == 35 else 1.0
    out = sensitivity(spec(), base_score=1.0, score_fn=score)
    assert out["worst_ratio"] == pytest.approx(1.0)
    assert any(r["ratio"] is None for r in out["results"])


def test_every_result_names_the_step_that_produced_it():
    out = sensitivity(spec(), base_score=1.0, score_fn=lambda s: 0.5)
    for r in out["results"]:
        assert set(r) == {"axis", "direction", "from", "to", "dense",
                          "score", "ratio"}
        assert r["ratio"] == pytest.approx(0.5)


def test_dense_only_excludes_a_coarse_step():
    """fixed_fraction.f [0.01, 0.02] is coarse: doubling position size is not a
    nudge. The gauntlet measures the small step or none."""
    s = spec()
    all_axes = {p["axis"] for p in perturbations(s)}
    dense_axes = {p["axis"] for p in perturbations(s, dense_only=True)}
    assert "fixed_fraction.f" in all_axes
    assert "fixed_fraction.f" not in dense_axes
    assert dense_axes and all("_dense." in a for a in dense_axes)


def test_sensitivity_records_which_mode_produced_it():
    out = sensitivity(spec(), 1.0, lambda s: 1.0, dense_only=True)
    assert out["dense_only"] is True
    assert out["n_perturbations"] == len(perturbations(spec(), dense_only=True))
