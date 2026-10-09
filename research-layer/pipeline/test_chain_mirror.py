"""chain_mirror: git-only segments of registry_log.jsonl
(docs/2026-10-09-registry-segments-design.md)."""
from __future__ import annotations

import pytest

from . import chain_mirror as cm


def test_constants_match_the_spec():
    assert cm.SEGMENT_MAX_BYTES == 40 * 1024 * 1024
    assert cm.FORMAT == "registry-segments-v1"
    assert cm.MANIFEST == "MANIFEST.json"
    assert cm.segment_name(1) == "000001.jsonl"
    assert cm.segment_name(123) == "000123.jsonl"


def test_read_live_normalises_crlf_and_drops_a_partial_tail(tmp_path):
    p = tmp_path / "registry_log.jsonl"
    p.write_bytes(b'{"a":1}\r\n{"b":2}\r\n{"c":')       # writer mid-append
    assert cm.read_live(p) == b'{"a":1}\n{"b":2}\n'


def test_read_live_of_lf_only_and_empty_files(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_bytes(b'{"a":1}\n')
    assert cm.read_live(p) == b'{"a":1}\n'
    p.write_bytes(b"")
    assert cm.read_live(p) == b""


def test_split_lines_keeps_each_lf():
    assert cm.split_lines(b"a\nbb\n") == [b"a\n", b"bb\n"]
    assert cm.split_lines(b"") == []


def test_plan_pieces_seals_only_when_the_next_line_would_overflow():
    lines = [b"aaaa\n"] * 5                     # 5 bytes each
    assert cm.plan_pieces(lines, 0, 10) == [(0, 2), (2, 4), (4, 5)]
    assert cm.plan_pieces(lines, 0, 25) == [(0, 5)]      # all fits: active only
    assert cm.plan_pieces(lines, 4, 10) == [(4, 5)]      # starts after sealed lines


def test_plan_pieces_does_not_seal_a_full_remainder_until_the_next_line():
    lines = [b"aaaa\n"] * 4
    # 2 lines fill a 10-byte segment exactly; nothing is due after the last
    # full piece until a line arrives, so the last piece is the remainder.
    assert cm.plan_pieces(lines, 0, 10) == [(0, 2), (2, 4)]
    assert cm.plan_pieces([], 0, 10) == [(0, 0)]


def test_plan_pieces_refuses_an_entry_larger_than_a_segment():
    with pytest.raises(cm.MirrorRefused, match="line 2"):
        cm.plan_pieces([b"a\n", b"x" * 20 + b"\n"], 0, 10)
