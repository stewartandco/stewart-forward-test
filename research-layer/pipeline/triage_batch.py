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
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .registry import Registry
from .triage import apply_decisions

REVIEWER = "auto-d31"
ESCALATED_STATE_NAME = "triage_escalated.json"


class EscalationState(NamedTuple):
    """entries: the skip-set as loaded (empty when absent OR unreadable).
    writable: False ONLY when the file exists but could not be read -- the
    caller must not overwrite it from an empty dict."""
    entries: dict
    writable: bool
# D33's pipeline cap, shared with the Composer -- see pipeline/budget.py.
from .budget import PIPELINE_CAP_USD
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
        meter.record_call(model, msg.usage, "triage", agent="pipeline")
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


def load_escalated(path: Path) -> EscalationState:
    """The advisory escalation skip-set: {card_id: {reason,
    first_escalated_utc, times_seen}}.

    ADVISORY, never a gate. A missing file is the normal first-run state and
    a corrupt one must degrade to "skip nothing" with a printed WARN -- this
    runs unattended inside a loop cycle, and taking the triage stage down
    (which fails the whole cycle, exit 1, Sentinel FAILs the digest) over a
    bookkeeping file would be far worse than re-reviewing some cards.

    Deliberately NOT a chain entry type (Coen 2026-08-29): this is
    operational state, not chain truth. A card_reviewed-style marker would
    misrepresent an un-dispositioned card as reviewed, and the chain is the
    trust asset -- escalation means "still waiting on Coen", which is exactly
    what leaving it pending already says.

    ABSENT vs UNREADABLE is the load-bearing distinction (2026-08-29 review).
    Both degrade to "skip nothing", but only ABSENT is safe to overwrite: a
    file that exists and merely could not be read this once (a JSON decode
    error, or a transient OSError -- on Windows an AV scanner or the search
    indexer holding a sharing lock is entirely plausible, and this runs
    unattended 3x/day) still holds the real history. Saving over it from an
    empty dict would silently destroy the whole skip-set and make the next
    cycle re-pay for the entire escalated backlog. `.writable` is False in
    that case and run() skips the save.
    """
    if not path.exists():
        return EscalationState({}, True)      # absent: safe to write fresh
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("skip-set must be a JSON object")
        return EscalationState({k: v for k, v in data.items()
                                if isinstance(v, dict)}, True)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(f"WARN: unreadable escalation skip-set at {path} ({exc}) -- "
              f"skipping nothing this run, and NOT overwriting it (the file "
              f"is still there and may hold real history; move it aside by "
              f"hand if it is genuinely corrupt)", flush=True)
        return EscalationState({}, False)


def save_escalated(path: Path, retained: dict[str, dict],
                   newly_escalated: dict[str, str]) -> None:
    """Write the skip-set: `retained` (entries carried forward from the load,
    whose cards are still pending) plus this run's `newly_escalated`.

    times_seen counts CYCLES THIS CARD HAS BEEN SIGHTED still waiting on
    Coen, so every retained entry is bumped -- a card sitting at times_seen
    40 has been blocking the head of the queue for weeks, which is exactly
    the signal a T3 queue needs. (Before the 2026-08-29 review this field was
    dead: a skipped card is filtered out of `pending` before review, so it
    could never be re-escalated and the only bump path never ran.)

    Written atomically (tmp+replace) so a crash mid-write cannot leave the
    corrupt file load_escalated then has to warn about.
    """
    now = datetime.now(timezone.utc).isoformat()
    out: dict[str, dict] = {}
    for cid, entry in retained.items():
        e = dict(entry)
        e["times_seen"] = int(e.get("times_seen", 1)) + 1
        out[cid] = e
    for cid, reason in newly_escalated.items():
        if cid in out:
            # Only reachable under --no-skip-escalated (a re-review of a card
            # already on the list). Already bumped above -- never twice.
            out[cid]["reason"] = reason
        else:
            out[cid] = {"reason": reason, "first_escalated_utc": now,
                        "times_seen": 1}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
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
    ap.add_argument("--escalated-state", type=Path, default=None,
                    help="advisory escalation skip-set (default: "
                         "<registry dir>/logs/" + ESCALATED_STATE_NAME + ")")
    ap.add_argument("--no-skip-escalated", action="store_true",
                    help="ignore the skip-set and re-review escalated cards "
                         "(for a deliberate re-run after a prompt change)")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    state_path = args.escalated_state or (
        args.registry.resolve().parent / "logs" / ESCALATED_STATE_NAME)
    all_cards = registry.cards()
    pending = {cid: c for cid, c in registry.cards(status="pending").items()}
    accepted = {cid: c for cid, c in all_cards.items()
                if (c.get("review") or {}).get("status") == "accepted"}

    # Skip-set filter runs BEFORE the --limit slice, and that order is the
    # whole fix. Escalated cards are never chained, so they stay pending at
    # the HEAD of chain order forever; filtering after the slice would hand
    # the panel a window already full of cards it must skip and review
    # nothing, while the cards behind them stay unreachable for good.
    # The skip-set is ALWAYS loaded, even under --no-skip-escalated. That flag
    # suppresses the FILTER, not the history: reading an empty set and then
    # saving from it wipes every first_escalated_utc/times_seen on the file
    # and makes the next automated cycle re-pay for the whole escalated
    # backlog (2026-08-29 review, MEDIUM).
    loaded = load_escalated(state_path)
    # Carried forward on save: every loaded entry whose card is still pending.
    # Computed from the FULL loaded set and from `pending` BEFORE the --limit
    # slice, so neither the flag nor the window can prune real history. An
    # entry whose card has left pending (Coen dispositioned it in T3) is
    # dropped here -- that is the intended garbage collection.
    retained_skips = {cid: e for cid, e in loaded.entries.items() if cid in pending}

    active_skips = {} if args.no_skip_escalated else retained_skips
    if active_skips:
        pending = {cid: c for cid, c in pending.items() if cid not in active_skips}
        print(f"skipping {len(active_skips)} previously-escalated card(s) "
              f"awaiting Coen (see {state_path.name}); they still count toward "
              f"the loop trigger")
    elif args.no_skip_escalated and retained_skips:
        print(f"--no-skip-escalated: re-reviewing {len(retained_skips)} "
              f"previously-escalated card(s); the skip-set is preserved, not "
              f"cleared")

    if args.limit:
        pending = dict(list(pending.items())[:args.limit])
    if not pending:
        print("No pending cards.")
        # Still a SIGHTING of everything on the skip-set: this is the steady
        # state once the whole remaining backlog is escalated, and without
        # this the times_seen counter freezes exactly when it is most useful
        # (a card that has blocked the queue for weeks). Guarded on --apply so
        # a dry run stays a dry run, and on writable so an unreadable file is
        # never overwritten.
        if args.apply and loaded.writable and retained_skips:
            save_escalated(state_path, retained_skips, {})
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

    # Persist AFTER the chain write: the skip-set is a cost control derived
    # from what actually happened, and recording a skip for a run whose
    # apply_decisions then failed would suppress cards that were never
    # really reviewed.
    #
    # Never write over a file that exists but could not be read -- doing so
    # would replace real history with this run's fragment. load_escalated has
    # already WARNed about it.
    if loaded.writable:
        save_escalated(state_path, retained_skips, out["escalated"])
    else:
        print(f"WARN: leaving {state_path.name} untouched this run "
              f"(unreadable at load); {len(out['escalated'])} new escalation(s) "
              f"not recorded", flush=True)
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
