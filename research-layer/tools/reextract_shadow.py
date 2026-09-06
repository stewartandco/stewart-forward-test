"""Re-extract shadow run: does today's extractor beat August's, on documents
we already own?

MEASUREMENT ONLY. This module never writes to the chain, never writes
loop_state, and lives outside `pipeline/` so it can never be mistaken for a
pipeline stage or picked up by the loop. Design:
docs/2026-09-06-reextract-shadow-design.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import re
import sys
from datetime import datetime, timezone
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

    A document whose panel STOPPED (budget) is not an error: it was loaded,
    extracted and partly judged, so its counts stay in and it is tallied
    under "stopped".
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
        "stopped": sum(1 for r in ok if r.get("stopped")),
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
    """"green" | "amber" | "red" | "no_result" per the design's decision rule.

    Stated before the run so a disappointing result cannot be re-read as an
    encouraging one. Green additionally requires an old rate to compare
    against; with nothing to beat, the honest answer is amber. A run that
    completed zero documents has NO verdict: a red on an empty sample would
    read as "close the question" when the question was never asked.
    """
    if "documents" in agg and agg["documents"] == 0:
        return "no_result"           # nothing was measured; no verdict is honest
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
        "stopped": None,
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
            # A stop is NOT an error: this document was loaded, extracted and
            # partly judged. Its counts stay in the aggregate; `stopped`
            # records why the panel did not finish.
            res["stopped"] = out.get("stopped") or None
    except Exception as exc:
        res["error"] = str(exc)[:200]
    res["usd"] = round(meter.month_spend() - before_usd, 6)
    return res


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _cell(text) -> str:
    """One markdown table cell: pipes and newlines would break the row."""
    return str(text or "").replace("|", "/").replace("\n", " ").replace("\r", " ")


def write_report(out_dir: Path, *, results: list[dict], agg: dict, seed: int,
                 model: str, panel_model: str, date_utc: str,
                 chain_note: str | None = None) -> tuple[Path, Path]:
    """Write `<date>-reextract-shadow-seed<seed>.md` plus a JSON sidecar;
    return both.

    The markdown is Coen's read; the JSON is the machine-readable record a
    later full run compares against. The Python version is recorded beside
    the seed because `random.shuffle` is only reproducible for a seed within
    one Python version. The seed is in the filename so a same-day re-run at a
    different seed never overwrites the earlier report. `chain_note`, when
    set, is a `ChainUnchanged` finding: the run still completed and is still
    reported, but the chain moved under it and that is a fact for the read,
    never a reason to lose the paid work.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    verdict = verdict_for(agg)
    py = platform.python_version()

    lines = [
        f"# Re-extract shadow run {date_utc}",
        "",
        (f"**Verdict: NO RESULT** ({agg['documents']} document(s) completed - "
         f"nothing was measured; {agg['errors']} errored)"
         if verdict == "no_result" else
         f"**Verdict: {verdict.upper()}** "
         f"({agg['novel_accepted_per_doc']:.2f} novel accepted per document; "
         f"green needs >= {GREEN_PER_DOC}, red is < {RED_PER_DOC})"),
    ]
    if chain_note:
        lines += ["", f"**CHAIN CHANGED DURING THE RUN:** {chain_note}"]
    lines += [
        "",
        f"Sample: {agg['documents']} document(s) at seed {seed}, python {py}, "
        f"extractor {model}, panel {panel_model}; {agg['errors']} errored, "
        f"{agg['stopped']} stopped at the budget. Spend USD {agg['usd_total']:.2f}.",
        "",
        "| measure | old corpus | this run |",
        "|---|---:|---:|",
        f"| accept rate (judged cards only) | {_fmt_rate(agg['old_accept_rate'])} "
        f"| {_fmt_rate(agg['novel_accept_rate'])} |",
        f"| undecided (old: pending share / new: unjudged count) | "
        f"{_fmt_rate(agg['old_pending_share'])} | {agg['novel_unjudged']} |",
        "",
        f"Claims proposed {agg['proposed']}, dropped by the honesty guard "
        f"{agg['dropped_quote_guard']}, duplicates of held cards "
        f"{agg['duplicate_of_existing']}, novel {agg['novel']} "
        f"(accepted {agg['novel_accepted']}, escalated {agg['novel_escalated']}, "
        f"unjudged {agg['novel_unjudged']}).",
        "",
        "**Limitation:** duplicate detection is `claim_fingerprint`, which is "
        "normalised but not semantic, so a paraphrase of a held claim counts "
        "as novel and reaches the panel. Read the novel figure with that in "
        "mind; the escalation reasons below are where paraphrases surface.",
        "",
        "## Per document",
        "",
        "| document | band | old A/R/P | proposed | guard | dupe | novel | acc | esc | unj | USD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        title = _cell(r.get("title") or r.get("key"))[:44]
        note = ""
        if r.get("error"):
            note = f" — ERROR: {r['error']}"
        elif r.get("stopped"):
            note = f" — STOPPED: {r['stopped']}"
        lines.append(
            f"| {title}{note} | {r.get('band','')} | "
            f"{r['old_accepted']}/{r['old_rejected']}/{r['old_pending']} | "
            f"{r['proposed']} | {r['dropped_quote_guard']} | "
            f"{r['duplicate_of_existing']} | {r['novel']} | "
            f"{r['novel_accepted']} | {r['novel_escalated']} | "
            f"{r.get('novel_unjudged', 0)} | {r['usd']:.3f} |")

    reasons = [x for r in results for x in r.get("escalation_reasons", [])]
    if reasons:
        lines += ["", "## Escalation reasons (every dissenting reviewer)", ""]
        lines += [f"- {_cell(reason)}" for reason in reasons]

    md_path = out_dir / f"{date_utc}-reextract-shadow-seed{seed}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    js_path = out_dir / f"{date_utc}-reextract-shadow-seed{seed}.json"
    js_path.write_text(json.dumps(
        {"date_utc": date_utc, "seed": seed, "python_version": py,
         "model": model, "panel_model": panel_model, "verdict": verdict,
         "chain_note": chain_note, "aggregate": agg, "documents": results},
        indent=2), encoding="utf-8")
    return md_path, js_path


DEFAULT_SEED = 20260906

# The chain's historical verdicts were made by triage_batch's default panel
# model (the loop passes none). The shadow panel MUST use the same model or
# the accept-rate comparison is opus-judged vs sonnet-judged - a different
# instrument, not a different extractor. Kept in sync by hand with the
# argparse default in pipeline/triage_batch.py run().
DEFAULT_PANEL_MODEL = "claude-sonnet-5"


def _load_cards(registry_path: Path) -> dict[str, dict]:
    """Read-only view of the chain's cards. Split out so tests can stub it."""
    from pipeline.registry import Registry
    return Registry(registry_path).cards()


def _live_extract_and_panel(model: str, panel_model: str, logs: Path):
    """(extract, panel, meter) bound to the real client, key and ledger.

    Mirrors triage_batch._client_and_meter: the sc-reader key lives in the
    reader's .env, not the ambient environment. `logs` is ALWAYS the caller's
    `--layer`-derived logs dir, never `__file__`-derived: in a worktree
    `logs/` is gitignored and absent, so a meter built from this module's own
    location would read a missing ledger as "zero spend this month" against
    the LIVE cap. The meter is scoped to agent "pipeline" and extraction is
    RECORDED under that same agent (the panel's review_card already
    hardcodes it), so one meter sees both and the resident scanner's
    "reader" rows cannot pollute the per-document spend delta.
    """
    import anthropic

    from pipeline.budget import BudgetMeter, PIPELINE_CAP_USD
    from pipeline.reader import extract_claims_usage
    from pipeline.scanner import DEFAULT_READER_ENV, _load_api_key
    from pipeline.triage_batch import build_decisions

    _load_api_key(DEFAULT_READER_ENV)
    client = anthropic.Anthropic()
    meter = BudgetMeter(logs / "budget_ledger.jsonl",
                        monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")

    def extract(label: str, chunk: str) -> list[dict]:
        if not meter.can_spend():
            raise RuntimeError("monthly pipeline cap reached - extraction refused")
        claims, usage = extract_claims_usage(client, model, label, chunk)
        meter.record_call(model, usage, purpose="reextract-shadow extract",
                          agent="pipeline")
        return claims

    def panel(cards: dict) -> dict:
        # `accepted` is deliberately EMPTY: build_decisions would otherwise
        # re-run its own pending-vs-accepted duplicate check, but
        # classify_claims has already deduped against every card in the chain
        # on a wider rule. Passing the real accepted set here would double-count
        # duplicates and hide them from the novel figure.
        # review_card hardcodes purpose="triage", so the panel's ledger rows
        # are not distinguishable from the loop's; only extraction carries
        # the shadow purpose.
        return build_decisions(client, panel_model, cards, {}, meter)

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
    ap.add_argument("--panel-model", default=DEFAULT_PANEL_MODEL,
                    help="model for the triage panel; defaults to what judged the chain")
    ap.add_argument("--dry-run", action="store_true",
                    help="select and report the sample; no model calls, no spend")
    args = ap.parse_args(argv)

    from pipeline.reader import DEFAULT_MODEL
    model = args.model or DEFAULT_MODEL
    panel_model = args.panel_model
    chain = args.layer / "registry_log.jsonl"
    logs = args.layer / "logs"

    cards = _load_cards(chain)          # parsed once: the chain is ~33k entries
    corpus = build_corpus(cards)
    picked = sample_documents(corpus, n=args.sample, seed=args.seed)

    print(f"corpus {len(corpus)} documents; sampled {len(picked)} at seed "
          f"{args.seed}; extractor {model}, panel {panel_model}")
    for doc in picked:
        print(f"  [{doc['band']:7}] {doc['old_accepted']}A/{doc['old_rejected']}R/"
              f"{doc['old_pending']}P  {doc.get('url') or doc['key']}")
    if args.dry_run:
        print("\nDRY RUN — no model calls, no spend, nothing written.")
        return 0

    # An absent ledger is never "zero spend this month" - it usually means
    # --layer is not the live research-layer (logs/ is gitignored, so a
    # worktree checkout has none). Checked before any other guard: touching
    # the cycle lock or the client on a wrong-tree run would be worse than a
    # refusal.
    ledger = logs / "budget_ledger.jsonl"
    if not ledger.exists():
        print(f"REFUSED: no ledger at {ledger} - is --layer the live research-layer? "
              "An absent ledger is never 'zero spend this month'.")
        return 2

    # Guards first, before any client or key is touched: a refusal must cost
    # nothing and read as a sentence, not a traceback.
    from pipeline.chainlock import ChainLock, ChainLockHeld
    try:
        ensure_no_cycle_running(logs)
    except RuntimeError as exc:
        print(f"REFUSED: {exc}")
        return 2
    lock = ChainLock(logs, holder="reextract-shadow",
                     purpose=f"shadow re-extract pilot, seed {args.seed}")
    try:
        lock.acquire()
    except ChainLockHeld as exc:
        print(f"REFUSED: {exc}")
        return 2

    results: list[dict] = []
    chain_note: str | None = None
    try:
        extract, panel, meter = _live_extract_and_panel(model, panel_model, logs)
        known = {claim_fingerprint(c.get("claim", "")) for c in cards.values()}
        ceiling_ok = spend_guard(meter.month_spend())

        def may_continue(current_usd: float) -> bool:
            return ceiling_ok(current_usd) and meter.can_spend()

        from pipeline.feeds import fetch_url, html_to_text
        from pipeline.reader import chunk_text, read_source_text
        local = LocalPdfMap.from_afml_dir()

        def load_text(doc: dict):
            return load_document_text(doc, local=local, read_pdf=read_source_text,
                                      fetch=fetch_url, html_to_text=html_to_text)

        try:
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
                          f"unjudged {res['novel_unjudged']}, USD {res['usd']:.3f}"
                          + (f"  ERROR {res['error']}" if res["error"] else "")
                          + (f"  STOPPED {res['stopped']}" if res.get("stopped") else ""))
        except RuntimeError as exc:
            if "chain changed" not in str(exc):
                raise
            chain_note = str(exc)        # reported, never swallowed, never fatal
        finally:
            agg = aggregate(results)
            date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            md, js = write_report(args.layer / "docs" / "runs", results=results, agg=agg,
                                  seed=args.seed, model=model, panel_model=panel_model,
                                  date_utc=date_utc, chain_note=chain_note)
            print(f"report -> {md}\njson   -> {js}")
    finally:
        lock.release()

    verdict = verdict_for(agg)
    print(f"\nVERDICT {verdict.upper().replace('_', ' ')} — extractor {model}, "
          f"panel {panel_model}; {agg['novel_accepted_per_doc']:.2f} novel "
          f"accepted per document, USD {agg['usd_total']:.2f}")
    return 3 if verdict == "no_result" else 0


if __name__ == "__main__":
    sys.exit(run())
