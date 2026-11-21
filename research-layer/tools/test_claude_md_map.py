"""research-layer/CLAUDE.md is a MAP (harness audit #11, Coen 2026-11-19).

The detail lives verbatim in path-scoped `.claude/rules/*.md` files, which Claude Code loads only when
it reads or edits a matching file. This pins the budget that keeps the map cheap to load in every
session, and the wiring that keeps the map and the rules files in step.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "CLAUDE.md"
RULES = ROOT / ".claude" / "rules"
MAX_LINES, MAX_BYTES, MAX_LINE = 200, 16 * 1024, 400
RULE_MAX_CHARS = 40_000        # Claude Code cuts a subdirectory's rules file there on 200K-context models


def _map_text() -> str:
    return MAP.read_bytes().decode("utf-8")


def _rule_files() -> list[Path]:
    return sorted(RULES.glob("*.md"))


def _paths(text: str) -> list[str] | None:
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return None
    return re.findall(r'^\s*-\s*"([^"]+)"\s*$', m.group(1), re.M)


def test_the_map_stays_within_budget():
    text = _map_text()
    lines = text.splitlines()
    assert len(lines) <= MAX_LINES, f"CLAUDE.md has {len(lines)} lines; move detail into .claude/rules/"
    assert len(text.encode("utf-8")) <= MAX_BYTES, "CLAUDE.md is over 16 KB; move detail into .claude/rules/"
    long_ = [l[:60] for l in lines if len(l) > MAX_LINE]
    assert not long_, f"map lines over {MAX_LINE} chars: {long_}"


def test_no_rules_section_crept_back_into_the_map():
    # the map keeps its two own sections; every topic section belongs in a rules file
    heads = [l for l in _map_text().splitlines() if l.startswith("## ")]
    assert len(heads) == 2 and heads[0].startswith("## Always") and heads[1].startswith("## Rules files"), heads


def test_every_rules_file_is_indexed_and_every_index_entry_exists():
    indexed = set(re.findall(r"^- `([a-z0-9-]+)\.md`:", _map_text(), re.M))
    present = {p.stem for p in _rule_files()}
    assert present, "no .claude/rules/*.md files"
    assert indexed == present, (f"in the map but missing: {sorted(indexed - present)}; "
                                f"present but not in the map: {sorted(present - indexed)}")


def test_every_rules_file_is_path_scoped_and_its_globs_match_something():
    for f in _rule_files():
        text = f.read_bytes().decode("utf-8")
        globs = _paths(text)
        assert globs, f"{f.name}: no paths: frontmatter (it would load in EVERY session)"
        assert any(list(ROOT.glob(g)) for g in globs), f"{f.name}: none of its globs matches a file: {globs}"
        assert len(text) <= RULE_MAX_CHARS, f"{f.name}: {len(text)} chars, over the {RULE_MAX_CHARS} cut"
        body = text[re.match(r"---\r?\n.*?\r?\n---\r?\n", text, re.S).end():]
        assert body.startswith("## "), f"{f.name}: the body must start with its section heading"
