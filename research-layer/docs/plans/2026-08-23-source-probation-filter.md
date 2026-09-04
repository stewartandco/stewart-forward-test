# Source Probation Filter (D27 case 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Coen's manual approve/block of single-citation source proposals with a mechanical filter (deterministic pre-filter → Sonnet source screen → probation-by-yield), chain-logged, Coen-revocable, visible on Morpheus /ops.

**Architecture:** A new pure-logic module `pipeline/probation.py` decides admissions and probation outcomes from dicts (queue entries, watchlist entries, per-source screen stats) with all I/O injected (fetch, LLM client, today). The resident scanner (`pipeline/scanner.py`) calls it once per cycle after the existing D27 auto-admissions, and screens probation items first (capped 40/source/run). Morpheus reads the chain events to show counts; its only write is a signed **revoke** record the scanner already knows how to consume as a block.

**Tech Stack:** Python 3.14 stdlib + anthropic SDK (research-layer), pytest (`python -m pytest research-layer/pipeline/test_probation.py -q`), FastAPI + React/TS (morpheus-hub, vitest).

**Spec:** `research-layer/docs/2026-08-23-source-probation-filter-design.md` (approved 2026-08-23).

**Repos / hazards:** `stewart-forward-test` (research-layer inside it; scoped `git add` only — the daily scanner modifies `sources/discovery_queue.jsonl` and `sources/verified_sources.json` in the working tree, NEVER stage those) and `morpheus-hub` (shared tree; never `git add -A`; bump `frontend2/public/sw.js` CACHE on any frontend change; hub on :8100 is live — build in a worktree on :8101/:5280).

---

## File map

| file | change |
|---|---|
| `pipeline/watchlist.py` | `tier` field (optional, default `verified`), provenance set gains `auto-d27-probation` + `auto-d27-promoted`, `remove_source()`, `set_discovery_status()` |
| `pipeline/relevance.py` | `SOURCE_SCREEN_SYSTEM`, `SOURCE_SCREEN_SCHEMA`, `build_source_screen_prompt()`, `parse_source_screen()`, `screen_source()` |
| `pipeline/probation.py` (new) | `prefilter()`, `source_stats()`, `decide_probation()`, `prioritise_items()`, `process_admissions()`, `process_reviews()`, `probation_counts()` |
| `pipeline/scanner.py` | call sites; priority screening; status items + digest line; `pending_tier3_count` counts cards only |
| `pipeline/scanstatus.py` | `write_digest(..., probation=...)` line |
| `pipeline/approvals.py` | a `block` record for a probation domain removes the watchlist entry |
| `pipeline/test_probation.py` (new) | all offline tests |
| morpheus `backend/app/services/constellation.py` | `reader_probation()`, `revoke_source()` |
| morpheus `backend/app/routers/constellation.py` | `GET /reader/probation`, `POST /reader/probation/revoke`; `POST /reader/proposals/decide` removed |
| morpheus `frontend2/src/ops/OpsApp.tsx` | `ProbationPanel` replaces `ProposalsPanel`; BigStat label |
| `stewartandco-agents/DECISIONS.md` + Reader `CONTRACT.md` | D27 case 3 text; `CONTRACT_VERSION` 1.6 → 1.7 |

Shared constants (defined once in `probation.py`, imported elsewhere):

```python
WINDOW_1 = 40          # screened items in the first window
WINDOW_2 = 80          # extended window after exactly one keep
PROMOTE_KEEPS = 2
TIMEOUT_DAYS = 90
PRIORITY_CAP = 40      # probation items screened per source per run
BLOCKED_SUBDOMAINS = ("store.", "shop.", "cms.", "app.", "login.", "my.")
MIN_INDEX_ITEMS = 5
KEEP_STATUSES = ("screen_keep", "screen_keep_low", "extracted", "paywalled",
                 "fetch_failed", "thin_content", "extract_failed")   # post-keep states
SCREENED_STATUSES = KEEP_STATUSES + ("screen_kill",)
```

---

### Task 1: Watchlist tier, provenance, and helpers

**Files:**
- Modify: `research-layer/pipeline/watchlist.py`
- Test: `research-layer/pipeline/test_probation.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd E:\Users\Coen\Claude\stewart-forward-test && python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL with `ImportError: cannot import name 'remove_source'`

- [ ] **Step 3: Implement in `watchlist.py`**

Change `POLLABLE_PROVENANCE` and add the helpers after `pollable`:

```python
POLLABLE_PROVENANCE = {"coen", "auto-d27", "auto-d27-probation", "auto-d27-promoted"}


def tier_of(source: dict) -> str:
    """'verified' unless the entry says otherwise (legacy entries carry no tier)."""
    return source.get("tier") or "verified"


def remove_source(path: str | Path, source_id: str) -> dict | None:
    """Delete one watchlist entry by id; returns it, or None if absent."""
    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    keep, removed = [], None
    for s in doc.get("sources", []):
        if s["id"] == source_id and removed is None:
            removed = s
        else:
            keep.append(s)
    if removed is not None:
        doc["sources"] = keep
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    return removed


def set_discovery_status(path: str | Path, domain: str, status: str, *,
                         reason: str) -> bool:
    """Flip one discovery-queue entry's status (any prior status) and record
    why. Returns False when the domain is not queued."""
    entries = load_discovery(path)
    hit = False
    for e in entries:
        if (e.get("domain") or discovery_domain(e["url"])) == domain:
            e["status"] = status
            e["status_reason"] = reason
            e["status_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            hit = True
    if hit:
        Path(path).write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
            encoding="utf-8")
    return hit
```

Also in `load_watchlist`, after the `feed` check, add:

```python
        if src.get("tier") not in (None, "verified", "probation"):
            raise WatchlistError(f"source {src['id']!r} has unknown tier {src['tier']!r}")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/watchlist.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): watchlist tier + probation provenance + remove/set-status helpers (D27 case 3)"
```

---

### Task 2: Source-level screen in `relevance.py`

**Files:**
- Modify: `research-layer/pipeline/relevance.py`
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL `ImportError: cannot import name 'build_source_screen_prompt'`

- [ ] **Step 3: Implement in `relevance.py`** (append at end)

```python
# ---------------- D27 case 3: source-level screen ----------------------------

SOURCE_SCREEN_SYSTEM = INTAKE_PARAMETERS + """

SOURCE MODE. You are now judging a whole SOURCE, not items. You see its domain,
its 10 most recent item titles, and the start of its landing/about text. Decide
whether a recurring reader of this source would expect testable trading /
portfolio-construction / execution / risk / market-microstructure / regime
research that would pass the item bar above at least occasionally. Macro or
market commentary without mechanisms, politics, gold-bug or doom sites, product
or course marketing, news recaps, and general finance journalism are NOT research
sources. Return research_source true/false, a one-line reason, and the asset
classes the source mostly covers."""

SOURCE_SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "research_source": {"type": "boolean"},
        "reason": {"type": "string"},
        "asset_classes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["research_source", "reason", "asset_classes"],
    "additionalProperties": False,
}
SOURCE_SCREEN_MAX_TOKENS = 400


