"""Human triage CLI: review pending research cards, accept or reject each.

Usage:
    python -m pipeline.triage --reviewer coen [--registry registry_log.jsonl]

Decisions are buffered in memory during the session — [u] undoes the last one,
and nothing is chained until the closing summary is confirmed with [w]rite.
Quitting or discarding writes nothing; skipped cards stay pending either way.
Only accepted cards may be cited by strategies.
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path

from .registry import Registry

REJECT_REASONS = {
    "o": "off_topic",
    "q": "quote_not_found",
    "c": "claim_not_supported",
    "d": "duplicate",
}


def show_card(i: int, total: int, cid: str, card: dict) -> None:
    src = card["source"]
    print(f"--- {i}/{total} · card {cid} ---")
    print(f"claim : {card['claim']}")
    print(f"quote : \"{card['quote']}\"")
    print(f"source: {src['title']} · {src['locator']} · {src['credibility_tier']}")
    print(f"tags  : {', '.join(card['tags']['topics'])} · {card['tags']['horizon']}"
          f" · testability {card['testability']['score']}")


def collect_decisions(pending: dict[str, dict], input_fn=input) -> dict[str, tuple[str, str | None]]:
    """Interactive review loop. Returns {card_id: (status, reject_reason)};
    skipped cards are absent. Nothing is written here."""
    items = list(pending.items())
    decisions: dict[int, tuple[str, str | None] | None] = {}  # idx -> decision, None = skip
    i = 0
    while i < len(items):
        cid, card = items[i]
        show_card(i + 1, len(items), cid, card)
        while True:
            try:
                choice = input_fn("[a/o/q/c/d/s/u/x] > ").strip().lower()
            except EOFError:
                choice = "x"
            if choice == "x":
                return {items[j][0]: d for j, d in decisions.items() if d is not None}
            if choice == "u":
                if i == 0:
                    print("nothing to undo")
                    continue
                i -= 1
                prev = decisions.pop(i, None)
                print(f"undid decision on card {items[i][0]}"
                      f" ({prev[0] if prev else 'skip'}); re-reviewing\n")
                break
            if choice == "s":
                decisions[i] = None
                i += 1
                break
            if choice == "a":
                decisions[i] = ("accepted", None)
                print("accepted (buffered)\n")
                i += 1
                break
            if choice in REJECT_REASONS:
                decisions[i] = ("rejected", REJECT_REASONS[choice])
                print(f"rejected ({REJECT_REASONS[choice]}, buffered)\n")
                i += 1
                break
            print("unrecognized — a/o/q/c/d/s/u/x")
    return {items[j][0]: d for j, d in decisions.items() if d is not None}


def confirm_write(decisions: dict[str, tuple[str, str | None]], n_pending: int,
                  input_fn=input) -> bool:
    """Show the session summary; only an explicit [w] commits to the chain."""
    n_acc = sum(1 for s, _ in decisions.values() if s == "accepted")
    n_rej = len(decisions) - n_acc
    print(f"\nSession summary: {n_acc} accepted, {n_rej} rejected, "
          f"{n_pending - len(decisions)} left pending.")
    if not decisions:
        return False
    while True:
        try:
            choice = input_fn("[w]rite decisions to chain / [q] discard > ").strip().lower()
        except EOFError:
            choice = "q"
        if choice == "w":
            return True
        if choice == "q":
            print("Discarded; no entries were chained.")
            return False
        print("unrecognized — w/q")


def apply_decisions(registry: Registry, decisions: dict[str, tuple[str, str | None]],
                    reviewer: str) -> None:
    for cid, (status, reason) in decisions.items():
        registry.review_card(cid, status, reviewer, reject_reason=reason)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    pending = registry.cards(status="pending")
    if not pending:
        print("No pending cards.")
        return 0

    print(f"{len(pending)} pending card(s). For each: [a]ccept, reject as "
          f"[o]ff_topic / [q]uote_not_found / [c]laim_not_supported / [d]uplicate, "
          f"[s]kip, [u]ndo last, [x] finish.\n"
          f"Decisions are buffered — nothing is chained until you confirm [w]rite "
          f"at the end.\n")

    decisions = collect_decisions(pending)
    if confirm_write(decisions, len(pending)):
        apply_decisions(registry, decisions, args.reviewer)
        print(f"{len(decisions)} card_reviewed entries chained.")
    print("Triage complete.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
