"""Offline tests for the Reader v2 scanner (no network, no API calls).

Run: python -m pytest research-layer/pipeline/test_scanner.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from .lock import FileLock, FileLockTimeout

from .watchlist import (WatchlistError, load_watchlist, pollable,
                        normalize_url, queue_discovery, load_discovery)
from .feeds import (parse_feed, extract_links, item_id, html_to_text,
                    looks_paywalled)
from .seen import SeenStore
from .budget import BudgetMeter, usd_for_usage
from .relevance import (build_screen_prompt, parse_screen_response,
                        screen_items)
from .scanstatus import ActionLog, verify_chain, write_status, write_digest
from .scanner import poll_source, process_new_items, pending_tier3_count
from .registry import Registry
from .reader import extract_claims_usage

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_source(**overrides):
    src = {
        "id": "test-blog", "class": "blog", "name": "Test Blog",
        "url": "https://example.org/blog/", "feed": "https://example.org/feed/",
        "poll_minutes": 60, "added_by": "coen",
        "verified_date": "2026-08-14", "notes": "",
    }
    src.update(overrides)
    return src


def write_watchlist(tmp_path, sources):
    p = tmp_path / "verified_sources.json"
    p.write_text(json.dumps({"version": 1, "sources": sources}), encoding="utf-8")
    return p


# ---------------- watchlist ----------------

def test_committed_watchlist_loads_and_gate_tracks_verification():
    sources = load_watchlist(
        HERE / "fixtures" / "verified_sources_snapshot_20260828.json")
    assert len(sources) >= 15
    classes = {s["class"] for s in sources}
    assert classes == {"arxiv", "aggregator", "blog", "ssrn", "central_bank", "github"}
    # The gate admits exactly the entries stamped by a recognised admission
    # path, and nothing else.
    #
    # This used to read `added_by == "coen"` alone, which was the whole story
    # until D27 (2026-08-15) authorised mechanical auto-admit for scout-found
    # and citation-endorsed domains, stamped `auto-d27` and NEVER `coen`. The
    # assertion was never updated, so it passed on the committed watchlist
    # (192 sources, all Coen's) and failed on any machine where the scanner had
    # actually run and admitted some (203 sources, 11 of them auto-d27). A test
    # that reads a live, agent-mutated file has to encode the rules the agent
    # is actually running under.
    #
    # It then happened AGAIN: D27 case 3 (source probation filter, designed
    # 2026-08-23, live same day, docs/2026-08-23-source-probation-filter-design.md)
    # added the probation pair `auto-d27-probation` (admitted by the source
    # screen, tier "probation") and `auto-d27-promoted` (>= 2 keeps in the
    # 40-item yield window, tier flips to "verified"). The live watchlist grew
    # 6 such entries by 2026-08-28 and this test, still reading the LIVE file
    # with a two-path admission set, was red for a week. Per the 2026-08-28
    # handoff decision it now reads a frozen fixture snapshot of that live
    # file (pipeline/fixtures/verified_sources_snapshot_20260828.json,
    # 218 sources: 192 coen / 20 auto-d27 / 5 probation / 1 promoted), so the
    # gate rules are asserted against a real, representative, immutable
    # watchlist instead of whatever the resident scanner mutated last night.
    #
    # What must NOT weaken: no source is pollable without a verified_date, and
    # no admission path exists beyond the recognised set below. That is D23's
    # Tier 3 gate as amended by D27 (cases 1-3).
    ADMISSION_PATHS = {
        "coen",                # Coen's own verification pass (D23)
        "auto-d27",            # D27 cases 1-2: scout-researched / 2+ citers
        "auto-d27-probation",  # D27 case 3: source screen passed, on probation
        "auto-d27-promoted",   # D27 case 3: probation yield bar met
    }
    # The runtime's gate must recognise exactly these paths -- a new stamp
    # added in watchlist.py without a decision landing here is the regression
    # this test exists to catch.
    from .watchlist import POLLABLE_PROVENANCE
    assert POLLABLE_PROVENANCE == ADMISSION_PATHS
    stamped = {s["id"] for s in sources
               if s["added_by"] in ADMISSION_PATHS and s["verified_date"]}
    assert {s["id"] for s in pollable(sources)} == stamped
    assert {s["added_by"] for s in sources} <= ADMISSION_PATHS
    # D27 case 3 state field: a probation admit carries tier "probation";
    # promotion flips the tier to "verified".
    for s in sources:
        if s["added_by"] == "auto-d27-probation":
            assert s.get("tier") == "probation", s["id"]
        if s["added_by"] == "auto-d27-promoted":
            assert s.get("tier") == "verified", s["id"]
    # The snapshot really contains probation-era entries (it is not a stale
    # pre-case-3 copy that would let the assertions above pass vacuously).
    assert any(s["added_by"] == "auto-d27-probation" for s in sources)
    assert any(s["added_by"] == "auto-d27-promoted" for s in sources)
    # The SSRN placeholder stays out until its URL is pinned.
    assert "ssrn-new-papers" not in stamped


def test_watchlist_rejects_duplicate_ids(tmp_path):
    p = write_watchlist(tmp_path, [make_source(), make_source()])
    with pytest.raises(WatchlistError, match="duplicate"):
        load_watchlist(p)


def test_watchlist_rejects_unknown_class(tmp_path):
    p = write_watchlist(tmp_path, [make_source(**{"class": "forum"})])
    with pytest.raises(WatchlistError, match="class"):
        load_watchlist(p)


def test_watchlist_rejects_missing_field(tmp_path):
    src = make_source()
    del src["poll_minutes"]
    p = write_watchlist(tmp_path, [src])
    with pytest.raises(WatchlistError, match="poll_minutes"):
        load_watchlist(p)


def test_pollable_requires_coen_verification(tmp_path):
    srcs = [
        make_source(id="ok"),
        make_source(id="unverified", verified_date=None),
        make_source(id="wrong-adder", added_by="claude"),
    ]
    p = write_watchlist(tmp_path, srcs)
    assert [s["id"] for s in pollable(load_watchlist(p))] == ["ok"]


def test_normalize_url_collapses_variants():
    a = normalize_url("HTTPS://Example.org/Post/?utm_source=rss&utm_medium=feed")
    b = normalize_url("https://example.org/Post")
    assert a == b
    assert normalize_url("https://example.org/a") != normalize_url("https://example.org/b")


def test_discovery_queue_dedups_and_never_marks_fetchable(tmp_path):
    q = tmp_path / "discovery_queue.jsonl"
    assert queue_discovery(q, "https://newblog.example/post?utm_source=x",
                           found_in="test-blog/abc", reason="cited")
    assert not queue_discovery(q, "https://newblog.example/post",
                               found_in="test-blog/def", reason="cited")
    entries = load_discovery(q)
    assert len(entries) == 1
    assert entries[0]["tier"] == 3
    assert entries[0]["status"] == "proposed"


def test_discovery_queue_dedups_at_domain_level(tmp_path):
    # a source proposal is a DOMAIN, not a post: second URL on the same
    # domain is not a new proposal
    q = tmp_path / "discovery_queue.jsonl"
    assert queue_discovery(q, "https://newblog.example/post-one",
                           found_in="a/1", reason="cited")
    assert not queue_discovery(q, "https://newblog.example/post-two",
                               found_in="a/2", reason="cited")
    assert not queue_discovery(q, "https://www.newblog.example/post-three",
                               found_in="a/3", reason="cited")
    assert len(load_discovery(q)) == 1
    assert load_discovery(q)[0]["domain"] == "newblog.example"


def test_discovery_queue_rejects_junk_platform_domains(tmp_path):
    q = tmp_path / "discovery_queue.jsonl"
    for junk in ("https://twitter.com/someone/status/1",
                 "https://www.linkedin.com/in/someone",
                 "https://www.youtube.com/watch?v=x",
                 "https://amazon.com/some-book"):
        assert not queue_discovery(q, junk, found_in="a/1", reason="cited")
    assert load_discovery(q) == []


# ---------------- feeds ----------------

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test Blog</title>
<item><title>Momentum persists in crypto</title>
<link>https://example.org/momo?utm_source=rss</link>
<description>A testable claim about momentum.</description>
<pubDate>Thu, 14 Aug 2026 01:00:00 GMT</pubDate></item>
<item><title>Our new course is out!</title>
<link>https://example.org/course</link>
<description>Buy our course.</description></item>
</channel></rss>"""

ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Test Atom</title>
<entry><title>Regime detection via HMM</title>
<link href="https://example.org/hmm"/>
<summary>Hidden Markov regimes.</summary>
<updated>2026-08-14T01:00:00Z</updated></entry>
</feed>"""


def test_parse_rss_items():
    items = parse_feed(RSS_XML, "test-blog")
    assert len(items) == 2
    assert items[0]["title"] == "Momentum persists in crypto"
    assert items[0]["link"] == "https://example.org/momo?utm_source=rss"
    assert items[0]["summary"] == "A testable claim about momentum."
    assert items[0]["source_id"] == "test-blog"
    assert items[0]["item_id"] == item_id("test-blog", items[0]["link"])


def test_parse_atom_items():
    items = parse_feed(ATOM_XML, "test-atom")
    assert len(items) == 1
    assert items[0]["title"] == "Regime detection via HMM"
    assert items[0]["link"] == "https://example.org/hmm"
    assert items[0]["summary"] == "Hidden Markov regimes."


def test_item_id_stable_across_tracking_params():
    a = item_id("s", "https://example.org/momo?utm_source=rss")
    assert a == item_id("s", "https://example.org/momo")
    assert a != item_id("s", "https://example.org/other")
    assert a != item_id("s2", "https://example.org/momo")
    assert len(a) == 16


def test_extract_links_resolves_and_dedups():
    html = ('<html><body><a href="/p/one">One</a>'
            '<a href="https://example.org/p/one">One again</a>'
            '<a href="#frag">skip</a><a href="mailto:x@y.z">skip</a>'
            '<a href="https://other.example/two">Two</a></body></html>')
    links = extract_links(html, "https://example.org/blog/")
    urls = [u for u, _ in links]
    assert "https://example.org/p/one" in urls
    assert "https://other.example/two" in urls
    assert len([u for u in urls if "p/one" in u]) == 1
    assert not any(u.startswith("mailto") or "#" in u for u in urls)


def test_html_to_text_strips_markup():
    text = html_to_text("<html><head><script>var x=1;</script>"
                        "<style>p{}</style></head>"
                        "<body><p>Sharpe ratios &amp; drawdowns.</p></body></html>")
    assert "Sharpe ratios & drawdowns." in text
    assert "var x" not in text and "p{}" not in text


def test_paywall_detection():
    assert looks_paywalled(403, "")
    assert looks_paywalled(402, "anything")
    assert looks_paywalled(200, "<p>Subscribe now to continue reading this article</p>" )
    assert not looks_paywalled(200, "<p>Free research post about momentum.</p>")


# ---------------- seen store ----------------

def test_seen_store_roundtrip_and_resume(tmp_path):
    p = tmp_path / "seen.jsonl"
    s = SeenStore(p)
    assert not s.is_seen("abc")
    s.record("abc", "src1", "seen", title="T", link="https://x")
    s.record("abc", "src1", "screen_keep")
    assert s.is_seen("abc")
    assert s.status("abc") == "screen_keep"
    # crash-safe resume: a fresh instance replays the file
    s2 = SeenStore(p)
    assert s2.is_seen("abc") and s2.status("abc") == "screen_keep"


def test_seen_store_latest_status_wins_and_filters(tmp_path):
    s = SeenStore(tmp_path / "seen.jsonl")
    s.record("a", "s1", "seen")
    s.record("b", "s1", "seen")
    s.record("a", "s1", "screen_kill", reason="blogspam")
    assert set(s.items_with_status("seen")) == {"b"}
    assert set(s.items_with_status("screen_kill")) == {"a"}


def test_seen_store_count_since(tmp_path):
    s = SeenStore(tmp_path / "seen.jsonl")
    old = _iso(_now() - timedelta(hours=30))
    s.record("old", "s1", "seen", ts_utc=old)
    s.record("new", "s1", "seen")
    assert s.count_since(hours=24) == 1
    assert s.count_since(hours=48) == 2


# ---------------- budget ----------------

def test_usd_for_usage_math():
    # sonnet-5 sticker: $3/MTok in, $15/MTok out
    usd = usd_for_usage("claude-sonnet-5", input_tokens=1_000_000,
                        output_tokens=100_000)
    assert usd == pytest.approx(3.0 + 1.5)
    usd2 = usd_for_usage("claude-sonnet-5", input_tokens=0, output_tokens=0,
                         cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert usd2 == pytest.approx(0.30 + 3.75)


def test_usd_for_usage_unknown_model_fails_loudly():
    with pytest.raises(KeyError):
        usd_for_usage("claude-nonexistent", input_tokens=1, output_tokens=1)


def test_budget_meter_accumulates_and_resumes(tmp_path):
    p = tmp_path / "ledger.jsonl"
    m = BudgetMeter(p, monthly_cap_usd=25.0)
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=0,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m.record_call("claude-sonnet-5", u, purpose="screen", agent="reader")
    assert m.month_spend() == pytest.approx(3.0)
    m2 = BudgetMeter(p, monthly_cap_usd=25.0)
    assert m2.month_spend() == pytest.approx(3.0)
    assert m2.state() == "OK" and m2.can_spend()


def test_budget_warn_at_80_percent_and_cap_blocks(tmp_path):
    m = BudgetMeter(tmp_path / "ledger.jsonl", monthly_cap_usd=25.0)
    u80 = SimpleNamespace(input_tokens=0, output_tokens=1_400_000,  # $21.00
                          cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m.record_call("claude-sonnet-5", u80, purpose="extract", agent="reader")
    assert m.state() == "WARN" and m.can_spend()
    u_more = SimpleNamespace(input_tokens=0, output_tokens=300_000,  # +$4.50 -> $25.50
                             cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m.record_call("claude-sonnet-5", u_more, purpose="extract", agent="reader")
    assert m.state() == "CAP" and not m.can_spend()


def test_budget_month_rollover_resets(tmp_path):
    p = tmp_path / "ledger.jsonl"
    m = BudgetMeter(p, monthly_cap_usd=25.0)
    last_month = (_now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    u = SimpleNamespace(input_tokens=10_000_000, output_tokens=0,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m.record_call("claude-sonnet-5", u, purpose="extract", agent="reader",
                  ts_utc=last_month)
    assert m.month_spend() == pytest.approx(0.0)
    assert m.can_spend()


# ---------------- relevance screen ----------------

def _screen_msg(decisions, in_tok=2000, out_tok=200):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text",
                                 text=json.dumps({"decisions": decisions}))],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0),
    )


class StubClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                if not outer._responses:
                    raise AssertionError("unexpected model call")
                return outer._responses.pop(0)

            def stream(self, **kw):
                outer.calls.append(kw)
                if not outer._responses:
                    raise AssertionError("unexpected model call")
                msg = outer._responses.pop(0)

                class _Stream:
                    def __enter__(self_s):
                        return self_s

                    def __exit__(self_s, *a):
                        return False

                    def get_final_message(self_s):
                        return msg
                return _Stream()
        self.messages = _Messages()


def _items(n=2):
    return [{"item_id": f"i{k}", "source_id": "test-blog",
             "title": f"Title {k}", "summary": f"Summary {k}",
             "link": f"https://example.org/{k}"} for k in range(n)]


def test_screen_prompt_carries_ids_titles_summaries():
    prompt = build_screen_prompt(_items(2))
    for needle in ("i0", "i1", "Title 0", "Summary 1"):
        assert needle in prompt


def test_parse_screen_response_defers_missing_ids():
    items = _items(3)
    data = {"decisions": [{"id": "i0", "keep": True, "reason": "testable claim"},
                          {"id": "i2", "keep": False, "reason": "marketing"},
                          {"id": "ghost", "keep": True, "reason": "?"}]}
    out = parse_screen_response(items, data)
    assert out["i0"] == ("screen_keep", "testable claim")
    assert out["i2"] == ("screen_kill", "marketing")
    assert out["i1"][0] == "deferred_screen"
    assert "ghost" not in out


def test_screen_items_records_usage_and_logs_rejections(tmp_path):
    items = _items(2)
    client = StubClient([_screen_msg(
        [{"id": "i0", "keep": True, "reason": "edge claim"},
         {"id": "i1", "keep": False, "reason": "blogspam"}])])
    meter = BudgetMeter(tmp_path / "ledger.jsonl")
    log = tmp_path / "screen_log.jsonl"
    out = screen_items(client, "claude-sonnet-5", items, meter, log)
    assert out["i0"][0] == "screen_keep" and out["i1"][0] == "screen_kill"
    assert meter.month_spend() > 0
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    kills = [r for r in rows if r["decision"] == "screen_kill"]
    assert kills and kills[0]["reason"] == "blogspam"


def test_screen_items_budget_cap_defers_without_model_call(tmp_path):
    meter = BudgetMeter(tmp_path / "ledger.jsonl", monthly_cap_usd=0.0)
    client = StubClient([])  # any call raises
    out = screen_items(client, "claude-sonnet-5", _items(2), meter,
                       tmp_path / "screen_log.jsonl")
    assert all(v[0] == "deferred_budget" for v in out.values())
    assert client.calls == []


# ---------------- reader extraction with usage ----------------

def test_extract_claims_usage_returns_claims_and_usage():
    claims = [{"claim": "c", "quote": "q", "locator": "l", "asset_classes": ["crypto"],
               "topics": ["t"], "horizon": "daily", "testability_score": 0.5,
               "data_required": ["d"], "notes": None}]
    msg = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps({"claims": claims}))],
        usage=SimpleNamespace(input_tokens=500, output_tokens=50,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0),
    )
    client = StubClient([msg])
    got, usage = extract_claims_usage(client, "claude-sonnet-5", "full document", "text")
    assert got == claims
    assert usage.input_tokens == 500


# ---------------- status artifacts + action chain ----------------

def test_write_status_matches_convention(tmp_path):
    p = tmp_path / "status.json"
    write_status(p, overall="OK", summary="scanning",
                 items={"sources_polled": 3, "items_seen_24h": 10,
                        "screened_pass": 4, "extracted": 2,
                        "cards_registered": 5, "budget": "OK"},
                 pending_tier3=7, digest_file="digest_20260814.txt")
    st = json.loads(p.read_text(encoding="utf-8"))
    assert st["agent"] == "reader" and st["domain"] == "intelligence"
    # Source of truth is the Reader's CONTRACT.md in stewartandco-agents, not
    # this file. It reached v1.6 on 2026-08-17 (D34 audit + D35 kill switch)
    # while the runtime still emitted "1.1" -- five versions of drift on the
    # one field a dashboard reads to say which rules an agent is running under.
    # A decision that only changed a contract has not shipped. The contract
    # then reached 1.7 (D36, 2026-08-18) while this runtime stayed at 1.6 --
    # drift closed here at 1.8 for D27 case 3 (source probation filter,
    # 2026-08-24).
    assert st["contract_version"] == "1.8"
    assert st["overall"] == "OK"
    assert st["items"]["budget"] == "OK"
    assert st["pending_tier3"] == 7
    assert st["digest_file"] == "digest_20260814.txt"
    assert "ts_utc" in st and "next_run" in st
    # atomic overwrite leaves no tmp file behind
    write_status(p, overall="WARN", summary="budget 80%",
                 items={"budget": "WARN"}, pending_tier3=0, digest_file=None)
    assert json.loads(p.read_text(encoding="utf-8"))["overall"] == "WARN"
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_digest_contains_sections(tmp_path):
    name = write_digest(tmp_path, date="20260814",
                        new_by_source={"test-blog": 3},
                        rejections={"blogspam": 2, "marketing": 1},
                        discoveries=["https://newblog.example/post"],
                        paywalled=["https://x.example/gated"],
                        spend_usd=1.23, cards_registered=4)
    text = (tmp_path / name).read_text(encoding="utf-8")
    assert "test-blog" in text and "blogspam" in text
    assert "newblog.example" in text and "1.23" in text
    assert name == "digest_20260814.txt"
    assert "BUDGET ALERT" not in text
    # at >=80% the digest carries an alert line the Ops Sentinel can grep
    write_digest(tmp_path, date="20260815", new_by_source={}, rejections={},
                 discoveries=[], paywalled=[], spend_usd=21.0,
                 cards_registered=0, budget_state="WARN")
    assert "BUDGET ALERT" in (tmp_path / "digest_20260815.txt").read_text(encoding="utf-8")


def test_action_log_chains_and_detects_tamper(tmp_path):
    p = tmp_path / "reader_actions.jsonl"
    log = ActionLog(p)
    log.event("scanner_started", {"watchlist": 20})
    log.event("screen_batch", {"n": 5, "kept": 2})
    assert verify_chain(p)
    lines = p.read_text().splitlines()
    lines[0] = lines[0].replace("scanner_started", "scanner_tampered")
    p.write_text("\n".join(lines) + "\n")
    assert not verify_chain(p)


# ---------------- D28: cap raise + tightened intake bar -------------------

def test_default_monthly_cap_is_35_per_d33():
    """D33 (2026-08-17) cut the Reader's standing budget from 50 to 35 so that
    Reader plus the new pipeline line (20) sits inside D28's Intelligence band
    of 30-60. The decision was ratified in the contract and the vault, and the
    runtime kept metering against 50 -- which put the 80% alert at 40 instead
    of 28, i.e. the alert would not have fired until after the real cap.

    Both defaults move together: BudgetMeter's and the CLI's. They are the
    same number in two places, and the CLI one is what an operator actually
    gets.
    """
    from .scanner import run as scanner_run
    import inspect
    from .budget import BudgetMeter
    assert BudgetMeter(Path("nul-unused")).monthly_cap_usd == 35.0
    src = inspect.getsource(scanner_run)
    assert '"--cap", type=float, default=35.0' in src.replace("'", '"')


def test_low_testability_keeps_are_recorded_but_not_extracted():
    """Extraction is the dominant cost ($0.072/item): only high-testability
    keeps earn a full fetch."""
    items = _items(3)
    data = {"decisions": [
        {"id": "i0", "keep": True, "reason": "explicit backtestable rule",
         "testability": 0.9},
        {"id": "i1", "keep": True, "reason": "vague directional musing",
         "testability": 0.2},
        {"id": "i2", "keep": False, "reason": "marketing", "testability": 0.0},
    ]}
    out = parse_screen_response(items, data)
    assert out["i0"][0] == "screen_keep"
    assert out["i1"][0] == "screen_keep_low"   # logged, never extracted
    assert out["i2"][0] == "screen_kill"


def test_missing_testability_defaults_to_pass():
    out = parse_screen_response(_items(1),
                                {"decisions": [{"id": "i0", "keep": True,
                                                "reason": "r"}]})
    assert out["i0"][0] == "screen_keep"


def test_thin_pages_skip_extraction(tmp_path):
    src = make_source()
    seen = SeenStore(tmp_path / "seen.jsonl")
    items = [{"source_id": src["id"], "item_id": "thin", "title": "T",
              "link": "https://example.org/thin", "summary": "s",
              "published": None}]
    seen.record("thin", src["id"], "seen", title="T",
                link="https://example.org/thin")
    client = StubClient([_screen_msg([{"id": "thin", "keep": True,
                                       "reason": "looks testable",
                                       "testability": 0.9}])])
    registry = Registry(tmp_path / "reg.jsonl")
    stats = process_new_items(
        items, client=client, model="claude-sonnet-5",
        meter=BudgetMeter(tmp_path / "led.jsonl"), seen=seen, registry=registry,
        fetch=_fetch_factory({"https://example.org/thin":
                              (200, "<html><body><p>Short teaser.</p></body></html>")}),
        watchlist_sources=[src], discovery_path=tmp_path / "d.jsonl",
        screen_log=tmp_path / "s.jsonl", actions=ActionLog(tmp_path / "a.jsonl"))
    assert stats["thin_content"] == 1
    assert stats["cards_registered"] == 0
    assert seen.status("thin") == "thin_content"
    assert registry.cards() == {}


# ---------------- incident 2026-08-15: runaway retry + link soup ----------

class BillingErrorClient(StubClient):
    def __init__(self, n_errors=99):
        super().__init__([])
        self.n_errors = n_errors
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                raise RuntimeError(
                    "Error code: 400 - {'type': 'error', 'error': {'type': "
                    "'invalid_request_error', 'message': 'Your credit balance "
                    "is too low to access the Anthropic API.'}}")
        self.messages = _Messages()


def test_credit_exhaustion_stops_the_batch_loop_immediately(tmp_path):
    """The 08-15 incident: a billing 400 must abort the run, not retry every
    batch (105k decisions / 100MB logs in 2h)."""
    from .relevance import screen_items, ApiCreditExhausted
    client = BillingErrorClient()
    items = _items(60)  # 3 batches of 20
    with pytest.raises(ApiCreditExhausted):
        screen_items(client, "claude-sonnet-5", items,
                     BudgetMeter(tmp_path / "led.jsonl"),
                     tmp_path / "screen_log.jsonl")
    assert len(client.calls) == 1  # aborted after the first failure


def test_deferred_items_park_after_retry_cap(tmp_path):
    from .seen import SeenStore
    from .scanner import refeedable_deferred
    seen = SeenStore(tmp_path / "seen.jsonl")
    seen.record("a", "s1", "seen", title="t", link="https://x/a")
    for _ in range(3):
        seen.record("a", "s1", "deferred_screen", reason="api_error",
                    title="t", link="https://x/a")
    seen.record("b", "s1", "deferred_screen", reason="api_error",
                title="t", link="https://x/b")
    refeed = refeedable_deferred(seen, max_attempts=3)
    assert [i["item_id"] for i in refeed] == ["b"]  # 'a' parked after 3 tries
    assert seen.status("a") == "deferred_parked"


def test_orphaned_seen_items_resume_after_restart(tmp_path):
    """poll_source marks items 'seen' BEFORE screening; a restart in between
    stranded them forever (2,002 items on 08-15) - dedup blocked re-polling
    and only deferred_* was re-fed."""
    from .scanner import refeedable_deferred
    seen = SeenStore(tmp_path / "seen.jsonl")
    old = _iso(_now() - timedelta(hours=2))
    seen.record("stranded", "s1", "seen", title="T", link="https://x/1",
                ts_utc=old)
    seen.record("this-cycle", "s1", "seen", title="T", link="https://x/2")
    seen.record("done", "s1", "seen", title="T", link="https://x/3", ts_utc=old)
    seen.record("done", "s1", "screen_kill", reason="spam")
    got = refeedable_deferred(seen)
    ids = [i["item_id"] for i in got]
    assert "stranded" in ids          # resumed
    assert "this-cycle" not in ids    # still being processed right now
    assert "done" not in ids          # already decided


def test_resume_respects_the_retry_cap(tmp_path):
    from .scanner import refeedable_deferred
    seen = SeenStore(tmp_path / "seen.jsonl")
    old = _iso(_now() - timedelta(hours=2))
    for _ in range(3):
        seen.record("cursed", "s1", "seen", title="T", link="https://x/c",
                    ts_utc=old)
    assert refeedable_deferred(seen) == []
    assert seen.status("cursed") == "deferred_parked"


def test_feed_autodiscovery_from_listing_html():
    from .feeds import discover_feed
    html = ('<html><head><link rel="alternate" type="application/rss+xml" '
            'href="/feed/" title="RSS"></head><body>x</body></html>')
    assert discover_feed(html, "https://blog.example/") == "https://blog.example/feed/"
    atom = ('<html><head><link rel="alternate" type="application/atom+xml" '
            'href="https://blog.example/atom.xml"></head></html>')
    assert discover_feed(atom, "https://blog.example/") == "https://blog.example/atom.xml"
    assert discover_feed("<html><head></head></html>", "https://blog.example/") is None


def test_html_listing_filters_to_plausible_articles_and_caps():
    from .feeds import article_links
    body = "".join(f'<a href="/posts/deep-dive-{i}">Post {i}</a>' for i in range(40))
    html = ("<html><body>"
            '<a href="/">Home</a><a href="/about">About</a>'
            '<a href="/tag/momentum">momentum</a><a href="/category/x">cat</a>'
            '<a href="https://other.example/thing">offsite</a>'
            '<a href="/feed/">RSS</a><a href="/wp-login.php">login</a>'
            f"{body}</body></html>")
    links = article_links(html, "https://blog.example/", cap=25)
    urls = [u for u, _ in links]
    assert len(urls) == 25                       # per-cycle cap holds
    assert all(u.startswith("https://blog.example/posts/") for u in urls)
    assert not any("/tag/" in u or "/about" in u or "other.example" in u
                   for u in urls)


def test_article_filter_rejects_date_archives_and_listing_variants():
    """Real URLs that survived the first filter and cost ~$7.85 to screen as
    'Date-range archive title with no content' (08-16 checkpoint)."""
    from .feeds import article_links
    base = "https://blog.example/"
    junk = [
        "/2013_09_22_archive.html",          # blogspot date archive
        "/2016_05_01_archive.html",
        "/archives/2019/03",                 # dated archive index
        "/2019/03/",                         # bare year/month
        "/2019/",                            # bare year
        "/weblog/2008/09/index.html",        # typepad archive index
        "/?m=1",                             # mobile duplicate of the listing
        "/posts/real-article?m=1",           # mobile duplicate of an article
        "/?updated-max=2019-03-01T00:00:00",  # blogspot pagination
        "/search/label/momentum",            # label listing
        "/author/admin-2/",                  # author listing
    ]
    keep = ["/2019/03/actual-post-title.html", "/posts/volatility-parity-sizing"]
    html = "".join(f'<a href="{u}">x</a>' for u in junk + keep)
    urls = [u for u, _ in article_links(html, base, cap=50)]
    assert sorted(urls) == sorted(base.rstrip("/") + k for k in keep)


def test_poll_source_html_mode_uses_article_filter(tmp_path):
    src = make_source(feed=None, url="https://blog.example/")
    html = ("<html><body><a href='/nav'>Nav</a>"
            + "".join(f"<a href='/2026/08/post-{i}'>Post {i}</a>" for i in range(5))
            + "</body></html>")
    seen = SeenStore(tmp_path / "seen.jsonl")
    got = poll_source(src, seen, _fetch_factory({src["url"]: (200, html)}))
    assert len(got) == 5 and all("/2026/08/post-" in i["link"] for i in got)


# ---------------- D27 quality-bar auto-admit ----------------

def test_discovery_queue_accumulates_distinct_citers(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://newsrc.example/a", found_in="blog1/item1", reason="cited")
    queue_discovery(q, "https://newsrc.example/b", found_in="blog1/item2", reason="cited")
    queue_discovery(q, "https://newsrc.example/c", found_in="blog2/item9", reason="cited")
    entry = load_discovery(q)[0]
    assert sorted(entry["cited_by"]) == ["blog1", "blog2"]  # distinct sources, not items


def test_pollable_accepts_auto_d27_provenance(tmp_path):
    srcs = [make_source(id="human"),
            make_source(id="auto", added_by="auto-d27"),
            make_source(id="auto-unstamped", added_by="auto-d27", verified_date=None),
            make_source(id="rogue", added_by="claude")]
    p = write_watchlist(tmp_path, srcs)
    assert {s["id"] for s in pollable(load_watchlist(p))} == {"human", "auto"}


def test_auto_admit_scout_and_two_citers_only(tmp_path):
    from .scanner import process_auto_admissions
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://scoutfind.example/", found_in="scout/2026-08-15",
                    reason="scout (blog): researched")
    queue_discovery(q, "https://endorsed.example/post", found_in="blog1/i1", reason="cited")
    queue_discovery(q, "https://endorsed.example/other", found_in="blog2/i2", reason="cited")
    queue_discovery(q, "https://oncecited.example/x", found_in="blog1/i3", reason="cited")
    wl = write_watchlist(tmp_path, [make_source()])
    actions = ActionLog(tmp_path / "act.jsonl")
    admitted = process_auto_admissions(discovery_path=q, watchlist_path=wl,
                                       actions=actions)
    assert {e["id"] for e in admitted} == {"scoutfind.example", "endorsed.example"}
    sources = load_watchlist(wl)
    added = {s["id"]: s for s in sources}
    assert added["scoutfind.example"]["added_by"] == "auto-d27"
    assert added["scoutfind.example"]["verified_date"]
    assert added["endorsed.example"] in pollable(sources)
    statuses = {e["domain"]: e["status"] for e in load_discovery(q)}
    assert statuses["scoutfind.example"] == "auto_admitted"
    assert statuses["endorsed.example"] == "auto_admitted"
    assert statuses["oncecited.example"] == "proposed"  # stays for the panel
    entries = [json.loads(l) for l in (tmp_path / "act.jsonl").read_text().splitlines()]
    assert sum(1 for e in entries if e["entry_type"] == "source_auto_admitted") == 2
    # idempotent: second pass admits nothing
    assert process_auto_admissions(discovery_path=q, watchlist_path=wl,
                                   actions=actions) == []


def test_auto_admit_never_readmits_blocked_or_existing(tmp_path):
    from .scanner import process_auto_admissions
    from .approvals import process_approvals
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://blockedone.example/", found_in="scout/2026-08-15",
                    reason="scout")
    entries = load_discovery(q)
    entries[0]["status"] = "blocked"  # Coen blocked it earlier
    q.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    wl = write_watchlist(tmp_path, [make_source(id="scoutfind.example",
                                                url="https://scoutfind.example/")])
    queue_discovery(q, "https://scoutfind.example/", found_in="scout/2026-08-15",
                    reason="scout")  # already on watchlist
    admitted = process_auto_admissions(discovery_path=q, watchlist_path=wl,
                                       actions=ActionLog(tmp_path / "act.jsonl"))
    assert admitted == []
    assert len(load_watchlist(wl)) == 1


# ---------------- approvals consumer (D26 write stage) ----------------

def _approval_record(key, domain="freshquant.example", decision="approve",
                     tamper=False):
    from .approvals import sign_record
    rec = {"action": "source_decision", "domain": domain,
           "url": f"https://{domain}/blog", "decision": decision,
           "name": "Fresh Quant", "source_class": "blog",
           "actor": "coen", "via": "morpheus-ops",
           "ts_utc": "2026-08-15T03:00:00Z"}
    rec["id"] = rec["domain"] + "-" + rec["ts_utc"]
    rec["sig"] = sign_record(rec, key)
    if tamper:
        rec["domain"] = "evil.example"
    return rec


def _approvals_env(tmp_path, records):
    q = tmp_path / "approvals_queue.jsonl"
    q.write_text("".join(json.dumps(r) + "\n" for r in records),
                 encoding="utf-8")
    wl = write_watchlist(tmp_path, [make_source()])
    disc = tmp_path / "discovery.jsonl"
    queue_discovery(disc, "https://freshquant.example/blog",
                    found_in="scout/2026-08-15", reason="scout: fresh")
    return q, wl, disc


def test_approval_admits_source_and_is_idempotent(tmp_path):
    from .approvals import process_approvals
    key = "k" * 64
    q, wl, disc = _approvals_env(tmp_path, [_approval_record(key)])
    actions = ActionLog(tmp_path / "act.jsonl")
    state = tmp_path / "approvals_state.json"
    result = process_approvals(queue_path=q, watchlist_path=wl,
                               discovery_path=disc, actions=actions,
                               state_path=state, key=key)
    assert len(result["approved"]) == 1
    sources = load_watchlist(wl)
    added = [s for s in sources if s["id"] == "freshquant.example"][0]
    assert added["added_by"] == "coen"
    assert added["verified_date"] == "2026-08-15"
    assert added in pollable(sources)
    assert [d["status"] for d in load_discovery(disc)] == ["approved"]
    entries = [json.loads(l) for l in (tmp_path / "act.jsonl").read_text().splitlines()]
    assert any(e["entry_type"] == "source_approved" for e in entries)
    # idempotent: run again, nothing changes
    again = process_approvals(queue_path=q, watchlist_path=wl,
                              discovery_path=disc, actions=actions,
                              state_path=state, key=key)
    assert again["approved"] == [] and len(load_watchlist(wl)) == 2


def test_tampered_approval_is_rejected_and_chain_logged(tmp_path):
    from .approvals import process_approvals
    key = "k" * 64
    q, wl, disc = _approvals_env(tmp_path, [_approval_record(key, tamper=True)])
    actions = ActionLog(tmp_path / "act.jsonl")
    result = process_approvals(queue_path=q, watchlist_path=wl,
                               discovery_path=disc, actions=actions,
                               state_path=tmp_path / "st.json", key=key)
    assert result["approved"] == [] and result["invalid"] == 1
    assert len(load_watchlist(wl)) == 1  # nothing admitted
    entries = [json.loads(l) for l in (tmp_path / "act.jsonl").read_text().splitlines()]
    assert any(e["entry_type"] == "approval_rejected" for e in entries)


def test_block_flips_proposal_without_touching_watchlist(tmp_path):
    from .approvals import process_approvals
    key = "k" * 64
    q, wl, disc = _approvals_env(tmp_path, [_approval_record(key, decision="block")])
    result = process_approvals(queue_path=q, watchlist_path=wl,
                               discovery_path=disc,
                               actions=ActionLog(tmp_path / "act.jsonl"),
                               state_path=tmp_path / "st.json", key=key)
    assert result["blocked"] == 1 and result["approved"] == []
    assert len(load_watchlist(wl)) == 1
    assert [d["status"] for d in load_discovery(disc)] == ["blocked"]
    # domain was never on the watchlist, so nothing was actually revoked
    assert result["revoked"] == []


def test_process_approvals_returns_revoked_domains_for_pruning(tmp_path):
    """`revoked` is what scanner.run() uses to drop a just-blocked source out
    of `sources`/`next_due` immediately, instead of waiting for the next
    cycle's refresh_sources() reload."""
    from .approvals import process_approvals
    key = "k" * 64
    wl = write_watchlist(tmp_path, [make_source(id="dead.example",
                                                url="https://dead.example/blog")])
    disc = tmp_path / "discovery.jsonl"
    q = tmp_path / "approvals_queue.jsonl"
    rec = _approval_record(key, domain="dead.example", decision="block")
    q.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    result = process_approvals(queue_path=q, watchlist_path=wl,
                               discovery_path=disc,
                               actions=ActionLog(tmp_path / "act.jsonl"),
                               state_path=tmp_path / "st.json", key=key)
    assert result["blocked"] == 1
    assert result["revoked"] == ["dead.example"]
    assert load_watchlist(wl) == []


