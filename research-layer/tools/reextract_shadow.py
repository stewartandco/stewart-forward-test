"""Re-extract shadow run: does today's extractor beat August's, on documents
we already own?

MEASUREMENT ONLY. This module never writes to the chain, never writes
loop_state, and lives outside `pipeline/` so it can never be mistaken for a
pipeline stage or picked up by the loop. Design:
docs/2026-09-06-reextract-shadow-design.md.
"""
from __future__ import annotations

import random


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
