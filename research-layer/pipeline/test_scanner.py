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
    sources = load_watchlist(LAYER / "sources" / "verified_sources.json")
    assert len(sources) >= 15
    classes = {s["class"] for s in sources}
    assert classes == {"arxiv", "aggregator", "blog", "ssrn", "central_bank", "github"}
    # The gate admits exactly the Coen-stamped entries, nothing else.
    stamped = {s["id"] for s in sources if s["added_by"] == "coen" and s["verified_date"]}
    assert {s["id"] for s in pollable(sources)} == stamped
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
    m.record_call("claude-sonnet-5", u, purpose="screen")
    assert m.month_spend() == pytest.approx(3.0)
    m2 = BudgetMeter(p, monthly_cap_usd=25.0)
    assert m2.month_spend() == pytest.approx(3.0)
    assert m2.state() == "OK" and m2.can_spend()


def test_budget_warn_at_80_percent_and_cap_blocks(tmp_path):
    m = BudgetMeter(tmp_path / "ledger.jsonl", monthly_cap_usd=25.0)
    u80 = SimpleNamespace(input_tokens=0, output_tokens=1_400_000,  # $21.00
                          cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m.record_call("claude-sonnet-5", u80, purpose="extract")
    assert m.state() == "WARN" and m.can_spend()
    u_more = SimpleNamespace(input_tokens=0, output_tokens=300_000,  # +$4.50 -> $25.50
                             cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m.record_call("claude-sonnet-5", u_more, purpose="extract")
    assert m.state() == "CAP" and not m.can_spend()


def test_budget_month_rollover_resets(tmp_path):
    p = tmp_path / "ledger.jsonl"
    m = BudgetMeter(p, monthly_cap_usd=25.0)
    last_month = (_now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    u = SimpleNamespace(input_tokens=10_000_000, output_tokens=0,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m.record_call("claude-sonnet-5", u, purpose="extract", ts_utc=last_month)
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
    assert st["contract_version"] == "1.1"
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


def test_concurrent_appends_keep_chain_linear(tmp_path):
    """The hazard that bit twice on 08-14: multiple processes appending the
    same registry must never fork the chain."""
    log = tmp_path / "reg.jsonl"
    script = (
        "import sys\n"
        "from pipeline.registry import Registry\n"
        f"r = Registry({str(log)!r})\n"
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

    article = ("<html><body><p>We find that momentum persists in crypto for "
               "20 days after formation.</p>"
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


def test_pending_tier3_counts_discoveries_plus_pending_cards(tmp_path):
    from .test_pipeline import make_card
    registry = Registry(tmp_path / "reg.jsonl")
    registry.register_card(make_card())
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://a.example/x", found_in="s/i", reason="cited")
    queue_discovery(q, "https://b.example/y", found_in="s/i", reason="cited")
    assert pending_tier3_count(registry, q) == 3