def test_idless_signed_record_is_processed_once_not_every_poll(tmp_path):
    """A signed record with no 'id' must dedup on the SAME key used to add
    it to the processed set, or the scanner's next poll cycle re-reads the
    same approvals_queue.jsonl and reprocesses it forever."""
    from .approvals import process_approvals, sign_record
    key = "k" * 64
    rec = {"action": "source_decision", "domain": "noid.example",
           "url": "https://noid.example/blog", "decision": "approve",
           "name": "No Id", "source_class": "blog", "actor": "coen",
           "via": "morpheus-ops", "ts_utc": "2026-08-15T03:00:00Z"}
    rec["sig"] = sign_record(rec, key)
    q = tmp_path / "approvals_queue.jsonl"
    q.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    wl = write_watchlist(tmp_path, [make_source()])
    disc = tmp_path / "discovery.jsonl"
    state = tmp_path / "st.json"
    actions = ActionLog(tmp_path / "act.jsonl")
    first = process_approvals(queue_path=q, watchlist_path=wl, discovery_path=disc,
                              actions=actions, state_path=state, key=key)
    assert len(first["approved"]) == 1
    # simulate the scanner's next poll cycle re-reading the same queue file
    # + state file, exactly as scanner.run() does every loop iteration
    second = process_approvals(queue_path=q, watchlist_path=wl, discovery_path=disc,
                               actions=actions, state_path=state, key=key)
    assert second["approved"] == [] and second["blocked"] == 0 and second["invalid"] == 0
    assert len(load_watchlist(wl)) == 2  # not re-admitted a second time


