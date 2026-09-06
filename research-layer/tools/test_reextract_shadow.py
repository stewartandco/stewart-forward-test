"""Unit tests for the re-extract shadow harness (docs/2026-09-06-reextract-shadow-design.md).

No network and no model calls: every impure edge is injected by the test.
"""
from tools.reextract_shadow import build_corpus, doc_key


def _card(url, status, claim, quote="q", title="T", ctype="blog"):
    return {
        "source": {"url": url, "title": title, "type": ctype},
        "claim": claim,
        "quote": quote,
        "review": {"status": status},
    }


def test_doc_key_prefers_url_then_falls_back_to_title():
    assert doc_key(_card("https://a.example/x", "accepted", "c")) == "https://a.example/x"
    no_url = {"source": {"url": None, "title": "Some Paper"}, "review": {}}
    assert doc_key(no_url) == "title:Some Paper"


def test_build_corpus_groups_cards_by_document_and_counts_dispositions():
    cards = {
        "c1": _card("https://a.example/x", "accepted", "claim one"),
        "c2": _card("https://a.example/x", "rejected", "claim two"),
        "c3": _card("https://a.example/x", "pending", "claim three"),
        "c4": _card("https://b.example/y", "accepted", "claim four"),
        "c5": _card("https://a.example/x", None, "claim five"),
    }
    corpus = build_corpus(cards)

    assert set(corpus) == {"https://a.example/x", "https://b.example/y"}
    a = corpus["https://a.example/x"]
    assert a["old_cards"] == 4            # true count: the None-status card counts here...
    assert a["old_accepted"] == 1
    assert a["old_rejected"] == 1
    assert a["old_pending"] == 1          # ...but in none of the three buckets
    assert a["title"] == "T"
    assert a["source_type"] == "blog"


from tools.reextract_shadow import band_of, sample_documents


def _doc(key, accepted, rejected, pending):
    return {"key": key, "url": f"https://x.example/{key}", "title": key,
            "source_type": "blog", "old_cards": accepted + rejected + pending,
            "old_accepted": accepted, "old_rejected": rejected,
            "old_pending": pending}


def test_band_of_splits_on_whether_the_documents_cards_mostly_passed():
    # accepted / (accepted + rejected + pending) > 0.5 -> "passed"
    assert band_of(_doc("a", 8, 1, 1)) == "passed"
    assert band_of(_doc("b", 1, 1, 8)) == "stalled"
    # exactly half is "stalled": the band is "mostly passed", strictly
    assert band_of(_doc("c", 5, 0, 5)) == "stalled"


def test_sample_documents_is_deterministic_for_a_seed():
    docs = {f"d{i}": _doc(f"d{i}", 9, 0, 1) for i in range(20)}
    docs.update({f"s{i}": _doc(f"s{i}", 0, 0, 9) for i in range(20)})
    first = sample_documents(docs, n=10, seed=1234)
    again = sample_documents(docs, n=10, seed=1234)
    assert [d["key"] for d in first] == [d["key"] for d in again]
    other = sample_documents(docs, n=10, seed=99)
    assert [d["key"] for d in first] != [d["key"] for d in other]


def test_sample_documents_draws_from_both_bands():
    docs = {f"d{i}": _doc(f"d{i}", 9, 0, 1) for i in range(20)}
    docs.update({f"s{i}": _doc(f"s{i}", 0, 0, 9) for i in range(20)})
    picked = sample_documents(docs, n=10, seed=7)
    bands = {d["band"] for d in picked}
    assert bands == {"passed", "stalled"}
    assert len(picked) == 10
    assert sum(1 for d in picked if d["band"] == "passed") == 5


def test_sample_documents_tops_up_when_one_band_is_short():
    docs = {f"d{i}": _doc(f"d{i}", 9, 0, 1) for i in range(9)}
    docs["s0"] = _doc("s0", 0, 0, 9)
    picked = sample_documents(docs, n=10, seed=7)
    assert len(picked) == 10
    assert sum(1 for d in picked if d["band"] == "stalled") == 1


def test_sample_documents_tops_up_in_the_other_direction_too():
    # the mirror case: "passed" is the short band, "stalled" is abundant
    docs = {f"d{i}": _doc(f"d{i}", 9, 0, 1) for i in range(3)}
    docs.update({f"s{i}": _doc(f"s{i}", 0, 0, 9) for i in range(20)})
    picked = sample_documents(docs, n=10, seed=7)
    assert len(picked) == 10
    assert sum(1 for d in picked if d["band"] == "passed") == 3
    assert sum(1 for d in picked if d["band"] == "stalled") == 7


