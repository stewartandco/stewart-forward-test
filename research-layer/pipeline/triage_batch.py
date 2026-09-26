"""Batch triage: pending cards -> a decision list, fully automatic (D44).

Three independent reviewers judge each card for OVERREACH - whether the claim
asserts more than its quote supports. A card is ACCEPTED only on a unanimous
accept from a full panel of three. ANY dissent REJECTS it
(`claim_not_supported`), and so does a panel that stays short after the
in-call retries: without three accepts there is no unanimity.

History, so the rule is not relaxed by accident: D31 (2026-08-16) accepted on
unanimity and sent every dissent to Coen's Tier 3 queue, forbidding the panel
to reject. That queue reached 261 cards and never drained. D44 (Coen,
2026-09-26) keeps D31's protection - an overreaching claim is never accepted,
because one reviewer spotting it can never be outvoted - and removes the human
step: dissent now rejects instead of waiting. A majority rule was offered again
and rejected again, for D31's reason.

Duplicates against the accepted corpus are rejected mechanically.

This module produces decisions. It does NOT chain them - applying is
triage.apply_decisions, the path the interactive CLI already proved. Every
rejection's dissenting reasons go to logs/triage_rejections.jsonl, so a
rejected card can still be audited and, if Coen disagrees, revoked.

Provenance on every auto decision is `auto-d44`, NEVER `coen`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .registry import Registry
from .triage import apply_decisions

REVIEWER = "auto-d44"
REJECTIONS_LOG_NAME = "triage_rejections.jsonl"


# D33's pipeline cap, shared with the Composer -- see pipeline/budget.py.
from .budget import PIPELINE_CAP_USD
PANEL_SIZE = 3
# A reviewer whose reply cannot be parsed is re-asked in the same run, up to
# this many calls in total per card. Retrying across cycles would need state
# and could pay for the same unreadable card forever; retrying here is bounded.
MAX_VOTE_ATTEMPTS = PANEL_SIZE + 2

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


def panel_verdict(votes: list[dict]) -> tuple[str, str]:
    """Collapse reviewer votes into (decision, basis).

    ("accepted", "unanimous")        full panel, every reviewer accepts
    ("rejected", "dissent")          full panel, at least one reviewer rejects
    ("rejected", "incomplete_panel") fewer than PANEL_SIZE usable votes

    There is no third outcome: nothing is left pending for a human (D44).
    """
    if len(votes) < PANEL_SIZE:
        return "rejected", "incomplete_panel"
    if all(v.get("accept") for v in votes):
        return "accepted", "unanimous"
    return "rejected", "dissent"


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
    than being read as agreement, and the reviewer is re-asked, up to
    MAX_VOTE_ATTEMPTS calls in total; a panel still short after that rejects.
    """
    prompt = REVIEW_PROMPT.format(
        claim=card.get("claim", ""),
        quote=card.get("quote", ""),
        title=(card.get("source") or {}).get("title", "unknown"))

    votes = []
    attempts = 0
    while len(votes) < panel_size and attempts < panel_size + (MAX_VOTE_ATTEMPTS - PANEL_SIZE):
        attempts += 1
        msg = client.messages.create(
            model=model,
            max_tokens=1500,   # thinking blocks eat the budget; 300 truncated the JSON
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema",
                                      "schema": VOTE_SCHEMA}},
        )
        meter.record_call(model, msg.usage, "triage", agent="pipeline")
        try:
            # The first block is NOT necessarily the answer: when the model
            # thinks, content[0] is a ThinkingBlock with no .text at all. Take
            # the first TEXT block, the same way relevance/reader/composer do.
            text = next(b.text for b in msg.content if b.type == "text")
            vote = json.loads(text)
        except (json.JSONDecodeError, AttributeError, IndexError, StopIteration):
            continue          # lost vote -> re-ask, bounded by MAX_VOTE_ATTEMPTS
        votes.append({"accept": bool(vote.get("accept")),
                      "reason": str(vote.get("reason", ""))})
    return votes


