"""Pins the two beats-hold note files (gauntlet-protocol-v6.2 and
quarantine-live-protocol-v2): each is chained VERBATIM from its LF bytes by
tools/grid_switch chain-note, which refuses a CR, and core.autocrlf is on, so
the bytes, the first-line prefix and the .gitattributes rule are each
load-bearing. The constants the v2 note names are parsed from its text and
compared with pipeline/beats_hold.py, so the note and the code cannot drift.
Modelled on test_amendment2_note.py."""
import re
from pathlib import Path

import pytest

from . import beats_hold
from .protocols import NOTE_LIVE_V2, NOTE_V62

LAYER = Path(__file__).resolve().parent.parent
NOTES = LAYER / "docs" / "notes"
GITATTRIBUTES = LAYER / ".gitattributes"
V62 = NOTES / "gauntlet-protocol-v6.2.md"
LIVE_V2 = NOTES / "quarantine-live-protocol-v2.md"
CASES = [(V62, NOTE_V62), (LIVE_V2, NOTE_LIVE_V2)]


@pytest.mark.parametrize("note,prefix", CASES, ids=["v6.2", "live-v2"])
def test_the_note_is_lf_ascii_and_carries_its_prefix(note, prefix):
    raw = note.read_bytes()
    assert b"\r" not in raw, "the note must be LF (the chain-note guard refuses CR)"
    text = raw.decode("ascii")
    assert text.startswith(prefix)
    assert "—" not in text and "–" not in text, "no em or en dashes"
    assert "STATUS" not in text


@pytest.mark.parametrize("note,prefix", CASES, ids=["v6.2", "live-v2"])
def test_the_note_arms_none_of_the_v61_or_v1_checks(note, prefix):
    """The worker's refusal, the hand-run gauntlet's kill switch and verifier
    invariant 12 key on `gauntlet-protocol-v6.1:`; livegate's v1 refusal keys
    on `quarantine-live-protocol-v1`. Neither note may satisfy them."""
    first = note.read_bytes().decode("ascii").split("\n", 1)[0]
    assert not first.startswith("gauntlet-protocol-v6.1:")
    assert not first.startswith("quarantine-live-protocol-v1")


@pytest.mark.parametrize("note", [V62, LIVE_V2], ids=["v6.2", "live-v2"])
def test_the_note_has_an_lf_rule(note):
    """core.autocrlf is on: without an eol=lf rule a checkout writes the note
    CRLF and chain-note refuses it."""
    lines = [ln.strip() for ln in GITATTRIBUTES.read_text(encoding="utf-8").splitlines()]
    assert f"docs/notes/{note.name} text eol=lf" in lines


def test_the_v2_note_constants_equal_the_code():
    text = LIVE_V2.read_bytes().decode("ascii")
    expected = {"HOLD_BLOCK_LEN": beats_hold.HOLD_BLOCK_LEN,
                "HOLD_TEST_PATHS": beats_hold.HOLD_TEST_PATHS,
                "HOLD_CHUNK_PATHS": beats_hold.HOLD_CHUNK_PATHS,
                "SR_DIFF_TOL": beats_hold.SR_DIFF_TOL}
    found: dict[str, list[float]] = {}
    for name, value in re.findall(
            r"\b(HOLD_BLOCK_LEN|HOLD_TEST_PATHS|HOLD_CHUNK_PATHS|SR_DIFF_TOL)"
            r" = ([0-9][0-9.e+-]*)", text):
        found.setdefault(name, []).append(float(value.rstrip(".")))
    assert set(found) == set(expected), f"the note must state each constant: {found}"
    for name, values in found.items():
        assert all(v == expected[name] for v in values), (name, values, expected[name])
    assert isinstance(beats_hold.HOLD_BLOCK_LEN, int)
    assert isinstance(beats_hold.HOLD_TEST_PATHS, int)
    assert isinstance(beats_hold.HOLD_CHUNK_PATHS, int)


def test_the_v2_note_states_the_refinement_rule_the_code_applies():
    """The floor refinement (final review I1): the note's rule, P = ceil(2 x m
    / q) when larger than HOLD_TEST_PATHS, applied only to a p_hold exactly on
    the floor, is the code's."""
    import math
    text = LIVE_V2.read_bytes().decode("ascii")
    for phrase in ("P = ceil(2 x m / q)",
                   "equals exactly 1 / (HOLD_TEST_PATHS + 1)",
                   "when P is larger than HOLD_TEST_PATHS",
                   "the same seed",
                   "p_hold_paths",
                   "is not positive, so the hold cannot be rebased"):
        assert phrase in text, phrase
    for m, q in ((1, 0.05), (50, 0.05), (51, 0.05), (300, 0.05), (900, 0.05),
                 (7, 0.01)):
        assert beats_hold.refined_paths(m, q) == math.ceil(2 * m / q)