# ---------------- source scout (weekly) ----------------

def _scout_msg(candidates, stop_reason="end_turn", searches=2):
    content = [SimpleNamespace(type="server_tool_use", name="web_search",
                               id=f"srvtoolu_{i}", input={"query": "q"})
               for i in range(searches)]
    content.append(SimpleNamespace(
        type="text", text=json.dumps({"candidates": candidates})))
    return SimpleNamespace(
        stop_reason=stop_reason, content=content,
        usage=SimpleNamespace(input_tokens=4000, output_tokens=400,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0))


def test_budget_record_call_extra_usd():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        m = BudgetMeter(Path(td) / "led.jsonl")
        u = SimpleNamespace(input_tokens=1_000_000, output_tokens=0,
                            cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)
        usd = m.record_call("claude-sonnet-5", u, purpose="scout", agent="reader",
                            extra_usd=0.08)
        assert usd == pytest.approx(3.08)
        assert m.month_spend() == pytest.approx(3.08)


def test_scout_queues_new_domains_and_skips_known(tmp_path):
    from .scout import run_scout
    watchlist = [make_source(id="rob-carver", url="https://qoppac.blogspot.com/",
                             feed="https://qoppac.blogspot.com/feeds/posts/default")]
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://alreadyqueued.example/post", found_in="x",
                    reason="cited")
    candidates = [
        {"url": "https://freshquant.example/blog", "name": "Fresh Quant",
         "why": "systematic futures research", "source_class": "blog"},
        {"url": "https://qoppac.blogspot.com/some-post", "name": "Carver",
         "why": "already on watchlist", "source_class": "blog"},
        {"url": "https://alreadyqueued.example/other", "name": "Dup",
         "why": "already queued", "source_class": "blog"},
        {"url": "https://twitter.com/quantguy", "name": "Junk",
         "why": "social", "source_class": "blog"},
    ]
    client = StubClient([_scout_msg(candidates, searches=3)])
    meter = BudgetMeter(tmp_path / "led.jsonl")
    result = run_scout(client=client, model="claude-sonnet-5", meter=meter,
                       watchlist_sources=watchlist, discovery_path=q,
                       actions=ActionLog(tmp_path / "act.jsonl"))
    assert result["queued"] == 1 and result["searches"] == 3
    domains = {e["domain"] for e in load_discovery(q)}
    assert domains == {"alreadyqueued.example", "freshquant.example"}
    rows = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text().splitlines()]
    assert rows[-1]["purpose"] == "scout"
    assert rows[-1]["usd"] > 0.03  # includes 3 searches at $0.01 on top of tokens


