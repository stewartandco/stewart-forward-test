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


# ---------------- the panel: unanimity or escalate ----------------

def _votes(*verdicts):
    """Fake panel results: each reviewer returns accept True/False + a note."""
    return [{"accept": v, "reason": "" if v else "claim exceeds quote"}
            for v in verdicts]


def test_unanimous_accept_is_the_only_path_to_auto_accept():
    assert tb.panel_verdict(_votes(True, True, True)) == ("accepted", None)


def test_any_dissent_leaves_the_card_pending_not_rejected():
    """Dissent escalates to Coen. It must never auto-REJECT - the panel is not
    trusted to destroy research, only to wave through what it all agrees on."""
    assert tb.panel_verdict(_votes(True, True, False)) == (None, "dissent")
    assert tb.panel_verdict(_votes(False, False, False)) == (None, "dissent")


def test_a_short_panel_escalates_rather_than_deciding():
    """If a reviewer errored or was skipped, we have fewer than PANEL_SIZE
    opinions and cannot claim unanimity."""
    assert tb.panel_verdict(_votes(True, True)) == (None, "incomplete_panel")
    assert tb.panel_verdict([]) == (None, "incomplete_panel")


def test_decisions_use_auto_provenance_never_coen():
    assert tb.REVIEWER == "auto-d31"
    assert "coen" not in tb.REVIEWER