def build_source_screen_prompt(domain: str, titles: list[str], about: str) -> str:
    lines = [f"Source domain: {domain}", "", "Recent item titles:"]
    lines += [f"- {t}" for t in titles] or ["- (none found)"]
    lines += ["", "Landing/about text (truncated):", about[:300] or "(none)"]
    return "\n".join(lines)


def parse_source_screen(data: dict) -> dict | None:
    """Strict: a missing or non-boolean verdict is malformed (None), never a pass."""
    verdict = data.get("research_source") if isinstance(data, dict) else None
    if not isinstance(verdict, bool):
        return None
    classes = data.get("asset_classes")
    return {"research_source": verdict,
            "reason": str(data.get("reason", ""))[:300],
            "asset_classes": [str(c) for c in classes] if isinstance(classes, list) else []}


def screen_source(client, model: str, meter, domain: str, titles: list[str],
                  about: str, log_path: str | Path) -> dict | None:
    """One metered Sonnet call. Returns the parsed verdict, or None when the
    budget is closed, the call failed, the model refused, or the output was
    malformed - the caller treats None as 'not admitted, try again later'."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _log(verdict, reason):
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts_utc": ts, "domain": domain, "model": model,
                                "verdict": verdict, "reason": reason},
                               ensure_ascii=False) + "\n")

    if not meter.can_spend():
        _log(None, "monthly cap reached")
        return None
    try:
        msg = client.messages.create(
            model=model, max_tokens=SOURCE_SCREEN_MAX_TOKENS,
            system=SOURCE_SCREEN_SYSTEM,
            output_config={"format": {"type": "json_schema",
                                      "schema": SOURCE_SCREEN_SCHEMA}},
            messages=[{"role": "user",
                       "content": build_source_screen_prompt(domain, titles, about)}])
    except Exception as exc:
        _log(None, f"api_error: {exc}"[:200])
        if any(m in str(exc).lower() for m in FATAL_API_MARKERS):
            raise ApiCreditExhausted(str(exc)) from exc
        return None
    meter.record_call(model, msg.usage, purpose="source_screen", agent="reader")
    if msg.stop_reason == "refusal":
        _log(None, "refusal")
        return None
    try:
        text = next(b.text for b in msg.content if b.type == "text")
        parsed = parse_source_screen(json.loads(text))
    except (StopIteration, ValueError, TypeError):
        parsed = None
    if parsed is None:
        _log(None, "malformed")
        return None
    _log(parsed["research_source"], parsed["reason"])
    return parsed
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/relevance.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): source-level Sonnet screen (metered, strict parse) for probation admission"
```

---

### Task 3: Deterministic pre-filter

**Files:**
- Create: `research-layer/pipeline/probation.py`
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from .probation import (prefilter, BLOCKED_SUBDOMAINS, MIN_INDEX_ITEMS)

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'pipeline.probation'`

- [ ] **Step 3: Create `pipeline/probation.py`**

```python
"""D27 case 3: mechanical admission of single-citation source proposals.

Pre-filter (no cost) -> source screen (one Sonnet call) -> probation by yield.
Pure functions over dicts; all I/O (fetch, LLM client, clock) is injected so the
state machine is fully testable offline. Spec:
docs/2026-08-23-source-probation-filter-design.md
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .feeds import (fetch_url, discover_feed, parse_feed, article_links,
                    html_to_text)
from .watchlist import JUNK_DOMAINS, discovery_domain

WINDOW_1 = 40
WINDOW_2 = 80
PROMOTE_KEEPS = 2
TIMEOUT_DAYS = 90
PRIORITY_CAP = 40
BLOCKED_SUBDOMAINS = ("store.", "shop.", "cms.", "app.", "login.", "my.")
MIN_INDEX_ITEMS = 5
MAX_MALFORMED_RUNS = 3
KEEP_STATUSES = ("screen_keep", "screen_keep_low", "extracted", "paywalled",
                 "fetch_failed", "thin_content", "extract_failed")
SCREENED_STATUSES = KEEP_STATUSES + ("screen_kill",)
PROVENANCE_PROBATION = "auto-d27-probation"
PROVENANCE_PROMOTED = "auto-d27-promoted"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------- pre-filter ----------------

def prefilter(url: str, fetch=fetch_url) -> dict:
    """Deterministic, no tokens. Returns {ok, reason, feed, titles, about}."""
    domain = discovery_domain(url)
    host = domain
    if not domain or domain in JUNK_DOMAINS or \
            any(domain.endswith("." + j) for j in JUNK_DOMAINS):
        return {"ok": False, "reason": "junk domain", "feed": None, "titles": [], "about": ""}
    if any(host.startswith(p) for p in BLOCKED_SUBDOMAINS):
        return {"ok": False, "reason": f"blocked subdomain {host.split('.')[0]}.",
                "feed": None, "titles": [], "about": ""}
    status, html, final = fetch(url)
    if status != 200:
        status, html, final = fetch(url)          # one retry
        if status != 200:
            return {"ok": False, "reason": f"unreachable: http {status}",
                    "feed": None, "titles": [], "about": ""}
    about = html_to_text(html)[:300]
    feed = discover_feed(html, final)
    titles: list[str] = []
    if feed:
        fstatus, ftext, _ = fetch(feed)
        if fstatus == 200:
            titles = [it["title"] for it in parse_feed(ftext, domain)][:10]
        else:
            feed = None
    if not titles:
        titles = [t or link for link, t in article_links(html, final, cap=25)][:10]
        if len(titles) < MIN_INDEX_ITEMS:
            return {"ok": False,
                    "reason": f"no feed and only {len(titles)} index items (< {MIN_INDEX_ITEMS})",
                    "feed": None, "titles": titles, "about": about}
    return {"ok": True, "reason": "feed" if feed else "index", "feed": feed,
            "titles": titles, "about": about}
```

Note: `parse_feed` does not enforce "last 12 months" for feeds; a feed with ≥1 item passes (a live feed is evidence of recurrence). The 12-month rule applies to index pages through `MIN_INDEX_ITEMS` on article links; dating index links reliably is not possible from HTML alone, so the spec's "dated items" is implemented as "≥5 article links" and recorded as such in the spec's §2 on ship (clarification, not a weakening: the screen and the yield window carry the quality burden).

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/probation.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): probation pre-filter (junk/subdomain/unreachable/feed-or-index)"
```

---

### Task 4: Yield statistics and the probation state machine

**Files:**
- Modify: `research-layer/pipeline/probation.py`
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from .probation import (source_stats, decide_probation, WINDOW_1, WINDOW_2,
                        PROMOTE_KEEPS, TIMEOUT_DAYS)
from .seen import SeenStore


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
    (5, PROMOTE_KEEPS, 1, "promote"),            # early promotion is fine
    (WINDOW_1, 1, 5, "wait"),                    # extended window
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL `ImportError: cannot import name 'source_stats'`

- [ ] **Step 3: Implement** (append to `probation.py`)

