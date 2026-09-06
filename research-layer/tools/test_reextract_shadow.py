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