def test_sample_documents_returns_everything_when_corpus_is_smaller_than_n():
    docs = {"d0": _doc("d0", 9, 0, 1), "s0": _doc("s0", 0, 0, 9)}
    assert len(sample_documents(docs, n=10, seed=7)) == 2


import pytest

from tools.reextract_shadow import LocalPdfMap, load_document_text


def test_load_document_text_uses_the_local_pdf_when_one_is_mapped(tmp_path):
    pdf = tmp_path / "ssrn-3270329.pdf"
    pdf.write_text("not really a pdf", encoding="utf-8")
    local = LocalPdfMap({"https://ssrn.com/abstract=3270329": pdf})
    seen = {}

    def fake_read_pdf(path):
        seen["path"] = path
        return "PDF TEXT"

    def fail_fetch(url, timeout=25):
        raise AssertionError("must not fetch when a local file is mapped")

    text, how = load_document_text({"url": "https://ssrn.com/abstract=3270329"},
                                   local=local, read_pdf=fake_read_pdf,
                                   fetch=fail_fetch, html_to_text=lambda h: h)
    assert text == "PDF TEXT"
    assert how == "local_pdf"
    assert seen["path"] == pdf


def test_load_document_text_fetches_and_converts_html():
    def fake_fetch(url, timeout=25):
        return 200, "<html><body>Hello</body></html>", url

    text, how = load_document_text({"url": "https://a.example/x"},
                                   local=LocalPdfMap({}),
                                   read_pdf=lambda p: "unused",
                                   fetch=fake_fetch,
                                   html_to_text=lambda h: "Hello")
    assert text == "Hello"
    assert how == "fetched"


def test_load_document_text_raises_on_a_non_200():
    def fake_fetch(url, timeout=25):
        return 403, "Forbidden", url

    with pytest.raises(RuntimeError, match="http 403"):
        load_document_text({"url": "https://ssrn.com/x"}, local=LocalPdfMap({}),
                           read_pdf=lambda p: "unused", fetch=fake_fetch,
                           html_to_text=lambda h: h)


def test_load_document_text_raises_when_there_is_no_url_and_no_local_file():
    with pytest.raises(RuntimeError, match="no url"):
        load_document_text({"url": None}, local=LocalPdfMap({}),
                           read_pdf=lambda p: "unused",
                           fetch=lambda u, timeout=25: (200, "x", u),
                           html_to_text=lambda h: h)


def test_local_pdf_map_from_afml_dir_maps_exact_stems_and_skips_the_rest(tmp_path):
    (tmp_path / "ssrn-3270329.pdf").write_bytes(b"%PDF")
    (tmp_path / "ssrn-3257415.pdf").write_bytes(b"%PDF")
    (tmp_path / "ssrn-3270329 (1).pdf").write_bytes(b"%PDF")   # a Windows duplicate
    (tmp_path / "notes.pdf").write_bytes(b"%PDF")
    local = LocalPdfMap.from_afml_dir(tmp_path)
    assert local.get("https://ssrn.com/abstract=3270329") == tmp_path / "ssrn-3270329.pdf"
    assert local.get("https://ssrn.com/abstract=3257415") == tmp_path / "ssrn-3257415.pdf"
    assert local.get("https://ssrn.com/abstract=32703291") is None   # never squashed
    assert local.get("https://ssrn.com/abstract=") is None


def test_local_pdf_map_from_a_missing_dir_is_empty_not_an_error(tmp_path):
    local = LocalPdfMap.from_afml_dir(tmp_path / "nope")
    assert local.get("https://ssrn.com/abstract=3270329") is None


def test_load_document_text_reports_a_network_error_with_its_reason():
    def fake_fetch(url, timeout=25):
        return 0, "URLError: getaddrinfo failed", url

    with pytest.raises(RuntimeError, match="network error: URLError"):
        load_document_text({"url": "https://gone.example/x"}, local=LocalPdfMap({}),
                           read_pdf=lambda p: "unused", fetch=fake_fetch,
                           html_to_text=lambda h: h)


def test_load_document_text_passes_plain_text_through_untouched():
    def fake_fetch(url, timeout=25):
        return 200, "Just plain text, no markup.", url

    text, how = load_document_text({"url": "https://a.example/t"}, local=LocalPdfMap({}),
                                   read_pdf=lambda p: "unused", fetch=fake_fetch,
                                   html_to_text=lambda h: "MUST NOT BE CALLED")
    assert text == "Just plain text, no markup."
    assert how == "fetched"


from pipeline.triage_batch import claim_fingerprint
from tools.reextract_shadow import classify_claims


