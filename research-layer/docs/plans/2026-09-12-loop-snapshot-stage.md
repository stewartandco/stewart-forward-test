# Loop Snapshot Stage + Freshness Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The pipeline loop refreshes the tradfi cells itself as stage 0 on every fire, and refuses a stale tree BEFORE any metered stage, so the 2026-09-11 failure (fx 20 days behind crypto, USD 1.90 spent, gauntlet refused) cannot recur.

**Architecture:** Two shared helpers move into `pipeline/screen.py` (`comparable_cells`, `cell_end_dates`) so the loop's cheap preflight and the gauntlet's full check derive the same cells from the same rule. `pipeline/loop.py` gains stage 0 (a `pipeline.tradfi_data snapshot` subprocess through the existing `_stage` helper, skipped when no producer repo exists) and preflight 4.0c (last-bar dates + `assert_cells_comparable`, zero spend). Two new FAIL outcomes: `snapshot_failed`, `stale_data`.

**Tech Stack:** Python 3.14, pytest, the repo's own `FakeRunner` test double (records argv, returns preset exit codes). No new dependencies.

**Spec:** `research-layer/docs/2026-09-12-loop-snapshot-stage-design.md` (commit b013b47). Read §2–§4 before starting; the spec wins over this plan where they differ.

**Working tree:** stewart-forward-test, LIVE tree, branch `claude/ai-agent-business-automation-0lzfd9` (the repo's working branch; there is no `master`). Every command below runs from `E:\Users\Coen\Claude\stewart-forward-test\research-layer`. Commit with scoped `git add <paths>` only, never `-A`. The loop is budget-parked until 1 October, so nothing here can collide with a live cycle; still stay clear of 08:20 (quarantine daily), 08:50 (free-lane refresh) and 09:00–09:15 (Reader, Sentinel).

**Before Task 1:** run the full suite once and record the baseline:

```
python -m pytest pipeline tools -q 2>&1 | tail -3
```

Expected: ~1441 passed and a small known set of data-pinned failures (four at last count). Write their names down; the suite must end with exactly that set after every task.

---

## File map

| File | Responsibility after this plan |
|---|---|
| `pipeline/screen.py` | `load_cell_data`, `assert_cells_comparable` (unchanged) + NEW `comparable_cells(all_specs)`, NEW `cell_end_dates(data_dir, cells)` |
| `pipeline/gauntlet.py` | calls `comparable_cells` instead of its inline copy (lines ~1091–1112); everything else unchanged |
| `pipeline/loop.py` | NEW `SNAPSHOT_CLASSES`, `_producer_root()`, `_snapshot_stage(...)`, `_freshness_preflight(...)`; `_run_cycle` calls stage 0 after the lock probes and 4.0c after the orphan check; `_write_status` merges `_cycle_items`; docstring lists the two new outcomes |
| `pipeline/test_screen.py` | tests for the two helpers |
| `pipeline/test_loop.py` | autouse fixture pins `TRADING_SYSTEMS_ROOT` to a non-existent dir; tests for stage 0 and the preflight |
| `CLAUDE.md` | new paragraph under `## Pipeline loop (25_PipelineLoop)` |
| `docs/2026-08-24-market-expansion-sp4-design.md` | dated amendment under §3's refresh-policy paragraph |

---

### Task 1: `screen.cell_end_dates` — last bar date per cell, reading only the tail

**Files:**
- Modify: `pipeline/screen.py` (add after `load_cell_data`, which ends at line ~91)
- Test: `pipeline/test_screen.py` (append at the end)

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_screen.py`:

```python
# ─── cell_end_dates (loop preflight helper, 2026-09-12 spec §3) ──────────────


def test_cell_end_dates_matches_load_cell_data_s_data_end(tmp_path):
    """One rule for 'when does this cell's data stop'. The loop's pre-spend
    preflight reads only each CSV's tail; the gauntlet loads every bar. They
    must agree to the byte, or the preflight could pass a tree the gauntlet
    then refuses (the 2026-09-11 failure, one stage later)."""
    from .screen import cell_end_dates, load_cell_data
    _write_cell_csv(tmp_path, "AUD", "1d", ["2026-08-19 00:00:00",
                                            "2026-08-20 00:00:00",
                                            "2026-08-21 00:00:00"])
    _write_cell_csv(tmp_path, "BTCUSD", "1d", ["2026-09-09 00:00:00",
                                               "2026-09-10 00:00:00"])
    cells = [("AUD", "1d"), ("BTCUSD", "1d")]

    ends = cell_end_dates(tmp_path, cells)

    assert ends == load_cell_data(tmp_path, cells, "9999-12-31")[2]
    assert ends == {"AUD_1d": "2026-08-21 00:00:00",
                    "BTCUSD_1d": "2026-09-10 00:00:00"}


def test_cell_end_dates_reads_only_the_tail(tmp_path):
    """A 10 MB file with an unparseable FIRST data line still answers: the
    helper must never read the whole file (the live tree holds 140 cells,
    some with 11,000+ rows, and the preflight runs on every fire)."""
    from .screen import cell_end_dates
    p = tmp_path / "SPY_1d.csv"
    junk = "this,is,not,a,bar,row\n"
    good = "".join(f"2020-01-{d:02d} 00:00:00,1,1,1,1,1\n" for d in range(1, 29))
    filler = good * 400          # ~1 MB of valid rows so the tail is far from the head
    p.write_text("date,open,high,low,close,volume\n" + junk + filler
                 + "2026-09-10 00:00:00,1,1,1,1,1\n", encoding="utf-8")

    assert cell_end_dates(tmp_path, [("SPY", "1d")]) == {"SPY_1d": "2026-09-10 00:00:00"}


def test_cell_end_dates_tolerates_a_trailing_blank_line(tmp_path):
    from .screen import cell_end_dates
    p = tmp_path / "GLD_1d.csv"
    p.write_text("date,open,high,low,close,volume\n"
                 "2026-09-09 00:00:00,1,1,1,1,1\n"
                 "2026-09-10 00:00:00,1,1,1,1,1\n\n", encoding="utf-8")
    assert cell_end_dates(tmp_path, [("GLD", "1d")]) == {"GLD_1d": "2026-09-10 00:00:00"}


def test_cell_end_dates_names_a_missing_cell(tmp_path):
    from .screen import cell_end_dates
    _write_cell_csv(tmp_path, "AUD", "1d", ["2026-08-21 00:00:00"])
    with pytest.raises(FileNotFoundError, match="EUR_1d"):
        cell_end_dates(tmp_path, [("AUD", "1d"), ("EUR", "1d")])


def test_cell_end_dates_header_only_file_is_an_empty_end(tmp_path):
    """assert_cells_comparable already refuses an empty end string; the helper
    reports it as '' rather than raising, so the message names the cell."""
    from .screen import cell_end_dates
    (tmp_path / "AUD_1d.csv").write_text("date,open,high,low,close,volume\n", encoding="utf-8")
    assert cell_end_dates(tmp_path, [("AUD", "1d")]) == {"AUD_1d": ""}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest pipeline/test_screen.py -q -k cell_end_dates`
Expected: 5 failed, each `ImportError: cannot import name 'cell_end_dates'`.

- [ ] **Step 3: Implement `cell_end_dates`**

In `pipeline/screen.py`, directly after the `load_cell_data` function (before `def assert_cells_comparable`), add:

```python
def cell_end_dates(data_dir: Path, cells) -> dict[str, str]:
    """{cell_id: last bar's date string} for each (asset, timeframe), reading
    only the TAIL of each `<asset>_<tf>.csv`.

    The loop's pre-spend freshness preflight (2026-09-12 spec §3) needs the
    same answer `load_cell_data(...)[2]` gives with cutoff 9999-12-31, on
    every fire, without loading 140 cells of full history. The date is the
    first comma-separated field of the last non-blank line, exactly the
    string the CSV carries (`YYYY-MM-DD HH:MM:SS`); `assert_cells_comparable`
    compares `[:10]`. A header-only file yields "" (the guard refuses that
    with a message naming the cell). A missing file raises FileNotFoundError
    naming the cell: a registered cell with no data is a defect the caller
    reports, not something to skip.
    """
    data_dir = Path(data_dir)
    ends: dict[str, str] = {}
    for asset, tf in cells:
        cid = cell_id(asset, tf)
        path = data_dir / f"{asset}_{tf}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{cid}: no price file at {path}")
        ends[cid] = _last_csv_date(path)
    return ends


