# Re-extract Shadow Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether today's extractor produces better claims than August's, on ten source documents we already own, without writing anything to the chain.

**Architecture:** A single-purpose harness at `research-layer/tools/reextract_shadow.py`, outside `pipeline/` so it can never be mistaken for a pipeline stage. It composes production functions — `feeds.fetch_url`, `feeds.html_to_text`, `reader.read_source_text`, `reader.chunk_text`, `reader.extract_claims`, `reader.build_card`, `common.quote_in_source`, `triage_batch.claim_fingerprint`, `triage_batch.build_decisions` — and never reimplements them. Pure functions (corpus building, sampling, classification, aggregation) are separated from the two impure edges (network/model calls, report writing) so the arithmetic is unit-testable without a network.

**Tech Stack:** Python 3.14, pytest, the existing `pipeline` package, `anthropic` SDK via `triage_batch._client_and_meter`'s key-loading pattern.

**Spec:** `research-layer/docs/2026-09-06-reextract-shadow-design.md` (commit `b64f8f5`).

---

## File Structure

| File | Responsibility |
|---|---|
| `research-layer/tools/__init__.py` | Create — makes `tools` importable so tests can `from tools.reextract_shadow import ...`. Empty. |
| `research-layer/tools/reextract_shadow.py` | Create — the harness. Corpus building, stratified sampling, per-document shadow extraction, classification, aggregation, report writing, CLI. |
| `research-layer/tools/test_reextract_shadow.py` | Create — unit tests. No network, no model calls: every impure edge is injected. |

Everything lives in one module because the pieces are small, share one data shape (`DocResult`), and are only ever used together. The repo's own `pipeline/*.py` modules follow the same pattern (`livegate.py`, `allowance.py`): pure helpers plus a `run(argv)` at the bottom.

**Naming contract used consistently across all tasks** — a `DocResult` is a plain dict with exactly these keys:

```python
{
  "key": str,                    # doc identity: url, or "title:<title>"
  "url": str | None,
  "title": str | None,
  "source_type": str | None,     # card's source.type, e.g. "paper", "blog"
  "band": str,                   # "passed" | "stalled"
  "old_cards": int, "old_accepted": int, "old_rejected": int, "old_pending": int,
  "proposed": int,
  "dropped_quote_guard": int,
  "duplicate_of_existing": int,
  "novel": int,
  "novel_accepted": int,
  "novel_escalated": int,
  "escalation_reasons": list[str],
  "usd": float,
  "error": str | None,           # set when the document could not be processed
}
```

---

## Task 1: Package marker and corpus builder

**Files:**
- Create: `research-layer/tools/__init__.py`
- Create: `research-layer/tools/reextract_shadow.py`
- Create: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Create `research-layer/tools/test_reextract_shadow.py`:

```python
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
    }
    corpus = build_corpus(cards)

    assert set(corpus) == {"https://a.example/x", "https://b.example/y"}
    a = corpus["https://a.example/x"]
    assert a["old_cards"] == 3
    assert a["old_accepted"] == 1
    assert a["old_rejected"] == 1
    assert a["old_pending"] == 1
    assert a["title"] == "T"
    assert a["source_type"] == "blog"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Write minimal implementation**

Create `research-layer/tools/__init__.py` as an empty file.

Create `research-layer/tools/reextract_shadow.py`:

```python
"""Re-extract shadow run: does today's extractor beat August's, on documents
we already own?

MEASUREMENT ONLY. This module never writes to the chain, never writes
loop_state, and lives outside `pipeline/` so it can never be mistaken for a
pipeline stage or picked up by the loop. Design:
docs/2026-09-06-reextract-shadow-design.md.
"""
from __future__ import annotations


def doc_key(card: dict) -> str:
    """Document identity for a card: its URL, else a title fallback.

    DOI/ISBN are deliberately not used: every card in the live corpus that
    lacks a URL also lacks both, so a third branch would be dead code.
    """
    src = card.get("source") or {}
    return src.get("url") or f"title:{src.get('title')}"