def test_scout_resumes_pause_turn(tmp_path):
    from .scout import run_scout
    final = _scout_msg([{"url": "https://newsource.example/", "name": "N",
                         "why": "w", "source_class": "blog"}], searches=1)
    paused = _scout_msg([], stop_reason="pause_turn", searches=2)
    client = StubClient([paused, final])
    result = run_scout(client=client, model="claude-sonnet-5",
                       meter=BudgetMeter(tmp_path / "led.jsonl"),
                       watchlist_sources=[make_source()],
                       discovery_path=tmp_path / "q.jsonl",
                       actions=ActionLog(tmp_path / "act.jsonl"))
    assert result["queued"] == 1
    assert len(client.calls) == 2
    # second call resumes with the paused assistant content appended
    assert client.calls[1]["messages"][-1]["role"] == "assistant"


def test_scout_respects_budget_cap(tmp_path):
    from .scout import run_scout
    client = StubClient([])  # any call raises
    result = run_scout(client=client, model="claude-sonnet-5",
                       meter=BudgetMeter(tmp_path / "led.jsonl",
                                         monthly_cap_usd=0.0),
                       watchlist_sources=[make_source()],
                       discovery_path=tmp_path / "q.jsonl",
                       actions=ActionLog(tmp_path / "act.jsonl"))
    assert result["queued"] == 0 and result.get("skipped") == "budget"
    assert client.calls == []