def _last_csv_date(path: Path, chunk: int = 4096) -> str:
    """First field of the last non-blank line of `path`, or "" when the only
    line is the header. Reads backwards in `chunk`-byte steps; never the
    whole file."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        buf = b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
            lines = [ln for ln in buf.split(b"\n") if ln.strip()]
            # Need at least two non-blank lines to know the last one is complete
            # and is not the header; or we have reached the start of the file.
            if len(lines) >= 2 or pos == 0:
                break
    lines = [ln for ln in buf.split(b"\n") if ln.strip()]
    if not lines:
        return ""
    last = lines[-1].decode("utf-8").strip()
    if last.lower().startswith("date,"):
        return ""                       # header-only file
    return last.split(",", 1)[0].strip()
```

`os` and `Path` are already imported at the top of `screen.py`; `cell_id` is the module's own function (defined further down in the same file, so the call resolves at run time).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest pipeline/test_screen.py -q`
Expected: all pass (the 5 new ones plus every existing screen test).

- [ ] **Step 5: Commit**

```bash
git add research-layer/pipeline/screen.py research-layer/pipeline/test_screen.py
git commit -m "feat(screen): cell_end_dates - last bar per cell from the CSV tail, same answer as load_cell_data"
```

---

### Task 2: `screen.comparable_cells` — one derivation of cells + classes, gauntlet uses it

**Files:**
- Modify: `pipeline/screen.py` (add after `cell_end_dates`)
- Modify: `pipeline/gauntlet.py:1091-1112` (replace the inline block)
- Test: `pipeline/test_screen.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `pipeline/test_screen.py`:

```python
# ─── comparable_cells (moved out of gauntlet.run, 2026-09-12 spec §3) ────────


def test_comparable_cells_derives_cells_and_classes_from_each_spec_s_own_universe():
    """The gauntlet derived (cells_needed, class_of) inline; the loop's
    preflight needs the identical derivation, so it lives in ONE function.
    Class comes from the spec's own universe.asset_class (default 'crypto'),
    never from cells.class_of_asset: production crypto specs register legacy
    BTCUSD/ETHUSD, which are not members of the USDT grid."""
    from .screen import comparable_cells
    specs = [
        {"universe": {"assets": ["AUD", "EUR"], "asset_class": "fx", "timeframe": "1d"}},
        {"universe": {"assets": ["BTCUSD", "ETHUSD"]}},                 # no class, no tf
        {"universe": {"assets": ["AUD"], "asset_class": "fx", "timeframe": "1d"}},  # duplicate cell
        {"universe": {"assets": ["SPY"], "asset_class": "equity_etf", "timeframe": "4h"}},
    ]

    cells_needed, class_of = comparable_cells(specs)

    assert cells_needed == [("AUD", "1d"), ("BTCUSD", "1d"), ("ETHUSD", "1d"),
                            ("EUR", "1d"), ("SPY", "4h")]
    assert class_of == {"AUD_1d": "fx", "EUR_1d": "fx", "BTCUSD_1d": "crypto",
                        "ETHUSD_1d": "crypto", "SPY_4h": "equity_etf"}


def test_comparable_cells_of_nothing_is_nothing():
    from .screen import comparable_cells
    assert comparable_cells([]) == ([], {})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest pipeline/test_screen.py -q -k comparable_cells`
Expected: 2 failed, `ImportError: cannot import name 'comparable_cells'`.

- [ ] **Step 3: Implement `comparable_cells` in `screen.py`**

Directly after `_last_csv_date`, add:

```python
def comparable_cells(all_specs) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """(cells_needed, class_of) for the set of registered specs the gauntlet
    compares registry-wide. Moved here from gauntlet.run() on 2026-09-12 so
    the loop's pre-spend preflight and the gauntlet's own check derive the
    same cells by the same rule and cannot drift.

    A CELL is (asset, timeframe), same as the screen. Timeframe defaults to
    '1d'. The class is read from each spec's OWN declared universe
    (`asset_class`, default 'crypto'), never from cells.class_of_asset:
    production crypto specs register legacy BTCUSD/ETHUSD tickers that are
    not members of cells.CLASSES's USDT grid (spec s10.9), so deriving the
    class from the ticker would raise on every one of them. asset_class
    already travels on every registered spec's universe (the composer stamps
    it; legacy fixtures declare it explicitly too).
    """
    cells_needed = sorted({(a, s["universe"].get("timeframe", "1d"))
                           for s in all_specs for a in s["universe"]["assets"]})
    class_of: dict[str, str] = {}
    for s in all_specs:
        tf = s["universe"].get("timeframe", "1d")
        cls = s["universe"].get("asset_class", "crypto")
        for a in s["universe"]["assets"]:
            class_of[cell_id(a, tf)] = cls
    return cells_needed, class_of
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest pipeline/test_screen.py -q -k comparable_cells`
Expected: 2 passed.

- [ ] **Step 5: Make the gauntlet call it (moved code, same output)**

In `pipeline/gauntlet.py`, line 1001 currently reads:

```python
    from .screen import assert_cells_comparable, bundle_hash, load_cell_data
```

Change it to:

```python
    from .screen import (assert_cells_comparable, bundle_hash, comparable_cells,
                         load_cell_data)
```

Then replace the block that starts at the comment `# A CELL is (asset, timeframe), same as the screen.` (line ~1091) and ends with the line `class_of[cells.cell_id(a, tf)] = cls` (line ~1112) — i.e. the `cells_needed = sorted(...)` statement, the `load_cell_data(...)` call, the long `# cell_id -> asset class ...` comment and the `class_of` loop — with:

```python
    # A CELL is (asset, timeframe), same as the screen; the derivation and
    # the class-per-cell rule live in screen.comparable_cells (2026-09-12) so
    # the loop's pre-spend freshness preflight cannot drift from this check.
    cells_needed, class_of = comparable_cells(all_specs)
    bars_by_cell, data_hashes, data_end = load_cell_data(
        args.data_dir, cells_needed, "9999-12-31")          # full history
```

Keep the `# This stage COMPARES: ...` comment and the `assert_cells_comparable(data_end, class_of=class_of)` line that follow exactly as they are.

- [ ] **Step 6: Run the gauntlet and screen suites**

Run: `python -m pytest pipeline/test_gauntlet.py pipeline/test_gauntlet_classes.py pipeline/test_gauntlet_pool.py pipeline/test_screen.py -q 2>&1 | tail -3`
Expected: all pass except any of the recorded baseline failures. `test_mixed_registry_e2e_unequal_history_intersects_before_check_aligned` and `test_cells_comparable_per_class` in `test_gauntlet_classes.py` are the ones that exercise the moved code end to end.

- [ ] **Step 7: Commit**

```bash
git add research-layer/pipeline/screen.py research-layer/pipeline/gauntlet.py research-layer/pipeline/test_screen.py
git commit -m "refactor(screen): comparable_cells - one derivation of cells + classes; gauntlet calls it"
```

---

### Task 3: Loop stage 0 — the snapshot subprocess, every fire

**Files:**
- Modify: `pipeline/loop.py` (constants near the top; new helpers near `_stage`; `_run_cycle` after section 1; `_write_status`; docstring)
- Test: `pipeline/test_loop.py` (autouse fixture + new tests)

- [ ] **Step 1: Pin the producer root for EVERY existing loop test**

On this development box `E:\Users\Coen\Claude\trading-systems` exists, so without this every existing test would see a `pipeline.tradfi_data` call at the head of `fr.calls` and the seven `_modules(fr) == [...]` assertions would break. Stage 0's skip rule (spec §2) is "no producer root", so point the env var at a directory that does not exist. In `pipeline/test_loop.py`, replace the autouse fixture at lines 29–45 with:

```python
@pytest.fixture(autouse=True)
def _no_real_schtasks(monkeypatch, tmp_path):
    """Stub the live-task window reader for EVERY test in this module.

    loop.run() reads the scheduled task's ExecutionTimeLimit at startup, which
    without this spawns a real `schtasks` subprocess on every one of the ~50
    loop.run() calls in this file. That is slow, and worse it makes the suite
    MACHINE-DEPENDENT: on this box the task exists at PT1H so the WARN prints
    into capsys, while on a box where it is absent (CI, a fresh clone) or
    already fixed at PT4H nothing prints. Tests must not care.

    None is the "cannot determine" answer, i.e. the silent no-op path. The
    warning itself is covered directly, with explicit stubs, by the
    test_*_task_window_* tests below.

    Likewise the stage-0 snapshot (2026-09-12): it is SKIPPED when the
    trading-systems producer root does not exist, and on this box it does.
    Point every test at a non-existent root so the stage argv the existing
    tests pin stays byte-identical; the stage-0 tests set an existing dir."""
    monkeypatch.setattr(loop, "_live_task_window_s", lambda *a, **k: None)
    monkeypatch.setenv("TRADING_SYSTEMS_ROOT", str(tmp_path / "no-producer-here"))
```

Run: `python -m pytest pipeline/test_loop.py -q 2>&1 | tail -2`
Expected: same result as the baseline (nothing in loop.py has changed yet).

- [ ] **Step 2: Write the failing stage-0 tests**

Append to `pipeline/test_loop.py`:

```python
# ─── stage 0: the loop takes the tradfi snapshot itself (2026-09-12 spec §2) ─


def _with_producer(monkeypatch, tmp_path):
    """Make stage 0 run: an existing (empty) producer root is all the loop
    checks; FakeRunner never executes the adapter."""
    root = tmp_path / "trading-systems"
    root.mkdir()
    monkeypatch.setenv("TRADING_SYSTEMS_ROOT", str(root))
    return root


def _snapshot_calls(fr):
    return [c for c in fr.calls if c[0] == sys.executable and "-m" in c
            and c[c.index("-m") + 1] == "pipeline.tradfi_data"]


def test_stage0_snapshot_runs_first_with_the_declared_classes(tmp_path, monkeypatch):
    """2026-09-11 22:30: fx cells were 20 days behind crypto because the
    tradfi snapshot was a hand step nobody ran. The loop now takes it as its
    own stage 0, before anything reads the chain."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _with_producer(monkeypatch, tmp_path)
    fr = FakeRunner()

    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)

    assert rc == 0
    snaps = _snapshot_calls(fr)
    assert len(snaps) == 1
    assert fr.calls[0] == snaps[0]                      # the very first call
    argv = snaps[0]
    assert argv[:4] == [sys.executable, "-m", "pipeline.tradfi_data", "snapshot"]
    assert argv[argv.index("--classes") + 1] == "fx,equity_etf,bond_etf,metal_etf"
    assert argv[argv.index("--classes") + 1] == ",".join(loop.SNAPSHOT_CLASSES)
    assert argv[argv.index("--out") + 1] == str(layer)
    assert fr.call_kwargs[0]["cwd"] == str(layer)
    assert _modules(fr) == ["pipeline.tradfi_data", "pipeline.triage_batch",
                            "pipeline.composer", "pipeline.composer",
                            "pipeline.screen", "pipeline.gauntlet"]


