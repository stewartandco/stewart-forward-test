"""Offline tests for the D27 case-3 probation filter (no network, no API)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from .watchlist import (load_watchlist, pollable, queue_discovery, load_discovery,
                        remove_source, set_discovery_status, tier_of)
from .relevance import (build_source_screen_prompt, parse_source_screen,
                        screen_source, SOURCE_SCREEN_SYSTEM,
                        SOURCE_SCREEN_SCHEMA, SOURCE_SCREEN_MAX_TOKENS,
                        ApiCreditExhausted)
from .probation import prefilter, BLOCKED_SUBDOMAINS, MIN_INDEX_ITEMS
from .probation import (source_stats, decide_probation, WINDOW_1, WINDOW_2,
                        PROMOTE_KEEPS, TIMEOUT_DAYS)
from .seen import SeenStore
from datetime import datetime, timedelta


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
    assert client.kwargs["max_tokens"] == SOURCE_SCREEN_MAX_TOKENS
    assert client.kwargs["output_config"]["format"]["schema"] is SOURCE_SCREEN_SCHEMA
    row = json.loads((tmp_path / "source_screen_log.jsonl").read_text().splitlines()[0])
    assert row["domain"] == "x.example" and row["verdict"] is True


def test_screen_source_closed_meter_proves_no_spend(tmp_path):
    # A VALID payload the client would happily return - proves the budget
    # gate short-circuits before any call is made, not merely that the
    # (already-null) response of an empty payload happens to come back None.
    meter = _Meter(ok=False)
    client = _Client(_source_msg({"research_source": True, "reason": "r",
                                  "asset_classes": []}))
    assert screen_source(client, "m", meter, "x", [], "",
                         tmp_path / "l.jsonl") is None
    assert client.kwargs is None
    assert meter.calls == []


def test_screen_source_refusal_charges_but_error_does_not(tmp_path):
    log = tmp_path / "l.jsonl"
    refusal_meter = _Meter()
    assert screen_source(_Client(_source_msg({}, stop="refusal")), "m",
                         refusal_meter, "x", [], "", log) is None
    assert refusal_meter.calls == ["source_screen"]

    error_meter = _Meter()
    assert screen_source(_Client(exc=RuntimeError("boom")), "m",
                         error_meter, "x", [], "", log) is None
    assert error_meter.calls == []


def test_screen_source_malformed_json_returns_none(tmp_path):
    log = tmp_path / "l.jsonl"
    assert screen_source(_Client(_source_msg("not json")), "m", _Meter(), "x",
                         [], "", log) is None


def test_screen_source_fatal_api_error_raises_and_aborts(tmp_path):
    log = tmp_path / "l.jsonl"
    with pytest.raises(ApiCreditExhausted):
        screen_source(_Client(exc=RuntimeError("credit balance is too low")),
                     "m", _Meter(), "x", [], "", log)


FEED_XML = """<?xml version="1.0"?><rss><channel><title>X</title>
<item><title>Post A</title><link>https://x.example/a-long-slug</link><pubDate>Mon, 01 Jul 2026 00:00:00 GMT</pubDate></item>
<item><title>Post B</title><link>https://x.example/b-long-slug</link><pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""

def _index_html(n):
    links = "".join(f'<a href="https://x.example/2026/post-number-{i}">Post {i}</a>' for i in range(n))
    return f"<html><head><title>X</title></head><body><p>About our quant research.</p>{links}</body></html>"

def _fetch_factory(pages: dict):
    def fetch(url, timeout=30):
        r = pages.get(url)
        if r is None: return 0, "URLError: nope", url
        return r
    return fetch


def test_prefilter_blocks_junk_and_subdomains():
    f = _fetch_factory({})
    assert prefilter("https://twitter.com/x", f)["ok"] is False
    for sub in BLOCKED_SUBDOMAINS:
        r = prefilter(f"https://{sub}example.com/", f)
        assert r["ok"] is False and "subdomain" in r["reason"]


def test_prefilter_blocks_unreachable_after_one_retry():
    calls = []
    def f(url, timeout=30):
        calls.append(url); return 503, "err", url
    r = prefilter("https://down.example/", f)
    assert r["ok"] is False and "http 503" in r["reason"] and len(calls) == 2


def test_prefilter_accepts_feed_and_collects_titles():
    f = _fetch_factory({"https://x.example/": (200, '<html><link rel="alternate" type="application/rss+xml" href="/feed"><body>About text here</body></html>', "https://x.example/"),
                        "https://x.example/feed": (200, FEED_XML, "https://x.example/feed")})
    r = prefilter("https://x.example/", f)
    assert r["ok"] is True and r["feed"] == "https://x.example/feed"
    assert r["titles"] == ["Post A", "Post B"] and "About text" in r["about"]


def test_prefilter_index_needs_min_items():
    thin = _fetch_factory({"https://x.example/": (200, _index_html(MIN_INDEX_ITEMS - 1), "https://x.example/")})
    assert prefilter("https://x.example/", thin)["ok"] is False
    ok = _fetch_factory({"https://x.example/": (200, _index_html(MIN_INDEX_ITEMS), "https://x.example/")})
    r = prefilter("https://x.example/", ok)
    assert r["ok"] is True and r["feed"] is None and len(r["titles"]) >= MIN_INDEX_ITEMS


def _seen_with(tmp_path, source_id, kills=0, keeps=0):
    seen = SeenStore(tmp_path / "seen.jsonl")
    for i in range(kills):
        seen.record(f"{source_id}-k{i}", source_id, "screen_kill", link="https://x/k")
    for i in range(keeps):
        seen.record(f"{source_id}-p{i}", source_id, "screen_keep", link="https://x/p")
    return seen


def test_source_stats_counts_screened_and_keeps(tmp_path):
    seen = _seen_with(tmp_path, "p.example", kills=5, keeps=2)
    seen.record("p.example-x", "p.example", "seen", link="https://x/s")   # unscreened
    seen.record("p.example-p0", "p.example", "extracted")                  # keep -> extracted still a keep
    assert source_stats(seen, "p.example") == {"screened": 7, "keeps": 2}
    assert source_stats(seen, "nobody") == {"screened": 0, "keeps": 0}


@pytest.mark.parametrize("screened,keeps,days,expected", [
    (10, 0, 5, "wait"),
    (WINDOW_1, 0, 5, "revoke"),
    (WINDOW_1, PROMOTE_KEEPS, 5, "promote"),
    (5, PROMOTE_KEEPS, 1, "promote"),
    (WINDOW_1, 1, 5, "wait"),
    (WINDOW_2 - 1, 1, 5, "wait"),
    (WINDOW_2, 1, 5, "revoke"),
    (WINDOW_2, 2, 5, "promote"),
    (3, 0, TIMEOUT_DAYS, "timeout"),
    (3, 1, TIMEOUT_DAYS + 10, "timeout"),
])
def test_decide_probation_edges(screened, keeps, days, expected):
    since = "2026-01-01"
    today = (datetime(2026, 1, 1) + timedelta(days=days)).strftime("%Y-%m-%d")
    d = decide_probation({"screened": screened, "keeps": keeps}, since, today)
    assert d["action"] == expected, d


def test_decide_probation_reason_names_the_window():
    assert decide_probation({"screened": 40, "keeps": 0}, "2026-01-01", "2026-01-10")["reason"] == "probation-yield 0/40"
    assert decide_probation({"screened": 80, "keeps": 1}, "2026-01-01", "2026-01-10")["reason"] == "probation-yield 1/80"
    assert decide_probation({"screened": 2, "keeps": 0}, "2026-01-01", "2026-04-02")["reason"] == "probation-timeout"