def build_corpus(cards: dict[str, dict]) -> dict[str, dict]:
    """{doc_key: partial DocResult} with the chain-side counts filled in.

    Pure: takes the cards dict, touches nothing else.
    """
    docs: dict[str, dict] = {}
    for card in cards.values():
        key = doc_key(card)
        src = card.get("source") or {}
        doc = docs.setdefault(key, {
            "key": key, "url": src.get("url"), "title": src.get("title"),
            "source_type": src.get("type"),
            "old_cards": 0, "old_accepted": 0, "old_rejected": 0,
            "old_pending": 0,
        })
        doc["old_cards"] += 1
        status = (card.get("review") or {}).get("status")
        if status in ("accepted", "rejected", "pending"):
            doc[f"old_{status}"] += 1
    return docs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 2 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/__init__.py research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): re-extract shadow harness - corpus builder

Groups the chain's cards by source document and counts their dispositions.
Measurement only; lives outside pipeline/ by design."
```

---

## Task 2: Stratified, seeded sampler

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
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


def test_sample_documents_returns_everything_when_corpus_is_smaller_than_n():
    docs = {"d0": _doc("d0", 9, 0, 1), "s0": _doc("s0", 0, 0, 9)}
    assert len(sample_documents(docs, n=10, seed=7)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'band_of'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py` (after `build_corpus`):

```python
import random

SAMPLE_SIZE = 10
BANDS = ("passed", "stalled")


def band_of(doc: dict) -> str:
    """"passed" when most of this document's cards were accepted, else
    "stalled".

    The denominator includes pending on purpose: a document whose cards are
    mostly stuck in escalation belongs in the "stalled" band, which is the
    band that asks whether re-extraction rescues failure. Exactly half is
    "stalled" - the band is "MOSTLY passed", strictly.
    """
    total = doc["old_cards"]
    return "passed" if total and doc["old_accepted"] / total > 0.5 else "stalled"


def sample_documents(corpus: dict[str, dict], n: int = SAMPLE_SIZE,
                     seed: int = 0) -> list[dict]:
    """`n` documents drawn reproducibly, split evenly across the two bands.

    Each returned doc carries its `band`. If one band cannot fill its half,
    the other tops the sample up, so the sample size is honoured even on a
    lopsided corpus. Sorting by key before shuffling makes the draw depend on
    the seed alone, not on dict insertion order.
    """
    rng = random.Random(seed)
    by_band: dict[str, list[dict]] = {b: [] for b in BANDS}
    for key in sorted(corpus):
        doc = dict(corpus[key])
        doc["band"] = band_of(doc)
        by_band[doc["band"]].append(doc)
    for bucket in by_band.values():
        rng.shuffle(bucket)

    per_band = n // len(BANDS)
    picked = [d for b in BANDS for d in by_band[b][:per_band]]
    if len(picked) < n:
        chosen = {d["key"] for d in picked}
        leftovers = [d for b in BANDS for d in by_band[b] if d["key"] not in chosen]
        picked.extend(leftovers[:n - len(picked)])
    return picked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): seeded two-band document sampler

Deterministic given a seed, splits evenly between documents whose cards
mostly passed and documents whose cards mostly stalled, and tops up from
the other band when one is short."
```

---

## Task 3: Document text loading (the one impure edge, injected)

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'LocalPdfMap'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py`:

```python
import re
from pathlib import Path

AFML_DIR = Path(r"E:\Users\Coen\Desktop\AFML")
FETCH_TIMEOUT = 25


class LocalPdfMap:
    """{document url: local PDF path} for documents whose text is on disk.

    The ten Lopez de Prado lecture decks were originally read from local PDFs
    and their SSRN pages now answer 403, so fetching them would measure
    SSRN's bot policy rather than our extractor.
    """

    def __init__(self, mapping: dict[str, Path]):
        self._map = dict(mapping)

    def get(self, url: str | None) -> Path | None:
        return self._map.get(url or "")

    @classmethod
    def from_afml_dir(cls, directory: Path = AFML_DIR) -> "LocalPdfMap":
        """Map `https://ssrn.com/abstract=NNNNN` -> `ssrn-NNNNN.pdf`.

        Missing directory or missing file is not an error: the document then
        falls through to the fetch path and reports its own failure.
        """
        mapping: dict[str, Path] = {}
        if not directory.is_dir():
            return cls(mapping)
        for pdf in directory.glob("ssrn-*.pdf"):
            digits = re.sub(r"[^0-9]", "", pdf.stem)
            if digits:
                mapping[f"https://ssrn.com/abstract={digits}"] = pdf
        return cls(mapping)


