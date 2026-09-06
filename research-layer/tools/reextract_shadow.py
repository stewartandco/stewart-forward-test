"""Re-extract shadow run: does today's extractor beat August's, on documents
we already own?

MEASUREMENT ONLY. This module never writes to the chain, never writes
loop_state, and lives outside `pipeline/` so it can never be mistaken for a
pipeline stage or picked up by the loop. Design:
docs/2026-09-06-reextract-shadow-design.md.
"""
from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path

from pipeline.common import quote_in_source
from pipeline.triage_batch import claim_fingerprint


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

        Missing directory is not an error, and a stem that is not exactly
        ssrn-<digits> is skipped rather than guessed: the document then
        falls through to the fetch path and reports its own failure.
        """
        mapping: dict[str, Path] = {}
        if not directory.is_dir():
            return cls(mapping)
        for pdf in directory.glob("ssrn-*.pdf"):
            # Anchored: `ssrn-3270329 (1).pdf` (a Windows duplicate) must be
            # SKIPPED, not squashed into a wrong abstract id.
            match = re.fullmatch(r"ssrn-(\d+)", pdf.stem)
            if match:
                mapping[f"https://ssrn.com/abstract={match.group(1)}"] = pdf
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
    if status == 0:
        # feeds.fetch_url reports a network error as status 0 with the
        # message in the body slot; "http 0" would say nothing.
        raise RuntimeError(f"network error: {body[:160]}")
    if status != 200:
        raise RuntimeError(f"http {status}")
    looks_html = body.lstrip()[:400].lower().startswith(("<!doctype", "<html", "<"))
    return (html_to_text(body) if looks_html else body), "fetched"


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
        quote = raw.get("quote") or ""
        # An empty quote is a guard FAILURE, not a pass: "" is a substring of
        # every text, so quote_in_source alone would wave an unsupported
        # claim straight into `novel`.
        if not quote or not quote_in_source(quote, text):
            out["dropped_quote_guard"] += 1
            continue
        fp = claim_fingerprint(raw.get("claim", ""))
        if fp in seen:
            out["duplicate_of_existing"] += 1
            continue
        seen.add(fp)
        out["novel"].append(raw)
    return out


GREEN_PER_DOC = 2.0
RED_PER_DOC = 0.5


def _rate(hits: int, total: int) -> float | None:
    return (hits / total) if total else None


def aggregate(results: list[dict]) -> dict:
    """Roll per-document results into the spec's headline comparators.

    Errored documents are excluded from every rate and counted separately -
    a document we could not fetch says nothing about the extractor.
    Its SPEND still counts: usd_total and the per-dollar yield are over every
    document, errored or not, because money spent is spent.

    Both accept rates are accepted / (accepted + rejected): a pending card is
    undecided, so counting it as a failure would understate the old corpus.
    The pending share is reported alongside so the exclusion is visible.

    The novel accept rate is accepted / (accepted + escalated) - JUDGED cards
    only. A card the panel never reached (a budget stop) is unjudged,
    reported separately, and never counted as a failure.
    """
    ok = [r for r in results if not r.get("error")]
    old_acc = sum(r["old_accepted"] for r in ok)
    old_rej = sum(r["old_rejected"] for r in ok)
    old_pend = sum(r["old_pending"] for r in ok)
    novel = sum(r["novel"] for r in ok)
    novel_acc = sum(r["novel_accepted"] for r in ok)
    novel_esc = sum(r["novel_escalated"] for r in ok)
    novel_unj = sum(r.get("novel_unjudged", 0) for r in ok)
    usd = sum(r.get("usd", 0.0) for r in results)
    return {
        "documents": len(ok),
        "errors": len(results) - len(ok),
        "old_accept_rate": _rate(old_acc, old_acc + old_rej),
        "old_pending_share": _rate(old_pend, old_acc + old_rej + old_pend),
        "novel_accept_rate": _rate(novel_acc, novel_acc + novel_esc),
        "novel": novel,
        "novel_accepted": novel_acc,
        "novel_escalated": novel_esc,
        "novel_unjudged": novel_unj,
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
        changed = self._fingerprint() != self._before
        if not changed:
            return False                      # clean exit, or propagate exc unchanged
        message = (
            f"chain changed during a shadow run: {self.chain_path}. Either this "
            "harness wrote to the chain (a defect - it must never) or a LEGITIMATE "
            "writer appended concurrently: the resident scanner's card batch, the "
            "quarantine daily, or a pipeline cycle, each under its own chain.lock. "
            "Diff the chain's tail before treating this as a harness defect.")
        if exc_type is not None:
            # The run crashed AND the chain moved: report both, chained, never
            # mask the original error.
            raise RuntimeError(message) from exc
        raise RuntimeError(message)


def ensure_no_cycle_running(logs_dir: Path) -> None:
    """Refuse to start while a pipeline cycle holds the chain lock.

    Not for the chain's sake - we never write it - but because a cycle's own
    spend calibration reads ledger deltas around its stages, and this run's
    charges would land inside that window and distort the loop's allowance.
    """
    lock = Path(logs_dir) / "chain.lock"
    if lock.exists():
        try:
            held_by = lock.read_text(encoding="utf-8").strip()[:200]
        except OSError:
            held_by = "(unreadable)"
        raise RuntimeError(
            f"a cycle is running ({lock} is held: {held_by}) - rerun when it is "
            "free; shadow spend inside a cycle would distort the loop's "
            "calibration. If the holder pid is dead this is a STALE lock - "
            "check it the way pipeline.chainlock does before clearing anything.")


def spend_guard(start_usd: float, ceiling: float = PILOT_CEILING_USD):
    """-> predicate(current_usd) that is False once spend at the START of a
    document would reach `ceiling`. Checked before each document, so the
    true total can overshoot by at most one document's cost - fine for a
    pilot at cents per document, but not an exact stop. Enforced, never
    assumed."""
    def may_continue(current_usd: float) -> bool:
        return (current_usd - start_usd) < ceiling
    return may_continue


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
        "novel_unjudged": 0,
        "escalation_reasons": [], "usd": 0.0, "error": None,
        "text_source": None,
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
            # Production's panel stops mid-batch when the meter refuses; the
            # cards it never reached are UNJUDGED, not failed, and must never
            # be folded into the accept rate.
            res["novel_unjudged"] = (len(cards) - res["novel_accepted"]
                                     - res["novel_escalated"])
            if out.get("stopped"):
                res["error"] = f"panel stopped: {out['stopped']}"
    except Exception as exc:
        res["error"] = str(exc)[:200]
    res["usd"] = round(meter.month_spend() - before_usd, 6)
    return res