```python
# ---------------- yield ----------------

def source_stats(seen, source_id: str) -> dict:
    """Screened / kept counts for one source from the seen store's latest
    statuses. Post-keep states (extracted, paywalled, ...) are keeps."""
    screened = keeps = 0
    for e in seen._latest.values():
        if e["source_id"] != source_id or e["status"] not in SCREENED_STATUSES:
            continue
        screened += 1
        if e["status"] in KEEP_STATUSES:
            keeps += 1
    return {"screened": screened, "keeps": keeps}


def decide_probation(stats: dict, since: str, today: str) -> dict:
    """The state machine. Returns {action: promote|revoke|timeout|wait, reason}."""
    screened, keeps = stats["screened"], stats["keeps"]
    if keeps >= PROMOTE_KEEPS:
        return {"action": "promote", "reason": f"probation-yield {keeps}/{screened}"}
    age = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(since, "%Y-%m-%d")).days
    if age >= TIMEOUT_DAYS:
        return {"action": "timeout", "reason": "probation-timeout"}
    window = WINDOW_1 if keeps == 0 else WINDOW_2
    if screened >= window:
        return {"action": "revoke", "reason": f"probation-yield {keeps}/{window}"}
    return {"action": "wait", "reason": f"{keeps} keeps in {screened}/{window}"}
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/probation.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): probation yield stats + state machine (2-of-40, 1->80, 90d timeout)"
```

---

### Task 5: Admissions — proposals → blocked / probation

**Files:**
- Modify: `research-layer/pipeline/probation.py`
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from .probation import process_admissions, PROVENANCE_PROBATION
from .scanstatus import ActionLog


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
    # idempotent
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


def test_admissions_skip_scout_and_multi_citer_proposals(tmp_path):
    """D27 cases 1-2 belong to process_auto_admissions; case 3 never touches them."""
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL `ImportError: cannot import name 'process_admissions'`

- [ ] **Step 3: Implement** (append to `probation.py`)

```python
# ---------------- admissions (proposal -> blocked | probation) ----------------

DEFAULT_POLL_MINUTES_PROBATION = 360


def _is_case_1_or_2(entry: dict) -> bool:
    citers = set(entry.get("cited_by") or [entry.get("found_in", "").split("/", 1)[0]])
    is_scout = "scout" in citers or entry.get("found_in", "").startswith("scout/")
    return is_scout or len(citers - {"scout"}) >= 2


def process_admissions(*, discovery_path, watchlist_path, actions, fetch=fetch_url,
                       screen, today: str | None = None) -> dict:
    """Case-3 admission pass. `screen(domain, titles, about) -> verdict|None`
    is injected (production binds relevance.screen_source). Returns the
    domains admitted / blocked / deferred this pass. Idempotent."""
    from .watchlist import load_discovery, set_discovery_status
    today = today or _today()
    entries = load_discovery(discovery_path)
    doc = json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
    known = {s["id"] for s in doc["sources"]}
    out = {"admitted": [], "blocked": [], "deferred": []}
    for e in entries:
        if e.get("status") != "proposed" or _is_case_1_or_2(e):
            continue
        domain = e.get("domain") or discovery_domain(e["url"])
        if domain in known:
            set_discovery_status(discovery_path, domain, "auto_admitted",
                                 reason="already on watchlist")
            continue
        pf = prefilter(e["url"], fetch)
        if not pf["ok"]:
            set_discovery_status(discovery_path, domain, "blocked",
                                 reason=f"prefilter: {pf['reason']}")
            actions.event("source_auto_blocked", {"domain": domain, "rule": "prefilter",
                                                  "reason": pf["reason"], "url": e["url"]})
            out["blocked"].append(domain)
            continue
        verdict = screen(domain, pf["titles"], pf["about"])
        if verdict is None:
            n = int(e.get("malformed_runs", 0)) + 1
            if n >= MAX_MALFORMED_RUNS:
                set_discovery_status(discovery_path, domain, "blocked",
                                     reason=f"source-screen malformed x{n}")
                actions.event("source_auto_blocked", {"domain": domain, "rule": "source-screen",
                                                      "reason": f"malformed x{n}", "url": e["url"]})
                out["blocked"].append(domain)
            else:
                _bump_malformed(discovery_path, domain, n)
                out["deferred"].append(domain)
            continue
        if not verdict["research_source"]:
            set_discovery_status(discovery_path, domain, "blocked",
                                 reason=f"source-screen: {verdict['reason']}")
            actions.event("source_auto_blocked", {"domain": domain, "rule": "source-screen",
                                                  "reason": verdict["reason"], "url": e["url"]})
            out["blocked"].append(domain)
            continue
        entry = {
            "id": domain, "class": "blog", "name": domain, "url": e["url"],
            "feed": pf["feed"], "poll_minutes": DEFAULT_POLL_MINUTES_PROBATION,
            "added_by": PROVENANCE_PROBATION, "verified_date": today,
            "tier": "probation", "probation_since": today,
            "notes": (f"probation from {today} per D27 case 3 (single citation; "
                      f"screen: {verdict['reason'][:120]}; classes "
                      f"{','.join(verdict['asset_classes']) or '-'}). Coen-revocable."),
        }
        doc["sources"].append(entry)
        known.add(domain)
        set_discovery_status(discovery_path, domain, "probation",
                             reason=f"admitted on probation: {verdict['reason']}")
        actions.event("source_auto_admitted", {"domain": domain, "rule": "probation",
                                               "reason": verdict["reason"],
                                               "asset_classes": verdict["asset_classes"],
                                               "url": e["url"], "feed": pf["feed"]})
        out["admitted"].append(domain)
    if out["admitted"]:
        Path(watchlist_path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    return out


def _bump_malformed(discovery_path, domain: str, n: int) -> None:
    from .watchlist import load_discovery
    entries = load_discovery(discovery_path)
    for e in entries:
        if (e.get("domain") or discovery_domain(e["url"])) == domain:
            e["malformed_runs"] = n
    Path(discovery_path).write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 26 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/probation.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): case-3 admissions: prefilter -> source screen -> probation entry, chain-logged, idempotent"
```

---

### Task 6: Reviews — promote / revoke / timeout / citation override

**Files:**
- Modify: `research-layer/pipeline/probation.py`
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from .probation import process_reviews, PROVENANCE_PROMOTED


def _probation_wl(tmp_path, domain, since="2026-08-01"):
    src = make_source(id=domain, url=f"https://{domain}/", feed=None,
                      added_by=PROVENANCE_PROBATION, tier="probation",
                      verified_date=since, probation_since=since)
    return write_watchlist(tmp_path, [make_source(), src])


def test_reviews_promote_revoke_timeout(tmp_path):
    wl = write_watchlist(tmp_path, [
        make_source(),
        make_source(id="win.example", url="https://win.example/", feed=None, added_by=PROVENANCE_PROBATION, tier="probation", verified_date="2026-08-01", probation_since="2026-08-01"),
        make_source(id="lose.example", url="https://lose.example/", feed=None, added_by=PROVENANCE_PROBATION, tier="probation", verified_date="2026-08-01", probation_since="2026-08-01"),
        make_source(id="quiet.example", url="https://quiet.example/", feed=None, added_by=PROVENANCE_PROBATION, tier="probation", verified_date="2026-05-01", probation_since="2026-05-01"),
        make_source(id="wait.example", url="https://wait.example/", feed=None, added_by=PROVENANCE_PROBATION, tier="probation", verified_date="2026-08-01", probation_since="2026-08-01"),
    ])
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
    assert out == {"promoted": ["win.example"], "revoked": ["lose.example", "quiet.example"], "waiting": ["wait.example"]}
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
    # idempotent
    assert process_reviews(watchlist_path=wl, discovery_path=q, seen=seen,
                           actions=actions, today="2026-08-20") == {"promoted": [], "revoked": [], "waiting": ["wait.example"]}


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


