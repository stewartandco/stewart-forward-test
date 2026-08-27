# Pipeline Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A scheduled trigger-check orchestrator (`pipeline/loop.py --once`) that runs triage -> compose -> screen -> gauntlet for a class when enough new accepted cards accumulate, under a chain-lock protocol and the existing budget caps.

**Architecture:** New modules `chainlock.py`, `loop_state.py`, `loop.py` in `pipeline/`; a `routable_cards()` extraction from `composer.py` shared by composer and loop; stage execution via subprocess against the existing `python -m pipeline.*` CLIs; status per AGENT_STATUS_CONVENTION via `pipeline_status`; task `\StewartCo\25_PipelineLoop` (XML, three daily triggers). Spec: `docs/2026-08-27-pipeline-loop-design.md`.

**Tech Stack:** Python stdlib only (json, subprocess, os, pathlib), pytest, Windows Task Scheduler, git.

**Repo rules that bind every task:** work on the research-layer branch only (D8: never touch repo root `forward_test_log.jsonl` / root `verify.py` / main). Scoped `git add <explicit paths>` only, never `-A`, never `reset --hard`. The scanner writes the live chain continuously: never edit `registry_log.jsonl` by hand, and run tests from the layer root `E:\Users\Coen\Claude\stewart-forward-test\research-layer` as `python -m pytest pipeline/<file> -q`.

---

### Task 0: Step-0 governance checklist (COEN-GATED - no code, do NOT skip past it silently)

These are session actions with Coen, from spec section "Step 0". Tasks 1-8 may be BUILT before this completes (D29: building is not gated), but Task 9 (activation) MUST NOT run until every box here is checked in-session with Coen.

- [ ] **0.1** Verify Ops Sentinel graduation evidence with Coen: Sentinel action log + his confirmation of week 2 (week 1 signed off 2026-08-18; the 2026-08-23 GitHub-504 false alarm is within the <1/week allowance but the call is his).
- [ ] **0.2** Run D31 triage activation: `python -m pipeline.triage_batch --limit 50 --apply` for the first applied batch, then hand-verify a 20-card sample against quotes with Coen per D34's precision stage. One confirmed overreach suspends auto-accept and returns the batch to Tier 3.
- [ ] **0.3** Record in the session note that D21's pre-approved Composer campaign (2026-08-13) is treated as historical.
- [ ] **0.4** Only after 0.1-0.3: proceed to Task 9 (supervised run + task registration).

---

### Task 1: Chain lock module

`Registry.append()` already serialises individual appends via `pipeline/lock.py` `FileLock` on `registry_log.jsonl.lock`. This task adds the coordination layer ABOVE that, per spec: an advisory `logs/chain.lock` that a writer holds for a WRITE WINDOW (a batch of chain appends), so the loop can see "someone is mid-write" and defer, and manual sessions can hold it while they work. Read paths never take it.

**Files:**
- Create: `pipeline/chainlock.py`
- Test: `pipeline/test_chainlock.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Offline tests for the advisory chain lock (no network).

Run: python -m pytest pipeline/test_chainlock.py -q
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from .chainlock import ChainLock, ChainLockHeld


def test_acquire_writes_holder_metadata(tmp_path):
    lk = ChainLock(tmp_path, holder="loop", purpose="cycle 2026-08-27-loop-fx")
    lk.acquire()
    try:
        info = json.loads((tmp_path / "chain.lock").read_text(encoding="utf-8"))
        assert info["holder"] == "loop"
        assert info["pid"] == os.getpid()
        assert info["purpose"] == "cycle 2026-08-27-loop-fx"
        assert info["ts_utc"].endswith("+00:00") or info["ts_utc"].endswith("Z")
    finally:
        lk.release()
    assert not (tmp_path / "chain.lock").exists()


def test_second_acquire_raises_held(tmp_path):
    a = ChainLock(tmp_path, holder="scanner", purpose="card batch")
    a.acquire()
    b = ChainLock(tmp_path, holder="loop", purpose="cycle")
    with pytest.raises(ChainLockHeld):
        b.acquire()
    a.release()
    b.acquire()
    b.release()


def test_info_none_when_absent_and_unreadable_when_corrupt(tmp_path):
    lk = ChainLock(tmp_path, holder="loop", purpose="x")
    assert lk.info() is None
    (tmp_path / "chain.lock").write_text("not json", encoding="utf-8")
    assert lk.info()["holder"] == "unreadable"


def test_stale_detection_and_break(tmp_path):
    lk = ChainLock(tmp_path, holder="loop", purpose="x", stale_after_s=1)
    other = ChainLock(tmp_path, holder="session", purpose="manual")
    other.acquire()
    assert not lk.is_stale()
    with pytest.raises(ChainLockHeld):
        lk.break_stale()          # refuses to break a fresh lock
    old = time.time() - 10
    os.utime(tmp_path / "chain.lock", (old, old))
    assert lk.is_stale()
    lk.break_stale()
    assert not (tmp_path / "chain.lock").exists()


def test_context_manager_releases_on_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with ChainLock(tmp_path, holder="loop", purpose="x"):
            raise RuntimeError("boom")
    assert not (tmp_path / "chain.lock").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_chainlock.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.chainlock'`

- [ ] **Step 3: Write the implementation**

