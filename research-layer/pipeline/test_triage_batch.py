"""triage_batch: turn pending cards into a decision list, fully automatic (D44).

Three reviewers must agree unanimously before a card is accepted. ANY dissent
rejects it (claim_not_supported), and so does a panel that stays short after
bounded in-call retries. Nothing is ever left pending for a human.
"""
import json
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


# ---------------- the panel: unanimity or reject ----------------

def _votes(*verdicts):
    """Fake panel results: each reviewer returns accept True/False + a note."""
    return [{"accept": v, "reason": "" if v else "claim exceeds quote"}
            for v in verdicts]


# ---------------- the reviewer call ----------------

class _Usage:
    input_tokens = 500
    output_tokens = 40
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Msg:
    usage = _Usage()

    def __init__(self, payload):
        # Real content blocks always carry .type; the first fake here omitted
        # it, which hid the ThinkingBlock bug the 2026-08-17 dry run found.
        self.content = [type("B", (), {"type": "text", "text": payload})()]


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


# ---------------- the decision list ----------------


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


# ---------------- CLI: dry run is the default ----------------

import json as _json
from pathlib import Path

from pipeline.registry import Registry


def _seed(tmp_path):
    """A registry with one accepted card and two pending."""
    reg = Registry(tmp_path / "registry_log.jsonl")
    for cid, claim in (("a1", "Momentum persists."), ("p1", "momentum persists"),
                       ("p2", "Volatility clusters.")):
        reg.append("card_registered", {
            "card_id": cid, "claim": claim, "quote": "q",
            "source": {"title": "t", "url": "u"},
            "review": {"status": "pending", "reject_reason": None},
        })
    reg.review_card("a1", "accepted", "coen")
    return reg


def test_dry_run_writes_nothing_to_the_chain(tmp_path, monkeypatch):
    reg = _seed(tmp_path)
    before = sum(1 for _ in reg.entries())
    monkeypatch.setattr(tb, "review_card", lambda *a, **k: _votes(True, True, True))
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    rc = tb.run(["--registry", str(tmp_path / "registry_log.jsonl")])

    assert rc == 0
    assert sum(1 for _ in reg.entries()) == before      # nothing chained


# ---------------- the client must load the reader key ----------------