def test_reviews_ignore_non_probation_entries(tmp_path):
    wl = write_watchlist(tmp_path, [make_source(id="coen.example", added_by="coen")])
    out = process_reviews(watchlist_path=wl, discovery_path=tmp_path / "q.jsonl",
                          seen=SeenStore(tmp_path / "seen.jsonl"),
                          actions=ActionLog(tmp_path / "act.jsonl"), today="2026-08-02")
    assert out == {"promoted": [], "revoked": [], "waiting": []}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL `ImportError: cannot import name 'process_reviews'`

- [ ] **Step 3: Implement** (append to `probation.py`)

```python
# ---------------- reviews (probation -> promoted | revoked | timeout) --------

def _citers(discovery_entries: list[dict], domain: str) -> set[str]:
    for e in discovery_entries:
        if (e.get("domain") or discovery_domain(e["url"])) == domain:
            return set(e.get("cited_by") or []) - {"scout"}
    return set()


def process_reviews(*, watchlist_path, discovery_path, seen, actions,
                    today: str | None = None) -> dict:
    """Evaluate every probation entry. D27 case 2 (2+ distinct citers) wins
    over yield; a timeout returns the domain to 'proposed' (re-proposable);
    a yield revoke blocks it. Idempotent: resolved entries leave probation."""
    from .watchlist import load_discovery, set_discovery_status, tier_of
    today = today or _today()
    doc = json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
    disc = load_discovery(discovery_path)
    out = {"promoted": [], "revoked": [], "waiting": []}
    keep: list[dict] = []
    dirty = False
    for s in doc["sources"]:
        if tier_of(s) != "probation":
            keep.append(s)
            continue
        domain = s["id"]
        stats = source_stats(seen, domain)
        if len(_citers(disc, domain)) >= 2:
            decision = {"action": "promote", "reason": "cited by 2 distinct verified sources"}
        else:
            decision = decide_probation(stats, s.get("probation_since", s["verified_date"]), today)
        if decision["action"] == "promote":
            s = {**s, "added_by": PROVENANCE_PROMOTED, "tier": "verified",
                 "verified_date": today,
                 "notes": s.get("notes", "") + f" | promoted {today}: {decision['reason']}"}
            s.pop("probation_since", None)
            keep.append(s)
            set_discovery_status(discovery_path, domain, "auto_admitted", reason=decision["reason"])
            actions.event("source_promoted", {"domain": domain, "rule": decision["reason"],
                                              "screened": stats["screened"], "keeps": stats["keeps"]})
            out["promoted"].append(domain); dirty = True
        elif decision["action"] in ("revoke", "timeout"):
            new_status = "blocked" if decision["action"] == "revoke" else "proposed"
            set_discovery_status(discovery_path, domain, new_status, reason=decision["reason"])
            actions.event("source_auto_revoked", {"domain": domain, "rule": decision["reason"],
                                                  "screened": stats["screened"], "keeps": stats["keeps"],
                                                  "requeued": decision["action"] == "timeout"})
            out["revoked"].append(domain); dirty = True
        else:
            keep.append(s)
            out["waiting"].append(domain)
    if dirty:
        doc["sources"] = keep
        Path(watchlist_path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    return out
```

Note on timeout → `proposed`: the entry keeps `cited_by`, so a later second citation promotes via case 2; a later single re-citation re-enters case 3. `queue_discovery` treats a `proposed` domain as existing (no duplicate).

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 29 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/probation.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): probation reviews: promote/revoke/timeout + citation override, chain-logged"
```

---

### Task 7: Priority screening and counts

**Files:**
- Modify: `research-layer/pipeline/probation.py`
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from .probation import prioritise_items, probation_counts, PRIORITY_CAP


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
    actions.event("source_auto_revoked", {"domain": "b"})
    actions.event("source_auto_blocked", {"domain": "c"})
    actions.event("source_auto_admitted", {"domain": "p1", "rule": "probation"})
    actions.event("source_auto_admitted", {"domain": "z", "rule": "scout-researched"})
    c = probation_counts(wl, tmp_path / "act.jsonl", days=30)
    assert c == {"on_probation": 2, "admitted": 1, "promoted": 1, "revoked": 1, "blocked": 1}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL `ImportError: cannot import name 'prioritise_items'`

- [ ] **Step 3: Implement** (append to `probation.py`)

```python
# ---------------- scheduling + visibility ----------------

def prioritise_items(items: list[dict], probation_ids: set[str],
                     cap: int = PRIORITY_CAP) -> tuple[list[dict], list[dict]]:
    """Probation-source items first (at most `cap` per source), then everything
    else in original order. Items over the cap are returned separately so the
    caller can leave them for the next cycle (they stay 'seen' in the store)."""
    first, rest, held = [], [], []
    per: dict[str, int] = {}
    for it in items:
        sid = it["source_id"]
        if sid in probation_ids:
            if per.get(sid, 0) < cap:
                first.append(it); per[sid] = per.get(sid, 0) + 1
            else:
                held.append(it)
        else:
            rest.append(it)
    return first + rest, held


def probation_counts(watchlist_path, actions_path, days: int = 30) -> dict:
    """Real counts: probation entries on the watchlist now, plus chain events in
    the trailing window. Nothing derived that the chain did not record."""
    from .watchlist import tier_of
    doc = json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
    on_probation = sum(1 for s in doc.get("sources", []) if tier_of(s) == "probation")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    counts = {"on_probation": on_probation, "admitted": 0, "promoted": 0,
              "revoked": 0, "blocked": 0}
    p = Path(actions_path)
    if not p.exists():
        return counts
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        ts = e.get("ts_utc") or e.get("ts") or ""
        try:
            when = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when < cutoff:
            continue
        t = e.get("entry_type")
        if t == "source_auto_admitted" and e.get("payload", {}).get("rule") == "probation":
            counts["admitted"] += 1
        elif t == "source_promoted":
            counts["promoted"] += 1
        elif t == "source_auto_revoked":
            counts["revoked"] += 1
        elif t == "source_auto_blocked":
            counts["blocked"] += 1
    return counts
```

Check the chain row's timestamp field name in `pipeline/common.py` / `registry.py` (`Registry.append`) before finalising the `ts_utc`/`ts` line; the test fixture writes through `ActionLog`, so the test fails loudly if the field is wrong.

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 31 passed

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/probation.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): probation priority screening (40/source/run) + real counts for status/dashboard"
```

---

### Task 8: Scanner wiring, status items, digest line, tier-3 count

**Files:**
- Modify: `research-layer/pipeline/scanner.py` (run loop; `_cycle_status`; `pending_tier3_count`)
- Modify: `research-layer/pipeline/scanstatus.py` (`write_digest`)
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from .scanstatus import write_digest
from .scanner import pending_tier3_count
from .registry import Registry