```python
"""Advisory chain lock for big writers on registry_log.jsonl.

Registry.append() already serialises individual appends (FileLock on
registry_log.jsonl.lock, pipeline/lock.py). This lock is the coordination
layer ABOVE that: a writer takes logs/chain.lock for a WRITE WINDOW (a
batch of chain appends), so the loop can defer instead of interleaving a
generation with another writer's batch, and manual sessions can hold it
while they work on the chain. Rules (spec 2026-08-27-pipeline-loop-design):

- Held for append windows, not whole runs; the scanner's cycle must never
  block on a gauntlet.
- The loop DEFERS when the lock is held; it never breaks a fresh lock.
- A stale lock is surfaced as WARN and only broken on a second sighting
  (the two-strike rule lives in loop.py, not here).
- Read paths never take this lock.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# A full gauntlet pass is now well under 1 h; 3 h marks a crashed holder.
STALE_AFTER_S = 3 * 3600


class ChainLockHeld(RuntimeError):
    """The lock is held (or fresh) and the requested action is refused."""


class ChainLock:
    def __init__(self, logs_dir: str | Path, holder: str, purpose: str,
                 stale_after_s: float = STALE_AFTER_S) -> None:
        self.path = Path(logs_dir) / "chain.lock"
        self.holder = holder
        self.purpose = purpose
        self.stale_after_s = stale_after_s
        self._acquired = False

    def info(self) -> dict | None:
        """Lock metadata, None when absent, holder='unreadable' on corrupt."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return {"holder": "unreadable", "pid": None, "ts_utc": None,
                    "purpose": None}

    def age_s(self) -> float | None:
        try:
            return time.time() - self.path.stat().st_mtime
        except OSError:
            return None

    def is_stale(self) -> bool:
        age = self.age_s()
        return age is not None and age > self.stale_after_s

    def acquire(self) -> None:
        payload = json.dumps({
            "holder": self.holder,
            "pid": os.getpid(),
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "purpose": self.purpose,
        })
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ChainLockHeld(f"chain.lock held: {self.info()}") from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        self._acquired = True

    def break_stale(self) -> None:
        """Remove a STALE lock. Refuses a fresh one. Two-strike rule is the
        caller's responsibility."""
        if not self.is_stale():
            raise ChainLockHeld("refusing to break a fresh chain.lock")
        self.path.unlink(missing_ok=True)

    def release(self) -> None:
        if self._acquired:
            self.path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self) -> "ChainLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_chainlock.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/chainlock.py research-layer/pipeline/test_chainlock.py
git commit -m "feat(loop): advisory chain lock for big chain writers"
```

---

### Task 2: Extract routable_cards() from composer (shared by composer and loop)

The class-routable card selection currently lives inline in `composer.run()` (composer.py lines ~1326-1388: `ROUTING`, proxy lanes via `INDEX_FUTURES_PROXY_TOPICS` / `METALS_PROXY_TOPICS` / `BOND_ETF_PROXY_TAGS`). The loop's watermark needs the same selection; duplicate logic would drift. Extract a pure function; composer calls it; behaviour must not change.

**Files:**
- Modify: `pipeline/composer.py` (extract function, rewire `run()`)
- Test: `pipeline/test_composer.py` (append new tests)

- [ ] **Step 1: Write the failing tests** (append to `pipeline/test_composer.py`, matching its existing import style)

```python
# --- routable_cards extraction (loop plan Task 2) ---

def _card(cid, asset_classes=None, topics=None):
    return {
        "card_id": cid,
        "claim": f"claim {cid}",
        "topics": topics or [],
        "tags": {"asset_classes": asset_classes} if asset_classes is not None else {},
        "review": {"status": "accepted", "reject_reason": None},
    }


def test_routable_cards_crypto_is_unrestricted():
    from .composer import routable_cards
    accepted = {"a": _card("a", ["equities"]), "b": _card("b", None)}
    cards, meta = routable_cards(accepted, "crypto")
    assert set(cards) == {"a", "b"}
    assert meta["routed_card_ids"] is None      # unrestricted: no routing applied


def test_routable_cards_fx_filters_on_tags():
    from .composer import routable_cards
    accepted = {
        "fx1": _card("fx1", ["fx"]),
        "crs": _card("crs", None),              # missing tags -> ["cross"] default
        "eq1": _card("eq1", ["equities"]),
    }
    cards, meta = routable_cards(accepted, "fx")
    assert set(cards) == {"fx1", "crs"}
    assert set(meta["routed_card_ids"]) == {"fx1", "crs"}
    assert meta["proxy_routed_card_ids"] == []


def test_routable_cards_equity_proxy_lane_recorded():
    from .composer import routable_cards, INDEX_FUTURES_PROXY_TOPICS
    topic = sorted(INDEX_FUTURES_PROXY_TOPICS)[0]
    accepted = {
        "eq1": _card("eq1", ["equities"]),
        "fut": _card("fut", ["futures"], topics=[topic]),
    }
    cards, meta = routable_cards(accepted, "equity_etf")
    assert "eq1" in cards and "fut" in cards
    assert "fut" in meta["proxy_routed_card_ids"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_composer.py -q -k routable_cards`
Expected: FAIL with `ImportError: cannot import name 'routable_cards'`

- [ ] **Step 3: Extract the function**

In `pipeline/composer.py`, above `run()`, add `routable_cards` by MOVING the existing selection logic (do not rewrite it - cut the exact filtering/proxy code out of `run()` and wrap it):

```python
def routable_cards(accepted: dict[str, dict], asset_class: str) -> tuple[dict[str, dict], dict]:
    """Pure selection of accepted cards routable to asset_class.

    Returns (cards, meta) where meta carries routed_card_ids and
    proxy_routed_card_ids exactly as run() previously computed them for the
    drift record (None / [] respectively for the unrestricted crypto path).
    Moved out of run() so pipeline/loop.py watermarks count the SAME set the
    composer would consume. Chain untouched; no side effects.
    """
```

The body is the moved code; `run()` then becomes:

```python
    accepted = registry.cards(status="accepted")
    ...
    propose_input, routing_meta = routable_cards(accepted, args.asset_class)
```

with `routing_meta` feeding `drift_record(...)` unchanged.

- [ ] **Step 4: Run the new tests AND the full composer regression net**

Run: `python -m pytest pipeline/test_composer.py pipeline/test_composer_fx.py pipeline/test_composer_equity.py pipeline/test_composer_2b.py pipeline/test_composer_budget.py -q`
Expected: all pass (the pre-existing tests pin that behaviour did not change)

- [ ] **Step 5: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/composer.py research-layer/pipeline/test_composer.py
git commit -m "refactor(composer): extract routable_cards() for loop watermarks"
```

---

### Task 3: Watermark state module

**Files:**
- Create: `pipeline/loop_state.py`
- Test: `pipeline/test_loop_state.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Offline tests for loop watermark state.

Run: python -m pytest pipeline/test_loop_state.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

from .loop_state import (DEFAULT_THRESHOLD, load, save, pick_class,
                         record_generation, record_stale_lock, clear_stale_lock)


def test_load_missing_file_returns_empty_state(tmp_path):
    st = load(tmp_path / "loop_state.json")
    assert st == {"classes": {}, "stale_lock": None}


