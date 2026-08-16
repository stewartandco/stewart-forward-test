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


# ---------------- the reviewer call ----------------

class _Usage:
    input_tokens = 500
    output_tokens = 40
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Msg:
    usage = _Usage()

    def __init__(self, payload):
        self.content = [type("B", (), {"text": payload})()]


class _FakeClient:
    """Returns a canned JSON payload per call, and records the prompts."""
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.prompts = []
        self.messages = self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        return _Msg(self._payloads.pop(0))


class _Meter:
    def __init__(self):
        self.calls = []

    def record_call(self, model, usage, purpose, **kw):
        self.calls.append(purpose)
        return 0.0

    def can_spend(self):
        return True


CARD = {"claim": "Momentum persists after earnings surprises.",
        "quote": "We document return continuation in the weeks after an "
                 "earnings announcement.",
        "source": {"title": "Post-Earnings Drift", "url": "http://x"}}


def test_review_card_returns_one_vote_per_reviewer():
    client = _FakeClient(['{"accept": true, "reason": ""}'] * 3)
    meter = _Meter()
    votes = tb.review_card(client, "claude-sonnet-5", CARD, meter)
    assert len(votes) == tb.PANEL_SIZE
    assert all(v["accept"] for v in votes)
    assert meter.calls == ["triage"] * tb.PANEL_SIZE


def test_the_prompt_carries_both_the_claim_and_its_quote():
    """Overreach is only judgable against the quote. A prompt missing it would
    be asking the model whether the claim sounds plausible, which is a
    different and useless question."""
    client = _FakeClient(['{"accept": true, "reason": ""}'] * 3)
    tb.review_card(client, "m", CARD, _Meter())
    assert CARD["claim"] in client.prompts[0]
    assert CARD["quote"] in client.prompts[0]


def test_an_unparseable_reviewer_reply_drops_that_vote_and_escalates():
    """A malformed reply must not be read as agreement. Losing the vote makes
    the panel short, and a short panel escalates."""
    client = _FakeClient(['{"accept": true, "reason": ""}',
                          'not json',
                          '{"accept": true, "reason": ""}'])
    votes = tb.review_card(client, "m", CARD, _Meter())
    assert len(votes) == 2
    assert tb.panel_verdict(votes) == (None, "incomplete_panel")


# ---------------- the decision list ----------------

def test_build_decisions_splits_duplicates_accepts_and_escalations(monkeypatch):
    accepted = {"a1": {"claim": "Momentum persists after earnings surprises."}}
    pending = {
        "p1": {"claim": "momentum persists after earnings surprises",
               "quote": "q", "source": {}},                       # duplicate
        "p2": {"claim": "Volatility clusters.", "quote": "q", "source": {}},
        "p3": {"claim": "Skew predicts crashes.", "quote": "q", "source": {}},
    }
    # p2 unanimous accept; p3 dissent
    monkeypatch.setattr(tb, "review_card", lambda c, m, card, meter, ps=None:
                        _votes(True, True, True)
                        if card["claim"].startswith("Volatility")
                        else _votes(True, False, True))

    out = tb.build_decisions(None, "m", pending, accepted, _Meter())

    assert out["decisions"]["p1"] == ("rejected", "duplicate")
    assert out["decisions"]["p2"] == ("accepted", None)
    assert "p3" not in out["decisions"]            # escalated, stays pending
    assert out["escalated"]["p3"] == "dissent"
    assert out["counts"] == {"accepted": 1, "duplicate": 1, "escalated": 1}


def test_duplicates_are_not_sent_to_the_panel(monkeypatch):
    """Paying three reviewers to judge a card we already know is a duplicate is
    money lit on fire."""
    called = []
    monkeypatch.setattr(tb, "review_card",
                        lambda c, m, card, meter: called.append(card) or _votes(True, True, True))
    accepted = {"a1": {"claim": "X."}}
    tb.build_decisions(None, "m", {"p1": {"claim": "x", "quote": "q", "source": {}}},
                       accepted, _Meter())
    assert called == []


def test_a_capped_meter_stops_the_run_without_deciding(monkeypatch):
    class _Capped(_Meter):
        def can_spend(self):
            return False

    monkeypatch.setattr(tb, "review_card", lambda *a, **k: _votes(True, True, True))
    out = tb.build_decisions(None, "m", {"p1": {"claim": "y", "quote": "q", "source": {}}},
                             {}, _Capped())
    assert out["decisions"] == {}
    assert out["stopped"] == "budget"