def load_document_text(doc: dict, *, local: LocalPdfMap, read_pdf, fetch,
                       html_to_text) -> tuple[str, str]:
    """(text, how) for one document, or raise RuntimeError with the reason.

    Every impure dependency is injected so the routing logic is testable
    without a network or a PDF. Production passes
    `reader.read_source_text`, `feeds.fetch_url` and `feeds.html_to_text`.
    """
    url = doc.get("url")
    path = local.get(url)
    if path is not None:
        return read_pdf(path), "local_pdf"
    if not url:
        raise RuntimeError("no url and no local file")
    status, body, _final = fetch(url, timeout=FETCH_TIMEOUT)
    if status != 200:
        raise RuntimeError(f"http {status}")
    looks_html = body.lstrip()[:400].lower().startswith(("<!doctype", "<html", "<"))
    return (html_to_text(body) if looks_html else body), "fetched"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): document text loading, local PDFs before fetch

The ten AFML decks answer 403 on SSRN but their PDFs are on disk, so they
are read locally; everything else goes through the pipeline's own fetch and
html_to_text. Dependencies injected so routing is testable offline."
```

---

## Task 4: Claim classification (guard, duplicate, novel)

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'classify_claims'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py`:

```python
from pipeline.common import quote_in_source
from pipeline.triage_batch import claim_fingerprint


def classify_claims(claims: list[dict], *, text: str,
                    known_fingerprints: set[str]) -> dict:
    """Split proposed claims into guard-drops, duplicates and novel.

    The honesty guard is production's own `quote_in_source`, applied first
    because a claim whose quote is not in the document is not a claim at all.
    Duplicates are exact `claim_fingerprint` collisions against
    `known_fingerprints`, which the caller builds from EVERY card in the
    chain - accepted, rejected and pending. That is deliberately wider than
    production's `find_duplicates` (pending vs accepted only): for this
    measurement a claim identical to one we already rejected is not novel
    either, and counting it as novel would flatter the result.

    A claim repeated inside one run is a duplicate of its own first
    occurrence, so a chatty extractor cannot inflate `novel`.
    """
    seen = set(known_fingerprints)
    out = {"proposed": len(claims), "dropped_quote_guard": 0,
           "duplicate_of_existing": 0, "novel": []}
    for raw in claims:
        if not quote_in_source(raw.get("quote", ""), text):
            out["dropped_quote_guard"] += 1
            continue
        fp = claim_fingerprint(raw.get("claim", ""))
        if fp in seen:
            out["duplicate_of_existing"] += 1
            continue
        seen.add(fp)
        out["novel"].append(raw)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 15 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): classify shadow claims - guard, duplicate, novel

Honesty guard first, then exact-fingerprint dedupe against every held card
(wider than production's pending-vs-accepted rule, by design), then novel.
In-run repeats collapse so a chatty extractor cannot inflate the count."
```

---

## Task 5: Aggregation and the decision rule

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
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


def test_verdict_for_is_amber_when_there_is_no_old_rate_to_compare():
    assert verdict_for({"novel_accepted_per_doc": 5.0, "novel_accept_rate": 0.9,
                        "old_accept_rate": None}) == "amber"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'aggregate'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py`:

```python
GREEN_PER_DOC = 2.0
RED_PER_DOC = 0.5


def _rate(hits: int, total: int) -> float | None:
    return (hits / total) if total else None


def aggregate(results: list[dict]) -> dict:
    """Roll per-document results into the spec's headline comparators.

    Errored documents are excluded from every rate and counted separately -
    a document we could not fetch says nothing about the extractor.

    Both accept rates are accepted / (accepted + rejected): a pending card is
    undecided, so counting it as a failure would understate the old corpus.
    The pending share is reported alongside so the exclusion is visible.
    """
    ok = [r for r in results if not r.get("error")]
    old_acc = sum(r["old_accepted"] for r in ok)
    old_rej = sum(r["old_rejected"] for r in ok)
    old_pend = sum(r["old_pending"] for r in ok)
    novel = sum(r["novel"] for r in ok)
    novel_acc = sum(r["novel_accepted"] for r in ok)
    usd = sum(r["usd"] for r in ok)
    return {
        "documents": len(ok),
        "errors": len(results) - len(ok),
        "old_accept_rate": _rate(old_acc, old_acc + old_rej),
        "old_pending_share": _rate(old_pend, old_acc + old_rej + old_pend),
        "novel_accept_rate": _rate(novel_acc, novel),
        "novel": novel,
        "novel_accepted": novel_acc,
        "novel_escalated": sum(r["novel_escalated"] for r in ok),
        "proposed": sum(r["proposed"] for r in ok),
        "dropped_quote_guard": sum(r["dropped_quote_guard"] for r in ok),
        "duplicate_of_existing": sum(r["duplicate_of_existing"] for r in ok),
        "novel_accepted_per_doc": (novel_acc / len(ok)) if ok else 0.0,
        "novel_accepted_per_usd": (novel_acc / usd) if usd else 0.0,
        "usd_total": usd,
    }