def test_digest_has_probation_line(tmp_path):
    f = write_digest(tmp_path, date="20260824", new_by_source={}, rejections={},
                     discoveries=[], paywalled=[], spend_usd=0.0, cards_registered=0,
                     probation={"on_probation": 3, "admitted": 2, "promoted": 1, "revoked": 0, "blocked": 4})
    text = Path(f).read_text(encoding="utf-8")
    assert "Source probation (D27 case 3): on probation 3 | admitted 2 | promoted 1 | revoked 0 | blocked 4" in text


def test_pending_tier3_counts_cards_only_now(tmp_path):
    q = tmp_path / "discovery.jsonl"
    queue_discovery(q, "https://x.example/", found_in="blog1/i1", reason="cited")
    reg = Registry(tmp_path / "registry.jsonl")
    assert pending_tier3_count(reg, q) == 0     # proposals no longer wait on Coen
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: 2 FAIL (`unexpected keyword argument 'probation'`; `assert 1 == 0`)

- [ ] **Step 3: Implement**

`scanstatus.py` — add `probation: dict | None = None` to `write_digest`'s signature and, after the "Cards registered" line:

```python
    if probation is not None:
        lines.append("Source probation (D27 case 3): "
                     f"on probation {probation['on_probation']} | admitted {probation['admitted']} | "
                     f"promoted {probation['promoted']} | revoked {probation['revoked']} | "
                     f"blocked {probation['blocked']}")
        lines.append("")
```

Also change the heading `"Off-list sources queued for Coen (Tier 3, never fetched):"` to `"Off-list sources queued today (Tier 3; admitted by D27 rules, never by default):"`.

`scanner.py`:

1. `pending_tier3_count` — proposals no longer wait on Coen:

```python
def pending_tier3_count(registry: Registry, discovery_path) -> int:
    """What still waits on Coen: cards in triage. Source proposals are admitted
    or blocked mechanically (D27 cases 1-3) since 2026-08-24."""
    return len(registry.cards(status="pending"))
```

2. In `run()`, replace the `new_items` assembly + `process_new_items` call so probation items go first:

```python
            from .probation import prioritise_items, process_admissions, process_reviews
            from .watchlist import tier_of
            probation_ids = {s["id"] for s in sources if tier_of(s) == "probation"}
            new_items, _held = prioritise_items(new_items, probation_ids)
```

(insert immediately before `try:` that wraps `process_new_items`; `_held` items remain `seen` and are re-fed by `refeedable_deferred` next cycle.)

3. After the D27 `process_auto_admissions` loop, add:

```python
            # D27 case 3: single-citation proposals -> prefilter -> source screen -> probation
            from .relevance import screen_source
            def _screen(domain, titles, about):
                return screen_source(client, args.model, meter, domain, titles, about,
                                     logs_dir / "source_screen_log.jsonl")
            adm = process_admissions(discovery_path=discovery_path,
                                     watchlist_path=args.watchlist, actions=actions,
                                     screen=_screen)
            rev = process_reviews(watchlist_path=args.watchlist,
                                  discovery_path=discovery_path, seen=seen, actions=actions)
            if adm["admitted"] or adm["blocked"] or rev["promoted"] or rev["revoked"]:
                print(f"probation: +{len(adm['admitted'])} admitted, {len(adm['blocked'])} blocked, "
                      f"{len(rev['promoted'])} promoted, {len(rev['revoked'])} revoked")
            sources = pollable(load_watchlist(args.watchlist))
            for s in sources:
                next_due.setdefault(s["id"], 0.0)
            for sid in [k for k in next_due if k not in {s["id"] for s in sources}]:
                next_due.pop(sid)
```

Wrap that block in the same `try/except ApiCreditExhausted` handling as the screening (`screen_source` raises it on billing errors): simplest is to move it inside the existing `try:` that wraps `process_new_items`/`process_inbox`, after `process_inbox`.

4. `_cycle_status`: add `watchlist_path` parameter, compute `from .probation import probation_counts; prob = probation_counts(watchlist_path, logs_dir / "reader_actions.jsonl")`, pass `probation=prob` to `write_digest`, and add `"probation": prob` to the `items` dict of `write_status`. Update the call site in `run()` to pass `args.watchlist`.

- [ ] **Step 4: Run the probation tests and the existing scanner suite**

