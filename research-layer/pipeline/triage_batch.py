"""Batch triage: pending cards -> a decision list, for D31.

Three independent reviewers judge each card for OVERREACH - whether the claim
asserts more than its quote supports. A card is auto-accepted ONLY on unanimous
accept. Any dissent leaves it pending for Coen's Tier 3 queue; dissent is the
signal, and a majority rule would let one reviewer spot overreach and be
outvoted, which defeats the gate's only purpose.

Duplicates against the accepted corpus are rejected mechanically.

This module produces decisions. It does NOT chain them - applying is
triage.apply_decisions, the path the interactive CLI already proved.

Provenance on every auto decision is `auto-d31`, NEVER `coen` (D31, mirroring
D27's honest-provenance rule).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .registry import Registry
from .triage import apply_decisions

REVIEWER = "auto-d31"
PANEL_SIZE = 3

_NOISE = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


def claim_fingerprint(claim: str) -> str:
    """Stable 16-hex fingerprint of a claim, normalised so that case,
    punctuation and whitespace differences collide.

    Deliberately NOT semantic: this catches restatements of the same sentence,
    not paraphrases. Paraphrased duplicates remain the panel's problem, and
    then Coen's - a false duplicate-reject is worse than a missed one, because
    the canonical card is the thing that stays citable.
    """
    norm = _SPACE.sub(" ", _NOISE.sub(" ", (claim or "").lower())).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def find_duplicates(pending: dict[str, dict],
                    accepted: dict[str, dict]) -> dict[str, str]:
    """{pending_card_id: accepted_card_id} for exact-fingerprint collisions."""
    by_fp = {claim_fingerprint(c.get("claim", "")): cid
             for cid, c in accepted.items()}
    out = {}
    for cid, card in pending.items():
        hit = by_fp.get(claim_fingerprint(card.get("claim", "")))
        if hit:
            out[cid] = hit
    return out


def panel_verdict(votes: list[dict]) -> tuple[str | None, str | None]:
    """Collapse reviewer votes into (decision, escalation_reason).

    Returns ("accepted", None) on unanimous accept from a full panel.
    Returns (None, reason) otherwise - the card stays PENDING for Coen.

    The panel may never auto-reject. It is trusted to wave through what it
    unanimously agrees on, not to destroy research on a majority opinion.
    """
    if len(votes) < PANEL_SIZE:
        return None, "incomplete_panel"
    if all(v.get("accept") for v in votes):
        return "accepted", None
    return None, "dissent"


VOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "accept": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["accept", "reason"],
    "additionalProperties": False,
}

REVIEW_PROMPT = """You are checking ONE research card for OVERREACH.

The card's claim must not assert more than its verbatim quote supports. This is
the only thing you are judging. You are NOT judging whether the claim is true,
useful, novel, well-written, or tradeable - later stages test all of that.

Reject (accept=false) when the claim:
- asserts a stronger, broader or more general relationship than the quote states
- turns a suggestion, proposal or planned test into a result
- reverses, inverts or changes the direction the quote describes
- adds an asset class, horizon or condition the quote does not mention

Accept (accept=true) when the claim is a faithful, possibly narrower,
restatement of what the quote actually says.

CLAIM:
{claim}

VERBATIM QUOTE FROM THE SOURCE:
{quote}

SOURCE: {title}