# ---------------- inbox drop-folder ingest ----------------

ARTICLE_HTML = ("<html><head><title>VIX trend following out of sample</title>"
                "</head><body><p>We find VIX trend following works out of "
                "sample with a Sharpe of 0.9.</p></body></html>")

INBOX_CLAIMS = [{"claim": "VIX trend following works out of sample.",
                 "quote": "VIX trend following works out of sample",
                 "locator": "full document", "asset_classes": ["cross"],
                 "topics": ["trend"], "horizon": "daily",
                 "testability_score": 0.7, "data_required": ["VIX"],
                 "notes": None}]


def _inbox_msg():
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text",
                                 text=json.dumps({"claims": INBOX_CLAIMS}))],
        usage=SimpleNamespace(input_tokens=300, output_tokens=30,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0))


def test_inbox_match_flagged_item_by_title():
    from .scanner import match_flagged
    seen = SeenStore(Path("nul") if False else Path("x"))  # not used for read
    seen._latest = {
        "p1": {"item_id": "p1", "source_id": "alpha-architect",
               "status": "paywalled", "title": "VIX Trend Following Out of Sample",
               "link": "https://alphaarchitect.com/vix-trend-following-out-of-sample/",
               "reason": "http 403", "ts_utc": "t", "first_seen_utc": "t"},
        "k1": {"item_id": "k1", "source_id": "blog", "status": "extracted",
               "title": "Something else", "link": "https://x/e", "reason": None,
               "ts_utc": "t", "first_seen_utc": "t"},
    }
    hit = match_flagged("VIX trend following out of sample", seen)
    assert hit and hit["item_id"] == "p1"
    assert match_flagged("Unrelated title entirely", seen) is None