Run: `python -m pytest research-layer/pipeline/test_probation.py research-layer/pipeline/test_scanner.py -q`
Expected: all pass (the scanner suite's `pending_tier3` assertions, if any count proposals, must be updated to the cards-only rule — change the test expectation, not the rule).

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/scanner.py research-layer/pipeline/scanstatus.py research-layer/pipeline/test_probation.py research-layer/pipeline/test_scanner.py
git commit -m "feat(reader): wire case-3 admissions/reviews into the scanner; probation screened first; digest + status counts; tier-3 = cards only"
```

---

### Task 9: Revoke via a signed block record

**Files:**
- Modify: `research-layer/pipeline/approvals.py`
- Test: `research-layer/pipeline/test_probation.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from .approvals import process_approvals, sign_record


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
    assert any(e["entry_type"] == "source_revoked_by_coen" for e in _events(tmp_path / "act.jsonl"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest research-layer/pipeline/test_probation.py -q`
Expected: FAIL (entry still on the watchlist, no `source_revoked_by_coen` event)

- [ ] **Step 3: Implement** — in `approvals.process_approvals`, in the `decision == "block"` branch, after `_flip_proposal(...)`:

```python
            from .watchlist import remove_source, set_discovery_status
            removed = remove_source(watchlist_path, record["domain"])
            if removed is not None:
                set_discovery_status(discovery_path, record["domain"], "blocked",
                                     reason="revoked by Coen (morpheus-ops)")
                actions.event("source_revoked_by_coen",
                              {"domain": record["domain"], "tier": removed.get("tier") or "verified",
                               "added_by": removed.get("added_by")})
```

Read `approvals.py` lines 74–120 first to place it inside the verified-record path (after `_verify` succeeds) so an unsigned record can never remove a source.

- [ ] **Step 4: Run tests**

Run: `python -m pytest research-layer/pipeline/test_probation.py research-layer/pipeline/test_scanner.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/approvals.py research-layer/pipeline/test_probation.py
git commit -m "feat(reader): a signed block record revokes a probation (or any) watchlist source -- Coen kill authority"
```

---

### Task 10: Morpheus backend — probation read + revoke write

**Files:**
- Modify: `morpheus-hub/backend/app/services/constellation.py`
- Modify: `morpheus-hub/backend/app/routers/constellation.py`
- Test: `morpheus-hub/backend/tests/test_constellation_probation.py` (create)

Work in a worktree of `morpheus-hub` (`superpowers:using-git-worktrees`), hub dev on :8101.

- [ ] **Step 1: Write the failing tests**

```python
"""Probation read + revoke write on the constellation service (D27 case 3)."""
import json
from pathlib import Path

import pytest

from app.services import constellation as c


def _reader_root(tmp_path, monkeypatch):
    root = tmp_path / "reader"
    (root / "sources").mkdir(parents=True); (root / "logs").mkdir()
    (root / "sources" / "verified_sources.json").write_text(json.dumps({"version": 1, "sources": [
        {"id": "coen.example", "class": "blog", "name": "C", "url": "https://coen.example/", "feed": None,
         "poll_minutes": 60, "added_by": "coen", "verified_date": "2026-08-01", "notes": ""},
        {"id": "prob.example", "class": "blog", "name": "prob.example", "url": "https://prob.example/", "feed": None,
         "poll_minutes": 360, "added_by": "auto-d27-probation", "verified_date": "2026-08-20",
         "tier": "probation", "probation_since": "2026-08-20", "notes": "probation from 2026-08-20 per D27 case 3"},
    ]}), encoding="utf-8")
    rows = [{"entry_type": "source_auto_admitted", "payload": {"domain": "prob.example", "rule": "probation"}, "ts_utc": "2026-08-20T09:00:00Z"},
            {"entry_type": "source_promoted", "payload": {"domain": "won.example", "rule": "probation-yield 2/31"}, "ts_utc": "2026-08-21T09:00:00Z"},
            {"entry_type": "source_auto_blocked", "payload": {"domain": "junk.example", "rule": "source-screen", "reason": "news"}, "ts_utc": "2026-08-21T09:00:00Z"}]
    (root / "logs" / "reader_actions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(c, "_reader_root", lambda: root)
    return root


def test_reader_probation_counts_and_lists(tmp_path, monkeypatch):
    _reader_root(tmp_path, monkeypatch)
    out = c.reader_probation(days=3650)
    assert out["counts"]["on_probation"] == 1 and out["counts"]["promoted"] == 1 and out["counts"]["blocked"] == 1
    assert out["on_probation"][0]["domain"] == "prob.example"
    assert out["on_probation"][0]["since"] == "2026-08-20"
    assert [e["domain"] for e in out["events"]] == ["junk.example", "won.example", "prob.example"]   # newest first


def test_revoke_source_writes_signed_block_record(tmp_path, monkeypatch):
    root = _reader_root(tmp_path, monkeypatch)
    monkeypatch.setenv("READER_APPROVAL_KEY", "k")
    out = c.revoke_source("prob.example")
    assert out["ok"] is True
    rec = json.loads((root / "logs" / "approvals_queue.jsonl").read_text().splitlines()[0])
    assert rec["decision"] == "block" and rec["domain"] == "prob.example" and rec["sig"]
    assert c.revoke_source("coen.example")["ok"] is True            # any watchlist entry is revocable
    assert "error" in c.revoke_source("nobody.example")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd <worktree>\backend && ..\..\morpheus-hub\venv\Scripts\python.exe -m pytest tests/test_constellation_probation.py -q -p no:warnings`
Expected: FAIL `AttributeError: module ... has no attribute 'reader_probation'`

- [ ] **Step 3: Implement** (append to `services/constellation.py`)

```python
PROBATION_EVENTS = {"source_auto_admitted", "source_promoted", "source_auto_revoked",
                    "source_auto_blocked", "source_revoked_by_coen"}


def reader_probation(days: int = 30) -> dict:
    """D27 case 3 surface: probation entries now + chain events in the window.
    Counts are rows; nothing is computed that the chain did not record."""
    root = _reader_root()
    if root is None:
        return {"error": "reader not in registry", "counts": {}, "on_probation": [], "events": []}
    wl = root / "sources" / "verified_sources.json"
    sources = json.loads(wl.read_text(encoding="utf-8")).get("sources", []) if wl.exists() else []
    on_probation = [{"domain": s["id"], "url": s["url"], "since": s.get("probation_since"),
                     "notes": s.get("notes", "")}
                    for s in sources if s.get("tier") == "probation"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    counts = {"on_probation": len(on_probation), "admitted": 0, "promoted": 0, "revoked": 0, "blocked": 0}
    events = []
    p = root / "logs" / "reader_actions.jsonl"
    for line in (p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = e.get("entry_type")
        if t not in PROBATION_EVENTS:
            continue
        try:
            when = datetime.strptime((e.get("ts_utc") or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when < cutoff:
            continue
        pl = e.get("payload", {})
        if t == "source_auto_admitted":
            if pl.get("rule") != "probation":
                continue
            counts["admitted"] += 1
        elif t == "source_promoted":
            counts["promoted"] += 1
        elif t in ("source_auto_revoked", "source_revoked_by_coen"):
            counts["revoked"] += 1
        elif t == "source_auto_blocked":
            counts["blocked"] += 1
        events.append({"ts_utc": e.get("ts_utc"), "type": t, "domain": pl.get("domain"),
                       "rule": pl.get("rule"), "reason": pl.get("reason")})
    events.sort(key=lambda x: x["ts_utc"] or "", reverse=True)
    return {"agent": "reader", "counts": counts, "on_probation": on_probation, "events": events}


def revoke_source(domain: str) -> dict:
    """Coen's kill authority: a signed block record for ANY watchlist entry.
    The Reader removes the entry and blocks the domain on its next cycle."""
    key = os.environ.get("READER_APPROVAL_KEY", "")
    if not key:
        return {"error": "READER_APPROVAL_KEY not configured; see D26"}
    root = _reader_root()
    if root is None:
        return {"error": "reader not in registry"}
    wl = root / "sources" / "verified_sources.json"
    sources = json.loads(wl.read_text(encoding="utf-8")).get("sources", []) if wl.exists() else []
    src = next((s for s in sources if s["id"] == domain), None)
    if src is None:
        return {"error": f"no watchlist entry for domain: {domain}"}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {"id": f"{domain}-{ts}", "action": "source_decision", "domain": domain,
              "url": src["url"], "decision": "block", "name": domain,
              "source_class": src.get("class", "blog"), "actor": "coen",
              "via": "morpheus-ops", "ts_utc": ts}
    record["sig"] = _sign(record, key)
    queue = root / "logs" / "approvals_queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    with queue.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "domain": domain, "decision": "block", "ts_utc": ts}
```

Ensure `timedelta` is imported at the top of the module. Leave `reader_proposals` and `decide_source` in place (read history) but remove the **route** for `decide` below.

Router (`routers/constellation.py`): replace the `post_reader_decision` route with:

```python
class RevokeBody(BaseModel):
    domain: str


@router.get("/reader/probation")
def get_reader_probation(days: int = Query(30, ge=1, le=365)):
    return constellation.reader_probation(days)


@router.post("/reader/probation/revoke")
def post_reader_revoke(body: RevokeBody):
    return constellation.revoke_source(body.domain)
```

Update the module docstring: "Read-only except the single write: POST /reader/probation/revoke appends a signed block record (Coen's kill authority, D27 case 3)."

- [ ] **Step 4: Run backend tests**

Run: `..\..\morpheus-hub\venv\Scripts\python.exe -m pytest tests -q -p no:warnings`
Expected: all pass; any test asserting `/reader/proposals/decide` exists must be updated to the revoke route (the decide route is gone by design).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/constellation.py backend/app/routers/constellation.py backend/tests/test_constellation_probation.py backend/tests/<any updated test>
git commit -m "feat(ops): reader probation read + revoke write replace D26 approve/block (D27 case 3)"
```

---

### Task 11: Morpheus frontend — ProbationPanel

**Files:**
- Modify: `morpheus-hub/frontend2/src/ops/OpsApp.tsx` (replace `Proposal`, `ProposalRow`, `ProposalsPanel`; tab label; BigStat)
- Modify: `morpheus-hub/frontend2/public/sw.js` (CACHE `mh-v11` → `mh-v12`) and the matching comment in `src/main.tsx`
- Test: `morpheus-hub/frontend2/src/ops/OpsApp.test.tsx`

- [ ] **Step 1: Write the failing test** (add to `OpsApp.test.tsx`, following the file's existing mock pattern for `api.get`)

```tsx
it('shows probation counts, the list, and a two-step revoke', async () => {
  mockGet('/reader/probation', {
    counts: { on_probation: 2, admitted: 3, promoted: 1, revoked: 0, blocked: 4 },
    on_probation: [{ domain: 'prob.example', url: 'https://prob.example/', since: '2026-08-20', notes: 'probation from 2026-08-20' }],
    events: [{ ts_utc: '2026-08-21T09:00:00Z', type: 'source_auto_blocked', domain: 'junk.example', rule: 'source-screen', reason: 'news' }],
  });
  render(<OpsApp />);
  await screen.findByText('ON PROBATION');
  expect(screen.getByText('2')).toBeInTheDocument();
  expect(screen.getByText('prob.example')).toBeInTheDocument();
  fireEvent.click(screen.getByText('revoke'));
  expect(screen.getByText('confirm revoke?')).toBeInTheDocument();
  expect(screen.queryByText('approve')).toBeNull();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend2 && npx vitest run src/ops/OpsApp.test.tsx`
Expected: FAIL (no `ON PROBATION` text)

- [ ] **Step 3: Implement** — replace the three proposal symbols with:

```tsx
interface ProbationEntry { domain: string; url: string; since: string | null; notes: string }
interface ProbationEvent { ts_utc: string; type: string; domain: string; rule: string | null; reason: string | null }
interface ProbationData {
  counts: { on_probation: number; admitted: number; promoted: number; revoked: number; blocked: number };
  on_probation: ProbationEntry[]; events: ProbationEvent[]; error?: string;
}

const EVENT_LABEL: Record<string, { text: string; cls: string }> = {
  source_auto_admitted: { text: 'admitted', cls: 'text-cyan' },
  source_promoted: { text: 'promoted', cls: 'text-mint' },
  source_auto_revoked: { text: 'revoked', cls: 'text-[#ff5c5c]' },
  source_revoked_by_coen: { text: 'revoked · coen', cls: 'text-[#ff5c5c]' },
  source_auto_blocked: { text: 'blocked', cls: 'text-[#ff9c9c]' },
};

// D27 case 3: the only write on this page is Coen's revoke (kill authority).
function ProbationRow({ p, onRevoke, sent }: { p: ProbationEntry; sent: boolean; onRevoke: (d: string) => void }) {
  const [armed, setArmed] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-cyan/8 py-2 last:border-b-0">
      <div className="min-w-0 flex-1">
        <a href={p.url} target="_blank" rel="noopener noreferrer"
           className="text-[12px] text-cyan underline decoration-cyan/30 hover:decoration-cyan">{p.domain}</a>
        <div className="truncate text-[10px] text-[#6b93a3]" title={p.notes}>{p.notes}</div>
        <div className="text-[9px] uppercase tracking-wider text-muted">probation since {p.since ?? '—'}</div>
      </div>
      {sent
        ? <span className="text-[10px] uppercase tracking-widest text-[#ff5c5c]">revoked · reader applies next cycle</span>
        : <button onMouseLeave={() => setArmed(false)}
            onClick={() => (armed ? onRevoke(p.domain) : setArmed(true))}
            className={`${HIT} min-h-[28px] border px-2 text-[10px] uppercase tracking-[0.14em] transition-colors duration-200 ${
              armed ? 'border-current bg-current/10 text-[#ff5c5c]' : 'border-cyan/15 text-muted hover:text-[#ff5c5c]'}`}>
            {armed ? 'confirm revoke?' : 'revoke'}
          </button>}
    </div>
  );
}

function ProbationPanel() {
  const [sent, setSent] = useState<Record<string, boolean>>({});
  const q = useQuery({
    queryKey: ['ops-probation'],
    queryFn: async () => (await api.get('/reader/probation')).data as ProbationData,
    refetchInterval: 60_000,
  });
  const revoke = async (domain: string) => {
    const res = (await api.post('/reader/probation/revoke', { domain })).data;
    if (res.ok) setSent((s) => ({ ...s, [domain]: true }));
    else alert(res.error ?? 'revoke failed');
  };
  const d = q.data;
  const counts = d?.counts;
  const stat = (label: string, v: number | undefined) => (
    <div className="border border-cyan/15 px-3 py-2">
      <div className="text-[18px] font-semibold text-mint">{v ?? '—'}</div>
      <div className="text-[9px] uppercase tracking-[0.18em] text-muted">{label}</div>
    </div>
  );
  return (
    <div>
      <div className="mb-2 text-[10px] uppercase tracking-wider text-muted">
        sources admitted by D27 rules · counts are chain rows · revoke is the only write
      </div>
      {d?.error && <div className="text-[11px] text-[#ff9c9c]">{d.error}</div>}
      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {stat('ON PROBATION', counts?.on_probation)}
        {stat('ADMITTED · 30D', counts?.admitted)}
        {stat('PROMOTED · 30D', counts?.promoted)}
        {stat('REVOKED · 30D', counts?.revoked)}
        {stat('AUTO-BLOCKED · 30D', counts?.blocked)}
      </div>
      {(d?.on_probation ?? []).map((p) => <ProbationRow key={p.domain} p={p} sent={!!sent[p.domain]} onRevoke={revoke} />)}
      {d && !d.on_probation.length && !d.error && <div className="text-[11px] text-muted">nothing on probation</div>}
      <div className="mt-3 text-[9px] uppercase tracking-wider text-muted">recent events</div>
      {(d?.events ?? []).slice(0, 30).map((e, i) => {
        const lab = EVENT_LABEL[e.type] ?? { text: e.type, cls: 'text-muted' };
        return (
          <div key={i} className="flex gap-2 border-b border-cyan/8 py-1 text-[10px] last:border-b-0">
            <span className="text-muted">{e.ts_utc.slice(5, 16).replace('T', ' ')}</span>
            <span className={`uppercase tracking-widest ${lab.cls}`}>{lab.text}</span>
            <span className="text-cyan">{e.domain}</span>
            <span className="truncate text-[#6b93a3]" title={e.reason ?? ''}>{e.rule}{e.reason ? ` · ${e.reason}` : ''}</span>
          </div>
        );
      })}
    </div>
  );
}
```

Then: rename the tab `'proposals'` → `'probation'` in the `tabs` tuple/state/`{tab === 'probation' && <ProbationPanel />}`; change the BigStat `label="tier-3 pending" sub="awaiting Coen"` to `sub="cards in triage"`; every new `<button>` has `cursor-pointer` via `HIT` (check `HIT` includes it). Bump `sw.js` CACHE to `mh-v12` and the comment in `main.tsx`.

- [ ] **Step 4: Run frontend tests + build**

Run: `npx vitest run && npm run build --silent`
Expected: tests pass; build succeeds; `grep -c "#4d7a8a" -r src` = 0.

- [ ] **Step 5: Commit**

```bash
git add frontend2/src/ops/OpsApp.tsx frontend2/src/ops/OpsApp.test.tsx frontend2/public/sw.js frontend2/src/main.tsx
git commit -m "feat(ops): ProbationPanel (counts + revoke) replaces D26 approve/block; sw mh-v12"
```

Verify in the worktree dev server (:5280 → :8101) that `/ops` → reader → probation renders against the live reader root, then merge to master per `superpowers:finishing-a-development-branch`. Cutover of the live hub = `Start Morpheus.bat` (after killing `electron.exe` of morpheus-hub only); two loads to activate `mh-v12`.

---

### Task 12: Governance + contract + docs

**Files:**
- Modify: `stewartandco-agents/DECISIONS.md` (append D27 case 3 as a dated amendment entry)
- Modify: `stewartandco-agents/hubs/intelligence/agents/reader/CONTRACT.md` (source admission section) and `research-layer/pipeline/scanstatus.py` `CONTRACT_VERSION = "1.7"` in the SAME commit pair
- Modify: `research-layer/docs/2026-08-23-source-probation-filter-design.md` §2 item 1 (index-items clarification from Task 3)
- Modify: `research-layer/CLAUDE.md` (if present) / vault note

- [ ] **Step 1: DECISIONS.md entry** (append; Coen ratifies in session, the text is the one he approved 2026-08-23)

```markdown
## D27 case 3. 2026-08-24. Source admission fully automated: probation-by-yield replaces human verification

Coen's call 2026-08-23: "remove the human verification, and implement a filter to do quality control instead." D27 cases 1-2 stand. Case 3 covers single-citation proposals: deterministic pre-filter (junk list, blocked subdomains, unreachable, no feed and < 5 index items) -> one Sonnet source screen (malformed output never admits; blocked after 3 malformed runs) -> admission ON PROBATION (added_by auto-d27-probation, tier probation). Probation items are screened ahead of the backlog (40/source/run). 2 keeps within 40 screened items promotes (auto-d27-promoted); 0 of 40 revokes and blocks; exactly 1 extends the window to 80; 90 days unresolved times out back to proposed. A second distinct citer promotes at once (case 2 outranks yield). Every transition is chain-logged (source_auto_admitted rule probation / source_promoted / source_auto_revoked / source_auto_blocked); the daily digest and status.json carry the counts. D26's approve path is retired; Coen's kill authority is a signed block record from /ops that removes any watchlist entry (source_revoked_by_coen). Calibration on record: corpus keep-rate ~3%, median verified source 0%, so thresholds are counts, not rates. Composer seed pool deliberately NOT restricted by asset class (Coen, same day); card->cell routing is market-expansion sub-project 4.
```

- [ ] **Step 2: CONTRACT.md + CONTRACT_VERSION** — add the same rules under the Reader's source-admission section, bump `CONTRACT_VERSION = "1.7"` in `scanstatus.py`, and assert it in `test_probation.py`:

```python
def test_contract_version_bumped_with_case_3():
    from .scanstatus import CONTRACT_VERSION
    assert CONTRACT_VERSION == "1.7"
```

- [ ] **Step 3: Spec clarification** — in the design doc §2 item 1 replace "index page with ≥ 5 dated items in the trailing 12 months" with "index page with ≥ 5 same-site article links (dating index links is not reliable from HTML; the source screen and the yield window carry the quality burden) — clarification recorded at build, 2026-08-24".

- [ ] **Step 4: Commit (two repos, scoped)**

```bash
# stewart-forward-test
git add research-layer/pipeline/scanstatus.py research-layer/pipeline/test_probation.py research-layer/docs/2026-08-23-source-probation-filter-design.md
git commit -m "docs(reader): contract 1.7 (D27 case 3) + spec clarification on index items"
# stewartandco-agents
git add DECISIONS.md hubs/intelligence/agents/reader/CONTRACT.md
git commit -m "D27 case 3: probation-by-yield source admission replaces human verification (ratified 2026-08-24)"
```

---

### Task 13: First live pass over the 114 pending, and the readout

**Files:** none (runtime)

- [ ] **Step 1: Dry inventory** — count what case 3 will see:

```bash
cd E:\Users\Coen\Claude\stewart-forward-test\research-layer
python -c "from pipeline.watchlist import load_discovery; from pipeline.probation import _is_case_1_or_2; q=load_discovery('sources/discovery_queue.jsonl'); print(sum(1 for e in q if e['status']=='proposed' and not _is_case_1_or_2(e)))"
```
Expected: 114 (or whatever the scanner has added since).

- [ ] **Step 2: One scanner cycle** — the resident scanner is a scheduled task (`StewartCo\21_ReaderScanner`, keepalive) and self-guards duplicates via `run_scanner.ps1`; do NOT start a second instance. Wait for the next cycle (≤ 60 s loop) and read `logs/source_screen_log.jsonl` + `logs/reader_actions.jsonl` tail:

```bash
python -c "import json;rows=[json.loads(l) for l in open('logs/source_screen_log.jsonl',encoding='utf-8')];print(len(rows),sum(1 for r in rows if r['verdict'] is True),sum(1 for r in rows if r['verdict'] is False),sum(1 for r in rows if r['verdict'] is None))"
python -m pipeline.report --hours 2
```

- [ ] **Step 3: Record** — the first-pass numbers (prefilter blocks / screen blocks / admitted / deferred / USD spent) go into the vault note `project_research_layer.md` and the spec's status line; the probation outcomes arrive over the following days via the digest line.

- [ ] **Step 4: Nothing to commit** except the vault note (outside git) and, if the scanner wrote them, the queue/watchlist files stay UNSTAGED as always.

---

## Self-review

- **Spec coverage:** §2 pre-filter → T3; source screen incl. malformed x3 → T2/T5; §3 probation entry, priority 40/source, 2-of-40 / 1→80 / 0→revoke / 90-day timeout / case-2 override / kill authority → T4/T6/T7/T9; §4 backlog first run → T13; blocked stay blocked → T5 (`status != proposed` skipped) + T9; Morpheus counts + revoke → T10/T11; digest line + status items → T8; governance D27 case 3 + D26 retirement + contract bump → T12; §6 tests enumerated → T1–T11; opt-in live smoke → T13 covers it with the real first pass (no separate fixture needed).
- **Placeholders:** none; every code step is complete. One implementation choice is stated (index items = ≥5 article links, T3 note → T12 spec clarification).
- **Type consistency:** `process_admissions(discovery_path, watchlist_path, actions, fetch, screen, today)`, `process_reviews(watchlist_path, discovery_path, seen, actions, today)`, `prioritise_items(items, probation_ids, cap)`, `probation_counts(watchlist_path, actions_path, days)`, `screen_source(client, model, meter, domain, titles, about, log_path)` used identically across T5–T8 and T10. Provenance strings `auto-d27-probation` / `auto-d27-promoted` match `watchlist.POLLABLE_PROVENANCE` (T1) and `probation.py` constants (T3).