def test_save_load_roundtrip_atomic(tmp_path):
    p = tmp_path / "loop_state.json"
    st = load(p)
    record_generation(st, "fx", run_id="2026-08-27-loop-fx",
                      routable_count=40, ts_utc="2026-08-27T10:30:00+00:00")
    save(p, st)
    assert not p.with_suffix(".json.tmp").exists()
    st2 = load(p)
    assert st2["classes"]["fx"]["watermark"] == 40
    assert st2["classes"]["fx"]["last_run_id"] == "2026-08-27-loop-fx"


def test_pick_class_requires_threshold_delta():
    st = {"classes": {
        "fx": {"watermark": 40, "threshold": 25,
               "last_gen_ts_utc": "2026-08-20T00:00:00+00:00", "last_run_id": "x"},
    }, "stale_lock": None}
    assert pick_class(st, {"fx": 60}) is None          # delta 20 < 25
    assert pick_class(st, {"fx": 66}) == "fx"          # delta 26 >= 25


def test_pick_class_prefers_oldest_and_never_run_counts_as_oldest():
    st = {"classes": {
        "crypto": {"watermark": 0, "threshold": 25,
                   "last_gen_ts_utc": "2026-08-26T00:00:00+00:00", "last_run_id": "a"},
        "fx": {"watermark": 0, "threshold": 25,
               "last_gen_ts_utc": "2026-08-20T00:00:00+00:00", "last_run_id": "b"},
    }, "stale_lock": None}
    assert pick_class(st, {"crypto": 30, "fx": 30}) == "fx"       # older gen wins
    assert pick_class(st, {"crypto": 30, "fx": 30, "equity_etf": 30}) == "equity_etf"  # never-run is oldest


def test_pick_class_unknown_class_uses_default_threshold():
    st = {"classes": {}, "stale_lock": None}
    assert DEFAULT_THRESHOLD == 25
    assert pick_class(st, {"fx": 24}) is None
    assert pick_class(st, {"fx": 25}) == "fx"