def test_classify_claims_drops_quotes_absent_from_the_text():
    text = "Momentum reverses after large volume shocks."
    claims = [
        {"claim": "A", "quote": "Momentum reverses after large volume shocks."},
        {"claim": "B", "quote": "This sentence is not in the document at all."},
    ]
    out = classify_claims(claims, text=text, known_fingerprints=set())
    assert out["dropped_quote_guard"] == 1
    assert [c["claim"] for c in out["novel"]] == ["A"]


def test_classify_claims_counts_fingerprint_duplicates_against_every_held_card():
    text = "Momentum reverses after large volume shocks."
    held = {claim_fingerprint("Momentum reverses after volume shocks")}
    claims = [
        # differs only by punctuation and case - same fingerprint
        {"claim": "momentum reverses after volume shocks!",
         "quote": "Momentum reverses after large volume shocks."},
        {"claim": "Overnight returns are higher after shocks",
         "quote": "Momentum reverses after large volume shocks."},
    ]
    out = classify_claims(claims, text=text, known_fingerprints=held)
    assert out["duplicate_of_existing"] == 1
    assert [c["claim"] for c in out["novel"]] == ["Overnight returns are higher after shocks"]


def test_classify_claims_treats_repeats_within_one_run_as_duplicates():
    text = "Momentum reverses after large volume shocks."
    claims = [
        {"claim": "Same claim", "quote": "Momentum reverses after large volume shocks."},
        {"claim": "Same claim", "quote": "Momentum reverses after large volume shocks."},
    ]
    out = classify_claims(claims, text=text, known_fingerprints=set())
    assert out["duplicate_of_existing"] == 1
    assert len(out["novel"]) == 1


def test_classify_claims_reports_the_proposed_total():
    out = classify_claims([], text="anything", known_fingerprints=set())
    assert out["proposed"] == 0 and out["novel"] == []


def test_classify_claims_treats_an_empty_or_missing_quote_as_a_guard_failure():
    text = "Momentum reverses after large volume shocks."
    claims = [
        {"claim": "No quote at all"},
        {"claim": "Empty quote", "quote": ""},
        {"claim": "None quote", "quote": None},
    ]
    out = classify_claims(claims, text=text, known_fingerprints=set())
    assert out["dropped_quote_guard"] == 3
    assert out["novel"] == []


def test_classify_claims_never_mutates_the_callers_known_fingerprints():
    text = "Momentum reverses after large volume shocks."
    known = {claim_fingerprint("something already held")}
    before = set(known)
    classify_claims([{"claim": "Brand new", "quote": text}], text=text,
                    known_fingerprints=known)
    assert known == before


from tools.reextract_shadow import aggregate, verdict_for


def _result(**kw):
    base = {"key": "k", "url": "u", "title": "t", "source_type": "blog",
            "band": "passed", "old_cards": 0, "old_accepted": 0,
            "old_rejected": 0, "old_pending": 0, "proposed": 0,
            "dropped_quote_guard": 0, "duplicate_of_existing": 0, "novel": 0,
            "novel_accepted": 0, "novel_escalated": 0,
            "escalation_reasons": [], "usd": 0.0, "error": None}
    base.update(kw)
    return base


def test_aggregate_excludes_pending_from_both_accept_rates():
    results = [
        _result(old_accepted=6, old_rejected=2, old_pending=92,
                novel=10, novel_accepted=9, novel_escalated=1, usd=0.10),
    ]
    agg = aggregate(results)
    # old rate is 6/(6+2), NOT 6/100 - pending is undecided, not a failure
    assert agg["old_accept_rate"] == pytest.approx(0.75)
    assert agg["novel_accept_rate"] == pytest.approx(0.9)
    assert agg["old_pending_share"] == pytest.approx(92 / 100)


def test_aggregate_computes_per_document_and_per_dollar_yield():
    results = [
        _result(novel=4, novel_accepted=3, usd=0.10),
        _result(novel=4, novel_accepted=1, usd=0.10),
    ]
    agg = aggregate(results)
    assert agg["documents"] == 2
    assert agg["novel_accepted_per_doc"] == pytest.approx(2.0)
    assert agg["novel_accepted_per_usd"] == pytest.approx(20.0)
    assert agg["usd_total"] == pytest.approx(0.20)


def test_aggregate_ignores_errored_documents_in_the_rates_but_reports_them():
    results = [
        _result(novel=4, novel_accepted=4, usd=0.10),
        _result(error="http 403"),
    ]
    agg = aggregate(results)
    assert agg["documents"] == 1
    assert agg["errors"] == 1
    assert agg["novel_accepted_per_doc"] == pytest.approx(4.0)


