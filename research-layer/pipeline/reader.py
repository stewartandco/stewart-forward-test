"""Reader agent: extract quote-grounded research cards from a source document.

Usage:
    python -m pipeline.reader paper.txt --title "..." --source-type paper \
        --author "A. Author" --year 2021 --url https://... \
        [--registry registry_log.jsonl] [--dry-run]

The extraction model proposes claims; this module enforces the honesty guard
(every quote must appear verbatim in the source, modulo whitespace), builds
full schema-conformant cards, and chains them into the registry as
card_registered entries with review.status="pending" for human triage.
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

from .common import content_id, quote_in_source
from .registry import Registry

PIPELINE_VERSION = "r1.0.0"
DEFAULT_MODEL = "claude-opus-5"
MAX_SINGLE_PASS_CHARS = 150_000
CHUNK_CHARS = 60_000
CHUNK_OVERLAP = 2_000

# Structured-output schema for the extraction call. Deliberately simpler than
# the full card schema: ids, source metadata, and review state are added by
# code, not the model. (Structured outputs don't support min/max constraints;
# full validation happens against schemas/research_card.schema.json after.)
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "quote": {"type": "string"},
                    "locator": {"type": "string"},
                    "asset_classes": {
                        "type": "array",
                        "items": {"enum": ["futures", "equities", "crypto", "fx",
                                            "options", "rates", "commodities", "cross"]},
                    },
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "horizon": {"enum": ["intraday", "daily", "weekly", "monthly", "multi_month"]},
                    "testability_score": {"type": "number"},
                    "data_required": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": ["string", "null"]},
                },
                "required": ["claim", "quote", "locator", "asset_classes", "topics",
                              "horizon", "testability_score", "data_required", "notes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are the Reader agent in Stewart & Co.'s quantitative research pipeline. You
extract testable claims from trading/finance research so they can be turned
into strategy hypotheses and backtested.

For each distinct, testable claim in the document, produce one entry:
- claim: one self-contained sentence stating the testable proposition. Include
  the market/asset class and horizon in the sentence where the source states them.
- quote: a VERBATIM passage (1-3 sentences) copied character-for-character from
  the document that supports the claim. Never paraphrase, never stitch together
  words from different places. If you cannot find a verbatim supporting passage,
  do not emit the claim at all. Quotes are mechanically checked against the
  source; a quote that is not an exact substring gets the card rejected.
- locator: where the quote sits (page, section heading, or chunk position as given).
- testability_score: 0-1, how directly this could be turned into a backtestable
  rule with ordinary market data (1.0 = precise rule with named data; 0.2 = vague
  qualitative observation).
- data_required: datasets needed to test it, as specific as the source allows.

Only extract claims relevant to trading, investing, or market behavior. Skip
boilerplate, literature-review restatements of other papers' claims (unless the
document endorses them with evidence), and untestable opinions. Fewer
high-quality cards beat many weak ones."""


def read_source_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise SystemExit("PDF input needs pypdf: pip install pypdf") from exc
        reader = PdfReader(str(path))
        return "\n\n".join(
            f"[page {i + 1}]\n" + (page.extract_text() or "")
            for i, page in enumerate(reader.pages)
        )
    return path.read_text(encoding="utf-8", errors="replace")


def chunk_text(text: str) -> list[tuple[str, str]]:
    """Return [(chunk_label, chunk_text)]; single chunk for small docs."""
    if len(text) <= MAX_SINGLE_PASS_CHARS:
        return [("full document", text)]
    chunks = []
    start, n = 0, 1
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        chunks.append((f"chunk {n} (chars {start}-{end})", text[start:end]))
        start = end - CHUNK_OVERLAP if end < len(text) else end
        n += 1
    return chunks


def extract_claims(client, model: str, chunk_label: str, chunk: str) -> list[dict]:
    """One structured-output extraction call. Streaming so long inputs don't
    hit HTTP timeouts; thinking is on by default on this model."""
    with client.messages.stream(
        model=model,
        max_tokens=32_000,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                f"Document section: {chunk_label}\n\n"
                f"<document>\n{chunk}\n</document>\n\n"
                "Extract the testable claims per your instructions."
            ),
        }],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        print(f"  refusal on {chunk_label}"
              + (f" (category: {message.stop_details.category})" if message.stop_details else ""),
              file=sys.stderr)
        return []
    text = next(b.text for b in message.content if b.type == "text")
    return json.loads(text)["claims"]


def build_card(raw: dict, source_meta: dict, model: str, run_id: str) -> dict:
    card = {
        "card_id": None,
        "version": 1,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {**source_meta, "locator": raw["locator"]},
        "claim": raw["claim"],
        "quote": raw["quote"],
        "tags": {
            "asset_classes": raw["asset_classes"] or ["cross"],
            "topics": raw["topics"] or ["uncategorized"],
            "horizon": raw["horizon"],
        },
        "testability": {
            "score": max(0.0, min(1.0, raw["testability_score"])),
            "data_required": raw["data_required"],
            "notes": raw["notes"],
        },
        "links": [],
        "relation": {},
        "extraction": {
            "agent": "reader",
            "model": model,
            "pipeline_version": PIPELINE_VERSION,
            "run_id": run_id,
        },
        "review": {"status": "pending", "reviewed_by": None,
                   "reviewed_utc": None, "reject_reason": None},
    }
    card["card_id"] = content_id(card, "card_id")
    return card


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", type=Path, help="text/markdown/PDF file to read")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source-type", required=True,
                    choices=["paper", "book", "blog", "forum", "filing", "dataset_doc"])
    ap.add_argument("--credibility", default="practitioner",
                    choices=["peer_reviewed", "practitioner", "gray"])
    ap.add_argument("--author", action="append", default=[])
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--doi", default=None)
    ap.add_argument("--isbn", default=None)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    ap.add_argument("--dry-run", action="store_true",
                    help="print cards, do not write to the registry")
    args = ap.parse_args(argv)

    if not any([args.url, args.doi, args.isbn]):
        ap.error("provide at least one of --url / --doi / --isbn")

    import anthropic
    client = anthropic.Anthropic()

    source_meta = {
        "type": args.source_type, "title": args.title, "authors": args.author,
        "year": args.year, "url": args.url, "doi": args.doi, "isbn": args.isbn,
        "credibility_tier": args.credibility,
    }
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-manual"
    text = read_source_text(args.source)
    registry = Registry(args.registry)
    known_claims = {c["claim"] for c in registry.cards().values()}

    kept, dropped_quote, dropped_dupe = [], 0, 0
    for label, chunk in chunk_text(text):
        print(f"reading {label} ({len(chunk):,} chars)...")
        for raw in extract_claims(client, args.model, label, chunk):
            if not quote_in_source(raw["quote"], text):
                dropped_quote += 1
                print(f"  DROPPED (quote not found verbatim): {raw['claim'][:80]}")
                continue
            if raw["claim"] in known_claims:
                dropped_dupe += 1
                continue
            known_claims.add(raw["claim"])
            kept.append(build_card(raw, source_meta, args.model, run_id))

    for card in kept:
        if args.dry_run:
            print(json.dumps(card, indent=2, ensure_ascii=False))
        else:
            registry.register_card(card)
            print(f"  registered {card['card_id']}  {card['claim'][:80]}")

    print(f"\n{len(kept)} cards {'extracted' if args.dry_run else 'registered'}, "
          f"{dropped_quote} dropped by honesty guard, {dropped_dupe} duplicates skipped.")
    if not args.dry_run and kept:
        print("Next: python -m pipeline.triage to review pending cards.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