def verdict_for(agg: dict) -> str:
    """"green" | "amber" | "red" per the design's decision rule.

    Stated before the run so a disappointing result cannot be re-read as an
    encouraging one. Green additionally requires an old rate to compare
    against; with nothing to beat, the honest answer is amber.
    """
    per_doc = agg.get("novel_accepted_per_doc") or 0.0
    new_rate = agg.get("novel_accept_rate")
    old_rate = agg.get("old_accept_rate")
    if per_doc < RED_PER_DOC:
        return "red"
    if (per_doc >= GREEN_PER_DOC and new_rate is not None
            and old_rate is not None and new_rate >= old_rate):
        return "green"
    return "amber"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 21 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): aggregation and the pre-stated decision rule

Both accept rates exclude still-pending cards; errored documents are
excluded from rates and counted. green/amber/red thresholds match the
design doc, including amber when there is no old rate to beat."
```

---

## Task 6: Safety guards — chain untouched, no cycle running, budget ceiling

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'ChainUnchanged'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py`:

```python
import hashlib

PILOT_CEILING_USD = 3.0


class ChainUnchanged:
    """Context manager asserting the chain file is byte-identical afterwards.

    This harness must never write to the chain. Rather than trusting that,
    the run proves it: sha256 and size before, compared after. A failure here
    means a code path opened the Registry for writing and is a defect, not a
    warning.
    """

    def __init__(self, chain_path: Path):
        self.chain_path = Path(chain_path)

    def _fingerprint(self) -> tuple[int, str]:
        data = self.chain_path.read_bytes()
        return len(data), hashlib.sha256(data).hexdigest()

    def __enter__(self) -> "ChainUnchanged":
        self._before = self._fingerprint()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None and self._fingerprint() != self._before:
            raise RuntimeError(
                f"chain changed during a shadow run: {self.chain_path} - "
                "this harness must never write to the chain")
        return False


def ensure_no_cycle_running(logs_dir: Path) -> None:
    """Refuse to start while a pipeline cycle holds the chain lock.

    Not for the chain's sake - we never write it - but because a cycle's own
    spend calibration reads ledger deltas around its stages, and this run's
    charges would land inside that window and distort the loop's allowance.
    """
    lock = Path(logs_dir) / "chain.lock"
    if lock.exists():
        raise RuntimeError(
            f"a cycle is running ({lock} is held) - rerun when it is free; "
            "shadow spend inside a cycle would distort the loop's calibration")


def spend_guard(start_usd: float, ceiling: float = PILOT_CEILING_USD):
    """-> predicate(current_usd) that is False once the pilot has spent
    `ceiling`. The ceiling is enforced, never assumed."""
    def may_continue(current_usd: float) -> bool:
        return (current_usd - start_usd) < ceiling
    return may_continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 27 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): shadow-run guards - chain hash, cycle lock, spend ceiling

The no-chain-writes promise is proved by sha256 before and after rather
than trusted; the run refuses to start inside a cycle window because its
spend would distort the loop's allowance calibration."
```

---

## Task 7: Per-document shadow run (wiring the pieces, model calls injected)

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'shadow_one_document'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py`:

```python
def _blank_result(doc: dict) -> dict:
    return {
        "key": doc["key"], "url": doc.get("url"), "title": doc.get("title"),
        "source_type": doc.get("source_type"), "band": doc.get("band"),
        "old_cards": doc.get("old_cards", 0),
        "old_accepted": doc.get("old_accepted", 0),
        "old_rejected": doc.get("old_rejected", 0),
        "old_pending": doc.get("old_pending", 0),
        "proposed": 0, "dropped_quote_guard": 0, "duplicate_of_existing": 0,
        "novel": 0, "novel_accepted": 0, "novel_escalated": 0,
        "escalation_reasons": [], "usd": 0.0, "error": None,
    }