def test_aggregate_counts_spend_on_errored_documents_too():
    # extraction cost money, then the panel raised: the money is still spent
    results = [
        _result(novel=2, novel_accepted=2, usd=0.10),
        _result(error="panel raised", usd=0.05),
    ]
    agg = aggregate(results)
    assert agg["documents"] == 1 and agg["errors"] == 1
    assert agg["usd_total"] == pytest.approx(0.15)
    assert agg["novel_accepted_per_usd"] == pytest.approx(2 / 0.15)


def test_aggregate_is_safe_on_an_empty_run():
    agg = aggregate([])
    assert agg["documents"] == 0
    assert agg["novel_accepted_per_doc"] == 0.0
    assert agg["novel_accept_rate"] is None
    assert agg["old_accept_rate"] is None


def test_verdict_for_applies_the_specs_thresholds():
    # green needs >= 2 per doc AND a novel rate no worse than the old rate
    assert verdict_for({"novel_accepted_per_doc": 2.0, "novel_accept_rate": 0.8,
                        "old_accept_rate": 0.8}) == "green"
    assert verdict_for({"novel_accepted_per_doc": 3.0, "novel_accept_rate": 0.5,
                        "old_accept_rate": 0.8}) == "amber"
    assert verdict_for({"novel_accepted_per_doc": 1.0, "novel_accept_rate": 0.9,
                        "old_accept_rate": 0.8}) == "amber"
    assert verdict_for({"novel_accepted_per_doc": 0.4, "novel_accept_rate": 0.9,
                        "old_accept_rate": 0.8}) == "red"
    # exactly 0.5 is NOT red: the rule is strictly less than
    assert verdict_for({"novel_accepted_per_doc": 0.5, "novel_accept_rate": 0.9,
                        "old_accept_rate": 0.8}) == "amber"


def test_verdict_for_is_amber_when_there_is_no_old_rate_to_compare():
    assert verdict_for({"novel_accepted_per_doc": 5.0, "novel_accept_rate": 0.9,
                        "old_accept_rate": None}) == "amber"


from tools.reextract_shadow import (ChainUnchanged, PILOT_CEILING_USD,
                                    ensure_no_cycle_running, spend_guard)


def test_chain_unchanged_passes_when_the_file_is_untouched(tmp_path):
    chain = tmp_path / "registry_log.jsonl"
    chain.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    with ChainUnchanged(chain):
        pass          # a well-behaved run writes nothing


def test_chain_unchanged_raises_when_a_line_is_appended(tmp_path):
    chain = tmp_path / "registry_log.jsonl"
    chain.write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="chain changed"):
        with ChainUnchanged(chain):
            with chain.open("a", encoding="utf-8") as fh:
                fh.write('{"b": 2}\n')


def test_ensure_no_cycle_running_raises_when_the_lock_is_held(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "chain.lock").write_text('{"holder": "loop", "pid": 1}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="chain.lock"):
        ensure_no_cycle_running(logs)


def test_ensure_no_cycle_running_is_quiet_when_the_lock_is_absent(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    ensure_no_cycle_running(logs)


def test_spend_guard_stops_the_run_at_the_pilot_ceiling():
    guard = spend_guard(start_usd=1.0, ceiling=PILOT_CEILING_USD)
    assert guard(1.5) is True                       # 0.5 spent, keep going
    assert guard(1.0 + PILOT_CEILING_USD) is False  # ceiling reached


def test_chain_unchanged_lets_an_exception_propagate_when_the_chain_is_untouched(tmp_path):
    chain = tmp_path / "registry_log.jsonl"
    chain.write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="the real error"):
        with ChainUnchanged(chain):
            raise ValueError("the real error")


def test_chain_unchanged_reports_a_write_even_when_the_block_raised(tmp_path):
    # the run crashed AND the chain moved: both must surface, chained
    chain = tmp_path / "registry_log.jsonl"
    chain.write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="chain changed") as info:
        with ChainUnchanged(chain):
            with chain.open("a", encoding="utf-8") as fh:
                fh.write('{"b": 2}\n')
            raise ValueError("the real error")
    assert isinstance(info.value.__cause__, ValueError)


def test_chain_unchanged_message_names_the_benign_causes(tmp_path):
    chain = tmp_path / "registry_log.jsonl"
    chain.write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="LEGITIMATE writer"):
        with ChainUnchanged(chain):
            with chain.open("a", encoding="utf-8") as fh:
                fh.write('{"b": 2}\n')


