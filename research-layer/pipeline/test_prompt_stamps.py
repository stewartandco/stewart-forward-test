"""Prompt + served-model stamps on every LLM output (harness audit 2026-11-07, Tier 1 #4).

1. PINS: each prompt's sha is pinned here. A prompt edit fails this file until the pin is
   bumped on purpose; bump it in the same commit as the prompt and say why in the message.
2. Every recorded output carries prompt_sha + the model the API actually served: cards
   (extraction), specs (generator), card_reviewed entries and their rejection-log rows, and
   the relevance screen log. The API messages themselves are byte-identical to before.

Run: python -m pytest pipeline/test_prompt_stamps.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from . import cells, composer, reader, relevance, triage_batch
from .prompt_stamp import prompt_sha, served_model
from .registry import Registry
from .test_composer import good_family, one_crypto_cell, seeded_registry  # noqa: F401
from .composer import run as composer_run

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"

# ---------------------------------------------------------------- 1. the pins

PINNED = {
    "reader extraction": ("16e8be0983f7fe4a", lambda: reader.EXTRACTION_PROMPT_SHA),
    "relevance screen": ("4f5abaecc4bda3bc", lambda: relevance.SCREEN_PROMPT_SHA),
    # bumped 2026-11-08: review prompt v3 (Coen: names yes, conditions no); v1 d2301c54f6ae81ef
    # and v2 4b3a8b9f7c5ec393 both failed the triage eval (6/104 false accepts each)
    "triage review": ("2b4f6e44a71c1a2e", lambda: triage_batch.REVIEW_PROMPT_SHA),
}
PINNED_COMPOSER = {
    "bond_etf": "631f239d38eaa78f", "crypto": "c8e7c223c7f520ab", "equity_etf": "33800e51c56dab96",
    "fx": "2a93663d364e38d3", "metal_etf": "a066bb738fdfdd2c",
}


@pytest.mark.parametrize("name", sorted(PINNED))
def test_prompt_is_pinned(name):
    want, got = PINNED[name]
    assert got() == want, (f"the {name} prompt changed (sha {got()} != pinned {want}). If that "
                           "was deliberate, bump the pin here in the same commit and say why.")


def test_every_composer_class_prompt_is_pinned():
    assert set(PINNED_COMPOSER) == set(cells.CLASSES), "a class was added or removed: pin it"
    for cls, want in PINNED_COMPOSER.items():
        assert composer.composer_prompt_sha(cls) == want, (cls, composer.composer_prompt_sha(cls))


def test_the_sha_moves_with_any_part_of_the_prompt():
    base = prompt_sha("system", {"schema": 1}, "user {x}")
    assert prompt_sha("system ", {"schema": 1}, "user {x}") != base
    assert prompt_sha("system", {"schema": 2}, "user {x}") != base
    assert prompt_sha("system", {"schema": 1}, "user {y}") != base
    assert len(base) == 16 and int(base, 16) >= 0


def test_reordering_schema_fields_moves_the_sha():
    # the model emits structured-output fields in schema order (reader.py relies on it)
    a = prompt_sha("s", {"properties": {"quote": 1, "claim": 2}})
    b = prompt_sha("s", {"properties": {"claim": 2, "quote": 1}})
    assert a != b


def test_composer_sha_covers_grammar_card_line_and_exclusion_note(monkeypatch):
    before = {c: composer.composer_prompt_sha(c) for c in ("crypto", "fx")}
    monkeypatch.setattr(composer, "grammar_summary", lambda: "a different grammar")
    assert composer.composer_prompt_sha("crypto") != before["crypto"]
    monkeypatch.undo()
    monkeypatch.setattr(composer, "cards_summary", lambda accepted: "* another card line format")
    assert composer.composer_prompt_sha("crypto") != before["crypto"]
    monkeypatch.undo()
    monkeypatch.setattr(composer, "_exclusion_note", lambda cls: "\n\nreworded exclusion")
    assert composer.composer_prompt_sha("fx") != before["fx"]


def test_relevance_sha_covers_the_item_framing(monkeypatch):
    before = relevance._screen_prompt_sha()
    monkeypatch.setattr(relevance, "build_screen_prompt", lambda items: "Screen these items, please:\n")
    assert relevance._screen_prompt_sha() != before


def test_served_model_prefers_the_response():
    class M:
        model = "claude-opus-5-20261101"
    assert served_model(M(), "claude-opus-5") == "claude-opus-5-20261101"
    assert served_model(object(), "claude-opus-5") == "claude-opus-5"
    class Empty:
        model = ""
    assert served_model(Empty(), "claude-opus-5") == "claude-opus-5"


# ---------------------------------------------------------------- fakes

class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Msg:
    def __init__(self, payload, model="claude-served-20261101", stop_reason="end_turn"):
        self.content = [_Text(json.dumps(payload))]
        self.model = model
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _StreamClient:
    """messages.stream(...) context manager returning a fixed final message."""

    def __init__(self, msg):
        self.calls = []
        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.calls.append(kw)

                class _S:
                    def __enter__(s):
                        return s

                    def __exit__(s, *a):
                        return False

                    def get_final_message(s):
                        return msg
                return _S()

            def create(self, **kw):
                outer.calls.append(kw)
                return msg
        self.messages = _Messages()


class _Meter:
    def __init__(self, spend=True):
        self.spend = spend

    def can_spend(self):
        return self.spend

    def record_call(self, *a, **k):
        pass


RAW = {"claim": "Momentum persists over 12 months.", "quote": "returns persist for twelve months",
       "locator": "p.3", "asset_classes": ["crypto"], "topics": ["momentum"], "horizon": "multi_month",
       "testability_score": 0.8, "data_required": ["daily close"], "notes": ""}
SOURCE = {"type": "paper", "title": "A paper", "credibility_tier": "practitioner", "url": "https://example.org/p"}


# ---------------------------------------------------------------- 2. cards (reader extraction)

def test_extraction_reports_the_served_model_and_the_card_records_it():
    client = _StreamClient(_Msg({"claims": [dict(RAW)]}, model="claude-served-1"))
    claims, _usage, served = reader.extract_claims_served(client, "claude-requested", "chunk 1", "text")
    assert served == "claude-served-1" and claims == [RAW]          # claims themselves untouched
    plain, _u = reader.extract_claims_usage(_StreamClient(_Msg({"claims": [dict(RAW)]})), "m", "c", "t")
    assert plain == [RAW]                                           # the old contract, unchanged
    card = reader.build_card(claims[0], SOURCE, "claude-requested", "run-1", served_model=served)
    ext = card["extraction"]
    assert ext["model"] == "claude-requested"                     # what was asked for: unchanged
    assert ext["served_model"] == "claude-served-1"               # what answered
    assert ext["prompt_sha"] == reader.EXTRACTION_PROMPT_SHA
    assert "_served" not in json.dumps(card)
    jsonschema.Draft202012Validator(json.loads(
        (SCHEMAS / "research_card.schema.json").read_text(encoding="utf-8"))).validate(card)


def test_extraction_sends_the_same_user_message_as_before():
    client = _StreamClient(_Msg({"claims": []}))
    reader.extract_claims_usage(client, "m", "chunk 2 (chars 0-9)", "a {brace} body")
    sent = client.calls[0]["messages"][0]["content"]
    assert sent == ("Document section: chunk 2 (chars 0-9)\n\n<document>\na {brace} body\n</document>\n\n"
                    "Extract the testable claims per your instructions.")


def test_a_card_no_model_call_produced_is_not_stamped():
    # fixtures and hand-built cards (tools_dryrun_fx, examples/) never ran the prompt
    card = reader.build_card(dict(RAW), SOURCE, "claude-requested", "run-1")
    assert "prompt_sha" not in card["extraction"] and "served_model" not in card["extraction"]


def test_an_extraction_refusal_returns_cleanly_with_its_usage():
    msg = _Msg({"claims": []}, model="claude-served-r", stop_reason="refusal")
    claims, usage, served = reader.extract_claims_served(_StreamClient(msg), "m", "c", "t")
    assert claims == [] and usage is msg.usage and served == "claude-served-r"
    assert reader.extract_claims_usage(_StreamClient(msg), "m", "c", "t") == ([], msg.usage)


def test_old_cards_without_stamps_still_validate():
    card = reader.build_card(dict(RAW), SOURCE, "m", "r")
    jsonschema.Draft202012Validator(json.loads(
        (SCHEMAS / "research_card.schema.json").read_text(encoding="utf-8"))).validate(card)


# ---------------------------------------------------------------- 3. relevance screen log

def test_screen_log_rows_carry_the_stamp_only_when_the_model_answered(tmp_path):
    items = [{"item_id": "i1", "source_id": "s", "title": "t", "summary": "x"}]
    answer = {"decisions": [{"id": "i1", "keep": True, "reason": "on topic", "testability": 0.9}]}
    log = tmp_path / "screen.jsonl"
    out = relevance.screen_items(_StreamClient(_Msg(answer, model="claude-served-2")), "m", items, _Meter(), log)
    assert out["i1"][0] == "screen_keep"
    relevance.screen_items(_StreamClient(_Msg(answer)), "m",
                           [{"item_id": "i2", "source_id": "s", "title": "t", "summary": "x"}],
                           _Meter(spend=False), log)                       # budget: no call made
    rows = {r["item_id"]: r for r in map(json.loads, log.read_text(encoding="utf-8").splitlines())}
    assert rows["i1"]["prompt_sha"] == relevance.SCREEN_PROMPT_SHA
    assert rows["i1"]["served_model"] == "claude-served-2"
    assert rows["i2"]["decision"] == "deferred_budget"
    assert "prompt_sha" not in rows["i2"] and "served_model" not in rows["i2"]


# ---------------------------------------------------------------- 4. triage panel -> card_reviewed

def test_panel_stamps_reach_the_chain_and_the_rejection_log(tmp_path):
    reg = Registry(tmp_path / "registry_log.jsonl")
    card = reader.build_card(dict(RAW), SOURCE, "m", "r")
    reg.register_card(card)
    pending = reg.cards(status="pending")
    vote_no = _Msg({"accept": False, "reason": "overreach"}, model="claude-served-3")
    out = triage_batch.build_decisions(_StreamClient(vote_no), "claude-requested", pending, {}, _Meter())
    cid = card["card_id"]
    assert out["stamps"][cid] == {"prompt_sha": triage_batch.REVIEW_PROMPT_SHA,
                                  "served_models": ["claude-served-3"]}
    triage_batch.apply_decisions(reg, out["decisions"], triage_batch.REVIEWER, stamps=out["stamps"])
    reviewed = [e["payload"] for e in reg.entries() if e["entry_type"] == "card_reviewed"]
    assert reviewed[-1]["review_prompt_sha"] == triage_batch.REVIEW_PROMPT_SHA
    assert reviewed[-1]["review_served_models"] == ["claude-served-3"]
    assert set(out["rejections"][cid]) == {"basis", "dissent_reasons"}      # contract unchanged
    triage_batch.append_rejections(tmp_path / "rej.jsonl", out["rejections"], stamps=out["stamps"])
    row = json.loads((tmp_path / "rej.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["prompt_sha"] == triage_batch.REVIEW_PROMPT_SHA and row["served_models"] == ["claude-served-3"]


def test_the_triage_cli_chains_the_stamp(tmp_path, monkeypatch):
    # the path the nightly loop runs: triage_batch.run --apply -> apply_decisions -> card_reviewed
    reg = Registry(tmp_path / "registry_log.jsonl")
    card = reader.build_card(dict(RAW), SOURCE, "m", "r")
    reg.register_card(card)
    vote_yes = _Msg({"accept": True, "reason": "supported"}, model="claude-served-6")
    monkeypatch.setattr(triage_batch, "_client_and_meter", lambda: (_StreamClient(vote_yes), _Meter()))
    triage_batch.run(["--registry", str(tmp_path / "registry_log.jsonl"), "--apply"])
    p = [e["payload"] for e in Registry(tmp_path / "registry_log.jsonl").entries()
         if e["entry_type"] == "card_reviewed"][-1]
    assert p["card_id"] == card["card_id"] and p["status"] == "accepted"
    assert p["review_prompt_sha"] == triage_batch.REVIEW_PROMPT_SHA
    assert p["review_served_models"] == ["claude-served-6"]


def test_votes_without_a_served_model_do_not_break_the_panel(tmp_path, monkeypatch):
    # test_triage_batch fakes review_card with bare {accept, reason} votes
    reg = Registry(tmp_path / "registry_log.jsonl")
    card = reader.build_card(dict(RAW), SOURCE, "m", "r")
    reg.register_card(card)
    monkeypatch.setattr(triage_batch, "review_card",
                        lambda *a, **k: [{"accept": True, "reason": "ok"}] * 3)
    out = triage_batch.build_decisions(None, "m", reg.cards(status="pending"), {}, _Meter())
    assert out["stamps"][card["card_id"]]["served_models"] == []


def test_a_human_review_has_no_llm_stamp(tmp_path):
    reg = Registry(tmp_path / "registry_log.jsonl")
    card = reader.build_card(dict(RAW), SOURCE, "m", "r")
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "coen")
    p = [e["payload"] for e in reg.entries() if e["entry_type"] == "card_reviewed"][-1]
    assert "review_prompt_sha" not in p and "review_served_models" not in p


# ---------------------------------------------------------------- 5. composer -> spec generator

@pytest.mark.usefixtures("one_crypto_cell")
def test_a_stamped_proposal_reaches_every_chained_spec_and_verifies(tmp_path):
    path, cid = seeded_registry(tmp_path)
    fam = good_family(card_ids=[cid])
    fam.update({"_prompt_sha": PINNED_COMPOSER["crypto"], "_served_model": "claude-served-4"})
    assert composer_run(["--registry", str(path), "--run-id", "t1"], propose_fn=lambda cards: [fam]) == 0
    specs = [e["payload"] for e in Registry(path).entries() if e["entry_type"] == "strategy_registered"]
    assert specs and all(s["generator"]["prompt_sha"] == PINNED_COMPOSER["crypto"] for s in specs)
    assert all(s["generator"]["served_model"] == "claude-served-4" for s in specs)
    assert all(not any(k.startswith("_") for k in s) for s in specs)
    import subprocess
    import sys
    out = subprocess.run([sys.executable, str(SCHEMAS.parent / "verify_registry.py"), str(path)],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)   # WinError 6 otherwise
    assert out.returncode == 0, out.stdout


def test_propose_families_stamps_what_it_returns(monkeypatch):
    fam = good_family(card_ids=["c1"])
    answer = {"families": [fam]}
    client = _StreamClient(_Msg(answer, model="claude-served-5"))
    monkeypatch.setattr(composer, "normalize_proposal", lambda families: families)
    got = composer.propose_families("claude-requested", {}, 3, client=client, meter=_Meter(),
                                    asset_class="crypto")
    assert got[0]["_prompt_sha"] == composer.composer_prompt_sha("crypto")
    assert got[0]["_served_model"] == "claude-served-5"
