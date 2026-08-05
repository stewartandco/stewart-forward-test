"""Human triage CLI: review pending research cards, accept or reject each.

Usage:
    python -m pipeline.triage --reviewer coen [--registry registry_log.jsonl]

Every decision is chained as a card_reviewed entry. Only accepted cards may be
cited by strategies.
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
          f"[s]kip, [x] quit.\n")

    for i, (cid, card) in enumerate(pending.items(), 1):
        src = card["source"]
        print(f"--- {i}/{len(pending)} · card {cid} ---")
        print(f"claim : {card['claim']}")
        print(f"quote : \"{card['quote']}\"")
        print(f"source: {src['title']} · {src['locator']} · {src['credibility_tier']}")
        print(f"tags  : {', '.join(card['tags']['topics'])} · {card['tags']['horizon']}"
              f" · testability {card['testability']['score']}")
        while True:
            choice = input("[a/o/q/c/d/s/x] > ").strip().lower()
            if choice == "x":
                print("Stopped; remaining cards stay pending.")
                return 0
            if choice == "s":
                break
            if choice == "a":
                registry.review_card(cid, "accepted", args.reviewer)
                print("accepted\n")
                break
            if choice in REJECT_REASONS:
                registry.review_card(cid, "rejected", args.reviewer,
                                     reject_reason=REJECT_REASONS[choice])
                print(f"rejected ({REJECT_REASONS[choice]})\n")
                break
            print("unrecognized — a/o/q/c/d/s/x")
    print("Triage complete.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