def test_ensure_no_cycle_running_shows_the_lock_holder(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "chain.lock").write_text('{"holder": "scanner", "pid": 4242}', encoding="utf-8")
    with pytest.raises(RuntimeError, match='"holder": "scanner"'):
        ensure_no_cycle_running(logs)


from tools.reextract_shadow import shadow_one_document


class _FakeMeter:
    """Stands in for BudgetMeter: month_spend() is all the harness reads."""

    def __init__(self, spend=0.0):
        self._spend = spend

    def month_spend(self, month=None, agent=None):
        return self._spend

    def bump(self, usd):
        self._spend += usd


def test_shadow_one_document_walks_extract_classify_and_panel():
    doc = {"key": "k", "url": "https://a.example/x", "title": "T",
           "source_type": "blog", "band": "passed", "old_cards": 3,
           "old_accepted": 2, "old_rejected": 1, "old_pending": 0}
    meter = _FakeMeter(1.0)

    def fake_load(d):
        return "Momentum reverses after large volume shocks.", "fetched"

    def fake_extract(label, chunk):
        meter.bump(0.02)
        return [
            {"claim": "New claim one", "quote": "Momentum reverses after large volume shocks."},
            {"claim": "Ghost", "quote": "absent from the text"},
        ]

    def fake_panel(cards):
        meter.bump(0.03)
        cid = next(iter(cards))
        return {"decisions": {cid: ("accepted", None)}, "escalated": {},
                "dissent_reasons": {},
                "counts": {"accepted": 1, "duplicate": 0, "escalated": 0},
                "stopped": None}

    res = shadow_one_document(doc, load_text=fake_load, extract=fake_extract,
                              panel=fake_panel, known_fingerprints=set(),
                              meter=meter, chunker=lambda t: [("full document", t)])

    assert res["error"] is None
    assert res["proposed"] == 2
    assert res["dropped_quote_guard"] == 1
    assert res["novel"] == 1
    assert res["novel_accepted"] == 1
    assert res["novel_escalated"] == 0
    assert res["usd"] == pytest.approx(0.05)
    assert res["old_accepted"] == 2          # chain-side fields carried through


def test_shadow_one_document_records_escalation_reasons():
    doc = {"key": "k", "url": "u", "title": "T", "source_type": "blog",
           "band": "stalled", "old_cards": 1, "old_accepted": 0,
           "old_rejected": 0, "old_pending": 1}

    def fake_panel(cards):
        cid = next(iter(cards))
        return {"decisions": {}, "escalated": {cid: "dissent"},
                "dissent_reasons": {cid: ["claim exceeds the quote"]},
                "counts": {"accepted": 0, "duplicate": 0, "escalated": 1},
                "stopped": None}

    res = shadow_one_document(
        doc, load_text=lambda d: ("the text", "fetched"),
        extract=lambda label, chunk: [{"claim": "C", "quote": "the text"}],
        panel=fake_panel, known_fingerprints=set(), meter=_FakeMeter(),
        chunker=lambda t: [("full document", t)])

    assert res["novel_escalated"] == 1
    assert res["escalation_reasons"] == ["claim exceeds the quote"]


def test_shadow_one_document_records_a_load_failure_without_raising():
    doc = {"key": "k", "url": "https://ssrn.com/x", "title": "T",
           "source_type": "paper", "band": "passed", "old_cards": 1,
           "old_accepted": 1, "old_rejected": 0, "old_pending": 0}

    def boom(d):
        raise RuntimeError("http 403")

    res = shadow_one_document(doc, load_text=boom,
                              extract=lambda label, chunk: [],
                              panel=lambda cards: {},
                              known_fingerprints=set(), meter=_FakeMeter(),
                              chunker=lambda t: [("full document", t)])
    assert res["error"] == "http 403"
    assert res["proposed"] == 0 and res["novel_accepted"] == 0


def test_shadow_one_document_skips_the_panel_when_nothing_is_novel():
    def fail_panel(cards):
        raise AssertionError("must not pay for a panel with no novel claims")

    res = shadow_one_document(
        {"key": "k", "url": "u", "title": "T", "source_type": "blog",
         "band": "passed", "old_cards": 0, "old_accepted": 0,
         "old_rejected": 0, "old_pending": 0},
        load_text=lambda d: ("text here", "fetched"),
        extract=lambda label, chunk: [{"claim": "C", "quote": "missing"}],
        panel=fail_panel, known_fingerprints=set(), meter=_FakeMeter(),
        chunker=lambda t: [("full document", t)])
    assert res["novel"] == 0 and res["novel_accepted"] == 0
