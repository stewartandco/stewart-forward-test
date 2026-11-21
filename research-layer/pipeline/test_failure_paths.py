"""Failure paths of the paid model calls (prompt audit 2026-10-10, R2-R4).

Success paths are unchanged; these pin what happens when an answer is unusable:
  R4  a failed Reader extraction is named (ExtractionFailed) and still metered;
  R2  a cut-off or text-less relevance screen defers its batch instead of crashing
      the scanner cycle; the Composer names a missing/cut-off answer;
  R3  every paused scout call is metered, not only the last.
"""
import json
from types import SimpleNamespace

import pytest

from .budget import BudgetMeter
from .reader import ExtractionFailed, extract_claims_usage
from .registry import Registry
from .relevance import screen_items
from .scanner import poll_source, process_new_items
from .scanstatus import ActionLog
from .seen import SeenStore
from .test_scanner import RSS_XML, StubClient, _fetch_factory, _scout_msg, make_source

USAGE = SimpleNamespace(input_tokens=800, output_tokens=32_000,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _msg(stop_reason, text=None, thinking=True):
    content = [SimpleNamespace(type="thinking", thinking="")] if thinking else []
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=USAGE)


def _rows(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


# ---- R4: reader -----------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    _msg("max_tokens"),                                   # thinking ate the budget: no text block
    _msg("max_tokens", text='{"claims": [{"claim": "cut'),  # truncated JSON
    _msg("end_turn", text='{"not_claims": []}'),           # wrong shape
])
def test_unusable_extraction_is_named_and_carries_usage(msg):
    with pytest.raises(ExtractionFailed) as err:
        extract_claims_usage(StubClient([msg]), "claude-sonnet-5", "full document", "text")
    assert err.value.usage is USAGE
    assert "stop_reason" in str(err.value)


def test_extraction_success_path_is_unchanged():
    claims = [{"claim": "c", "quote": "q"}]
    got, usage = extract_claims_usage(
        StubClient([_msg("end_turn", text=json.dumps({"claims": claims}))]),
        "claude-sonnet-5", "full document", "text")
    assert got == claims and usage is USAGE


def test_failed_extraction_is_metered_and_marks_the_item(tmp_path):
    src = make_source()
    seen = SeenStore(tmp_path / "seen.jsonl")
    new_items = poll_source(src, seen, _fetch_factory({src["feed"]: (200, RSS_XML)}))
    filler = "<p>We test the effect across a long sample of daily bars in detail.</p>" * 30
    pages = {new_items[0]["link"]: (200, "<html><body>" + filler + "</body></html>"),
             new_items[1]["link"]: (403, "forbidden")}
    screen = _msg("end_turn", thinking=False, text=json.dumps({"decisions": [
        {"id": new_items[0]["item_id"], "keep": True, "reason": "edge"},
        {"id": new_items[1]["item_id"], "keep": True, "reason": "check"}]}))
    client = StubClient([screen, _msg("max_tokens")])         # extraction cut off
    ledger = tmp_path / "ledger.jsonl"
    stats = process_new_items(
        new_items, client=client, model="claude-sonnet-5", meter=BudgetMeter(ledger),
        seen=seen, registry=Registry(tmp_path / "registry_log.jsonl"),
        fetch=_fetch_factory(pages), watchlist_sources=[src],
        discovery_path=tmp_path / "discovery.jsonl",
        screen_log=tmp_path / "screen_log.jsonl", actions=ActionLog(tmp_path / "actions.jsonl"))
    assert seen.status(new_items[0]["item_id"]) == "extract_failed"
    assert stats["extracted"] == 0
    purposes = [r["purpose"] for r in _rows(ledger)]
    assert purposes == ["screen", "extract"]          # the failed call is on the ledger


# ---- R2: relevance screen -------------------------------------------------------

def _items(n=2):
    return [{"item_id": f"i{k}", "source_id": "s", "title": f"t{k}", "link": f"https://x/{k}",
             "summary": "s"} for k in range(n)]


@pytest.mark.parametrize("msg,reason", [
    (_msg("max_tokens"), "max_tokens"),
    (_msg("max_tokens", text='{"decisions": [{"id": "i0"'), "max_tokens"),
    (_msg("end_turn"), "no_text"),
    (_msg("refusal"), "refusal"),
])
def test_unusable_screen_defers_the_batch(tmp_path, msg, reason):
    ledger = tmp_path / "ledger.jsonl"
    out = screen_items(StubClient([msg]), "claude-sonnet-5", _items(), BudgetMeter(ledger),
                       tmp_path / "screen_log.jsonl")
    assert out == {"i0": ("deferred_screen", reason), "i1": ("deferred_screen", reason)}
    assert [r["purpose"] for r in _rows(ledger)] == ["screen"]