def build_decisions(client, model: str, pending: dict[str, dict],
                    accepted: dict[str, dict], meter,
                    panel_size: int = PANEL_SIZE) -> dict:
    """Turn pending cards into a decision list without chaining anything.

    Returns {decisions, rejections, counts, stopped}. `decisions` is the shape
    triage.apply_decisions consumes: {card_id: (status, reject_reason|None)};
    every reviewed card is in it. `rejections` maps each panel-rejected card to
    {basis, dissent_reasons} for the audit log. A card is left undecided only
    when the budget stops the run before its panel is asked.
    """
    dupes = find_duplicates(pending, accepted)
    decisions: dict[str, tuple[str, str | None]] = {
        cid: ("rejected", "duplicate") for cid in dupes}
    rejections: dict[str, dict] = {}
    stopped = None

    for cid, card in pending.items():
        if cid in dupes:
            continue                       # already decided, never pay for it
        if not meter.can_spend():
            stopped = "budget"
            break
        votes = review_card(client, model, card, meter, panel_size)
        decision, basis = panel_verdict(votes)
        if decision == "accepted":
            decisions[cid] = ("accepted", None)
        else:
            decisions[cid] = ("rejected", "claim_not_supported")
            rejections[cid] = {
                "basis": basis,
                "dissent_reasons": [str(v.get("reason", "")).strip()
                                    for v in votes if not v.get("accept")],
            }

    return {
        "decisions": decisions,
        "rejections": rejections,
        "counts": {
            "accepted": sum(1 for v in decisions.values() if v[0] == "accepted"),
            "rejected": len(rejections),
            "duplicate": len(dupes),
        },
        "stopped": stopped,
    }


def append_rejections(path: Path, rejections: dict[str, dict],
                      reviewer: str = REVIEWER) -> None:
    """Audit trail for panel rejections: the chain records only
    `claim_not_supported`, so the reviewers' own words live here."""
    if not rejections:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for cid, r in rejections.items():
            f.write(json.dumps({"ts_utc": now, "card_id": cid, "reviewed_by": reviewer,
                                "basis": r["basis"],
                                "dissent_reasons": r["dissent_reasons"]},
                               ensure_ascii=False) + "\n")


RESULT_NAME = "triage_result.json"


def save_result(path: Path, reviewed: int) -> None:
    """Record how many cards the panel actually reviewed this run.

    The loop needs this and cannot infer it: it knows --limit, but not how
    many pending cards existed, so it cannot tell "reviewed the whole backlog"
    from "reviewed a window of it". Banking the difference is what stranded
    cards behind the watermark before 2026-08-31.

    `skipped_escalated` is always 0 since D44 removed the escalation skip-set;
    it stays in the file because pipeline.loop._triage_reviewed reads it.

    Written on every --apply path INCLUDING the zero case, so that an absent
    file means "triage did not report", never "triage reviewed nothing".
    Atomic (tmp+replace): a torn file would read as a contract
    violation and cost a re-triage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"reviewed": reviewed,
                               "skipped_escalated": 0},
                              indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


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
    return anthropic.Anthropic(), BudgetMeter(logs / "budget_ledger.jsonl",
                                              monthly_cap_usd=PIPELINE_CAP_USD,
                                              agent="pipeline")


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
    logs_dir = args.registry.resolve().parent / "logs"
    result_path = logs_dir / RESULT_NAME
    pending = {cid: c for cid, c in registry.cards(status="pending").items()}
    accepted = {cid: c for cid, c in registry.cards().items()
                if (c.get("review") or {}).get("status") == "accepted"}

    if args.limit:
        pending = dict(list(pending.items())[:args.limit])
    if not pending:
        print("No pending cards.")
        if args.apply:
            save_result(result_path, 0)
        return 0

    client, meter = _client_and_meter()
    out = build_decisions(client, args.model, pending, accepted, meter)

    c = out["counts"]
    print(f"{len(pending)} pending -> {c['accepted']} auto-accepted, "
          f"{c['rejected']} rejected (claim_not_supported), {c['duplicate']} duplicate")
    if out["stopped"]:
        print(f"STOPPED EARLY: {out['stopped']}")
    for cid, r in sorted(out["rejections"].items()):
        print(f"  rejected {cid}: {r['basis']}")

    if not args.apply:
        print("\nDRY RUN - nothing chained. Re-run with --apply to write.")
        return 0

    # Only cards the panel actually decided count as reviewed: a budget stop
    # leaves the rest pending, and banking them would strand them behind the
    # loop's watermark.
    save_result(result_path, sum(1 for cid in pending if cid in out["decisions"]))
    apply_decisions(registry, out["decisions"], REVIEWER)
    print(f"{len(out['decisions'])} card_reviewed entries chained as {REVIEWER}.")
    # After the chain write, so the audit log never describes a rejection
    # that apply_decisions failed to record.
    append_rejections(logs_dir / REJECTIONS_LOG_NAME, out["rejections"])
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