def test_inbox_ingests_dropped_html_and_resolves_flagged_item(tmp_path):
    from .scanner import process_inbox
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "saved_page.html").write_text(ARTICLE_HTML, encoding="utf-8")
    seen = SeenStore(tmp_path / "seen.jsonl")
    seen.record("p1", "alpha-architect", "paywalled",
                title="VIX Trend Following Out of Sample",
                link="https://alphaarchitect.com/vix-trend-following-out-of-sample/",
                reason="http 403")
    registry = Registry(tmp_path / "reg.jsonl")
    meter = BudgetMeter(tmp_path / "led.jsonl")
    client = StubClient([_inbox_msg()])
    stats = process_inbox(client=client, model="claude-sonnet-5", meter=meter,
                          seen=seen, registry=registry,
                          actions=ActionLog(tmp_path / "act.jsonl"),
                          inbox=inbox)
    assert stats["files"] == 1 and stats["cards_registered"] == 1
    card = next(iter(registry.cards(status="pending").values()))
    assert card["source"]["url"].startswith("https://alphaarchitect.com/vix")
    assert card["extraction"]["run_id"].endswith("-inbox")
    assert seen.status("p1") == "extracted"
    assert not (inbox / "saved_page.html").exists()
    assert (inbox / "processed" / "saved_page.html").exists()
    assert verify_chain(tmp_path / "act.jsonl")


def test_inbox_sidecar_url_wins_and_unidentified_file_is_left(tmp_path):
    from .scanner import process_inbox
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.html").write_text(ARTICLE_HTML, encoding="utf-8")
    (inbox / "a.html.meta.json").write_text(
        json.dumps({"url": "https://papers.ssrn.com/paper=123",
                    "source_type": "paper"}), encoding="utf-8")
    (inbox / "mystery.html").write_text(
        "<html><title>No identity here</title><body>text</body></html>",
        encoding="utf-8")
    registry = Registry(tmp_path / "reg.jsonl")
    client = StubClient([_inbox_msg()])
    stats = process_inbox(client=client, model="claude-sonnet-5",
                          meter=BudgetMeter(tmp_path / "led.jsonl"),
                          seen=SeenStore(tmp_path / "seen.jsonl"),
                          registry=registry,
                          actions=ActionLog(tmp_path / "act.jsonl"),
                          inbox=inbox)
    card = next(iter(registry.cards(status="pending").values()))
    assert card["source"]["url"] == "https://papers.ssrn.com/paper=123"
    assert card["source"]["type"] == "paper"
    assert stats["skipped_no_identity"] == 1
    assert (inbox / "mystery.html").exists()  # left for Coen to add a sidecar
    assert not (inbox / "a.html").exists()
    assert not (inbox / "a.html.meta.json").exists()


