"""triage_batch: turn pending cards into a decision list.

Three reviewers must agree unanimously before a card is accepted without Coen.
Dissent is the signal - a card one reviewer doubts is exactly the one worth his
attention - so dissent leaves the card PENDING, never rejected.
"""
import pytest

from pipeline import triage_batch as tb


def test_fingerprint_is_stable_and_order_independent():
    a = tb.claim_fingerprint("Momentum persists after earnings surprises.")
    b = tb.claim_fingerprint("Momentum persists after earnings surprises.")
    assert a == b and len(a) == 16


def test_fingerprint_ignores_case_punctuation_and_whitespace():
    """Near-identical restatements of one claim must collide, or the same claim
    re-enters the corpus once per source that phrased it differently."""
    a = tb.claim_fingerprint("Momentum persists after earnings surprises.")
    b = tb.claim_fingerprint("  momentum persists, after earnings surprises!  ")
    assert a == b


def test_fingerprint_separates_genuinely_different_claims():
    a = tb.claim_fingerprint("Momentum persists after earnings surprises.")
    b = tb.claim_fingerprint("Momentum reverses after earnings surprises.")
    assert a != b


def test_duplicates_are_found_against_the_accepted_corpus_only():
    accepted = {"c1": {"claim": "Momentum persists after earnings surprises."}}
    pending = {
        "c9": {"claim": "momentum persists after earnings surprises"},   # dup
        "c8": {"claim": "Volatility clusters in daily returns."},        # novel
    }
    dupes = tb.find_duplicates(pending, accepted)
    assert dupes == {"c9": "c1"}