def test_screen_success_path_is_unchanged(tmp_path):
    text = json.dumps({"decisions": [{"id": "i0", "keep": True, "reason": "r"},
                                     {"id": "i1", "keep": False, "reason": "r"}]})
    out = screen_items(StubClient([_msg("end_turn", text=text)]), "claude-sonnet-5", _items(),
                       BudgetMeter(tmp_path / "ledger.jsonl"), tmp_path / "screen_log.jsonl")
    assert {k: v[0] for k, v in out.items()} == {"i0": "screen_keep", "i1": "screen_kill"}


# ---- R2: composer ---------------------------------------------------------------

class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class _ComposerClient:
    def __init__(self, msg):
        self.messages = SimpleNamespace(stream=lambda **kw: _Stream(msg))


@pytest.mark.parametrize("msg", [_msg("max_tokens"), _msg("max_tokens", text='{"families": [')])
def test_composer_names_a_missing_or_cut_off_answer(tmp_path, msg):
    from . import composer as composer_mod
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(RuntimeError, match="no complete answer"):
        composer_mod.propose_families("claude-opus-5", {}, 3, client=_ComposerClient(msg),
                                      meter=BudgetMeter(ledger, agent="pipeline"))
    assert [r["purpose"] for r in _rows(ledger)] == ["composer"]   # still metered


# ---- R3: scout ------------------------------------------------------------------

def test_every_paused_scout_call_is_metered(tmp_path):
    from .scout import run_scout
    final = _scout_msg([{"url": "https://newsource.example/", "name": "N",
                         "why": "w", "source_class": "blog"}], searches=1)
    paused1 = _scout_msg([], stop_reason="pause_turn", searches=2)
    paused2 = _scout_msg([], stop_reason="pause_turn", searches=2)
    ledger = tmp_path / "led.jsonl"
    run_scout(client=StubClient([paused1, paused2, final]), model="claude-sonnet-5",
              meter=BudgetMeter(ledger), watchlist_sources=[make_source()],
              discovery_path=tmp_path / "q.jsonl", actions=ActionLog(tmp_path / "act.jsonl"))
    rows = _rows(ledger)
    assert [r["purpose"] for r in rows] == ["scout", "scout", "scout"]
    tokens = [r["input_tokens"] for r in rows]
    assert tokens == [4000, 4000, 4000]
    # search charges ride on the final row only, never double-counted
    assert rows[-1]["usd"] > rows[0]["usd"] == rows[1]["usd"]


def test_scout_exhausting_its_continuations_meters_each_call_once(tmp_path):
    from .scout import MAX_CONTINUATIONS, run_scout
    calls = MAX_CONTINUATIONS + 1
    paused = [_scout_msg([], stop_reason="pause_turn", searches=1) for _ in range(calls)]
    ledger = tmp_path / "led.jsonl"
    client = StubClient(paused)
    run_scout(client=client, model="claude-sonnet-5", meter=BudgetMeter(ledger),
              watchlist_sources=[make_source()], discovery_path=tmp_path / "q.jsonl",
              actions=ActionLog(tmp_path / "act.jsonl"))
    assert len(client.calls) == calls
    rows = _rows(ledger)
    assert len(rows) == calls                       # one row per call, never one extra
    assert sum(r["input_tokens"] for r in rows) == 4000 * calls


def test_failed_inbox_extraction_is_metered_before_it_propagates(tmp_path):
    from .scanner import process_inbox
    from .test_scanner import ARTICLE_HTML
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "saved_page.html").write_text(ARTICLE_HTML, encoding="utf-8")
    seen = SeenStore(tmp_path / "seen.jsonl")
    seen.record("p1", "alpha-architect", "paywalled",
                title="VIX Trend Following Out of Sample",
                link="https://alphaarchitect.com/vix-trend-following-out-of-sample/",
                reason="http 403")
    ledger = tmp_path / "led.jsonl"
    with pytest.raises(ExtractionFailed):
        process_inbox(client=StubClient([_msg("max_tokens")]), model="claude-sonnet-5",
                      meter=BudgetMeter(ledger), seen=seen,
                      registry=Registry(tmp_path / "reg.jsonl"),
                      actions=ActionLog(tmp_path / "act.jsonl"), inbox=inbox)
    assert [r["purpose"] for r in _rows(ledger)] == ["inbox_extract"]
    assert (inbox / "saved_page.html").exists()     # left in place, as before


def test_composer_names_an_end_turn_answer_without_text(tmp_path):
    from . import composer as composer_mod
    with pytest.raises(RuntimeError, match="stop_reason=end_turn"):
        composer_mod.propose_families("claude-opus-5", {}, 3, client=_ComposerClient(_msg("end_turn")),
                                      meter=BudgetMeter(tmp_path / "led.jsonl", agent="pipeline"))
