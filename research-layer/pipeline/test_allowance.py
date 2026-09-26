"""Phase 3 steps 4-5 (2026-09-03): the per-cycle spend allowance.

Inputs from Coen: cap USD 40 (D39), reserve 0.15, a park counts as a clean
day. Everything else is derived -- from the cycle history and from the
loop's own measured spend deltas -- never hand-typed. These are pure
functions with fixed numbers; the loop wiring is tested in test_loop.py.
"""
import pytest

from . import allowance as al


# ---------------- expected cycles ------------------------------------------

def test_expected_cycles_counts_the_trailing_window_only():
    state = {"cycles": [
        {"run_id": "a", "ts_utc": "2026-08-01T00:00:00+00:00", "asset_class": "fx"},
        {"run_id": "b", "ts_utc": "2026-08-20T00:00:00+00:00", "asset_class": "fx"},
        {"run_id": "c", "ts_utc": "2026-09-01T10:30:00+00:00", "asset_class": "crypto"},
        {"run_id": "d", "ts_utc": "2026-09-02T10:30:00+00:00", "asset_class": "fx"},
    ]}
    now = al._parse("2026-09-03T00:00:00+00:00")
    assert al.expected_cycles(state, now=now, floor=1) == 3      # b, c, d; a is 33 days old


def test_expected_cycles_floor_stops_a_quiet_month_inflating_the_allowance():
    assert al.expected_cycles({"cycles": []}, now=al._parse("2026-09-03T00:00:00+00:00")) == al.CYCLES_FLOOR
    assert al.CYCLES_FLOOR == 10


# ---------------- the allowance --------------------------------------------

def test_allowance_is_cap_less_reserve_over_expected_cycles():
    assert al.cycle_allowance(cap=40.0, reserve=0.15, expected_cycles=20) == pytest.approx(1.70)
    assert al.RESERVE == 0.15


# ---------------- the derived triage count ---------------------------------

def test_triage_count_is_what_the_money_buys_after_the_composer_pair():
    # (1.70 - 0.64) / 0.018 = 58.9 -> 58
    assert al.triage_count(allowance=1.70, composer_pair_usd=0.64, usd_per_card=0.018, ceiling=200) == 58


def test_triage_count_is_clamped_to_the_ceiling_and_never_below_one():
    assert al.triage_count(allowance=100.0, composer_pair_usd=0.64, usd_per_card=0.018, ceiling=200) == 200
    assert al.triage_count(allowance=0.50, composer_pair_usd=0.64, usd_per_card=0.018, ceiling=200) == 1
    assert al.triage_count(allowance=1.70, composer_pair_usd=0.64, usd_per_card=0.0, ceiling=200) == 200  # free cards


# ---------------- calibration: measured, with priors -----------------------

def test_calibration_starts_at_the_measured_priors():
    c = al.Calibration.from_state({})
    assert c.usd_per_card == pytest.approx(0.018)
    assert c.composer_pair_usd == pytest.approx(0.64)
    assert c.samples == 0


def test_calibration_replaces_priors_with_trailing_means_and_round_trips():
    c = al.Calibration.from_state({})
    c.record_triage(spent_delta=3.60, reviewed=200)      # 0.018 exactly
    c.record_triage(spent_delta=2.00, reviewed=100)      # 0.020
    c.record_composer(spent_delta=0.70)
    assert c.usd_per_card == pytest.approx((0.018 + 0.020) / 2)
    assert c.composer_pair_usd == pytest.approx(0.70)
    state = {}
    c.save(state)
    c2 = al.Calibration.from_state(state)
    assert c2.usd_per_card == pytest.approx(c.usd_per_card)
    assert c2.composer_pair_usd == pytest.approx(0.70)
    assert c2.samples == 3


def test_calibration_keeps_only_the_trailing_window():
    c = al.Calibration.from_state({})
    for _ in range(al.CALIBRATION_WINDOW + 5):
        c.record_composer(spent_delta=1.0)
    c.record_composer(spent_delta=0.0)
    assert c.composer_pair_usd == pytest.approx((al.CALIBRATION_WINDOW - 1) / al.CALIBRATION_WINDOW)


def test_a_zero_reviewed_triage_is_not_a_sample():
    c = al.Calibration.from_state({})
    c.record_triage(spent_delta=0.0, reviewed=0)          # nothing was reviewed; no division by nothing
    assert c.samples == 0
    assert c.usd_per_card == pytest.approx(0.018)


def test_a_negative_delta_is_clamped_not_recorded_as_a_refund():
    """A concurrent writer can make a fresh ledger read smaller than the
    previous one only if rows were removed, which never happens; but a
    misordered timestamp can. Never learn a negative price."""
    c = al.Calibration.from_state({})
    c.record_composer(spent_delta=-0.5)
    assert c.samples == 0