def test_stale_lock_two_strike_bookkeeping(tmp_path):
    st = load(tmp_path / "s.json")
    first = record_stale_lock(st, {"holder": "loop", "pid": 1,
                                   "ts_utc": "2026-08-27T01:00:00+00:00"})
    assert first is False                              # first sighting: not yet breakable
    again = record_stale_lock(st, {"holder": "loop", "pid": 1,
                                   "ts_utc": "2026-08-27T01:00:00+00:00"})
    assert again is True                               # same lock seen twice: breakable
    other = record_stale_lock(st, {"holder": "loop", "pid": 2,
                                   "ts_utc": "2026-08-27T02:00:00+00:00"})
    assert other is False                              # different lock: strike count resets
    clear_stale_lock(st)
    assert st["stale_lock"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_loop_state.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.loop_state'`

- [ ] **Step 3: Write the implementation**

```python
"""Per-class watermark state for the pipeline loop (logs/loop_state.json).

The trigger rule (spec 2026-08-27-pipeline-loop-design): a class fires when
routable_accepted_now - watermark >= threshold (default 25). The watermark
is the routable-accepted count recorded at that class's last completed
generation. pick_class returns ONE class per fire: the over-threshold class
whose last generation is oldest; a never-run class counts as oldest;
ties break by cells.LIVE_CLASSES order. Also holds the two-strike stale
chain.lock bookkeeping (the lock itself is pipeline/chainlock.py).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import cells

DEFAULT_THRESHOLD = 25

_EMPTY = {"classes": {}, "stale_lock": None}


def load(path: str | Path) -> dict:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"classes": {}, "stale_lock": None}


def save(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def record_generation(state: dict, asset_class: str, *, run_id: str,
                      routable_count: int, ts_utc: str) -> None:
    entry = state["classes"].setdefault(asset_class, {"threshold": DEFAULT_THRESHOLD})
    entry["watermark"] = routable_count
    entry["last_run_id"] = run_id
    entry["last_gen_ts_utc"] = ts_utc


def pick_class(state: dict, routable_counts: dict[str, int]) -> str | None:
    over: list[str] = []
    for cls in cells.LIVE_CLASSES:
        if cls not in routable_counts:
            continue
        entry = state["classes"].get(cls, {})
        threshold = entry.get("threshold", DEFAULT_THRESHOLD)
        watermark = entry.get("watermark", 0)
        if routable_counts[cls] - watermark >= threshold:
            over.append(cls)
    if not over:
        return None
    order = {c: i for i, c in enumerate(cells.LIVE_CLASSES)}
    # never-run sorts before any timestamp; then oldest timestamp; then declared order
    return min(over, key=lambda c: (
        state["classes"].get(c, {}).get("last_gen_ts_utc") is not None,
        state["classes"].get(c, {}).get("last_gen_ts_utc") or "",
        order[c],
    ))


def _lock_key(info: dict) -> str:
    return f"{info.get('holder')}|{info.get('pid')}|{info.get('ts_utc')}"


def record_stale_lock(state: dict, info: dict) -> bool:
    """Record a stale-lock sighting. True when the SAME lock was already
    recorded on a previous fire (second strike: the caller may break it)."""
    key = _lock_key(info)
    if state.get("stale_lock") == key:
        return True
    state["stale_lock"] = key
    return False


def clear_stale_lock(state: dict) -> None:
    state["stale_lock"] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_loop_state.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/loop_state.py research-layer/pipeline/test_loop_state.py
git commit -m "feat(loop): per-class watermark state with two-strike stale-lock bookkeeping"
```

---

### Task 4: Atomic status writer for the pipeline agent

`pipeline/pipeline_status.py` already builds the status dict (`build(stage_results, spent, escalations)`, `worst_of`, `PUSH_TRIGGERS`) but nothing writes it. Add an atomic writer mirroring `scanstatus.write_status` (tmp + `os.replace`).

**Files:**
- Modify: `pipeline/pipeline_status.py`
- Test: `pipeline/test_pipeline_status.py` (append)

- [ ] **Step 1: Write the failing test** (append to `pipeline/test_pipeline_status.py`, matching its import style)

```python
def test_write_is_atomic_and_roundtrips(tmp_path):
    from .pipeline_status import build, write
    import json
    payload = build({"trigger": "OK"}, spent=1.23)
    p = tmp_path / "status.json"
    write(p, payload)
    assert not (tmp_path / "status.json.tmp").exists()
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["agent"] == "pipeline"
    assert on_disk == payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/test_pipeline_status.py -q -k write_is_atomic`
Expected: FAIL with `ImportError: cannot import name 'write'`

- [ ] **Step 3: Implement** (append to `pipeline/pipeline_status.py`)

```python
def write(path, payload: dict) -> None:
    """Atomic write (tmp + replace), mirroring scanstatus.write_status."""
    import json
    import os
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_pipeline_status.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/pipeline_status.py research-layer/pipeline/test_pipeline_status.py
git commit -m "feat(loop): atomic status writer for the pipeline agent"
```

---

### Task 5: Scanner adopts the chain lock for its card-registration window

The scanner's `_extract_item` registers cards in a loop (`scanner.py` ~lines 143-152) with per-append FileLock only. Wrap the registration WINDOW (the `for raw in claims:` block) in `ChainLock(holder="scanner")` so a batch of card appends is one visible write window. Short window (seconds); the scanner still never blocks on a gauntlet because the loop holds chain.lock only for ITS write windows, not whole cycles.

**Files:**
- Modify: `pipeline/scanner.py` (`_extract_item`)
- Test: `pipeline/test_scanner.py` (append)

- [ ] **Step 1: Write the failing test** (append to `pipeline/test_scanner.py`; reuse that file's existing fake-client/registry fixture style for `_extract_item` - follow the pattern of its existing extraction tests)

```python
def test_extract_item_takes_chain_lock_for_registration_window(tmp_path, monkeypatch):
    """While cards are being registered, logs/chain.lock exists and names the
    scanner; after extraction it is gone."""
    from . import scanner as sc
    from .registry import Registry

    seen_during_append = {}
    reg = Registry(tmp_path / "registry_log.jsonl")
    orig = Registry.register_card

    def spying_register(self, card):
        lock_path = tmp_path / "logs" / "chain.lock"
        seen_during_append["present"] = lock_path.exists()
        if lock_path.exists():
            import json
            seen_during_append["holder"] = json.loads(
                lock_path.read_text(encoding="utf-8"))["holder"]
        return orig(self, card)

    monkeypatch.setattr(Registry, "register_card", spying_register)
    monkeypatch.setattr(sc, "LOGS_DIR", tmp_path / "logs", raising=False)

    # drive _extract_item with the file's existing fake client returning one
    # claim whose quote IS in page_text (copy the arrange block from the
    # nearest existing _extract_item test in this file)
    ...

    assert seen_during_append["present"] is True
    assert seen_during_append["holder"] == "scanner"
    assert not (tmp_path / "logs" / "chain.lock").exists()
```

Note to implementer: the `...` arrange block is intentionally not reproduced here because it must copy the CURRENT fake-client fixture from `test_scanner.py` verbatim (it has changed before; copy, don't invent). Everything else in the test is complete as written. Resolve how `_extract_item` learns the logs dir in Step 3 and mirror it in the monkeypatch.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/test_scanner.py -q -k chain_lock_for_registration`
Expected: FAIL (`seen_during_append["present"] is False` - no lock taken yet)

- [ ] **Step 3: Implement**

In `pipeline/scanner.py` `_extract_item`, wrap ONLY the registration loop. The logs dir is derivable from the registry path (`Path(registry.log_path).parent / "logs"`); pass it explicitly if `_extract_item` already receives a logs/status path - follow whichever is already threaded through.

```python
from .chainlock import ChainLock, ChainLockHeld

    # ... claims extracted, before: for raw in claims:
    lock = ChainLock(logs_dir, holder="scanner",
                     purpose=f"card batch {source.get('id', '?')}")
    try:
        lock.acquire()
    except ChainLockHeld:
        # another writer is mid-window; register on the next cycle instead of
        # interleaving - items stay in seen-store state and re-feed
        return ExtractOutcome(registered=0, dropped=dropped, deferred_lock=True)
    try:
        for raw in claims:
            ...existing body unchanged...
    finally:
        lock.release()
```

(If `_extract_item` returns a plain tuple today, extend it the way the existing code signals other defer states - match the current return shape rather than introducing a new dataclass.)

- [ ] **Step 4: Run the scanner suite**

Run: `python -m pytest pipeline/test_scanner.py -q`
Expected: all pass (existing extraction tests unaffected: with no contention the lock is take-and-release)

- [ ] **Step 5: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/scanner.py research-layer/pipeline/test_scanner.py
git commit -m "feat(loop): scanner takes chain.lock for its card-registration window"
```

---

### Task 6: Quarantine daily adopts the chain lock for its write phase

**Files:**
- Modify: `pipeline/quarantine.py` (`run()`, `--date` write phase only; `--review` writes nothing and takes no lock)
- Test: `pipeline/test_gen3b.py` (append - quarantine tests live there)

- [ ] **Step 1: Write the failing test** (append to `pipeline/test_gen3b.py`, following its existing quarantine-run fixture style)

```python
def test_quarantine_date_run_defers_when_chain_lock_held(tmp_path, capsys):
    """A held chain.lock defers the daily run with exit 0 and a clear line -
    tomorrow's backfill (--date) covers the gap; it must not interleave."""
    from .chainlock import ChainLock
    from . import quarantine as q

    # arrange a minimal valid registry + data dir exactly as the nearest
    # existing run() test in this file does (copy its arrange block)
    ...

    other = ChainLock(layer_logs_dir, holder="session", purpose="manual work")
    other.acquire()
    try:
        rc = q.run(["--registry", str(reg_path), "--data-dir", str(data_dir),
                    "--artifacts-dir", str(art_dir), "--date", date])
    finally:
        other.release()
    assert rc == 0
    assert "deferred_lock" in capsys.readouterr().out
```

(Same note as Task 5: copy the current arrange block from the nearest existing `run()` test in `test_gen3b.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/test_gen3b.py -q -k defers_when_chain_lock`
Expected: FAIL (run proceeds and returns 0 without printing deferred_lock, or writes rows)

- [ ] **Step 3: Implement**

In `quarantine.run()`, after argument validation and before the snapshot/decision write phase:

```python
from .chainlock import ChainLock, ChainLockHeld

    if args.date:
        lock = ChainLock(Path(args.registry).parent / "logs",
                         holder="quarantine", purpose=f"daily {args.date}")
        try:
            lock.acquire()
        except ChainLockHeld:
            print(f"deferred_lock: chain.lock held, skipping {args.date}; "
                  f"re-run with --date {args.date} to backfill")
            return 0
        try:
            ...existing snapshot + decision write phase unchanged...
        finally:
            lock.release()
```

- [ ] **Step 4: Run the quarantine suite**

Run: `python -m pytest pipeline/test_gen3b.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/quarantine.py research-layer/pipeline/test_gen3b.py
git commit -m "feat(loop): quarantine daily defers politely when chain.lock is held"
```

---

### Task 7: The loop orchestrator

**Files:**
- Create: `pipeline/loop.py`
- Test: `pipeline/test_loop.py`

Design constraints baked in:
- Stages run as subprocesses against the existing CLIs (their argv contracts and exit codes are the interface; a stage failure must not corrupt loop state).
- `runner` is injectable for tests.
- The loop holds `ChainLock(holder="loop")` around each STAGE that writes the chain (triage apply, composer real run, screen, gauntlet) - windows, not the whole cycle - and checks for a foreign holder once at cycle start (defer if held; stale handling per two-strike rule).
- Budget: `BudgetMeter(ledger, monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")` + `pipeline_budget.may_start_batch(spent)` checked BEFORE triage and BEFORE composer. Screen/gauntlet are never budget-blocked.
- Exit 0 for: ran clean, no trigger, deferred (lock or budget) - distinguished in `logs/pipeline_status.json` `items.outcome`. Exit 1 only for real defects.

- [ ] **Step 1: Write the failing tests**

```python
"""Offline tests for the pipeline loop orchestrator (no network, no API).

Run: python -m pytest pipeline/test_loop.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .chainlock import ChainLock
from .registry import Registry
from . import loop


def _mk_layer(tmp_path, accepted_fx=0):
    """Minimal layer: registry with N accepted fx-routable cards, logs dir."""
    layer = tmp_path
    (layer / "logs").mkdir()
    reg = Registry(layer / "registry_log.jsonl")
    for i in range(accepted_fx):
        cid = f"card{i:04d}"
        reg.register_card({"card_id": cid, "claim": f"c{i}", "quote": "q",
                           "topics": [], "tags": {"asset_classes": ["fx"]},
                           "review": {"status": "pending", "reject_reason": None},
                           "source": {}, "links": [], "credibility_tier": "practitioner"})
        reg.review_card(cid, "accepted", "coen")
    return layer, reg


class FakeRunner:
    """Records invocations; returns preset exit codes per module."""
    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        module = argv[argv.index("-m") + 1] if "-m" in argv else argv[0]
        class R: pass
        r = R(); r.returncode = self.codes.get(module, 0)
        return r


def test_no_trigger_exits_zero_and_says_so(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=3)   # 3 < 25
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_trigger"
    assert fr.calls == []                            # no stage ran


def test_foreign_lock_defers(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    other = ChainLock(layer / "logs", holder="session", purpose="manual")
    other.acquire()
    try:
        rc = loop.run(["--once", "--layer", str(layer)], runner=FakeRunner())
    finally:
        other.release()
    assert rc == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_lock"


def test_trigger_runs_stages_in_order_and_advances_watermark(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    modules = [c[c.index("-m") + 1] for c in fr.calls if "-m" in c]
    assert modules == ["pipeline.triage_batch", "pipeline.composer",
                       "pipeline.composer", "pipeline.screen", "pipeline.gauntlet"]
    # composer appears twice: --dry-run preflight then the real run
    dry = fr.calls[1]; real = fr.calls[2]
    assert "--dry-run" in dry and "--dry-run" not in real
    assert "--asset-class" in real and real[real.index("--asset-class") + 1] == "fx"
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"]["fx"]["watermark"] == 30
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"
    assert status["items"]["asset_class"] == "fx"


def test_stage_failure_exits_nonzero_and_does_not_advance_watermark(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    fr = FakeRunner(codes={"pipeline.screen": 1})
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    assert st["classes"] == {}                       # watermark NOT advanced
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["overall"] == "FAIL"
    assert status["items"]["failed_stage"] == "pipeline.screen"


def test_budget_cap_parks_before_spending(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    ledger = layer / "logs" / "budget_ledger.jsonl"
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ledger.write_text(json.dumps({"ts_utc": f"{month}-01T00:00:00+00:00",
                                  "usd": 20.0, "purpose": "triage",
                                  "model": "claude-sonnet-5"}) + "\n",
                      encoding="utf-8")
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []                            # nothing metered was started
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "deferred_budget"


def test_dry_run_reports_trigger_without_running(tmp_path):
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    fr = FakeRunner()
    rc = loop.run(["--once", "--dry-run", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert fr.calls == []
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "dry_run_would_fire"
    assert status["items"]["asset_class"] == "fx"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_loop.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.loop'`

- [ ] **Step 3: Write the implementation**

```python
"""Pipeline loop orchestrator: triage -> compose -> screen -> gauntlet when a
class accumulates enough new accepted cards.

Spec: docs/2026-08-27-pipeline-loop-design.md. Invoked by
\\StewartCo\\25_PipelineLoop (~3x daily) as `python -m pipeline.loop --once`.

Exit 0: cycle_complete | no_trigger | deferred_lock | deferred_budget |
        dry_run_would_fire   (distinguished in logs/pipeline_status.json)
Exit 1: a stage failed or the loop itself hit a defect.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import cells, loop_state, pipeline_budget, pipeline_status
from .budget import BudgetMeter, PIPELINE_CAP_USD
from .chainlock import ChainLock, ChainLockHeld
from .composer import routable_cards
from .registry import Registry

LAYER_DEFAULT = Path(__file__).resolve().parent.parent


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _routable_counts(registry: Registry) -> dict[str, int]:
    accepted = registry.cards(status="accepted")
    return {cls: len(routable_cards(accepted, cls)[0]) for cls in cells.LIVE_CLASSES}


def _write_status(logs_dir: Path, outcome: str, *, overall: str = "OK",
                  extra: dict | None = None, spent: float = 0.0,
                  escalations: list[str] | None = None) -> None:
    items = {"outcome": outcome}
    items.update(extra or {})
    payload = pipeline_status.build({"loop": overall}, spent, escalations)
    payload["items"] = {**payload.get("items", {}), **items}
    payload["overall"] = overall
    payload["summary"] = f"loop: {outcome}"
    pipeline_status.write(logs_dir / "pipeline_status.json", payload)


def _stage(runner, argv: list[str], cwd: Path) -> int:
    print(f"loop: running {' '.join(argv)}", flush=True)
    return runner(argv, cwd=str(cwd)).returncode


def run(argv: list[str] | None = None, runner=subprocess.run) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.loop")
    ap.add_argument("--once", action="store_true", required=True,
                    help="run one trigger check (the only supported mode)")
    ap.add_argument("--layer", type=Path, default=LAYER_DEFAULT,
                    help="research-layer root (tests point this at a tmp dir)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the trigger decision; run nothing")
    args = ap.parse_args(argv)

    layer: Path = args.layer
    logs = layer / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    reg_path = layer / "registry_log.jsonl"
    state_path = logs / "loop_state.json"
    state = loop_state.load(state_path)

    # --- foreign chain.lock: defer, with the two-strike stale rule ---
    probe = ChainLock(logs, holder="loop", purpose="probe")
    info = probe.info()
    if info is not None:
        if probe.is_stale():
            second = loop_state.record_stale_lock(state, info)
            if second:
                print(f"loop: breaking stale chain.lock (second sighting): {info}")
                probe.break_stale()
                loop_state.clear_stale_lock(state)
                loop_state.save(state_path, state)
                # fall through: lock gone, cycle may proceed
            else:
                loop_state.save(state_path, state)
                _write_status(logs, "deferred_lock", overall="WARN",
                              extra={"lock_holder": str(info.get("holder")),
                                     "lock_stale": "true"})
                return 0
        else:
            _write_status(logs, "deferred_lock",
                          extra={"lock_holder": str(info.get("holder")),
                                 "lock_stale": "false"})
            return 0
    else:
        loop_state.clear_stale_lock(state)

    # --- trigger ---
    registry = Registry(reg_path)
    counts = _routable_counts(registry)
    chosen = loop_state.pick_class(state, counts)
    if chosen is None:
        _write_status(logs, "no_trigger",
                      extra={f"routable_{c}": str(n) for c, n in counts.items()})
        loop_state.save(state_path, state)
        return 0

    # --- budget (metered stages only) ---
    meter = BudgetMeter(logs / "budget_ledger.jsonl",
                        monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")
    spent = meter.month_spend()
    if not pipeline_budget.may_start_batch(spent):
        _write_status(logs, "deferred_budget", overall="WARN", spent=spent,
                      extra={"asset_class": chosen})
        loop_state.save(state_path, state)
        return 0

    if args.dry_run:
        _write_status(logs, "dry_run_would_fire", spent=spent,
                      extra={"asset_class": chosen,
                             "routable": str(counts[chosen])})
        return 0

    # --- cycle ---
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%d") + f"-loop-{chosen}"
    py = sys.executable
    stages = [
        [py, "-m", "pipeline.triage_batch", "--apply"],
        [py, "-m", "pipeline.composer", "--run-id", run_id,
         "--asset-class", chosen, "--dry-run"],
        [py, "-m", "pipeline.composer", "--run-id", run_id,
         "--asset-class", chosen],
        [py, "-m", "pipeline.screen"],
        [py, "-m", "pipeline.gauntlet"],
    ]
    for stage_argv in stages:
        module = stage_argv[stage_argv.index("-m") + 1]
        writes_chain = not (module == "pipeline.composer" and "--dry-run" in stage_argv)
        if writes_chain:
            try:
                lock = ChainLock(logs, holder="loop", purpose=f"{run_id} {module}")
                lock.acquire()
            except ChainLockHeld as e:
                _write_status(logs, "deferred_lock", overall="WARN",
                              extra={"asset_class": chosen, "at_stage": module})
                return 0
            try:
                rc = _stage(runner, stage_argv, cwd=layer)
            finally:
                lock.release()
        else:
            rc = _stage(runner, stage_argv, cwd=layer)
        if rc != 0:
            _write_status(logs, "stage_failed", overall="FAIL", spent=spent,
                          extra={"asset_class": chosen, "failed_stage": module,
                                 "exit_code": str(rc)},
                          escalations=["run_aborted"])
            return 1

    # --- watermark + status ---
    counts_after = _routable_counts(Registry(reg_path))
    loop_state.record_generation(state, chosen, run_id=run_id,
                                 routable_count=counts_after[chosen],
                                 ts_utc=_now_utc())
    loop_state.save(state_path, state)
    _write_status(logs, "cycle_complete", spent=meter.month_spend(),
                  extra={"asset_class": chosen, "run_id": run_id})
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
```

Implementation note (not optional): `pipeline_status.build`'s exact signature is `build(stage_results: dict, spent: float, escalations: list[str] | None = None) -> dict` - if `_write_status` above does not match how `build` shapes `items`, adapt `_write_status`, never `build` (it has consumers and tests).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_loop.py -q`
Expected: 6 passed

- [ ] **Step 5: Run the touched-module regression net**

Run: `python -m pytest pipeline/test_loop.py pipeline/test_loop_state.py pipeline/test_chainlock.py pipeline/test_pipeline_status.py pipeline/test_pipeline_budget.py pipeline/test_composer.py pipeline/test_scanner.py pipeline/test_gen3b.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/loop.py research-layer/pipeline/test_loop.py
git commit -m "feat(loop): trigger-check orchestrator (triage -> compose -> screen -> gauntlet)"
```

---

### Task 8: Scoped chain commit after a completed cycle

After a clean cycle the loop commits exactly what its cycle appended: `registry_log.jsonl` plus the artifact bundles of the strategy_ids registered in this run. Follows the `run_quarantine.bat` precedent (scoped paths, `git diff --quiet` as the commit signal). Never `git add -A`, never push.

**Files:**
- Modify: `pipeline/loop.py`
- Test: `pipeline/test_loop.py` (append)

- [ ] **Step 1: Write the failing test** (append to `pipeline/test_loop.py`)

```python
def test_cycle_commit_is_scoped_to_registry_and_this_runs_artifacts(tmp_path):
    """collect_commit_paths returns the registry plus artifacts/<sid> dirs for
    strategies whose strategy_registered entries appeared after start_line,
    and only those that exist on disk."""
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    start_line = 0
    reg.register_strategy({"strategy_id": "aaaa000011112222", "family": "f",
                           "universe": {"asset_class": "fx"}, "blocks": {}})
    reg.register_strategy({"strategy_id": "bbbb000011112222", "family": "f",
                           "universe": {"asset_class": "fx"}, "blocks": {}})
    (layer / "artifacts" / "aaaa000011112222").mkdir(parents=True)
    paths = loop.collect_commit_paths(layer, start_line)
    rel = [p.replace("\\", "/") for p in paths]
    assert "research-layer/registry_log.jsonl" in rel
    assert "research-layer/artifacts/aaaa000011112222" in rel
    assert not any("bbbb" in p for p in rel)         # no bundle on disk -> not added
```

Note: `_mk_layer`'s `register_strategy` call requires a spec shape `register_strategy` accepts - if the registry validates more fields, extend the fixture spec minimally until it appends (the test's subject is path collection, not spec validation).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/test_loop.py -q -k scoped_to_registry`
Expected: FAIL with `AttributeError: module 'pipeline.loop' has no attribute 'collect_commit_paths'`

- [ ] **Step 3: Implement** (add to `pipeline/loop.py`)

```python
def _chain_line_count(reg_path: Path) -> int:
    try:
        with reg_path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except FileNotFoundError:
        return 0


def collect_commit_paths(layer: Path, start_line: int) -> list[str]:
    """Repo-relative paths for this cycle's chain delta: the registry plus
    artifacts/<sid> for every strategy_registered entry after start_line
    whose bundle exists on disk."""
    import json as _json
    rel_root = "research-layer"
    paths = [f"{rel_root}/registry_log.jsonl"]
    reg_path = layer / "registry_log.jsonl"
    with reg_path.open("r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    for ln in lines[start_line:]:
        try:
            entry = _json.loads(ln)
        except _json.JSONDecodeError:
            continue                # partial concurrent line; tail rules apply
        if entry.get("entry_type") != "strategy_registered":
            continue
        sid = entry.get("payload", {}).get("strategy_id")
        if sid and (layer / "artifacts" / sid).is_dir():
            paths.append(f"{rel_root}/artifacts/{sid}")
    return paths


def commit_cycle(layer: Path, start_line: int, run_id: str, runner=subprocess.run) -> None:
    """Scoped commit of this cycle's chain delta. Best-effort: a git failure
    is loud (printed) but never fails the cycle - the chain itself is the
    trust asset; the commit is bookkeeping. Never pushes."""
    repo = layer.parent
    paths = collect_commit_paths(layer, start_line)
    diff = runner(["git", "diff", "--quiet", "--"] + paths, cwd=str(repo))
    staged = runner(["git", "diff", "--cached", "--quiet", "--"] + paths, cwd=str(repo))
    untracked_add_needed = len(paths) > 1   # artifact dirs are new -> always add
    if diff.returncode == 0 and staged.returncode == 0 and not untracked_add_needed:
        return
    add = runner(["git", "add", "--"] + paths, cwd=str(repo))
    if add.returncode != 0:
        print("loop: WARNING git add failed; chain delta left uncommitted", flush=True)
        return
    cm = runner(["git", "commit", "-q", "-m", f"loop: {run_id} chain delta"],
                cwd=str(repo))
    if cm.returncode != 0:
        print("loop: WARNING git commit failed (possibly nothing staged)", flush=True)
```

Wire into `run()`: capture `start_line = _chain_line_count(reg_path)` immediately BEFORE the stages loop; after the watermark save and final status write, call `commit_cycle(layer, start_line, run_id, runner=runner)` - only on the `cycle_complete` path. In tests the FakeRunner absorbs the git calls; add to `test_trigger_runs_stages_in_order_and_advances_watermark` an assertion that no `git` call carries `-A`:

```python
    git_calls = [c for c in fr.calls if c and c[0] == "git"]
    assert all("-A" not in c and "." not in c[1:2] for c in git_calls)
```

(FakeRunner's `__call__` indexes `argv[argv.index("-m") + 1]` - guard it for non-python argv: `module = argv[0] if argv[0] != sys.executable else ...`; adjust the helper accordingly.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest pipeline/test_loop.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/pipeline/loop.py research-layer/pipeline/test_loop.py
git commit -m "feat(loop): scoped chain-delta commit after a clean cycle"
```

---

### Task 9: Task wiring - bat, XML, scheduler entry, retry list, docs (registration itself is COEN-GATED)

Everything here is BUILT and committed; nothing is REGISTERED until Task 0 is complete and a supervised `--once` has run clean in session (spec "Step 0" item 4).

**Files:**
- Create: `tasks/run_pipeline_loop.bat` (research-layer)
- Create: `E:\Users\Coen\Claude\quant\tasks\xml\25_PipelineLoop.xml`
- Modify: `E:\Users\Coen\Claude\quant\tasks\setup_scheduler.bat` (research-layer block)
- Modify: `E:\Users\Coen\Claude\quant\tasks\apply_retry_settings.ps1` (`$RETRY_TASKS` list)
- Modify: `E:\Users\Coen\Claude\stewart-forward-test\research-layer\CLAUDE.md` (or the repo-root CLAUDE.md if that is where research-layer rules live - check first) - chain-lock protocol for manual sessions + loop ops

- [ ] **Step 1: Write the bat** (`research-layer/tasks/run_pipeline_loop.bat`, modeled on `run_quarantine.bat`)

```bat
@echo off
rem 25_PipelineLoop - trigger-check for the pipeline loop (spec 2026-08-27).
rem Exit code is load-bearing: Ops Sentinel FAILs the digest on nonzero.
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set LOG=%LAYER%\logs\pipeline-loop-run.log

echo ==== %DATE% %TIME% pipeline loop fire ==== >> "%LOG%"
cd /d "%LAYER%"
python -m pipeline.loop --once >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

echo ==== %DATE% %TIME% ok ==== >> "%LOG%"
exit /b 0

:fail
echo ==== %DATE% %TIME% FAILED exit %ERRORLEVEL% ==== >> "%LOG%"
exit /b 2
```

- [ ] **Step 2: Write the XML** (`quant\tasks\xml\25_PipelineLoop.xml`; three CalendarTriggers because `schtasks /Create` attaches only one - the 21_ReaderScanner precedent). Author it by EXPORTING, not hand-typing: register a throwaway task with the first trigger, `Export-ScheduledTask`, add the second and third `<CalendarTrigger>` blocks (10:30, 15:30, 21:30 +08:00), set `<ExecutionTimeLimit>PT2H</ExecutionTimeLimit>`, `<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>`, `<StartWhenAvailable>true</StartWhenAvailable>`, action:

```xml
<Exec>
  <Command>E:\Users\Coen\Claude\stewart-forward-test\research-layer\tasks\run_pipeline_loop.bat</Command>
</Exec>
```

Save UTF-16 like the existing XMLs. Delete the throwaway task afterwards (`schtasks /Delete /TN "StewartCo\25_PipelineLoop" /F`) - real registration is Step 6, Coen-gated.

- [ ] **Step 3: Add the setup_scheduler.bat entry** (research-layer block, after the 23 entry; same shape as the 21 XML line)

```bat
schtasks /Create /TN "StewartCo\25_PipelineLoop"      /XML "%XMLDIR%\25_PipelineLoop.xml" /F
echo [25]  Pipeline Loop          10:30/15:30/21:30 local  (from XML - three triggers; research-layer repo)
```

- [ ] **Step 4: Add `"25_PipelineLoop"` to `$RETRY_TASKS`** in `apply_retry_settings.ps1` (append to the list, after `"24_TradfiFreeRefresh"`). Do NOT run the script here - it needs an elevated shell and runs at activation (Step 6).

- [ ] **Step 5: Document the protocol.** In the research-layer CLAUDE.md (check whether research-layer rules live in `research-layer/CLAUDE.md` or the repo root; edit the file that exists), add:

```markdown
## Chain lock (logs/chain.lock) - manual sessions MUST honour it
Before any batch of chain writes (generation, backfill, hand-run gauntlet):
take the lock via `python -c "from pipeline.chainlock import ChainLock; ..."`
or simply check it exists and wait. The loop (25_PipelineLoop), the scanner's
card batches, and quarantine daily all honour it. Never delete a fresh
chain.lock; a stale one (>3h) is broken by the loop on its second sighting.

## Pipeline loop (25_PipelineLoop)
- `python -m pipeline.loop --once` from the layer root; `--dry-run` reports
  the trigger decision without running anything.
- State: `logs/loop_state.json` (per-class watermarks + thresholds, Coen-editable).
- Status: `logs/pipeline_status.json`; run log `logs/pipeline-loop-run.log`.
- Fires 10:30 / 15:30 / 21:30 local; exit 0 covers no_trigger and polite
  deferrals; nonzero = real defect (Sentinel FAILs the digest).
```

- [ ] **Step 6 (COEN-GATED - do not execute without Task 0 complete): activation sequence**, in session with Coen:

1. Supervised run: `python -m pipeline.loop --once --dry-run` then, if it would fire and Coen agrees, `python -m pipeline.loop --once` watched end to end; verify chain `python verify_registry.py registry_log.jsonl` = VALID after.
2. Register: `schtasks /Create /TN "StewartCo\25_PipelineLoop" /XML "E:\Users\Coen\Claude\quant\tasks\xml\25_PipelineLoop.xml" /F`
3. Elevated: `powershell -ExecutionPolicy Bypass -File E:\Users\Coen\Claude\quant\tasks\apply_retry_settings.ps1 -Task 25_PipelineLoop` (from an admin shell; un-elevated reaches only user-created tasks).
4. After 3 clean scheduled days: pin in `E:\Users\Coen\Claude\sc-ops-sentinel\manifest.json` (daily class - deliberately, per the standing rule) AND `E:\Users\Coen\Claude\morpheus-hub\backend\app\connectors\fleet\tasks.py` in the SAME pass, then restart the hub and verify `/api/threepio/fleet` shows 25 pinned with `extra: []`.

- [ ] **Step 7: Commit the buildable pieces**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test
git add research-layer/tasks/run_pipeline_loop.bat research-layer/CLAUDE.md
git commit -m "feat(loop): task bat + chain-lock protocol docs (registration Coen-gated)"
```

(quant\tasks\ is a different repo/folder - commit its two edits per that folder's convention; check `git -C E:\Users\Coen\Claude\quant status` first and use scoped adds.)

---

### Task 10: Full-suite gate and wrap-up

- [ ] **Step 1: Full pipeline suite**

Run (from the layer root): `python -m pytest pipeline/ -q`
Expected: everything passes except the one known live-data-coupled scanner test if the environment lacks live data (pre-existing, chip open) - any OTHER failure is a defect introduced by this plan; fix before proceeding.

- [ ] **Step 2: Chain verify against the live registry**

Run: `python verify_registry.py registry_log.jsonl`
Expected: VALID, entry count >= the count before this work started.

- [ ] **Step 3: Update the vault** - `project_morpheus_3_0.md` (Track 1 built, activation state) + MEMORY.md pointer line; note anything Coen-gated still open.

- [ ] **Step 4: Final scoped commit of any stragglers** (explicit paths only), then STOP - pushing is a separate decision (`git log @{u}..HEAD` first; unpushed commits in this repo are routinely deliberate).

---

## Self-review notes (spec coverage)

- Spec "Chain lock" -> Tasks 1, 5, 6, 7 (defer paths), 9 (docs). Two-strike stale rule: Task 3 bookkeeping + Task 7 probe logic.
- Spec "Watermark state" -> Tasks 2 (routable set), 3 (state), 7 (trigger).
- Spec "Cycle body" -> Task 7 (stage order incl. composer dry-run preflight; one class per fire via pick_class).
- Spec "Budget behaviour" -> Task 7 (may_start_batch before metered stages; deferred_budget parks with exit 0).
- Spec "Ops wiring" -> Task 9 (bat, XML 10:30/15:30/21:30, setup_scheduler, $RETRY_TASKS, sentinel+fleet pins after 3 clean days).
- Spec "Exit codes and honesty" -> Task 7 (outcome taxonomy in status; nonzero only for defects), Task 4 (atomic writer).
- Spec "Step 0" -> Task 0 + Task 9 Step 6 (both Coen-gated).
- Spec "commit ... chain + artifacts" -> Task 8.
- Deliberately NOT implemented (spec out-of-scope): discovery widening, live transitions, engine/gate changes, threshold lowering.
