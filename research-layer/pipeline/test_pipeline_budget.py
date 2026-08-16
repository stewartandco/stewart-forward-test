"""The pipeline's budget line: a standing monthly cap with a hard stop, plus
an 80% stop BETWEEN batches (the D21 Composer pattern).

The 2026-08-15 scanner runaway is why the hard stop exists: billing errors were
retried forever and logged 105,565 decisions in two hours. An alert alone does
not stop a machine."""
import pytest

from pipeline import pipeline_budget as pb


def test_the_standing_line_is_declared_not_guessed():
    assert pb.MONTHLY_USD == 20.0
    assert pb.BATCH_STOP_FRACTION == 0.80


def test_under_the_line_a_batch_may_start():
    assert pb.may_start_batch(spent=5.0) is True


def test_at_eighty_percent_the_next_batch_is_refused():
    """Stopping BETWEEN batches, not mid-batch, so a batch is never half-done
    and half-chained."""
    assert pb.may_start_batch(spent=16.0) is False
    assert pb.may_start_batch(spent=15.99) is True


def test_at_the_cap_nothing_may_spend():
    assert pb.may_spend(spent=20.0) is False
    assert pb.may_spend(spent=19.99) is True


def test_a_batch_already_running_is_not_killed_mid_flight():
    """may_spend stays true above the batch line: the stop is a gate on
    STARTING work, not a guillotine on work in progress."""
    assert pb.may_start_batch(spent=17.0) is False
    assert pb.may_spend(spent=17.0) is True


def test_state_names_which_limit_was_hit():
    assert pb.state(spent=5.0) == "OK"
    assert pb.state(spent=16.0) == "BATCH_STOP"
    assert pb.state(spent=20.0) == "CAP"
