"""Offline tests for the D27 case-3 probation filter (no network, no API)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from .watchlist import (load_watchlist, pollable, queue_discovery, load_discovery,
                        remove_source, set_discovery_status, tier_of)


def make_source(**o):
    src = {"id": "test-blog", "class": "blog", "name": "Test Blog",
           "url": "https://example.org/blog/", "feed": "https://example.org/feed/",
           "poll_minutes": 60, "added_by": "coen", "verified_date": "2026-08-14",
           "notes": ""}
    src.update(o)
    return src


def write_watchlist(tmp_path, sources):
    p = tmp_path / "verified_sources.json"
    p.write_text(json.dumps({"version": 1, "sources": sources}), encoding="utf-8")
    return p


def test_tier_defaults_to_verified_for_legacy_entries(tmp_path):
    wl = write_watchlist(tmp_path, [make_source()])
    src = load_watchlist(wl)[0]
    assert tier_of(src) == "verified"
    assert tier_of(make_source(tier="probation")) == "probation"


def test_probation_and_promoted_provenance_are_pollable(tmp_path):
    srcs = [make_source(id="p", added_by="auto-d27-probation", tier="probation"),
            make_source(id="q", added_by="auto-d27-promoted"),
            make_source(id="r", added_by="somebody-else")]
    assert [s["id"] for s in pollable(srcs)] == ["p", "q"]


def test_remove_source_and_set_discovery_status(tmp_path):
    wl = write_watchlist(tmp_path, [make_source(id="a"), make_source(id="b")])
    removed = remove_source(wl, "a")
    assert removed["id"] == "a"
    assert [s["id"] for s in load_watchlist(wl)] == ["b"]
    assert remove_source(wl, "zzz") is None
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://x.example/p", found_in="blog1/i1", reason="cited")
    assert set_discovery_status(q, "x.example", "probation", reason="admitted") is True
    e = load_discovery(q)[0]
    assert e["status"] == "probation" and e["status_reason"] == "admitted"
    assert set_discovery_status(q, "nope.example", "blocked", reason="x") is False