def shadow_one_document(doc: dict, *, load_text, extract, panel,
                        known_fingerprints: set[str], meter,
                        chunker) -> dict:
    """One document end to end, returning a DocResult. Never raises.

    `extract(chunk_label, chunk_text) -> [raw claim]` and
    `panel(cards) -> build_decisions payload` are injected so the wiring is
    testable without a model. Spend is measured from the meter's own ledger
    around the work, not estimated.

    The panel is skipped entirely when nothing survives classification: a
    panel with no cards costs money and answers nothing.
    """
    res = _blank_result(doc)
    before_usd = meter.month_spend()
    try:
        text, how = load_text(doc)
        res["text_source"] = how
        claims: list[dict] = []
        for label, chunk in chunker(text):
            claims.extend(extract(label, chunk))
        split = classify_claims(claims, text=text,
                                known_fingerprints=known_fingerprints)
        res.update(proposed=split["proposed"],
                   dropped_quote_guard=split["dropped_quote_guard"],
                   duplicate_of_existing=split["duplicate_of_existing"],
                   novel=len(split["novel"]))
        if split["novel"]:
            cards = {f"shadow-{i}": {"claim": c.get("claim", ""),
                                     "quote": c.get("quote", ""),
                                     "source": {"title": doc.get("title")}}
                     for i, c in enumerate(split["novel"])}
            out = panel(cards)
            res["novel_accepted"] = sum(
                1 for v in out.get("decisions", {}).values() if v[0] == "accepted")
            res["novel_escalated"] = len(out.get("escalated", {}))
            res["escalation_reasons"] = [
                r for reasons in out.get("dissent_reasons", {}).values()
                for r in reasons]
    except Exception as exc:
        res["error"] = str(exc)[:200]
    res["usd"] = round(meter.month_spend() - before_usd, 6)
    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 31 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): per-document shadow run

Load, chunk, extract, classify, then judge only the novel claims. Spend is
measured from the ledger rather than estimated, a load failure is recorded
rather than raised, and a document with nothing novel never pays for a panel."
```

---

## Task 8: Report writer

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
import json

from tools.reextract_shadow import write_report


def test_write_report_writes_markdown_and_a_json_sidecar(tmp_path):
    results = [_result(key="https://a.example/x", title="Doc A", novel=4,
                       novel_accepted=3, novel_escalated=1, proposed=6,
                       dropped_quote_guard=1, duplicate_of_existing=1,
                       old_accepted=5, old_rejected=1, usd=0.11,
                       escalation_reasons=["claim exceeds the quote"])]
    agg = aggregate(results)
    md, js = write_report(tmp_path, results=results, agg=agg,
                          seed=1234, model="claude-opus-5",
                          date_utc="2026-09-07")

    assert md.name == "2026-09-07-reextract-shadow.md"
    assert js.name == "2026-09-07-reextract-shadow.json"
    body = md.read_text(encoding="utf-8")
    assert "seed 1234" in body
    assert "claude-opus-5" in body
    assert "Doc A" in body
    assert "claim exceeds the quote" in body
    assert verdict_for(agg).upper() in body

    payload = json.loads(js.read_text(encoding="utf-8"))
    assert payload["seed"] == 1234
    assert payload["aggregate"]["novel_accepted"] == 3
    assert payload["documents"][0]["title"] == "Doc A"


def test_write_report_states_the_paraphrase_limitation(tmp_path):
    agg = aggregate([])
    md, _ = write_report(tmp_path, results=[], agg=agg, seed=1, model="m",
                         date_utc="2026-09-07")
    assert "paraphrase" in md.read_text(encoding="utf-8").lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'write_report'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py`:

```python
import json


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def write_report(out_dir: Path, *, results: list[dict], agg: dict, seed: int,
                 model: str, date_utc: str) -> tuple[Path, Path]:
    """Write `<date>-reextract-shadow.md` plus a JSON sidecar; return both.

    The markdown is Coen's read; the JSON is the machine-readable record a
    later full run compares against.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    verdict = verdict_for(agg)

    lines = [
        f"# Re-extract shadow run {date_utc}",
        "",
        f"**Verdict: {verdict.upper()}** "
        f"({agg['novel_accepted_per_doc']:.2f} novel accepted per document; "
        f"green needs >= {GREEN_PER_DOC}, red is < {RED_PER_DOC})",
        "",
        f"Sample: {agg['documents']} document(s) at seed {seed}, "
        f"model {model}, {agg['errors']} errored. "
        f"Spend USD {agg['usd_total']:.2f}.",
        "",
        "| measure | old corpus | this run |",
        "|---|---:|---:|",
        f"| accept rate (excl. pending) | {_fmt_rate(agg['old_accept_rate'])} "
        f"| {_fmt_rate(agg['novel_accept_rate'])} |",
        f"| still pending (old only) | {_fmt_rate(agg['old_pending_share'])} | - |",
        "",
        f"Claims proposed {agg['proposed']}, dropped by the honesty guard "
        f"{agg['dropped_quote_guard']}, duplicates of held cards "
        f"{agg['duplicate_of_existing']}, novel {agg['novel']} "
        f"(accepted {agg['novel_accepted']}, escalated {agg['novel_escalated']}).",
        "",
        "**Limitation:** duplicate detection is `claim_fingerprint`, which is "
        "normalised but not semantic, so a paraphrase of a held claim counts "
        "as novel and reaches the panel. Read the novel figure with that in "
        "mind; the escalation reasons below are where paraphrases surface.",
        "",
        "## Per document",
        "",
        "| document | band | old A/R/P | proposed | guard | dupe | novel | acc | esc | USD |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        title = (r.get("title") or r.get("key") or "")[:44]
        note = f" — ERROR: {r['error']}" if r.get("error") else ""
        lines.append(
            f"| {title}{note} | {r.get('band','')} | "
            f"{r['old_accepted']}/{r['old_rejected']}/{r['old_pending']} | "
            f"{r['proposed']} | {r['dropped_quote_guard']} | "
            f"{r['duplicate_of_existing']} | {r['novel']} | "
            f"{r['novel_accepted']} | {r['novel_escalated']} | {r['usd']:.3f} |")

    reasons = [x for r in results for x in r.get("escalation_reasons", [])]
    if reasons:
        lines += ["", "## Escalation reasons (every dissenting reviewer)", ""]
        lines += [f"- {reason}" for reason in reasons]

    md_path = out_dir / f"{date_utc}-reextract-shadow.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    js_path = out_dir / f"{date_utc}-reextract-shadow.json"
    js_path.write_text(json.dumps(
        {"date_utc": date_utc, "seed": seed, "model": model,
         "verdict": verdict, "aggregate": agg, "documents": results},
        indent=2), encoding="utf-8")
    return md_path, js_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 33 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): shadow-run report, markdown plus JSON sidecar

Leads with the pre-stated verdict, compares both accept rates with pending
excluded and shown separately, and states the paraphrase limitation in the
report itself so the novel figure is never read as more novelty than it is."
```

---

## Task 9: CLI wiring and a live dry run

**Files:**
- Modify: `research-layer/tools/reextract_shadow.py`
- Test: `research-layer/tools/test_reextract_shadow.py`

- [ ] **Step 1: Write the failing test**

Append to `research-layer/tools/test_reextract_shadow.py`:

```python
from tools.reextract_shadow import run


def test_run_dry_run_selects_documents_and_spends_nothing(tmp_path, capsys, monkeypatch):
    chain = tmp_path / "registry_log.jsonl"
    chain.write_text("", encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()

    cards = {f"c{i}": _card(f"https://a.example/{i}", "accepted", f"claim {i}")
             for i in range(6)}
    cards.update({f"p{i}": _card(f"https://b.example/{i}", "pending", f"stuck {i}")
                  for i in range(6)})
    monkeypatch.setattr("tools.reextract_shadow._load_cards", lambda path: cards)

    rc = run(["--dry-run", "--layer", str(tmp_path), "--seed", "42", "--sample", "4"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "seed 42" in out
    assert out.count("https://") >= 4          # the chosen documents are listed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'run'`

- [ ] **Step 3: Write minimal implementation**

Add to `research-layer/tools/reextract_shadow.py`:

```python
import argparse
import sys
from datetime import datetime, timezone

DEFAULT_SEED = 20260906


def _load_cards(registry_path: Path) -> dict[str, dict]:
    """Read-only view of the chain's cards. Split out so tests can stub it."""
    from pipeline.registry import Registry
    return Registry(registry_path).cards()


def _live_extract_and_panel(model: str):
    """(extract, panel, meter) bound to the real client, key and ledger.

    Mirrors triage_batch._client_and_meter: the sc-reader key lives in the
    reader's .env, not the ambient environment.
    """
    import anthropic

    from pipeline.budget import BudgetMeter, PIPELINE_CAP_USD
    from pipeline.reader import extract_claims
    from pipeline.scanner import DEFAULT_READER_ENV, _load_api_key
    from pipeline.triage_batch import build_decisions

    _load_api_key(DEFAULT_READER_ENV)
    client = anthropic.Anthropic()
    logs = Path(__file__).resolve().parent.parent / "logs"
    meter = BudgetMeter(logs / "budget_ledger.jsonl",
                        monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")

    def extract(label: str, chunk: str) -> list[dict]:
        return extract_claims(client, model, label, chunk)

    def panel(cards: dict) -> dict:
        # `accepted` is deliberately EMPTY: build_decisions would otherwise
        # re-run its own pending-vs-accepted duplicate check, but
        # classify_claims has already deduped against every card in the chain
        # on a wider rule. Passing the real accepted set here would double-count
        # duplicates and hide them from the novel figure.
        return build_decisions(client, model, cards, {}, meter)

    return extract, panel, meter


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--layer", type=Path,
                    default=Path(__file__).resolve().parent.parent,
                    help="research-layer root (holds registry_log.jsonl and logs/)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--sample", type=int, default=SAMPLE_SIZE)
    ap.add_argument("--model", default=None,
                    help="defaults to pipeline.reader.DEFAULT_MODEL")
    ap.add_argument("--dry-run", action="store_true",
                    help="select and report the sample; no model calls, no spend")
    args = ap.parse_args(argv)

    from pipeline.reader import DEFAULT_MODEL
    model = args.model or DEFAULT_MODEL
    chain = args.layer / "registry_log.jsonl"
    logs = args.layer / "logs"

    cards = _load_cards(chain)          # parsed once: the chain is ~33k entries
    corpus = build_corpus(cards)
    picked = sample_documents(corpus, n=args.sample, seed=args.seed)

    print(f"corpus {len(corpus)} documents; sampled {len(picked)} at seed "
          f"{args.seed}; model {model}")
    for doc in picked:
        print(f"  [{doc['band']:7}] {doc['old_accepted']}A/{doc['old_rejected']}R/"
              f"{doc['old_pending']}P  {doc.get('url') or doc['key']}")
    if args.dry_run:
        print("\nDRY RUN — no model calls, no spend, nothing written.")
        return 0

    ensure_no_cycle_running(logs)
    extract, panel, meter = _live_extract_and_panel(model)
    known = {claim_fingerprint(c.get("claim", "")) for c in cards.values()}
    may_continue = spend_guard(meter.month_spend())

    from pipeline.feeds import fetch_url, html_to_text
    from pipeline.reader import chunk_text, read_source_text
    local = LocalPdfMap.from_afml_dir()

    def load_text(doc: dict):
        return load_document_text(doc, local=local, read_pdf=read_source_text,
                                  fetch=fetch_url, html_to_text=html_to_text)

    results = []
    with ChainUnchanged(chain):
        for doc in picked:
            if not may_continue(meter.month_spend()):
                print(f"STOPPED at the USD {PILOT_CEILING_USD:.0f} pilot ceiling "
                      f"after {len(results)} document(s)")
                break
            res = shadow_one_document(doc, load_text=load_text, extract=extract,
                                      panel=panel, known_fingerprints=known,
                                      meter=meter, chunker=chunk_text)
            results.append(res)
            print(f"  {res['key'][:60]}: proposed {res['proposed']}, "
                  f"novel {res['novel']}, accepted {res['novel_accepted']}, "
                  f"USD {res['usd']:.3f}"
                  + (f"  ERROR {res['error']}" if res["error"] else ""))

    agg = aggregate(results)
    date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md, js = write_report(args.layer / "docs" / "runs", results=results, agg=agg,
                          seed=args.seed, model=model, date_utc=date_utc)
    print(f"\nVERDICT {verdict_for(agg).upper()} — "
          f"{agg['novel_accepted_per_doc']:.2f} novel accepted per document, "
          f"USD {agg['usd_total']:.2f}")
    print(f"report -> {md}\njson   -> {js}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools/test_reextract_shadow.py -q -p no:cacheprovider`
