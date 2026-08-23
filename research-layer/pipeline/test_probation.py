"""Offline tests for the D27 case-3 probation filter (no network, no API)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from .watchlist import (load_watchlist, pollable, queue_discovery, load_discovery,
                        set_discovery_status, tier_of)
from .relevance import (build_source_screen_prompt, parse_source_screen,
                        screen_source, SOURCE_SCREEN_SYSTEM,
                        SOURCE_SCREEN_SCHEMA, SOURCE_SCREEN_MAX_TOKENS,
                        ApiCreditExhausted)
from .probation import prefilter, BLOCKED_SUBDOMAINS, MIN_INDEX_ITEMS
from .probation import (source_stats, decide_probation, WINDOW_1, WINDOW_2,
                        PROMOTE_KEEPS, TIMEOUT_DAYS)
from .probation import process_admissions, PROVENANCE_PROBATION
from .probation import process_reviews, PROVENANCE_PROMOTED
from .probation import prioritise_items, probation_counts, PRIORITY_CAP
from .probation import ADMISSIONS_PER_RUN
from .seen import SeenStore
from .scanstatus import ActionLog
from .approvals import process_approvals, sign_record
from datetime import datetime, timedelta


def test_contract_version_bumped_with_case_3():
    from .scanstatus import CONTRACT_VERSION
    assert CONTRACT_VERSION == "1.8"


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


def test_set_discovery_status(tmp_path):
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

# links need two path segments and a >=6-char slug to pass feeds.article_links
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


def test_prefilter_empty_feed_falls_back_to_index():
    empty_feed = '<?xml version="1.0"?><rss><channel><title>X</title></channel></rss>'
    f = _fetch_factory({
        "https://x.example/": (200, '<html><link rel="alternate" type="application/rss+xml" href="/feed">'
                                     f'<body>{_index_html(MIN_INDEX_ITEMS)}</body></html>', "https://x.example/"),
        "https://x.example/feed": (200, empty_feed, "https://x.example/feed"),
    })
    r = prefilter("https://x.example/", f)
    assert r["ok"] is True and r["feed"] is None and r["reason"] == "index"


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
    (WINDOW_1 - 1, 0, 5, "wait"),
    (3, 0, TIMEOUT_DAYS - 1, "wait"),
    (WINDOW_1, 0, TIMEOUT_DAYS, "timeout"),  # precedence: timeout beats revoke
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


def test_decide_probation_malformed_since_is_total():
    d = decide_probation({"screened": 3, "keeps": 0}, "2026-13-01", "2026-01-10")
    assert d["action"] == "wait" and d["reason"] == "bad probation_since '2026-13-01'"


def test_decide_probation_future_since_is_total():
    d = decide_probation({"screened": 3, "keeps": 0}, "2026-06-01", "2026-01-10")
    assert d["action"] == "wait" and d["reason"] == "probation_since 2026-06-01 is in the future"


def _events(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _good_pages(domain):
    return {f"https://{domain}/": (200, '<html><link rel="alternate" type="application/rss+xml" href="/feed"><body>Quant research notes</body></html>', f"https://{domain}/"),
            f"https://{domain}/feed": (200, FEED_XML.replace("x.example", domain), f"https://{domain}/feed")}


def test_admissions_block_prefilter_screen_false_and_admit_true(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://good.example/", found_in="blog1/i1", reason="cited")
    queue_discovery(q, "https://news.example/", found_in="blog1/i2", reason="cited")
    queue_discovery(q, "https://down.example/", found_in="blog1/i3", reason="cited")
    wl = write_watchlist(tmp_path, [make_source()])
    pages = {**_good_pages("good.example"), **_good_pages("news.example")}
    verdicts = {"good.example": {"research_source": True, "reason": "quant", "asset_classes": ["fx"]},
                "news.example": {"research_source": False, "reason": "news", "asset_classes": []}}
    screen = lambda domain, titles, about: verdicts.get(domain)
    actions = ActionLog(tmp_path / "act.jsonl")
    out = process_admissions(discovery_path=q, watchlist_path=wl, actions=actions,
                             fetch=_fetch_factory(pages), screen=screen, today="2026-08-24")
    assert out["admitted"] == ["good.example"] and out["blocked"] == ["news.example", "down.example"]
    st = {e["domain"]: e for e in load_discovery(q)}
    assert st["good.example"]["status"] == "probation"
    assert st["news.example"]["status"] == "blocked" and "source-screen" in st["news.example"]["status_reason"]
    assert st["down.example"]["status"] == "blocked" and "unreachable" in st["down.example"]["status_reason"]
    src = {s["id"]: s for s in load_watchlist(wl)}["good.example"]
    assert src["added_by"] == PROVENANCE_PROBATION and src["tier"] == "probation"
    assert src["probation_since"] == "2026-08-24" and src["feed"] == "https://good.example/feed"
    assert src["verified_date"] == "2026-08-24" and src["notes"].startswith("probation")
    types = [e["entry_type"] for e in _events(tmp_path / "act.jsonl")]
    assert types.count("source_auto_admitted") == 1 and types.count("source_auto_blocked") == 2
    adm = next(e for e in _events(tmp_path / "act.jsonl") if e["entry_type"] == "source_auto_admitted")
    assert adm["payload"]["rule"] == "probation"
    again = process_admissions(discovery_path=q, watchlist_path=wl, actions=actions,
                               fetch=_fetch_factory(pages), screen=screen, today="2026-08-24")
    assert again["admitted"] == [] and again["blocked"] == []


def test_admissions_malformed_screen_retries_then_blocks(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://flaky.example/", found_in="blog1/i1", reason="cited")
    wl = write_watchlist(tmp_path, [make_source()])
    actions = ActionLog(tmp_path / "act.jsonl")
    kw = dict(discovery_path=q, watchlist_path=wl, actions=actions,
              fetch=_fetch_factory(_good_pages("flaky.example")),
              screen=lambda d, t, a: None)
    for day in ("2026-08-24", "2026-08-25"):
        out = process_admissions(today=day, **kw)
        assert out["admitted"] == [] and out["blocked"] == []
        assert load_discovery(q)[0]["status"] == "proposed"
    out = process_admissions(today="2026-08-26", **kw)
    assert out["blocked"] == ["flaky.example"]
    assert load_discovery(q)[0]["status_reason"] == "source-screen malformed x3"
    malformed_events = [e for e in _events(tmp_path / "act.jsonl")
                        if e["entry_type"] == "source_screen_malformed"]
    assert [e["payload"]["run"] for e in malformed_events] == [1, 2]


def test_admissions_skip_scout_and_multi_citer_proposals(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://scouted.example/", found_in="scout/2026-08-15", reason="scout")
    queue_discovery(q, "https://twice.example/a", found_in="blog1/i1", reason="cited")
    queue_discovery(q, "https://twice.example/b", found_in="blog2/i2", reason="cited")
    wl = write_watchlist(tmp_path, [make_source()])
    out = process_admissions(discovery_path=q, watchlist_path=wl,
                             actions=ActionLog(tmp_path / "act.jsonl"),
                             fetch=_fetch_factory({}), screen=lambda d, t, a: None,
                             today="2026-08-24")
    assert out == {"admitted": [], "blocked": [], "deferred": []}
    assert all(e["status"] == "proposed" for e in load_discovery(q))


def _probation_wl(tmp_path, domain, since="2026-08-01"):
    src = make_source(id=domain, url=f"https://{domain}/", feed=None,
                      added_by=PROVENANCE_PROBATION, tier="probation",
                      verified_date=since, probation_since=since)
    return write_watchlist(tmp_path, [make_source(), src])


def test_reviews_promote_revoke_timeout(tmp_path):
    def prob(d, since):
        return make_source(id=d, url=f"https://{d}/", feed=None, added_by=PROVENANCE_PROBATION,
                           tier="probation", verified_date=since, probation_since=since)
    wl = write_watchlist(tmp_path, [make_source(), prob("win.example", "2026-08-01"),
                                    prob("lose.example", "2026-08-01"), prob("quiet.example", "2026-05-01"),
                                    prob("wait.example", "2026-08-01")])
    seen = SeenStore(tmp_path / "seen.jsonl")
    for i in range(40): seen.record(f"w{i}", "win.example", "screen_keep" if i < 2 else "screen_kill", link="h")
    for i in range(40): seen.record(f"l{i}", "lose.example", "screen_kill", link="h")
    for i in range(3): seen.record(f"q{i}", "quiet.example", "screen_kill", link="h")
    for i in range(10): seen.record(f"t{i}", "wait.example", "screen_kill", link="h")
    q = tmp_path / "discovery.jsonl"
    for d in ("win.example", "lose.example", "quiet.example", "wait.example"):
        queue_discovery(q, f"https://{d}/", found_in="blog1/i1", reason="cited")
        set_discovery_status(q, d, "probation", reason="t")
    actions = ActionLog(tmp_path / "act.jsonl")
    out = process_reviews(watchlist_path=wl, discovery_path=q, seen=seen,
                          actions=actions, today="2026-08-20")
    assert out == {"promoted": ["win.example"], "revoked": ["lose.example"],
                   "timed_out": ["quiet.example"],
                   "waiting": {"wait.example": "0 keeps in 10/40"}}
    src = {s["id"]: s for s in load_watchlist(wl)}
    assert src["win.example"]["added_by"] == PROVENANCE_PROMOTED and src["win.example"]["tier"] == "verified"
    assert src["win.example"]["verified_date"] == "2026-08-20" and "probation_since" not in src["win.example"]
    assert "lose.example" not in src and "quiet.example" not in src and "wait.example" in src
    st = {e["domain"]: e for e in load_discovery(q)}
    assert st["win.example"]["status"] == "auto_admitted"
    assert st["lose.example"]["status"] == "blocked" and st["lose.example"]["status_reason"] == "probation-yield 0/40"
    assert st["quiet.example"]["status"] == "proposed" and st["quiet.example"]["status_reason"] == "probation-timeout"
    types = [e["entry_type"] for e in _events(tmp_path / "act.jsonl")]
    assert types.count("source_promoted") == 1 and types.count("source_auto_revoked") == 2
    revoke_events = [e for e in _events(tmp_path / "act.jsonl") if e["entry_type"] == "source_auto_revoked"]
    assert sorted(e["payload"]["action"] for e in revoke_events) == ["revoke", "timeout"]
    assert process_reviews(watchlist_path=wl, discovery_path=q, seen=seen,
                           actions=actions, today="2026-08-20") == {
        "promoted": [], "revoked": [], "timed_out": [],
        "waiting": {"wait.example": "0 keeps in 10/40"}}


def test_reviews_second_citer_promotes_immediately(tmp_path):
    wl = _probation_wl(tmp_path, "cited.example")
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://cited.example/a", found_in="blog1/i1", reason="cited")
    set_discovery_status(q, "cited.example", "probation", reason="t")
    entries = load_discovery(q); entries[0]["cited_by"] = ["blog1", "blog2"]
    q.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    out = process_reviews(watchlist_path=wl, discovery_path=q,
                          seen=SeenStore(tmp_path / "seen.jsonl"),
                          actions=ActionLog(tmp_path / "act.jsonl"), today="2026-08-02")
    assert out["promoted"] == ["cited.example"]
    ev = [e for e in _events(tmp_path / "act.jsonl") if e["entry_type"] == "source_promoted"][0]
    assert ev["payload"]["rule"] == "cited by 2 distinct verified sources"


class _ExplodingSeen:
    """Stands in for a SeenStore that must never be touched: a watchlist
    with no probation entries has nothing for process_reviews to compute
    stats over, so it should return before taking a single seen-store
    pass, not just discard the result of one."""
    @property
    def _latest(self):
        raise AssertionError("process_reviews touched seen with no probation entries")


def test_reviews_ignore_non_probation_entries(tmp_path):
    wl = write_watchlist(tmp_path, [make_source(id="coen.example", added_by="coen")])
    out = process_reviews(watchlist_path=wl, discovery_path=tmp_path / "q.jsonl",
                          seen=_ExplodingSeen(),
                          actions=ActionLog(tmp_path / "act.jsonl"), today="2026-08-02")
    assert out == {"promoted": [], "revoked": [], "timed_out": [], "waiting": {}}


def test_reviews_batches_stats_in_one_pass_matching_source_stats(tmp_path):
    # Noise from two non-probation sources in the same seen store must not
    # leak into the probation domain's stats -- and the batched result must
    # match what source_stats() would compute for that domain alone.
    domain = "review.example"
    wl = _probation_wl(tmp_path, domain, since="2026-08-01")
    seen = SeenStore(tmp_path / "seen.jsonl")
    for i in range(5):
        seen.record(f"r{i}", domain, "screen_keep" if i == 0 else "screen_kill", link="h")
    for i in range(4):
        seen.record(f"n1-{i}", "noise-one", "screen_kill", link="h")
    for i in range(3):
        seen.record(f"n2-{i}", "noise-two", "screen_keep", link="h")
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, f"https://{domain}/", found_in="blog1/i1", reason="cited")
    set_discovery_status(q, domain, "probation", reason="t")
    expected = source_stats(seen, domain)
    assert expected == {"screened": 5, "keeps": 1}
    out = process_reviews(watchlist_path=wl, discovery_path=q, seen=seen,
                          actions=ActionLog(tmp_path / "act.jsonl"), today="2026-08-02")
    window = WINDOW_1 if expected["keeps"] == 0 else WINDOW_2
    assert out["waiting"][domain] == f"{expected['keeps']} keeps in {expected['screened']}/{window}"


def test_admissions_malformed_then_admit_pops_run_counter(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://flaky.example/", found_in="blog1/i1", reason="cited")
    wl = write_watchlist(tmp_path, [make_source()])
    actions = ActionLog(tmp_path / "act.jsonl")
    fetch = _fetch_factory(_good_pages("flaky.example"))
    verdicts = iter([None, {"research_source": True, "reason": "quant", "asset_classes": ["fx"]}])
    screen = lambda d, t, a: next(verdicts)
    out1 = process_admissions(discovery_path=q, watchlist_path=wl, actions=actions,
                              fetch=fetch, screen=screen, today="2026-08-24")
    assert out1["deferred"] == ["flaky.example"]
    assert load_discovery(q)[0]["malformed_runs"] == 1
    out2 = process_admissions(discovery_path=q, watchlist_path=wl, actions=actions,
                              fetch=fetch, screen=screen, today="2026-08-25")
    assert out2["admitted"] == ["flaky.example"]
    assert "malformed_runs" not in load_discovery(q)[0]
    types = [e["entry_type"] for e in _events(tmp_path / "act.jsonl")]
    assert types.count("source_screen_malformed") == 1
    run = next(e["payload"]["run"] for e in _events(tmp_path / "act.jsonl")
              if e["entry_type"] == "source_screen_malformed")
    assert run == 1


def test_admissions_known_via_watchlist_domain_not_id(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://legacy.example/", found_in="blog1/i1", reason="cited")
    wl = write_watchlist(tmp_path, [make_source(id="legacy-id", url="https://legacy.example/")])
    out = process_admissions(discovery_path=q, watchlist_path=wl,
                             actions=ActionLog(tmp_path / "act.jsonl"),
                             fetch=_fetch_factory({}), screen=lambda d, t, a: None,
                             today="2026-08-24")
    assert out == {"admitted": [], "blocked": [], "deferred": []}
    e = load_discovery(q)[0]
    assert e["status"] == "auto_admitted" and e["status_reason"] == "already on watchlist"


def test_prioritise_items_puts_probation_first_capped_and_keeps_backlog():
    items = [{"item_id": f"b{i}", "source_id": "backlog"} for i in range(5)]
    items += [{"item_id": f"p{i}", "source_id": "prob.example"} for i in range(PRIORITY_CAP + 10)]
    items += [{"item_id": f"q{i}", "source_id": "other.example"} for i in range(3)]
    out, held = prioritise_items(items, {"prob.example", "other.example"})
    ids = [i["item_id"] for i in out]
    assert ids[:PRIORITY_CAP] == [f"p{i}" for i in range(PRIORITY_CAP)]
    assert ids[PRIORITY_CAP:PRIORITY_CAP + 3] == ["q0", "q1", "q2"]
    assert ids[-5:] == [f"b{i}" for i in range(5)]          # backlog never dropped
    assert [i["item_id"] for i in held] == [f"p{i}" for i in range(PRIORITY_CAP, PRIORITY_CAP + 10)]
    assert prioritise_items([], set()) == ([], [])


def test_probation_counts_from_watchlist_and_chain(tmp_path):
    wl = write_watchlist(tmp_path, [make_source(), make_source(id="p1", added_by=PROVENANCE_PROBATION, tier="probation"),
                                    make_source(id="p2", added_by=PROVENANCE_PROBATION, tier="probation")])
    actions = ActionLog(tmp_path / "act.jsonl")
    actions.event("source_promoted", {"domain": "a"})
    actions.event("source_auto_revoked", {"domain": "b", "action": "revoke"})
    actions.event("source_auto_revoked", {"domain": "b2", "action": "timeout"})
    actions.event("source_auto_blocked", {"domain": "c"})
    actions.event("source_auto_admitted", {"domain": "p1", "rule": "probation"})
    actions.event("source_auto_admitted", {"domain": "z", "rule": "scout-researched"})
    actions.event("source_revoked_by_coen", {"domain": "x", "tier": "probation"})
    c = probation_counts(wl, tmp_path / "act.jsonl", days=30)
    assert c == {"on_probation": 2, "admitted": 1, "promoted": 1, "revoked": 2, "timed_out": 1, "blocked": 1}
    assert probation_counts(wl, tmp_path / "missing.jsonl")["admitted"] == 0


def test_block_record_revokes_probation_source(tmp_path):
    wl = _probation_wl(tmp_path, "prob.example")
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://prob.example/", found_in="blog1/i1", reason="cited")
    set_discovery_status(q, "prob.example", "probation", reason="t")
    rec = {"id": "prob.example-1", "action": "source_decision", "domain": "prob.example",
           "url": "https://prob.example/", "decision": "block", "name": "prob.example",
           "source_class": "blog", "actor": "coen", "via": "morpheus-ops",
           "ts_utc": "2026-08-24T00:00:00Z"}
    rec["sig"] = sign_record(rec, "k")
    (tmp_path / "approvals.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    actions = ActionLog(tmp_path / "act.jsonl")
    out = process_approvals(queue_path=tmp_path / "approvals.jsonl", watchlist_path=wl,
                            discovery_path=q, actions=actions,
                            state_path=tmp_path / "state.json", key="k")
    assert out["blocked"] == 1
    assert "prob.example" not in {s["id"] for s in load_watchlist(wl)}
    assert {e["domain"]: e["status"] for e in load_discovery(q)}["prob.example"] == "blocked"
    ev = [e for e in _events(tmp_path / "act.jsonl") if e["entry_type"] == "source_revoked_by_coen"]
    assert len(ev) == 1 and ev[0]["payload"]["tier"] == "probation"


def test_block_record_revokes_verified_source_not_in_queue(tmp_path):
    wl = write_watchlist(tmp_path, [make_source(id="coen.example", url="https://coen.example/")])
    rec = {"id": "coen.example-1", "action": "source_decision", "domain": "coen.example",
           "url": "https://coen.example/", "decision": "block", "name": "coen.example",
           "source_class": "blog", "actor": "coen", "via": "morpheus-ops",
           "ts_utc": "2026-08-24T00:00:00Z"}
    rec["sig"] = sign_record(rec, "k")
    (tmp_path / "approvals.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    actions = ActionLog(tmp_path / "act.jsonl")
    out = process_approvals(queue_path=tmp_path / "approvals.jsonl", watchlist_path=wl,
                            discovery_path=tmp_path / "discovery.jsonl", actions=actions,
                            state_path=tmp_path / "state.json", key="k")
    assert out["blocked"] == 1 and out["revoked"] == ["coen.example"]
    assert load_watchlist(wl) == []
    assert any(e["entry_type"] == "source_revoked_by_coen" and e["payload"]["tier"] == "verified"
               for e in _events(tmp_path / "act.jsonl"))


def _refuse_screen(*a, **kw):
    raise AssertionError("a permanently blocked queue row must never reach source-screen")


def test_block_of_legacy_verified_source_writes_permanent_block_row(tmp_path):
    """A verified source Coen added directly (never discovered through the
    pipeline) has no discovery-queue row. Blocking it must still leave a
    permanent 'blocked' row behind, or a future citation of the same domain
    would sail straight past queue_discovery's dedup and get re-proposed."""
    wl = write_watchlist(tmp_path, [make_source(id="coen.example", url="https://coen.example/")])
    q = tmp_path / "discovery.jsonl"
    rec = {"id": "coen.example-1", "action": "source_decision", "domain": "coen.example",
           "url": "https://coen.example/", "decision": "block", "name": "coen.example",
           "source_class": "blog", "actor": "coen", "via": "morpheus-ops",
           "ts_utc": "2026-08-24T00:00:00Z"}
    rec["sig"] = sign_record(rec, "k")
    (tmp_path / "approvals.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    actions = ActionLog(tmp_path / "act.jsonl")
    out = process_approvals(queue_path=tmp_path / "approvals.jsonl", watchlist_path=wl,
                            discovery_path=q, actions=actions,
                            state_path=tmp_path / "state.json", key="k")
    assert out["blocked"] == 1 and out["revoked"] == ["coen.example"]
    row = {e["domain"]: e for e in load_discovery(q)}["coen.example"]
    assert row["status"] == "blocked"
    # queue_discovery refuses to re-queue a domain with an existing row,
    # regardless of that row's status
    assert queue_discovery(q, "https://coen.example/post", found_in="blog1/i9",
                           reason="cited") is False
    adm = process_admissions(discovery_path=q, watchlist_path=wl, actions=actions,
                             screen=_refuse_screen)
    assert adm == {"admitted": [], "blocked": [], "deferred": []}
    assert load_discovery(q)[0]["status"] == "blocked"


def test_unsigned_block_record_never_removes_a_source(tmp_path):
    wl = _probation_wl(tmp_path, "prob.example")
    rec = {"id": "x", "action": "source_decision", "domain": "prob.example", "url": "https://prob.example/",
           "decision": "block", "name": "prob.example", "source_class": "blog", "actor": "coen",
           "via": "morpheus-ops", "ts_utc": "2026-08-24T00:00:00Z", "sig": "bad"}
    (tmp_path / "approvals.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    out = process_approvals(queue_path=tmp_path / "approvals.jsonl", watchlist_path=wl,
                            discovery_path=tmp_path / "discovery.jsonl",
                            actions=ActionLog(tmp_path / "act.jsonl"),
                            state_path=tmp_path / "state.json", key="k")
    assert out["invalid"] == 1 and "prob.example" in {s["id"] for s in load_watchlist(wl)}


def test_mixed_approve_and_block_batch_persists_both(tmp_path):
    wl = _probation_wl(tmp_path, "prob.example")
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://prob.example/", found_in="blog1/i1", reason="cited")
    set_discovery_status(q, "prob.example", "probation", reason="t")
    approve_rec = {"id": "new.example-1", "action": "source_decision", "domain": "new.example",
                   "url": "https://new.example/", "decision": "approve", "name": "new.example",
                   "source_class": "blog", "actor": "coen", "via": "morpheus-ops",
                   "ts_utc": "2026-08-24T00:00:00Z"}
    approve_rec["sig"] = sign_record(approve_rec, "k")
    block_rec = {"id": "prob.example-1", "action": "source_decision", "domain": "prob.example",
                "url": "https://prob.example/", "decision": "block", "name": "prob.example",
                "source_class": "blog", "actor": "coen", "via": "morpheus-ops",
                "ts_utc": "2026-08-24T00:00:00Z"}
    block_rec["sig"] = sign_record(block_rec, "k")
    (tmp_path / "approvals.jsonl").write_text(
        json.dumps(approve_rec) + "\n" + json.dumps(block_rec) + "\n", encoding="utf-8")
    actions = ActionLog(tmp_path / "act.jsonl")
    out = process_approvals(queue_path=tmp_path / "approvals.jsonl", watchlist_path=wl,
                            discovery_path=q, actions=actions,
                            state_path=tmp_path / "state.json", key="k")
    ids = {s["id"] for s in load_watchlist(wl)}
    assert "new.example" in ids
    assert "prob.example" not in ids
    assert {e["domain"]: e["status"] for e in load_discovery(q)}["prob.example"] == "blocked"
    types = {e["entry_type"] for e in _events(tmp_path / "act.jsonl")}
    assert "source_approved" in types
    assert "source_revoked_by_coen" in types


def _idx_html(domain, n=MIN_INDEX_ITEMS):
    links = "".join(f'<a href="https://{domain}/2026/post-number-{i}">Post {i}</a>'
                    for i in range(n))
    return f"<html><head><title>X</title></head><body><p>About text.</p>{links}</body></html>"


def test_admissions_bounded_to_max_per_run_leaves_rest_proposed(tmp_path):
    q = tmp_path / "discovery.jsonl"
    for i in range(25):
        queue_discovery(q, f"https://d{i}.example/", found_in=f"blog{i}/i1", reason="cited")
    wl = write_watchlist(tmp_path, [make_source()])
    calls = []

    def fetch(url, timeout=30):
        calls.append(url)
        domain = url.split("//", 1)[1].split("/", 1)[0]
        return 200, _idx_html(domain), url  # no feed link -> index mode, one fetch each

    verdict = lambda d, t, a: {"research_source": True, "reason": "quant", "asset_classes": []}
    actions = ActionLog(tmp_path / "act.jsonl")
    out1 = process_admissions(discovery_path=q, watchlist_path=wl, actions=actions,
                              fetch=fetch, screen=verdict, today="2026-08-24",
                              max_per_run=20)
    assert len(calls) == 20                        # exactly 20 prefilter fetches
    assert len(out1["admitted"]) == 20
    remaining = [e for e in load_discovery(q) if e["status"] == "proposed"]
    assert len(remaining) == 5                      # the other 5 untouched, still proposed
    out2 = process_admissions(discovery_path=q, watchlist_path=wl, actions=actions,
                              fetch=fetch, screen=verdict, today="2026-08-24",
                              max_per_run=20)
    assert len(out2["admitted"]) == 5
    assert all(e["status"] != "proposed" for e in load_discovery(q))
    assert len(calls) == 25


def test_admissions_default_max_per_run_is_the_constant(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://only.example/", found_in="blog1/i1", reason="cited")
    wl = write_watchlist(tmp_path, [make_source()])
    import inspect
    assert inspect.signature(process_admissions).parameters["max_per_run"].default == ADMISSIONS_PER_RUN
    assert ADMISSIONS_PER_RUN == 20


def test_probation_counts_skips_malformed_trailing_json_line(tmp_path):
    wl = write_watchlist(tmp_path, [make_source()])
    actions_path = tmp_path / "act.jsonl"
    actions = ActionLog(actions_path)
    actions.event("source_promoted", {"domain": "a"})
    actions.event("source_auto_blocked", {"domain": "b"})
    # simulate a crash mid-append: a truncated trailing line, no closing brace/newline
    with actions_path.open("a", encoding="utf-8") as f:
        f.write('{"entry_type": "source_promoted", "payload": {"domain": "c"}, "ts_utc": "2026-0')
    c = probation_counts(wl, actions_path, days=30)
    assert c["promoted"] == 1 and c["blocked"] == 1
