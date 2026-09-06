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
