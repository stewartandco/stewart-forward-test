"""Pins the gauntlet-protocol-v6.1-amendment-2 note file (final review M1):
it is chained VERBATIM from its LF bytes by tools/grid_switch chain-note,
which refuses a CR, and core.autocrlf is on, so the bytes, the first-line
prefix and the .gitattributes rule are each load-bearing. Mirrors the note
pins in test_switch2.py and test_verify_v61.py."""
from pathlib import Path

from . import gauntlet_stats as gs

LAYER = Path(__file__).resolve().parent.parent
NOTE = LAYER / "docs" / "notes" / "gauntlet-protocol-v6.1-amendment-2.md"
GITATTRIBUTES = LAYER / ".gitattributes"


def test_the_note_is_lf_ascii_and_carries_its_prefix():
    raw = NOTE.read_bytes()
    assert b"\r" not in raw, "the note must be LF (the chain-note guard refuses CR)"
    text = raw.decode("ascii")
    assert text.startswith("gauntlet-protocol-v6.1-amendment-2: ")
    assert gs.is_amendment2_note(text)
    assert "\u2014" not in text and "\u2013" not in text, "no em or en dashes"


def test_the_note_arms_none_of_the_v61_checks():
    """The worker's refusal, the rollback gauntlet's kill switch and verifier
    invariant 12 key on `gauntlet-protocol-v6.1:` (colon straight after
    v6.1); this note has a hyphen there, and is not amendment-1 either."""
    text = NOTE.read_bytes().decode("ascii")
    assert not text.startswith("gauntlet-protocol-v6.1:")
    assert not gs.is_amendment_note(text)


def test_the_note_has_an_lf_rule():
    """core.autocrlf is on: without an eol=lf rule a checkout writes the note
    CRLF and chain-note refuses it."""
    lines = [ln.strip() for ln in GITATTRIBUTES.read_text(encoding="utf-8").splitlines()]
    assert "docs/notes/gauntlet-protocol-v6.1-amendment-2.md text eol=lf" in lines
