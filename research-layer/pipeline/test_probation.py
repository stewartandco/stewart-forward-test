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


from .relevance import (build_source_screen_prompt, parse_source_screen,
                        screen_source, SOURCE_SCREEN_SYSTEM)


class _Meter:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []
    def can_spend(self): return self.ok
    def record_call(self, model, usage, purpose, *, agent, **kw):
        self.calls.append(purpose)
        return 0.003


def _source_msg(payload: dict | str, stop="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop,
                           content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=800, output_tokens=60,
                                                 cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


class _Client:
    def __init__(self, msg=None, exc=None):
        self._msg, self._exc, self.kwargs = msg, exc, None
        self.messages = self
    def create(self, **kw):
        self.kwargs = kw
        if self._exc: raise self._exc
        return self._msg


def test_source_screen_prompt_carries_titles_and_about():
    p = build_source_screen_prompt("x.example", ["T1", "T2"], "About us text")
    assert "x.example" in p and "- T1" in p and "About us text" in p
    assert "testable" in SOURCE_SCREEN_SYSTEM


def test_parse_source_screen_true_false_malformed():
    assert parse_source_screen({"research_source": True, "reason": "r",
                                "asset_classes": ["futures"]}) == \
        {"research_source": True, "reason": "r", "asset_classes": ["futures"]}
    assert parse_source_screen({"research_source": False, "reason": "news",
                                "asset_classes": []})["research_source"] is False
    assert parse_source_screen({"reason": "no verdict"}) is None
    assert parse_source_screen({"research_source": "yes", "reason": "", "asset_classes": []}) is None


def test_screen_source_meters_and_logs(tmp_path):
    meter = _Meter()
    client = _Client(_source_msg({"research_source": True, "reason": "quant blog",
                                  "asset_classes": ["equities"]}))
    out = screen_source(client, "m", meter, "x.example", ["T1"], "about",
                        tmp_path / "source_screen_log.jsonl")
    assert out["research_source"] is True and meter.calls == ["source_screen"]
    assert client.kwargs["system"] == SOURCE_SCREEN_SYSTEM
    row = json.loads((tmp_path / "source_screen_log.jsonl").read_text().splitlines()[0])
    assert row["domain"] == "x.example" and row["verdict"] is True


def test_screen_source_returns_none_on_budget_refusal_or_error(tmp_path):
    log = tmp_path / "l.jsonl"
    assert screen_source(_Client(_source_msg({})), "m", _Meter(ok=False), "x", [], "", log) is None
    assert screen_source(_Client(_source_msg({}, stop="refusal")), "m", _Meter(), "x", [], "", log) is None
    assert screen_source(_Client(exc=RuntimeError("boom")), "m", _Meter(), "x", [], "", log) is None
    assert screen_source(_Client(_source_msg("not json")), "m", _Meter(), "x", [], "", log) is None