def test_inbox_cards_count_in_cumulative_total(tmp_path):
    from .scanner import scanner_cards_total
    from .reader import build_card
    registry = Registry(tmp_path / "reg.jsonl")
    meta = {"type": "paper", "title": "T", "authors": [], "year": None,
            "url": "https://x/a", "doi": None, "isbn": None,
            "credibility_tier": "practitioner"}
    raw = INBOX_CLAIMS[0]
    registry.register_card(build_card(raw, meta, "m", "2026-08-15-inbox"))
    assert scanner_cards_total(registry) == 1


# ---------------- 48h checkpoint report ----------------

def _ev(item, src, status, first, ts=None, reason=None):
    return {"item_id": item, "source_id": src, "status": status,
            "title": "t", "link": f"https://x/{item}", "reason": reason,
            "ts_utc": ts or first, "first_seen_utc": first}


def test_report_aggregates_per_source_within_window():
    from .report import build_report
    since = "2026-08-14T00:00:00Z"
    seen = [
        _ev("a", "blog1", "extracted", "2026-08-14T10:00:00Z", reason="3 cards"),
        _ev("b", "blog1", "screen_kill", "2026-08-14T11:00:00Z", reason="spam"),
        _ev("c", "blog2", "paywalled", "2026-08-14T12:00:00Z"),
        _ev("old", "blog1", "extracted", "2026-08-10T00:00:00Z", reason="9 cards"),
    ]
    screen_rows = [
        {"ts_utc": "2026-08-14T10:00:00Z", "item_id": "a", "decision": "screen_keep",
         "reason": "edge", "model": "m"},
        {"ts_utc": "2026-08-14T11:00:00Z", "item_id": "b", "decision": "screen_kill",
         "reason": "spam", "model": "m"},
        {"ts_utc": "2026-08-10T00:00:00Z", "item_id": "old", "decision": "screen_keep",
         "reason": "edge", "model": "m"},
    ]
    ledger = [
        {"ts_utc": "2026-08-14T10:00:00Z", "purpose": "screen", "usd": 0.10,
         "model": "m", "input_tokens": 1, "output_tokens": 1,
         "cache_read_tokens": 0, "cache_write_tokens": 0},
        {"ts_utc": "2026-08-14T10:05:00Z", "purpose": "extract", "usd": 0.50,
         "model": "m", "input_tokens": 1, "output_tokens": 1,
         "cache_read_tokens": 0, "cache_write_tokens": 0},
        {"ts_utc": "2026-08-10T00:00:00Z", "purpose": "extract", "usd": 9.99,
         "model": "m", "input_tokens": 1, "output_tokens": 1,
         "cache_read_tokens": 0, "cache_write_tokens": 0},
    ]
    rep = build_report(seen, screen_rows, ledger, discovery=[], since_utc=since)
    assert rep["window"]["items_seen"] == 3  # 'old' outside the cohort
    b1 = rep["per_source"]["blog1"]
    assert b1["seen"] == 2 and b1["extracted"] == 1 and b1["cards"] == 3
    assert rep["per_source"]["blog2"]["paywalled"] == 1
    assert rep["spend"]["screen"] == pytest.approx(0.10)
    assert rep["spend"]["extract"] == pytest.approx(0.50)
    assert rep["spend"]["total"] == pytest.approx(0.60)
    assert rep["kill_reasons"]["spam"] == 1


def test_report_renders_readable_text():
    from .report import build_report, render_report
    seen = [_ev("a", "blog1", "extracted", "2026-08-14T10:00:00Z",
                reason="2 cards")]
    rep = build_report(seen, [], [], discovery=[],
                       since_utc="2026-08-14T00:00:00Z")
    text = render_report(rep)
    assert "blog1" in text and "2" in text
    assert "spend" in text.lower()


# ---------------- registry file lock ----------------

def test_filelock_acquire_release_roundtrip(tmp_path):
    target = tmp_path / "reg.jsonl"
    lock = FileLock(target)
    with lock:
        assert lock.lock_path.exists()
    assert not lock.lock_path.exists()
    with FileLock(target):  # reacquirable after release
        pass


def test_filelock_contention_times_out(tmp_path):
    target = tmp_path / "reg.jsonl"
    holder = FileLock(target)
    holder.acquire()
    try:
        with pytest.raises(FileLockTimeout):
            FileLock(target, timeout=0.3).acquire()
    finally:
        holder.release()
    # released -> acquirable again
    with FileLock(target, timeout=0.3):
        pass


def test_filelock_breaks_stale_lock(tmp_path):
    target = tmp_path / "reg.jsonl"
    stale = FileLock(target)
    stale.lock_path.write_text("dead-pid")
    old = time.time() - 3600
    os.utime(stale.lock_path, (old, old))
    with FileLock(target, timeout=1.0, stale_after=60.0):  # must not time out
        pass


def test_filelock_retries_transient_permission_error(tmp_path, monkeypatch):
    """Windows delete-pending race: os.open(O_CREAT|O_EXCL) on a lockfile
    that another process is mid-unlink (or that AV briefly holds) fails with
    ERROR_ACCESS_DENIED -> PermissionError, not FileExistsError. acquire()
    must treat that as contention and retry, not crash the writer."""
    target = tmp_path / "reg.jsonl"
    lock_path = str(target) + ".lock"
    real_open = os.open
    denials = {"left": 3}

    def flaky_open(path, flags, *args, **kwargs):
        if str(path) == lock_path and denials["left"] > 0:
            denials["left"] -= 1
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky_open)
    with FileLock(target, timeout=5.0, poll=0.01):
        pass
    assert denials["left"] == 0  # the denials were consumed, then it won


def test_filelock_persistent_permission_error_times_out(tmp_path, monkeypatch):
    """A genuinely unwritable lock dir must surface as FileLockTimeout after
    the deadline -- bounded, no hot spin, no raw PermissionError escape."""
    target = tmp_path / "reg.jsonl"
    lock_path = str(target) + ".lock"
    real_open = os.open

    def denied_open(path, flags, *args, **kwargs):
        if str(path) == lock_path:
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied_open)
    with pytest.raises(FileLockTimeout):
        FileLock(target, timeout=0.3, poll=0.02).acquire()


def test_concurrent_appends_keep_chain_linear(tmp_path):
    """The hazard that bit twice on 08-14: multiple processes appending the
    same registry must never fork the chain."""
    log = tmp_path / "reg.jsonl"
    # The 60s lock timeout (vs the 10s default) is CPU-load headroom for
    # full-suite runs where writer processes get starved; it is setup
    # hardening only. The assertions below are untouched: every append
    # lands (100 entries) and the chain stays linear.
    script = (
        "import sys, functools\n"
        "import pipeline.registry as reg\n"
        "reg.FileLock = functools.partial(reg.FileLock, timeout=60.0)\n"
        f"r = reg.Registry({str(log)!r})\n"
        "for i in range(25):\n"
        "    r.append('note', {'writer': sys.argv[1], 'i': i})\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(w)],
                              cwd=str(LAYER)) for w in range(4)]
    for p in procs:
        assert p.wait(timeout=120) == 0
    entries = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 100
    assert verify_chain(log)


# ---------------- scanner orchestration ----------------

def _fetch_factory(pages):
    def fetch(url, timeout=30):
        status, text = pages[url]
        return status, text, url
    return fetch


def test_poll_source_dedups_against_seen_store(tmp_path):
    src = make_source()
    seen = SeenStore(tmp_path / "seen.jsonl")
    fetch = _fetch_factory({src["feed"]: (200, RSS_XML)})
    new1 = poll_source(src, seen, fetch)
    assert len(new1) == 2
    for it in new1:
        assert seen.status(it["item_id"]) == "seen"
    new2 = poll_source(src, seen, fetch)
    assert new2 == []