def test_stage0_precedes_the_first_registry_read(tmp_path, monkeypatch):
    layer, _ = _mk_layer(tmp_path, accepted_fx=0)
    _with_producer(monkeypatch, tmp_path)
    fr = FakeRunner()
    seen_at = []

    class RecordingRegistry(Registry):
        def __init__(self, *a, **k):
            seen_at.append(len(fr.calls))
            super().__init__(*a, **k)

    monkeypatch.setattr(loop, "Registry", RecordingRegistry)
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)

    assert rc == 0
    assert seen_at and min(seen_at) >= 1, "registry constructed before stage 0 ran"
    assert _snapshot_calls(fr) == [fr.calls[0]]


def test_stage0_runs_on_a_no_trigger_fire_and_on_a_budget_park(tmp_path, monkeypatch):
    """Deliberate (spec §2): the 08:20 quarantine daily reads the same data/,
    so a fire that does nothing else still leaves fresh cells behind."""
    # no_trigger
    (tmp_path / "a").mkdir()                      # _mk_layer's mkdir is not recursive
    layer, _ = _mk_layer(tmp_path / "a", accepted_fx=0)
    _with_producer(monkeypatch, tmp_path)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_trigger"
    assert _modules(fr) == ["pipeline.tradfi_data"]

    # deferred_budget: spend already past the batch-stop line
    (tmp_path / "b").mkdir()
    layer2, _ = _mk_layer(tmp_path / "b", accepted_fx=30)
    _seed_crypto_caught_up(layer2, 30)
    ledger = layer2 / "logs" / "budget_ledger.jsonl"
    ledger.write_text(json.dumps({"ts_utc": loop._now_utc(), "agent": "pipeline",
                                  "usd": PIPELINE_CAP_USD * 0.9, "model": "m",
                                  "purpose": "test"}) + "\n", encoding="utf-8")
    fr2 = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer2)], runner=fr2) == 0
    status2 = json.loads((layer2 / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status2["items"]["outcome"] == "deferred_budget"
    assert _modules(fr2) == ["pipeline.tradfi_data"]


def test_stage0_runs_under_dry_run(tmp_path, monkeypatch):
    """A dry run that reported 'would fire' on stale data would be lying
    about what the real fire will do. The snapshot writes data/, never the
    chain, so the dry run takes it too."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _with_producer(monkeypatch, tmp_path)
    fr = FakeRunner()
    rc = loop.run(["--once", "--dry-run", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert _modules(fr) == ["pipeline.tradfi_data"]     # and nothing metered


def test_stage0_failure_ends_the_fire_before_any_chain_read(tmp_path, monkeypatch, capsys):
    """A SnapshotRefused (unpinned / hash-mismatched series) or a crash in the
    adapter is a defect: exit 1 (Sentinel FAIL, task retry), zero spend, no
    later stage, and no counts in the status (no chain read happened)."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _with_producer(monkeypatch, tmp_path)
    fr = FakeRunner(codes={"pipeline.tradfi_data": 1})

    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)

    assert rc == 1
    assert _modules(fr) == ["pipeline.tradfi_data"]
    out = capsys.readouterr().out
    assert "snapshot_failed" in out
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["overall"] == "FAIL"
    assert status["items"]["outcome"] == "snapshot_failed"
    assert status["items"]["snapshot_rc"] == "1"
    assert "triggerable_fx" not in status["items"]        # before the chain read
    assert "snapshot_failed" in status.get("escalations", [])


def test_stage0_is_skipped_without_a_producer_root(tmp_path, capsys):
    """tmp registries, tests, a fresh clone: no trading-systems repo to read
    from. The autouse fixture already points TRADING_SYSTEMS_ROOT at a
    non-existent dir, so this is the default path every other test runs."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert _snapshot_calls(fr) == []
    assert "snapshot_skipped: no producer at" in capsys.readouterr().out
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["snapshot_skipped"] == "1"
    assert "snapshot_utc" not in status["items"]


def test_status_carries_the_snapshot_utc_of_the_manifest_on_disk(tmp_path, monkeypatch):
    """The digest can say which snapshot a cycle was bred on."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=0)
    _with_producer(monkeypatch, tmp_path)
    (layer / "data").mkdir()
    (layer / "data" / "tradfi_snapshot_manifest.json").write_text(json.dumps(
        {"snapshot_utc": "2026-09-12T14:30:05+00:00", "series": {}}), encoding="utf-8")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "no_trigger"
    assert status["items"]["snapshot_utc"] == "2026-09-12T14:30:05+00:00"
    assert "snapshot_skipped" not in status["items"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest pipeline/test_loop.py -q -k "stage0 or snapshot_utc"`
Expected: 7 failed (`AttributeError: module 'pipeline.loop' has no attribute 'SNAPSHOT_CLASSES'`, missing `snapshot_skipped` items, no `pipeline.tradfi_data` call, and so on).

- [ ] **Step 4: Implement stage 0 in `pipeline/loop.py`**

(a) Module docstring, lines 16–20. Change the exit-1 line to:

```
Exit 1: stage_failed | chain_invalid | gauntlet_orphan | snapshot_failed | stale_data |
        loop_crashed -- a real defect.
```

(b) After the `SAFETY_MARGIN_S` constant block (around line 133–140; any spot among the module constants is fine, but keep it before `_write_status`), add:

```python
# Stage 0 (2026-09-12, docs/2026-09-12-loop-snapshot-stage-design.md): the
# loop takes the tradfi snapshot itself on every fire, before anything reads
# the chain. Every class in cells.CLASSES except crypto -- crypto's BTCUSD/
# ETHUSD are fetched daily by the quarantine task (pipeline.data_fetch) and
# the 100-coin USDT grid is unscheduled and uncompared today. Skipped when no
# producer repo exists (tests, a fresh clone); never skipped in production.
SNAPSHOT_CLASSES = ("fx", "equity_etf", "bond_etf", "metal_etf")

# Items that belong to THIS run and must appear on every status path written
# after they are known (snapshot_skipped, snapshot_utc). Reset at run() start.
_cycle_items: dict[str, str] = {}
```

(c) In `_write_status` (line ~606), change the two lines

```python
    if counts is not None:
        items.update(_count_items(*counts))
    items.update(extra or {})
```

to

```python
    if counts is not None:
        items.update(_count_items(*counts))
    items.update(_cycle_items)
    items.update(extra or {})
```

(d) After `_stage` (line ~636), add the two helpers:

```python
def _producer_root() -> Path:
    """The trading-systems repo the tradfi adapter reads from: the env var
    the adapter itself honours, else its default. Imported lazily: the
    adapter pulls in pandas, which the loop otherwise never needs."""
    from .tradfi_data import DEFAULT_TS_ROOT
    return Path(os.environ.get("TRADING_SYSTEMS_ROOT") or DEFAULT_TS_ROOT)


def _snapshot_stage(runner: Runner, layer: Path) -> int | None:
    """Stage 0: refresh the tradfi cells from the producer. Returns the
    adapter's exit code, or None when skipped (no producer root). The
    adapter's own stdout/stderr reach the run log because stages inherit
    stdio; a refusal prints its reason there. Writes data/ only, never the
    chain, so no chain.lock is taken."""
    root = _producer_root()
    if not root.exists():
        print(f"snapshot_skipped: no producer at {root}", flush=True)
        _cycle_items["snapshot_skipped"] = "1"
        return None
    rc = _stage(runner, [sys.executable, "-m", "pipeline.tradfi_data", "snapshot",
                         "--classes", ",".join(SNAPSHOT_CLASSES),
                         "--out", str(layer), "--ts-root", str(root)], cwd=layer)
    if rc == 0:
        _cycle_items.update(_snapshot_items(layer))
    return rc


def _snapshot_items(layer: Path) -> dict[str, str]:
    """`snapshot_utc` from the manifest the adapter just rewrote, so the
    digest can say which snapshot a cycle was bred on. Absent or unreadable
    manifest -> no item (the FakeRunner writes nothing; a real run always
    does)."""
    p = layer / "data" / "tradfi_snapshot_manifest.json"
    try:
        utc = json.loads(p.read_text(encoding="utf-8")).get("snapshot_utc")
    except (OSError, ValueError):
        return {}
    return {"snapshot_utc": str(utc)} if utc else {}
```

(e) In `run()` (line ~800), right after `args = ap.parse_args(argv)`, add:

```python
    _cycle_items.clear()
```

(f) In `_run_cycle`, find the end of section 1 — the `else:` branch that ends with

```python
        if state.get("stale_lock") is not None:
            loop_state.clear_stale_lock(state)
            loop_state.save(state_path, state)
```

and insert, before the `# -- 2. trigger check` comment:

```python
    # -- 1b. stage 0: tradfi snapshot, BEFORE the first chain read -----------
    # Runs on every fire that gets past the locks, no_trigger and budget
    # parks included: the quarantine daily reads the same data/. A refusal
    # is a defect (a pinned series changed or vanished), never weather.
    snap_rc = _snapshot_stage(runner, layer)
    if snap_rc not in (None, 0):
        print(f"snapshot_failed: pipeline.tradfi_data exited {snap_rc}; "
              f"the tradfi cells were not refreshed -- see the adapter's "
              f"output above. Nothing was spent and the chain was not read.",
              flush=True)
        _write_status(logs_dir, "snapshot_failed", overall="FAIL",
                      extra={"snapshot_rc": str(snap_rc)},
                      spent=_spent(logs_dir), escalations=["snapshot_failed"],
                      state=state)
        return 1
```

- [ ] **Step 5: Run the stage-0 tests, then the whole loop file**

Run: `python -m pytest pipeline/test_loop.py -q -k "stage0 or snapshot_utc"`
Expected: 7 passed.

Run: `python -m pytest pipeline/test_loop.py -q 2>&1 | tail -2`
Expected: everything passes that passed at baseline. If any of the seven `_modules(fr) == [...]` assertions fails with `pipeline.tradfi_data` at the head of the list, the autouse fixture from Step 1 is not in effect for that test — fix the fixture, never the assertion.

- [ ] **Step 6: Commit**

```bash
git add research-layer/pipeline/loop.py research-layer/pipeline/test_loop.py
git commit -m "feat(loop): stage 0 - the loop takes the tradfi snapshot itself on every fire"
```

---

### Task 4: Preflight 4.0c — refuse a stale tree before triage, at zero spend

**Files:**
- Modify: `pipeline/loop.py` (new `_freshness_preflight`; `_run_cycle` after the orphan check)
- Test: `pipeline/test_loop.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_loop.py`:

```python
# ─── 4.0c freshness preflight (2026-09-12 spec §3) ──────────────────────────

from .common import content_id
from .test_screen import _write_cell_csv


def _spec_on(card_ids, asset, asset_class):
    """A registered spec whose universe is ONE daily cell of the given class
    (make_strategy's default is a 15m futures cell with no CSV on disk)."""
    spec = make_strategy(card_ids)
    spec["universe"] = {"assets": [asset], "asset_class": asset_class,
                        "timeframe": "1d", "session": "RTH"}
    spec["strategy_id"] = None
    spec["strategy_id"] = content_id(spec, "strategy_id")
    return spec


def _layer_with_two_registered_cells(tmp_path, fx_end, crypto_end):
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    register_example_blocks(reg)
    reg.register_strategy(_spec_on(["card0000"], "AUD", "fx"))
    reg.register_strategy(_spec_on(["card0001"], "BTCUSD", "crypto"))
    data = layer / "data"
    data.mkdir()
    if fx_end:
        _write_cell_csv(data, "AUD", "1d", ["2026-01-02 00:00:00", fx_end])
    if crypto_end:
        _write_cell_csv(data, "BTCUSD", "1d", ["2026-01-02 00:00:00", crypto_end])
    return layer


def test_stale_data_parks_the_cycle_before_triage_at_zero_spend(tmp_path, capsys):
    """The 2026-09-11 failure, caught one stage in instead of four: AUD ends
    2026-08-21, BTCUSD 2026-09-10 -- 20 days, allowance 13 (3 + fx's 10)."""
    layer = _layer_with_two_registered_cells(tmp_path, "2026-08-21 00:00:00",
                                             "2026-09-10 00:00:00")
    fr = FakeRunner()

    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)

    assert rc == 1
    assert _modules(fr) == []                     # NOTHING metered ran
    out = capsys.readouterr().out
    assert "stale_data:" in out and "AUD_1d" in out and "BTCUSD_1d" in out
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["overall"] == "FAIL"
    assert status["items"]["outcome"] == "stale_data"
    assert "AUD_1d" in status["items"]["stale_detail"]
    assert status["items"]["data_end_min"] == "2026-08-21"
    assert status["items"]["data_end_max"] == "2026-09-10"
    assert "run_aborted" in status["escalations"]   # a PUSH_TRIGGERS member; "stale_data" is not
    assert status["push"] is True
    assert "triggerable_fx" in status["items"]     # after the chain read, so counts present
    st = json.loads((layer / "logs" / "loop_state.json").read_text(encoding="utf-8"))
    # nothing banked: a stale abort never creates classes["fx"] at all
    assert "watermark" not in st.get("classes", {}).get("fx", {})


def test_fresh_data_passes_the_preflight_and_the_stage_argv_is_unchanged(tmp_path):
    layer = _layer_with_two_registered_cells(tmp_path, "2026-09-04 00:00:00",
                                             "2026-09-10 00:00:00")   # 6 days, allowed
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert _modules(fr) == ["pipeline.triage_batch", "pipeline.composer",
                            "pipeline.composer", "pipeline.screen", "pipeline.gauntlet"]
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "cycle_complete"


def test_a_registered_cell_with_no_price_file_is_stale_data_naming_the_cell(tmp_path, capsys):
    layer = _layer_with_two_registered_cells(tmp_path, None, "2026-09-10 00:00:00")
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 1
    assert _modules(fr) == []
    status = json.loads((layer / "logs" / "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["items"]["outcome"] == "stale_data"
    assert "AUD_1d" in status["items"]["stale_detail"]
    assert "AUD_1d" in capsys.readouterr().out


def test_no_registered_specs_means_nothing_to_compare(tmp_path):
    """Every pre-existing loop test registers nothing before triage; the
    preflight must be a no-op there, not a FileNotFoundError on data/."""
    layer, _ = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    assert _modules(fr)[0] == "pipeline.triage_batch"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest pipeline/test_loop.py -q -k "stale_data or fresh_data or no_price_file or nothing_to_compare"`
Expected: `test_stale_data_parks...` and `test_a_registered_cell_with_no_price_file...` FAIL (rc is 0 and the metered stages ran); the other two pass already (they pin behaviour that must not change).

- [ ] **Step 3: Implement the preflight in `pipeline/loop.py`**

(a) After `_gauntlet_orphans` (line ~464–480), add:

```python
def _freshness_preflight(registry: Registry, data_dir: Path) -> tuple[str | None, dict[str, str]]:
    """(problem, items). problem is None when every cell the gauntlet will
    compare ends within the cross-class allowance; otherwise the text
    assert_cells_comparable (or a missing price file) gives. Reads only each
    CSV's tail, so it is cheap enough for every fire.

    Sits with the chain verify and the orphan preflight (4.0a/4.0b) for the
    same reason: the gauntlet refuses on this condition at the END of the
    cycle, after triage and both composer calls have been paid for. On
    2026-09-11 that cost USD 1.90 and the month's last cycle. With stage 0
    in front of this check, staleness now means the source lags beyond its
    declared max_end_lag_days or a cell vanished -- a defect, never weather.
    """
    from .screen import assert_cells_comparable, cell_end_dates, comparable_cells
    all_specs = [e["payload"] for e in registry.entries()
                 if e["entry_type"] == "strategy_registered"]
    cells_needed, class_of = comparable_cells(all_specs)
    if not cells_needed:
        return None, {}
    try:
        ends = cell_end_dates(data_dir, cells_needed)
    except FileNotFoundError as exc:
        return str(exc), {}
    days = sorted(e[:10] for e in ends.values() if e)
    items = {"data_end_min": days[0], "data_end_max": days[-1]} if days else {}
    try:
        assert_cells_comparable(ends, class_of=class_of)
    except ValueError as exc:
        return str(exc), items
    return None, items
```

(b) In `_run_cycle`, directly after the orphan preflight block (the one ending `return 1` after `_write_status(logs_dir, "gauntlet_orphan", ...)`) and BEFORE `entries_before = _entry_count(registry_path)`, insert:

```python
    # 4.0c freshness pre-flight, same zero-spend position. Stage 0 has just
    # refreshed the tradfi cells; if the tree is STILL not comparable, the
    # gauntlet would refuse after every metered stage ran (2026-09-11).
    problem, fresh_items = _freshness_preflight(registry, layer / "data")
    if problem is not None:
        print(f"stale_data: {problem}", flush=True)
        _write_status(logs_dir, "stale_data", overall="FAIL",
                      extra={"stale_detail": problem[:400], **fresh_items,
                             "asset_class": asset_class},
                      spent=_spent(logs_dir), escalations=["run_aborted"],
                      state=state, counts=trigger_counts)
        return 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest pipeline/test_loop.py -q -k "stale_data or fresh_data or no_price_file or nothing_to_compare"`
Expected: 4 passed.

Run: `python -m pytest pipeline/test_loop.py -q 2>&1 | tail -2`
Expected: all pass (baseline failures excepted).

- [ ] **Step 5: Full suite**

Run: `python -m pytest pipeline tools -q 2>&1 | tail -3`
Expected: passed count = baseline + 18 new tests (5 + 2 + 7 + 4); the failure set is exactly the recorded baseline set.

- [ ] **Step 6: Commit**

```bash
git add research-layer/pipeline/loop.py research-layer/pipeline/test_loop.py
git commit -m "feat(loop): 4.0c freshness preflight - a stale tree parks before triage at zero spend"
```

---

### Task 5: Documentation — CLAUDE.md, the SP4 amendment

**Files:**
- Modify: `CLAUDE.md` (section `## Pipeline loop (25_PipelineLoop)`)
- Modify: `docs/2026-08-24-market-expansion-sp4-design.md` (§3, after the "Refresh policy:" paragraph)

- [ ] **Step 1: CLAUDE.md**

In `CLAUDE.md`, find the bullet that begins `- State: logs/loop_state.json (per-class watermarks + thresholds, Coen-editable).` inside `## Pipeline loop (25_PipelineLoop)` and insert this bullet directly BEFORE it:

```markdown
- **Stage 0 snapshot + freshness preflight (2026-09-12, `docs/2026-09-12-loop-snapshot-stage-design.md`).**
  Every fire that gets past the lock probes first runs
  `python -m pipeline.tradfi_data snapshot --classes fx,equity_etf,bond_etf,metal_etf --out <layer>`
  (loop.SNAPSHOT_CLASSES) BEFORE the first chain read -- on `no_trigger` and budget parks too,
  so the 08:20 quarantine daily always finds fresh cells. Skipped (printed + `snapshot_skipped=1`)
  only when the trading-systems producer root does not exist (tests, a fresh clone). A non-zero
  exit is `snapshot_failed` (FAIL, exit 1, task retry, zero spend). Then, after the orphan check
  and before triage, `_freshness_preflight` reads each registered cell's LAST bar and runs the
  gauntlet's own `assert_cells_comparable`; a breach is `stale_data` (FAIL, exit 1, zero spend,
  `stale_detail` names the cells). Both derive their cells from `screen.comparable_cells`, the
  one implementation the gauntlet also uses. Successful fires carry `snapshot_utc` in the status.
  **A hand `tradfi_data snapshot` is now a REPAIR, never a routine** -- the 2026-09-11 22:30 cycle
  failed in the gauntlet after USD 1.90 because the last hand snapshot was 11 days old and fx
  (FRED, ~1 week lag) sat 20 days behind crypto against a 13-day allowance.
```

- [ ] **Step 2: SP4 design amendment**

In `docs/2026-08-24-market-expansion-sp4-design.md`, directly after the paragraph

```
Refresh policy: a snapshot is taken at track start and then deliberately (before a
generation run), never on a schedule — generations pin to the snapshot they were bred on.
```

insert:

```
> **Amended 2026-09-12** (`docs/2026-09-12-loop-snapshot-stage-design.md`, Coen's decision):
> the pipeline loop IS the generation run and fires unattended, so "deliberately before a
> generation run" is done by the loop itself as its stage 0, on every fire. The pinning half
> is unchanged — each generation still records the snapshot it was bred on — and there is
> still no separate schedule for the snapshot. A hand `tradfi_data snapshot` is a repair, not a
> routine. Why: the 2026-09-11 22:30 cycle refused in the gauntlet (fx 20 days behind crypto)
> because the last hand snapshot was eleven days old.
```

- [ ] **Step 3: Commit**

```bash
git add research-layer/CLAUDE.md research-layer/docs/2026-08-24-market-expansion-sp4-design.md
git commit -m "docs: stage 0 snapshot + freshness preflight in CLAUDE.md; SP4 s3 refresh policy amended"
```

---

### Task 6: Live proof in the LIVE tree (read the artifacts, never assume)

**Files:** none modified by hand. This task runs the real thing once and reads what it wrote.

Preconditions: clock clear of 08:20 / 08:50 / 09:00–09:15; `logs/chain.lock` absent; `logs/loop.lock` absent. The month is budget-parked, so the dry run exercises stage 0 for real and then parks.

- [ ] **Step 1: Record the state BEFORE**

```
tail -1 data/AUD_1d.csv; tail -1 data/SPY_1d.csv; tail -1 data/GLD_1d.csv
python -c "import json;print(json.load(open('data/tradfi_snapshot_manifest.json'))['snapshot_utc'])"
```

Expected before: AUD ends `2026-08-21`, SPY `2026-08-28`, GLD `2026-08-28`, manifest `2026-08-31T09:35:16...`.

- [ ] **Step 2: Dry run in the live tree**

```
python -m pipeline.loop --once --dry-run 2>&1 | tail -15
```

Expected, in order: `loop: running ... -m pipeline.tradfi_data snapshot --classes fx,equity_etf,bond_etf,metal_etf --out ...`, then the adapter's `snapshot: wrote 38 series to ...data`, then `deferred_budget: pipeline spend USD 32.47 ...` (the park). No `snapshot_failed`, no `stale_data`, no traceback. If the adapter REFUSES, read its reason: it names the series; do not force anything — report it.

- [ ] **Step 3: Read the state AFTER**

```
tail -1 data/AUD_1d.csv; tail -1 data/SPY_1d.csv; tail -1 data/GLD_1d.csv
python -c "import json;m=json.load(open('data/tradfi_snapshot_manifest.json'));print(m['snapshot_utc'], m['source_snapshot_utc'])"
python -c "import json;i=json.load(open('logs/pipeline_status.json'))['items'];print(i['outcome'], i.get('snapshot_utc'), i.get('snapshot_skipped'))"
git -C .. status --porcelain research-layer/data | head
```

Expected: AUD ends on the producer's fx end (2026-09-04 or later), SPY and GLD on the producer's ETF end (2026-09-10 or later), manifest `snapshot_utc` = today, status outcome `deferred_budget` with `snapshot_utc` = the same timestamp and no `snapshot_skipped`. `git status` shows ONLY `research-layer/data/tradfi_snapshot_manifest.json` modified: `data/*.csv` is gitignored (`.gitignore` line ~14), so the CSVs never appear. The manifest is tracked and stage 0 rewrites it on every fire; leave it modified (CLAUDE.md now says so).

- [ ] **Step 4: Prove the preflight against the NOW-FRESH tree**

```
python - <<'EOF'
from pathlib import Path
from pipeline.registry import Registry
from pipeline import loop
problem, items = loop._freshness_preflight(Registry("registry_log.jsonl"), Path("data"))
print("problem:", problem); print("items:", items)
EOF
```

Expected: `problem: None`, items with `data_end_min` within 13 days of `data_end_max`.

- [ ] **Step 5: Vault + push**

Append the outcome (the before/after ends, the manifest timestamps, the status line) to `project_market_data_campaign.md` in the vault and shorten the index line to "stage 0 snapshot + preflight SHIPPED <sha>". Then, per the standing rule, `git log --oneline @{u}..HEAD` first, and push the working branch only when the list is the commits from Tasks 1–5 (plus any automated `quarantine: forward record` commits, which are routine).

---

## Self-review

**Spec coverage.** §2 stage 0: placement (Task 3 (f)), argv + constant (3 (d)), skip rule (3 (d)), failure outcome + no counts (3 (f), test), dry run included (test), provenance via manifest (`_snapshot_items`), no commit (Task 6 step 3 confirms). §3 preflight: helpers (Tasks 1–2), placement after 4.0b (Task 4 (b)), outcome/items/escalation (4 (a)–(b)), FileNotFoundError path (4 (a), test), gauntlet keeps its check (Task 2 step 5). §4 status items (`snapshot_utc`, `snapshot_skipped`, both outcomes in the docstring), docs (Task 5), tests 1–10 map to Tasks 1–4, live proof (Task 6). §5 amendment (Task 5 step 2). §6 out of scope: nothing here touches the allowance, the USDT grid, or the quarantine backfill.

**Placeholders.** None: every code step carries the code, every run step the command and expected result.

**Type consistency.** `cell_end_dates(data_dir, cells) -> dict[str, str]`, `comparable_cells(all_specs) -> (list[tuple[str,str]], dict[str,str])`, `_snapshot_stage(runner, layer) -> int | None`, `_freshness_preflight(registry, data_dir) -> (str | None, dict[str,str])` are used with those shapes in every task. `SNAPSHOT_CLASSES` is a tuple, joined with `","` in the loop and in the test. `_cycle_items` is cleared in `run()` and merged in `_write_status` before `extra`, so an explicit `extra` still wins.