def test_missing_api_key_fails_with_a_clear_message_not_an_sdk_traceback(monkeypatch):
    """The first live dry run died 30 frames deep in the anthropic SDK with
    'Could not resolve authentication method'. The CLI must say what is wrong
    and where the key lives, in one line."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("READER_ENV_PATH", "no-such-file.env")
    with pytest.raises(SystemExit) as e:
        tb._client_and_meter()
    assert "ANTHROPIC_API_KEY" in str(e.value)


def test_the_client_loads_the_reader_env_rather_than_inventing_its_own(monkeypatch, tmp_path):
    """Reuse scanner._load_api_key - one loader, one source of truth for where
    the sc-reader key lives."""
    env = tmp_path / "reader.env"
    env.write_text("ANTHROPIC_API_KEY=sk-test-not-a-real-key\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("READER_ENV_PATH", str(env))
    client, meter = tb._client_and_meter()
    assert client is not None and meter is not None


# ---------------- thinking blocks are not the answer ----------------

class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        if text is not None:
            self.text = text


class _ThinkingMsg:
    """What the API actually returns when the model thinks: content[0] is a
    ThinkingBlock with no .text at all."""
    usage = _Usage()

    def __init__(self, payload):
        self.content = [_Block("thinking"), _Block("text", payload)]


class _ThinkingClient:
    def __init__(self, n):
        self._n = n
        self.messages = self

    def create(self, **kw):
        self._n -= 1
        return _ThinkingMsg('{"accept": true, "reason": "faithful"}')


def test_a_thinking_block_before_the_json_does_not_lose_the_vote():
    """Live dry run 2026-08-17: 15 of 20 cards escalated as incomplete_panel
    because content[0] was a ThinkingBlock, which has no .text, so every vote
    raised AttributeError and was dropped. The JSON is in the first TEXT block,
    not the first block."""
    votes = tb.review_card(_ThinkingClient(3), "m", CARD, _Meter())
    assert len(votes) == tb.PANEL_SIZE
    assert tb.panel_verdict(votes) == ("accepted", "unanimous")


def test_max_tokens_leaves_room_for_thinking_plus_the_json():
    """Live dry run 2026-08-17: 7 of 20 cards still escalated after the
    ThinkingBlock fix, because stop_reason was max_tokens at exactly 300 - the
    model thought, began the JSON, and was cut off mid-object. Truncated JSON
    is a dropped vote, and we paid for every one of those tokens. The ceiling
    must clear thinking plus a one-sentence reason."""
    class _Recorder:
        usage = _Usage()
        def __init__(self):
            self.kwargs = []
            self.messages = self
        def create(self, **kw):
            self.kwargs.append(kw)
            return _Msg('{"accept": true, "reason": "ok"}')

    rec = _Recorder()
    tb.review_card(rec, "m", CARD, _Meter())
    assert rec.kwargs[0]["max_tokens"] >= 1000


# ---------------- D44: unanimous accepts, any dissent rejects ----------------

DISSENT_MSG = ("a card with a dissenting reviewer was not rejected -- D44: only "
               "a unanimous full panel accepts, and nothing waits for Coen")


def test_unanimous_full_panel_is_the_only_path_to_accept():
    assert tb.panel_verdict(_votes(True, True, True)) == ("accepted", "unanimous")


def test_any_dissent_rejects_even_one_reviewer_outvoted():
    """D31's reason stands: one reviewer spotting overreach must never be
    outvoted, so 2-to-1 in favour is still a reject, not an accept."""
    assert tb.panel_verdict(_votes(True, True, False)) == ("rejected", "dissent"), DISSENT_MSG
    assert tb.panel_verdict(_votes(False, False, False)) == ("rejected", "dissent"), DISSENT_MSG


def test_a_short_panel_rejects_because_it_is_not_unanimous():
    assert tb.panel_verdict(_votes(True, True)) == ("rejected", "incomplete_panel")
    assert tb.panel_verdict([]) == ("rejected", "incomplete_panel")


def test_decisions_use_d44_auto_provenance_never_coen():
    assert tb.REVIEWER == "auto-d44"
    assert "coen" not in tb.REVIEWER


def test_an_unparseable_reply_is_re_asked_in_the_same_run():
    """A lost vote is re-asked, not escalated: one malformed reply followed by
    three good ones is a full, unanimous panel."""
    client = _FakeClient(['{"accept": true, "reason": ""}',
                          'not json',
                          '{"accept": true, "reason": ""}',
                          '{"accept": true, "reason": ""}'])
    votes = tb.review_card(client, "m", CARD, _Meter())
    assert len(votes) == tb.PANEL_SIZE
    assert len(client.prompts) == 4
    assert tb.panel_verdict(votes) == ("accepted", "unanimous")


def test_re_asking_is_bounded_so_an_unreadable_card_cannot_bill_forever():
    client = _FakeClient(["not json"] * 50)
    meter = _Meter()
    votes = tb.review_card(client, "m", CARD, meter)
    assert votes == []
    assert len(client.prompts) == tb.MAX_VOTE_ATTEMPTS
    assert meter.calls == ["triage"] * tb.MAX_VOTE_ATTEMPTS
    assert tb.panel_verdict(votes) == ("rejected", "incomplete_panel")


def test_build_decisions_decides_every_card_nothing_left_pending(monkeypatch):
    accepted = {"a1": {"claim": "Momentum persists after earnings surprises."}}
    pending = {
        "p1": {"claim": "momentum persists after earnings surprises",
               "quote": "q", "source": {}},                       # duplicate
        "p2": {"claim": "Volatility clusters.", "quote": "q", "source": {}},
        "p3": {"claim": "Skew predicts crashes.", "quote": "q", "source": {}},
    }
    # p2 unanimous accept; p3 one dissent out of three
    monkeypatch.setattr(tb, "review_card", lambda c, m, card, meter, ps=None:
                        _votes(True, True, True)
                        if card["claim"].startswith("Volatility")
                        else _votes(True, False, True))

    out = tb.build_decisions(None, "m", pending, accepted, _Meter())

    assert out["decisions"]["p1"] == ("rejected", "duplicate")
    assert out["decisions"]["p2"] == ("accepted", None)
    assert out["decisions"]["p3"] == ("rejected", "claim_not_supported"), DISSENT_MSG
    assert set(out["decisions"]) == set(pending), "a reviewed card was left undecided"
    assert out["rejections"]["p3"] == {"basis": "dissent",
                                       "dissent_reasons": ["claim exceeds quote"]}
    assert out["counts"] == {"accepted": 1, "rejected": 1, "duplicate": 1}


def test_apply_chains_accepts_and_rejects_with_d44_provenance_and_audits(tmp_path, monkeypatch):
    reg = _seed(tmp_path)
    reg.append("card_registered", {
        "card_id": "p3", "claim": "Skew predicts crashes.", "quote": "q",
        "source": {"title": "t", "url": "u"},
        "review": {"status": "pending", "reject_reason": None},
    })
    monkeypatch.setattr(tb, "review_card", lambda c, m, card, meter, ps=None:
                        _votes(True, True, False) if card["claim"].startswith("Skew")
                        else _votes(True, True, True))
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    tb.run(["--registry", str(tmp_path / "registry_log.jsonl"), "--apply"])

    reviews = [e for e in reg.entries() if e["entry_type"] == "card_reviewed"]
    auto = {r["payload"]["card_id"]: r["payload"] for r in reviews
            if r["payload"].get("reviewed_by") == "auto-d44"}
    assert auto["p1"]["status"] == "rejected" and auto["p1"]["reject_reason"] == "duplicate"
    assert auto["p2"]["status"] == "accepted"
    assert auto["p3"]["status"] == "rejected", DISSENT_MSG
    assert auto["p3"]["reject_reason"] == "claim_not_supported"
    assert not reg.cards(status="pending"), "a card was left pending for a human"
    audit = [_json.loads(l) for l in
             (tmp_path / "logs" / tb.REJECTIONS_LOG_NAME).read_text(encoding="utf-8").splitlines()]
    assert [(a["card_id"], a["basis"], a["reviewed_by"]) for a in audit] == [("p3", "dissent", "auto-d44")]
    assert audit[0]["dissent_reasons"] == ["claim exceeds quote"]
    result = _json.loads((tmp_path / "logs" / tb.RESULT_NAME).read_text(encoding="utf-8"))
    assert result == {"reviewed": 3, "skipped_escalated": 0}
    assert not (tmp_path / "logs" / "triage_escalated.json").exists()


def test_a_budget_stop_banks_only_the_cards_actually_decided(tmp_path, monkeypatch):
    """Cards the panel never saw must stay visible to the loop's watermark."""
    _seed(tmp_path)

    class _Closed(_Meter):
        def can_spend(self):
            return False                    # no card may be sent to the panel

    monkeypatch.setattr(tb, "review_card", lambda *a, **k: _votes(True, True, True))
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Closed()))
    tb.run(["--registry", str(tmp_path / "registry_log.jsonl"), "--apply"])
    result = _json.loads((tmp_path / "logs" / tb.RESULT_NAME).read_text(encoding="utf-8"))
    # p1 is a duplicate (decided free, before the budget check); p2 never reached the panel
    assert result["reviewed"] == 1


def test_the_skip_set_cli_is_gone():
    """D44 removed the human queue: no flag re-creates it."""
    with pytest.raises(SystemExit):
        tb.run(["--queue"])