def test_full_funnel_registers_pending_card_and_flags_paywall(tmp_path):
    src = make_source()
    seen = SeenStore(tmp_path / "seen.jsonl")
    fetch = _fetch_factory({src["feed"]: (200, RSS_XML)})
    new_items = poll_source(src, seen, fetch)

    # realistic article length: the thin-content guard skips sub-1200-char pages
    filler = ("<p>We test the effect across a long sample of daily bars and "
              "report the resulting risk-adjusted performance in detail.</p>" * 12)
    article = ("<html><body><p>We find that momentum persists in crypto for "
               "20 days after formation.</p>" + filler +
               '<a href="https://citedblog.example/research">cited</a>'
               "</body></html>")
    pages = {
        "https://example.org/momo?utm_source=rss": (200, article),
        "https://example.org/course": (403, "forbidden"),
    }
    claims = [{"claim": "Crypto momentum persists for 20 days after formation.",
               "quote": "momentum persists in crypto for 20 days after formation",
               "locator": "full document", "asset_classes": ["crypto"],
               "topics": ["momentum"], "horizon": "daily",
               "testability_score": 0.8, "data_required": ["daily OHLCV"],
               "notes": None},
              {"claim": "A fabricated claim with no support.",
               "quote": "this quote is not in the article",
               "locator": "full document", "asset_classes": ["crypto"],
               "topics": ["momentum"], "horizon": "daily",
               "testability_score": 0.8, "data_required": ["daily OHLCV"],
               "notes": None}]
    client = StubClient([
        _screen_msg([{"id": new_items[0]["item_id"], "keep": True, "reason": "edge"},
                     {"id": new_items[1]["item_id"], "keep": True, "reason": "check"}]),
        SimpleNamespace(  # extraction call for the one fetchable article
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text",
                                     text=json.dumps({"claims": claims}))],
            usage=SimpleNamespace(input_tokens=800, output_tokens=80,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0)),
    ])
    registry = Registry(tmp_path / "registry_log.jsonl")
    meter = BudgetMeter(tmp_path / "ledger.jsonl")
    stats = process_new_items(
        new_items, client=client, model="claude-sonnet-5", meter=meter,
        seen=seen, registry=registry, fetch=_fetch_factory(pages),
        watchlist_sources=[src], discovery_path=tmp_path / "discovery.jsonl",
        screen_log=tmp_path / "screen_log.jsonl",
        actions=ActionLog(tmp_path / "actions.jsonl"))

    cards = registry.cards(status="pending")
    assert len(cards) == 1  # honesty guard dropped the fabricated one
    card = next(iter(cards.values()))
    assert card["extraction"]["model"] == "claude-sonnet-5"
    assert card["source"]["url"].startswith("https://example.org/momo")
    assert seen.status(new_items[0]["item_id"]) == "extracted"
    assert seen.status(new_items[1]["item_id"]) == "paywalled"
    assert stats["cards_registered"] == 1
    assert stats["honesty_dropped"] == 1
    # off-watchlist cited link queued for Coen, never fetched
    disc = load_discovery(tmp_path / "discovery.jsonl")
    assert any("citedblog.example" in d["url"] for d in disc)
    assert verify_chain(tmp_path / "actions.jsonl")


def test_funnel_at_budget_cap_polls_but_defers(tmp_path):
    src = make_source()
    seen = SeenStore(tmp_path / "seen.jsonl")
    new_items = poll_source(src, seen, _fetch_factory({src["feed"]: (200, RSS_XML)}))
    assert len(new_items) == 2  # polling still works at cap
    client = StubClient([])  # no model call may happen
    meter = BudgetMeter(tmp_path / "ledger.jsonl", monthly_cap_usd=0.0)
    process_new_items(
        new_items, client=client, model="claude-sonnet-5", meter=meter,
        seen=seen, registry=Registry(tmp_path / "reg.jsonl"),
        fetch=_fetch_factory({}), watchlist_sources=[src],
        discovery_path=tmp_path / "d.jsonl",
        screen_log=tmp_path / "s.jsonl",
        actions=ActionLog(tmp_path / "a.jsonl"))
    assert all(seen.status(i["item_id"]) == "deferred_budget" for i in new_items)
    assert client.calls == []


def test_scanner_cards_total_is_cumulative_and_survives_triage(tmp_path):
    """The status counter must not drop to zero when triage clears pending -
    it reports what the scanner has registered, ever."""
    from .scanner import scanner_cards_total
    from .reader import build_card
    registry = Registry(tmp_path / "reg.jsonl")
    meta = {"type": "blog", "title": "T", "authors": [], "year": None,
            "url": "https://x.example/a", "doi": None, "isbn": None,
            "credibility_tier": "practitioner"}
    raw = {"claim": "Scanner claim one.", "quote": "q", "locator": "l",
           "asset_classes": ["crypto"], "topics": ["t"], "horizon": "daily",
           "testability_score": 0.5, "data_required": ["d"], "notes": None}
    c1 = build_card(raw, meta, "claude-sonnet-5", "2026-08-14-scanner")
    c2 = build_card({**raw, "claim": "Scanner claim two."}, meta,
                    "claude-sonnet-5", "2026-08-15-scanner")
    manual = build_card({**raw, "claim": "Manual corpus claim."}, meta,
                        "claude-opus-5", "2026-08-14-manual")
    for c in (c1, c2, manual):
        registry.register_card(c)
    assert scanner_cards_total(registry) == 2
    registry.review_card(c1["card_id"], "accepted", "coen")  # triage happened
    assert scanner_cards_total(registry) == 2  # cumulative, not pending


def test_pending_tier3_counts_pending_cards_not_discoveries(tmp_path):
    # 08-24: D27 case 3 admits/blocks proposals mechanically, so they no
    # longer wait on Coen -- only cards in triage count now.
    from .test_pipeline import make_card
    registry = Registry(tmp_path / "reg.jsonl")
    registry.register_card(make_card())
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://a.example/x", found_in="s/i", reason="cited")
    queue_discovery(q, "https://b.example/y", found_in="s/i", reason="cited")
    assert pending_tier3_count(registry) == 1  # q holds discoveries, no longer counted


def test_digest_has_probation_line(tmp_path):
    f = write_digest(tmp_path, date="20260824", new_by_source={}, rejections={},
                     discoveries=[], paywalled=[], spend_usd=0.0, cards_registered=0,
                     probation={"on_probation": 3, "admitted": 2, "promoted": 1, "revoked": 0, "timed_out": 1, "blocked": 4})
    text = (tmp_path / f).read_text(encoding="utf-8")
    assert "Source probation (D27 case 3): on probation 3 | admitted 2 | promoted 1 | revoked 0 | timed out 1 | blocked 4" in text


def test_pending_tier3_counts_cards_only_now(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://x.example/", found_in="blog1/i1", reason="cited")
    reg = Registry(tmp_path / "registry.jsonl")
    assert pending_tier3_count(reg) == 0     # proposals no longer wait on Coen


def test_refresh_sources_updates_next_due_preserving_existing_times(tmp_path):
    from .scanner import refresh_sources
    old = [make_source(id="keep"), make_source(id="drop")]
    wl = write_watchlist(tmp_path, [make_source(id="keep"), make_source(id="new")])
    next_due = {"keep": 123.0, "drop": 456.0}
    out = refresh_sources(wl, old, next_due)
    assert {s["id"] for s in out} == {"keep", "new"}
    # 'keep' keeps its existing due time exactly; 'new' defaults to due-now;
    # 'drop' (no longer on the watchlist) is gone
    assert next_due == {"keep": 123.0, "new": 0.0}


def test_refresh_sources_empty_reload_keeps_previous_and_leaves_next_due_untouched(tmp_path, capsys):
    from .scanner import refresh_sources
    old = [make_source(id="keep")]
    wl = write_watchlist(tmp_path, [])
    next_due = {"keep": 999.0}
    out = refresh_sources(wl, old, next_due)
    assert out is old
    assert next_due == {"keep": 999.0}
    assert "WARNING" in capsys.readouterr().err