Expected: PASS, 34 passed

- [ ] **Step 5: Verify the whole suite still passes**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m pytest tools pipeline -q -p no:cacheprovider`
Expected: 34 tools tests pass; the pipeline suite is unchanged apart from the four known data-pinned failures (`test_composer_equity::test_live_registry_has_exactly_one_accepted_proxy_match`, `test_gen4::test_all_80_existing_fingerprints_unchanged`, `test_gen4::test_the_diagnostic_writes_nothing`, `test_livegate::test_the_live_chain_dry_run_holds_everything_and_writes_nothing`).

- [ ] **Step 6: Live dry run against the real chain — no spend**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && python -m tools.reextract_shadow --dry-run`
Expected: prints ~200 documents in the corpus, ten sampled at seed 20260906, five in each band, then `DRY RUN — no model calls, no spend, nothing written.`

- [ ] **Step 7: Commit**

```bash
git add research-layer/tools/reextract_shadow.py research-layer/tools/test_reextract_shadow.py
git commit -m "feat(tools): shadow-run CLI with a spend-free dry run

--dry-run selects and prints the sample without a model call, so the
document choice can be reviewed before any money is spent. The live path
loads the reader key the way every other entry point does, holds the chain
hash open across the run, and stops at the pilot ceiling."
```

---

## Task 10: Documentation

**Files:**
- Modify: `research-layer/CLAUDE.md`

- [ ] **Step 1: Add the section**

Insert immediately before the `## Triage cost controls (loop stage 4a)` heading in `research-layer/CLAUDE.md`:

```markdown
## Re-extract shadow run (tools/, NOT a pipeline stage)

`python -m tools.reextract_shadow [--dry-run] [--seed N] [--sample N]` measures
whether today's extractor beats August's on documents we already own. Design:
`docs/2026-09-06-reextract-shadow-design.md`.

- **It lives in `tools/`, never `pipeline/`, and must never be scheduled.** It is a
  measurement, not a stage.
- **It never writes to the chain** and proves it: the chain's sha256 and size are
  compared before and after, and a mismatch raises rather than warns.
- **It refuses to run while a cycle holds `logs/chain.lock`** - not for the chain's
  sake but because a cycle's allowance calibration reads ledger deltas around its own
  stages, and shadow spend inside that window would distort the loop's triage limit.
- **Pilot ceiling USD 3**, enforced through the meter. Its spend is real and lands in
  the shared monthly pipeline cap.
- `--dry-run` selects and prints the sample with no model call and no spend. Always
  run it first: the sample is seeded, so the dry run shows exactly what the live run
  will read.
- The verdict thresholds (green >= 2 novel accepted per document, red < 0.5) are
  stated in the design doc BEFORE the run, so a disappointing result cannot be
  re-read as an encouraging one.
- **Known limitation, stated in every report:** duplicate detection is
  `claim_fingerprint`, normalised but not semantic, so a paraphrase of a held claim
  counts as novel and reaches the panel.
```

- [ ] **Step 2: Verify the file still reads correctly**

Run: `cd E:/Users/Coen/Claude/stewart-forward-test/research-layer && grep -n "^## " CLAUDE.md | head -20`
Expected: the new `## Re-extract shadow run` heading appears immediately before `## Triage cost controls (loop stage 4a)`.

- [ ] **Step 3: Commit**

```bash
git add research-layer/CLAUDE.md
git commit -m "docs(research-layer): re-extract shadow run, and why it is not a stage"
```

---

## Definition of done

- [ ] 34 tests in `tools/test_reextract_shadow.py` pass, and the pipeline suite is unchanged apart from the four known data-pinned failures.
- [ ] `python -m tools.reextract_shadow --dry-run` prints a ten-document sample split five and five across the bands, with no spend.
- [ ] `research-layer/CLAUDE.md` carries the section above.
- [ ] The live run is **not** part of this plan. It costs real money against the shared September cap and needs Coen's go-ahead, ideally in October when the cap resets, or now if he accepts the ~USD 1-3 against the remaining headroom.