Answer with accept and a one-sentence reason. If you reject, name the specific
words in the claim that the quote does not support."""


def review_card(client, model: str, card: dict, meter,
                panel_size: int = PANEL_SIZE) -> list[dict]:
    """Ask `panel_size` independent reviewers whether this card overreaches.

    Each reviewer is a separate call - no shared context, so one reviewer's
    reasoning cannot anchor another's. A malformed reply drops that vote rather
    than being read as agreement; the resulting short panel escalates.
    """
    prompt = REVIEW_PROMPT.format(
        claim=card.get("claim", ""),
        quote=card.get("quote", ""),
        title=(card.get("source") or {}).get("title", "unknown"))

    votes = []
    for _ in range(panel_size):
        msg = client.messages.create(
            model=model,
            max_tokens=1500,   # thinking blocks eat the budget; 300 truncated the JSON
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema",
                                      "schema": VOTE_SCHEMA}},
        )
        meter.record_call(model, msg.usage, "triage")
        try:
            # The first block is NOT necessarily the answer: when the model
            # thinks, content[0] is a ThinkingBlock with no .text at all. Take
            # the first TEXT block, the same way relevance/reader/composer do.
            text = next(b.text for b in msg.content if b.type == "text")
            vote = json.loads(text)
        except (json.JSONDecodeError, AttributeError, IndexError, StopIteration):
            continue          # lost vote -> short panel -> escalation
        votes.append({"accept": bool(vote.get("accept")),
                      "reason": str(vote.get("reason", ""))})
    return votes


def build_decisions(client, model: str, pending: dict[str, dict],
                    accepted: dict[str, dict], meter,
                    panel_size: int = PANEL_SIZE) -> dict:
    """Turn pending cards into a decision list without chaining anything.

    Returns {decisions, escalated, counts, stopped}. `decisions` is the shape
    triage.apply_decisions consumes: {card_id: (status, reject_reason|None)}.
    `escalated` cards are deliberately absent from `decisions` - they stay
    pending, which is what puts them in Coen's Tier 3 queue.
    """
    dupes = find_duplicates(pending, accepted)
    decisions: dict[str, tuple[str, str | None]] = {
        cid: ("rejected", "duplicate") for cid in dupes}
    escalated: dict[str, str] = {}
    stopped = None

    for cid, card in pending.items():
        if cid in dupes:
            continue                       # already decided, never pay for it
        if not meter.can_spend():
            stopped = "budget"
            break
        decision, reason = panel_verdict(
            review_card(client, model, card, meter, panel_size))
        if decision == "accepted":
            decisions[cid] = ("accepted", None)
        else:
            escalated[cid] = reason

    return {
        "decisions": decisions,
        "escalated": escalated,
        "counts": {
            "accepted": sum(1 for v in decisions.values() if v[0] == "accepted"),
            "duplicate": len(dupes),
            "escalated": len(escalated),
        },
        "stopped": stopped,
    }


def _client_and_meter():
    """Real client and budget meter. Split out so tests can stub it.

    The sc-reader key lives in the reader's .env, not in the ambient
    environment, so load it the way every other entry point does. Without this
    the first live run dies thirty frames deep in the anthropic SDK on
    "Could not resolve authentication method", which says nothing about where
    the key actually belongs.
    """
    import anthropic

    from .budget import BudgetMeter
    from .scanner import DEFAULT_READER_ENV, _load_api_key

    _load_api_key(DEFAULT_READER_ENV)      # raises SystemExit with the path
    logs = Path(__file__).resolve().parent.parent / "logs"
    return anthropic.Anthropic(), BudgetMeter(logs / "budget_ledger.jsonl")


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--limit", type=int, default=None,
                    help="review at most N pending cards (cost control)")
    ap.add_argument("--apply", action="store_true",
                    help="CHAIN the decisions. Without this it is a dry run "
                         "that writes nothing (D29: activation is gated).")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    all_cards = registry.cards()
    pending = {cid: c for cid, c in registry.cards(status="pending").items()}
    accepted = {cid: c for cid, c in all_cards.items()
                if (c.get("review") or {}).get("status") == "accepted"}
    if args.limit:
        pending = dict(list(pending.items())[:args.limit])
    if not pending:
        print("No pending cards.")
        return 0

    client, meter = _client_and_meter()
    out = build_decisions(client, args.model, pending, accepted, meter)

    c = out["counts"]
    print(f"{len(pending)} pending -> {c['accepted']} auto-accepted, "
          f"{c['duplicate']} duplicate, {c['escalated']} escalated to Coen")
    if out["stopped"]:
        print(f"STOPPED EARLY: {out['stopped']}")
    for cid, reason in sorted(out["escalated"].items()):
        print(f"  escalated {cid}: {reason}")

    if not args.apply:
        print("\nDRY RUN - nothing chained. Re-run with --apply to write.")
        return 0

    apply_decisions(registry, out["decisions"], REVIEWER)
    print(f"{len(out['decisions'])} card_reviewed entries chained as {REVIEWER}.")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
