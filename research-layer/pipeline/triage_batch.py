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

import hashlib
import re

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
