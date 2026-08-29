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


def test_apply_chains_with_auto_provenance(tmp_path, monkeypatch):
    reg = _seed(tmp_path)
    monkeypatch.setattr(tb, "review_card", lambda *a, **k: _votes(True, True, True))
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    tb.run(["--registry", str(tmp_path / "registry_log.jsonl"), "--apply"])

    reviews = [e for e in reg.entries() if e["entry_type"] == "card_reviewed"]
    auto = [r for r in reviews if r["payload"].get("reviewed_by") == "auto-d31"]
    assert auto, "no auto-provenance reviews chained"
    assert all(r["payload"]["reviewed_by"] != "coen" for r in auto)
    # p1 duplicate rejected, p2 accepted
    by_id = {r["payload"]["card_id"]: r["payload"] for r in auto}
    assert by_id["p1"]["status"] == "rejected"
    assert by_id["p1"]["reject_reason"] == "duplicate"
    assert by_id["p2"]["status"] == "accepted"


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
    assert tb.panel_verdict(votes) == ("accepted", None)


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


# ---------------- escalation skip-set (Gate 1, 2026-08-29) ----------------
# An escalated card is deliberately absent from `decisions` (build_decisions'
# docstring): nothing is chained for it, so it stays pending forever until
# Coen dispositions it in T3. run() slices `pending` in chain order, so those
# cards re-occupy the head of the --limit window on EVERY cycle and get
# re-reviewed (and re-paid for) indefinitely, while cards behind them are
# never reached. logs/triage_escalated.json is the advisory skip-set that
# breaks that loop.


def _seed_pending(tmp_path, n, prefix="c"):
    reg = Registry(tmp_path / "registry_log.jsonl")
    for i in range(n):
        reg.append("card_registered", {
            "card_id": f"{prefix}{i}", "claim": f"claim {prefix}{i}", "quote": "q",
            "source": {"title": "t", "url": "u"},
            "review": {"status": "pending", "reject_reason": None},
        })
    return reg


def test_escalated_cards_are_skipped_on_the_next_run(tmp_path, monkeypatch):
    """The whole point: a card the panel escalated once must not be sent to
    the panel again on the next cycle."""
    _seed_pending(tmp_path, 2)
    reg_path = tmp_path / "registry_log.jsonl"
    state = tmp_path / "triage_escalated.json"
    seen = []

    def _dissent(client, model, card, meter, panel_size=3):
        seen.append(card["card_id"])
        return _votes(True, False, True)          # dissent -> escalated

    monkeypatch.setattr(tb, "review_card", _dissent)
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    argv = ["--registry", str(reg_path), "--escalated-state", str(state), "--apply"]
    tb.run(argv)
    assert sorted(seen) == ["c0", "c1"]           # both reviewed the first time
    assert state.exists()
    recorded = _json.loads(state.read_text(encoding="utf-8"))
    assert set(recorded) == {"c0", "c1"}
    assert recorded["c0"]["reason"] == "dissent"
    assert "first_escalated_utc" in recorded["c0"]

    seen.clear()
    tb.run(argv)
    assert seen == []                              # NOT re-reviewed, not re-paid for


def test_skip_set_does_not_hide_cards_from_the_trigger_count(tmp_path, monkeypatch):
    """The skip-set is a TRIAGE-cost control, never a trigger input. An
    escalated card is real pending work awaiting Coen; if it stopped counting
    toward the loop's triggerable count the trigger would silently drop, which
    is the deadlock wearing a different hat."""
    from pipeline import loop
    _seed_pending(tmp_path, 3)
    reg_path = tmp_path / "registry_log.jsonl"
    state = tmp_path / "triage_escalated.json"
    monkeypatch.setattr(tb, "review_card", lambda *a, **k: _votes(True, False, True))
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    before = loop._triggerable_counts(Registry(reg_path))["crypto"]
    tb.run(["--registry", str(reg_path), "--escalated-state", str(state), "--apply"])
    after = loop._triggerable_counts(Registry(reg_path))["crypto"]

    assert before == 3
    assert after == 3          # escalation chained nothing; all three still count


def test_a_stale_skip_entry_for_a_now_accepted_card_is_harmless(tmp_path, monkeypatch):
    """Coen dispositions an escalated card in T3: it leaves `pending` on its
    own, so the stale skip entry simply never matches again. It must not
    crash, and must not suppress anything else."""
    reg = _seed_pending(tmp_path, 2)
    reg_path = tmp_path / "registry_log.jsonl"
    state = tmp_path / "triage_escalated.json"
    state.write_text(_json.dumps({
        "c0": {"reason": "dissent", "first_escalated_utc": "2020-01-01T00:00:00+00:00",
               "times_seen": 1},
        "ghost": {"reason": "dissent", "first_escalated_utc": "2020-01-01T00:00:00+00:00",
                  "times_seen": 9},
    }), encoding="utf-8")
    reg.review_card("c0", "accepted", "coen")      # Coen dispositions it

    seen = []
    def _accept(client, model, card, meter, panel_size=3):
        seen.append(card["card_id"])
        return _votes(True, True, True)
    monkeypatch.setattr(tb, "review_card", _accept)
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    rc = tb.run(["--registry", str(reg_path), "--escalated-state", str(state), "--apply"])
    assert rc == 0
    assert seen == ["c1"]        # c0 is no longer pending; ghost matches nothing


def test_a_corrupt_skip_set_warns_and_skips_nothing(tmp_path, monkeypatch, capsys):
    """Advisory state, never a gate: a corrupt file must degrade to 'no skips'
    with a loud WARN, never take the triage stage (and the loop cycle around
    it) down with a JSONDecodeError."""
    _seed_pending(tmp_path, 1)
    reg_path = tmp_path / "registry_log.jsonl"
    state = tmp_path / "triage_escalated.json"
    state.write_text("{not json at all", encoding="utf-8")

    seen = []
    def _accept(client, model, card, meter, panel_size=3):
        seen.append(card["card_id"])
        return _votes(True, True, True)
    monkeypatch.setattr(tb, "review_card", _accept)
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    rc = tb.run(["--registry", str(reg_path), "--escalated-state", str(state), "--apply"])
    assert rc == 0
    assert seen == ["c0"]                     # degraded to no skips, still ran
    assert "WARN" in capsys.readouterr().out


def test_skip_filter_runs_before_the_limit_slice(tmp_path, monkeypatch):
    """The defect was positional: escalated cards sat at the HEAD of the chain
    order and ate the whole --limit window. Filtering after the slice would
    leave the window full of skipped cards and review nothing."""
    _seed_pending(tmp_path, 5)
    reg_path = tmp_path / "registry_log.jsonl"
    state = tmp_path / "triage_escalated.json"
    state.write_text(_json.dumps({
        f"c{i}": {"reason": "dissent", "first_escalated_utc": "2020-01-01T00:00:00+00:00",
                  "times_seen": 1} for i in range(3)}), encoding="utf-8")

    seen = []
    def _accept(client, model, card, meter, panel_size=3):
        seen.append(card["card_id"])
        return _votes(True, True, True)
    monkeypatch.setattr(tb, "review_card", _accept)
    monkeypatch.setattr(tb, "_client_and_meter", lambda: (None, _Meter()))

    tb.run(["--registry", str(reg_path), "--escalated-state", str(state),
            "--limit", "2", "--apply"])
    # c0-c2 skipped; the window of 2 is spent on the cards BEHIND them.
    assert seen == ["c3", "c4"]
