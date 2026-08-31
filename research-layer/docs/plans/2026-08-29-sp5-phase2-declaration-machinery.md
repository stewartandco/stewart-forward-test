# SP5 Phase 2: Declaration + Machinery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Land the class-generic cell activation gate, migrate the crypto class onto the per-cell path WITH ITS ACTIVE SET EMPTY, add sweep rotation, and ship the D9 re-trial protocol + D10 sweep queues — all behavior-frozen for what actually sweeps. Phase 2 of `docs/2026-08-28-market-data-universe-design.md` (D4, D5, D6, D9, D10).

**Architecture:** Declaration is separated from activation. `ACTIVE_CELLS` (new, in `cells.py`) is the per-cell gate; the four tradfi classes declare `"all"` so their behavior is byte-identical to today, and crypto declares EMPTY so the legacy pooled composer path keeps serving live crypto triggers until Coen's Phase 3 activation commit. Family openness (re-trials, queues) changes what the composer may PROPOSE, not what any class sweeps.

**Tech stack:** Python stdlib + pytest. No new dependencies. No network. No LLM calls in tests.

---

## Conventions for every task (read first)

- Work in the worktree `E:\Users\Coen\Claude\stewart-forward-test-sp5\research-layer`, branch `feat/sp5-phase2` (create it off the merged branch tip before Task 1). NEVER touch `E:\Users\Coen\Claude\stewart-forward-test` (live tree: resident scanner + scheduled tasks write there). A THIRD worktree `stewart-forward-test-loopfix` may exist for an unrelated loop hotfix — leave it alone.
- Run from the layer root: `python -m pytest pipeline -q` (baseline 1058 passed, 0 failed).
- `python -c "import pipeline.cells"` must stay clean after EVERY task — import-time assertions are load-bearing.
- Scoped `git add` with explicit paths, one commit per task. NEVER `git add -A`.
- NEVER write to `registry_log.jsonl`, `logs/`, or `data/`. No task in this phase writes to the chain except Task 5, which appends ONE `note` entry, in the live tree, supervised by the main session — NOT by a subagent.
- **THE INVARIANT OF THIS PHASE:** when it merges, a live loop fire must behave EXACTLY as it does today. Crypto triggers still take the legacy pooled path; the four tradfi classes sweep the same cells they sweep now. Every task must preserve that, and Task 6 proves it.

## File map

| File | Task | Role |
|---|---|---|
| `pipeline/cells.py` | 1, 3 | ACTIVE_CELLS gate + `active_cells()`; then the crypto class migration |
| `pipeline/test_cells.py` | 1, 3 | gate tests; migration pins (counts/order/tuples) |
| `pipeline/composer.py` | 2, 4 | sweep `active_cells`; then sweep queues (D10) + re-trial protocol (D9) |
| `pipeline/test_composer*.py` | 2, 4 | byte-identical-for-tradfi pin; queue + re-trial tests |
| `pipeline/loop_state.py`, `pipeline/loop.py` | 4 | rotation cursor + queue persistence in loop state |
| `pipeline/test_loop.py` | 4 | rotation + queue-drain tests |
| `docs/2026-08-29-sp5-crypto-grid-addendum.md` | 5 | the Lane-B addendum (declaration, honesty limits, build deltas, ship bar) |
| `docs/notes/family-openness-v1.md` + chain note | 5 | Lane-A pre-declaration for D9/D10 (MAIN SESSION appends the chain entry) |

---

### Task 1: ACTIVE_CELLS gate (class-generic, behavior-frozen)

**Files:** Modify `pipeline/cells.py`, `pipeline/test_cells.py`.

- [ ] **Step 1: Write failing tests** in `test_cells.py`:

```python
def test_active_cells_declares_every_live_class():
    for cls in cells.LIVE_CLASSES:
        assert cls in cells.ACTIVE_CELLS

def test_tradfi_classes_are_fully_active_so_behavior_is_unchanged():
    for cls in ("fx", "equity_etf", "bond_etf", "metal_etf"):
        assert cells.active_cells(cls) == cells.class_cells(cls)

def test_crypto_active_set_is_empty_until_activation():
    # Phase 2 declares; Phase 3 (Coen's own commit) activates. An empty
    # active set is what keeps the legacy pooled path serving crypto.
    assert cells.active_cells("crypto") == []

def test_active_cells_is_a_subset_of_the_declared_grid():
    for cls in cells.LIVE_CLASSES:
        assert set(cells.active_cells(cls)) <= set(cells.class_cells(cls))

def test_validate_cell_still_accepts_the_whole_declared_grid():
    # declaration admits data/import work; activation admits sweeping
    for asset, tf in cells.class_cells("crypto"):
        cells.validate_cell(asset, tf)
```

- [ ] **Step 2: Run** `python -m pytest pipeline/test_cells.py -q` -> FAIL (no ACTIVE_CELLS).
- [ ] **Step 3: Implement** in `cells.py`, immediately after `LIVE_CLASSES`:

```python
# SP5 D4/D5 (docs/2026-08-28-market-data-universe-design.md s3): LIVE_CLASSES
# gates CLASSES; it cannot stage an ALREADY-LIVE class's expansion. crypto is
# live, so widening its grid without this gate would sweep the expansion on
# the next loop fire with no activation event. ACTIVE_CELLS is that gate at
# cell granularity: growing an entry is the DENOMINATOR EVENT and is Coen's
# own reviewed commit, never a side effect of declaring assets above.
# "all" = the class's whole declared grid (the four tradfi classes: byte-
# identical to pre-SP5 behavior, test-pinned).
ACTIVE_CELLS = {
    "crypto":     {"assets": (), "timeframes": ()},
    "fx":         {"assets": "all", "timeframes": "all"},
    "equity_etf": {"assets": "all", "timeframes": "all"},
    "bond_etf":   {"assets": "all", "timeframes": "all"},
    "metal_etf":  {"assets": "all", "timeframes": "all"},
}


def active_cells(asset_class: str) -> list[tuple[str, str]]:
    """The cells a generation MAY sweep for this class: class_cells()
    restricted to the ACTIVE_CELLS subsets. Declaration (CLASSES) is a space;
    this is the decision to search part of it."""
    spec = _class_spec(asset_class)
    gate = ACTIVE_CELLS[asset_class]
    assets = spec["assets"] if gate["assets"] == "all" else gate["assets"]
    tfs = spec["timeframes"] if gate["timeframes"] == "all" else gate["timeframes"]
    return [(a, tf) for a in assets for tf in tfs]
```

plus import-time assertions in the existing assertion block (every LIVE_CLASSES member has an ACTIVE_CELLS entry; every non-`"all"` tuple is a subset of the class's declared tuple), with `del` of any new loop variables to match the file's convention.

- [ ] **Step 4: Verify** targeted green, `python -c "import pipeline.cells"` clean, full suite green.
- [ ] **Step 5: Commit** `git add pipeline/cells.py pipeline/test_cells.py`
  `feat(sp5): ACTIVE_CELLS - cell-level activation gate, class-generic (D4) (P2-T1)`

### Task 2: Composer sweeps the active set

**Files:** Modify `pipeline/composer.py` (ONLY `expand_family_for_class`'s cell source), `pipeline/test_composer_equity.py` (or wherever per-cell expansion is pinned — find it).

- [ ] **Step 1: Write the failing test** (in the per-cell expansion test module):

```python
def test_expansion_sweeps_the_active_set_not_the_declared_grid(monkeypatch):
    # A class whose active set is a strict subset expands to that subset only.
    monkeypatch.setitem(cells.ACTIVE_CELLS, "equity_etf",
                        {"assets": ("SPY",), "timeframes": "all"})
    specs = composer.expand_family_for_class(FAM, "run", "model", TS, "equity_etf")
    assert {s["universe"]["assets"][0] for s in specs} == {"SPY"}

def test_full_active_set_expansion_is_unchanged_for_tradfi():
    # byte-identical to pre-gate behavior: "all"/"all" -> class_cells
    specs = composer.expand_family_for_class(FAM, "run", "model", TS, "equity_etf")
    assert len(specs) == len(EXPECTED_COMBOS) * len(cells.class_cells("equity_etf"))
```

- [ ] **Step 2: Run** -> the subset test FAILS (expansion still uses class_cells).
- [ ] **Step 3: Implement:** in `expand_family_for_class`, change the one call `cells.class_cells(asset_class)` to `cells.active_cells(asset_class)`. Add a comment: the declared grid admits data work; the ACTIVE set admits sweeping (SP5 s3). Change NOTHING else in composer.py in this task.
- [ ] **Step 4: Verify** the full composer test modules green (`test_composer.py test_composer_fx.py test_composer_equity.py test_composer_2b.py`), then full suite.
- [ ] **Step 5: Commit** `git add pipeline/composer.py <the test file>`
  `feat(sp5): composer sweeps active_cells, not the declared grid (D4) (P2-T2)`

### Task 3: Crypto class migration (declaration only — active set stays empty)

**Files:** Modify `pipeline/cells.py`, `pipeline/test_cells.py`.

- [ ] **Step 1: Write failing tests:**

```python
def test_crypto_assets_are_the_pinned_universe_manifest():
    import json, pathlib
    man = json.loads((pathlib.Path(__file__).resolve().parent.parent
                      / "data" / "crypto_universe_manifest.json").read_text(encoding="utf-8"))
    assert list(cells.CLASSES["crypto"]["assets"]) == [
        a["binance_symbol"] for a in man["admitted"]]

def test_crypto_declares_a_cost_model_and_self_benchmark():
    spec = cells.CLASSES["crypto"]
    assert spec["cost_model"] == {"commission_per_side": 0.001,
                                  "slippage_ticks": 0.0005}
    assert spec["benchmark"] == "self"

def test_crypto_declared_grid_is_100_assets_by_6_timeframes():
    assert len(cells.CLASSES["crypto"]["assets"]) == 100
    assert len(cells.CLASSES["crypto"]["timeframes"]) == 6
    assert len(cells.class_cells("crypto")) == 600

def test_crypto_is_declared_but_still_inactive():
    assert cells.active_cells("crypto") == []
```

Also UPDATE the stale pins that encode the old 5-asset grid — find them all (`test_the_grid_is_five_assets_by_six_timeframes` pinning `len(all_cells())==30`, the fixed-order test pinning first/last cell, the phase-1 count test, `test_classes_registry_crypto_unchanged`, and `test_unknown_cells_are_rejected` which asserts `validate_cell("DOGEUSDT","1h")` RAISES — DOGE is now very likely IN the universe, so that probe must change to a ticker that is genuinely undeclared; verify against the manifest which one to use). Report every pin you change.

- [ ] **Step 2: Run** -> FAIL.
- [ ] **Step 3: Implement** the crypto class dict change: `assets` = the 100 manifest `binance_symbol`s as a literal tuple in manifest order, `cost_model` = the literal dict above, `benchmark` = `"self"`; `timeframes`, `session`, `periods_per_year`, `bar_kind`, `eras`, `max_end_lag_days`, `excluded_block_types` UNCHANGED. Above it, a comment block in the house style: names the addendum path (`docs/2026-08-29-sp5-crypto-grid-addendum.md`), the manifest as provenance, the amended active-trading rule, the observed same-day data end (2026-08-27, verified at build time — re-verify and record what you observe), and that ACTIVE_CELLS crypto is EMPTY so nothing sweeps until Coen's Phase 3 commit. Keep `ASSETS`/`TIMEFRAMES` back-compat aliases consistent (`ASSETS` should now be the 100 — check every consumer of `cells.ASSETS` first and report them).
- [ ] **Step 4: Verify** `python -c "import pipeline.cells"` clean (the disjointness assertion is the likeliest trip — 100 crypto tickers must not collide with fx/ETF asset ids; if one does, STOP and report), targeted green, full suite green.
- [ ] **Step 5: Commit** `git add pipeline/cells.py pipeline/test_cells.py`
  `feat(sp5): declare the 100-asset crypto grid, active set still empty (D4) (P2-T3)`

### Task 4: Sweep rotation + queues + re-trials (family openness)

**Files:** Modify `pipeline/composer.py`, `pipeline/loop_state.py`, `pipeline/loop.py`, plus their test modules.

This is the biggest task. If it feels too large while working, STOP and report — splitting it is fine.

- [ ] **Step 1: Write failing tests.**

*Re-trials (D9), in the composer test module that covers `screen_siblings`:*
```python
def test_buried_composition_returns_after_the_retrial_window():
    # D9: a composition buried on data that has since moved >= 6 months past
    # the burying verdict's cutoff is re-testable as a NEW trial.
    kept, notes, malformed = composer.screen_siblings(
        [SPEC], known_fps={FP: OLD_SID}, run_fps={},
        retrial_ok={FP: True})          # or whatever signature you choose
    assert kept == [SPEC]

def test_buried_composition_still_blocked_inside_the_window():
    kept, notes, _ = composer.screen_siblings(
        [SPEC], known_fps={FP: OLD_SID}, run_fps={}, retrial_ok={FP: False})
    assert kept == [] and "already registered" in notes[0]

def test_in_run_duplicates_are_still_malformed():
    # same-data duplicates are NOT re-trials
    _, _, malformed = composer.screen_siblings([SPEC, SPEC], {}, {})
    assert malformed is True
```
The eligibility computation itself (cutoff of the burying verdict vs the cell's current data end, >= 183 days) belongs in a small pure function — test it directly with hand-picked dates, both sides of the boundary.

*Queues (D10):*
```python
def test_oversized_family_queues_the_remainder_instead_of_refusing():
    specs = composer.expand_family_for_class(BIG_FAM, ...)   # > cap
    kept, queued = composer.split_for_cycle(specs, cap=60)
    assert len(kept) == 60 and len(queued) == len(specs) - 60

def test_queued_specs_drain_on_later_cycles_and_nothing_is_dropped():
    # union of kept across cycles == the full sweep, no duplicates
```
And a test that `validate_family` NO LONGER rejects on sibling count (the "exceeds cap - rejected, not clipped" error must be gone; grep it).

*Rotation (D6):*
```python
def test_rotation_window_advances_and_wraps():
    st = {"classes": {"crypto": {"rotation_cursor": 96}}}
    window = loop_state.rotation_window(st, "crypto", assets=ALL_100, size=12)
    assert len(window) == 12 and window[0] == ALL_100[96]   # wraps past the end
def test_rotation_covers_every_asset_before_repeating():
    # 100 assets, size 12 -> 9 cycles covers all 100 exactly once
```

- [ ] **Step 2: Run** -> FAIL.
- [ ] **Step 3: Implement.**
  - `composer.split_for_cycle(specs, cap)` + queue persistence: the loop passes any queued specs from `loop_state` into the next cycle for that class BEFORE proposing new families, and records the remainder. Delete `validate_family`'s cap refusal; keep the cap as the per-cycle WINDOW size only. Invariant to pin: no proposed variation is ever dropped without either a gauntlet verdict or a queue entry.
  - `loop_state.rotation_window(state, cls, assets, size)` + `rotation_cursor` persisted per class; the loop passes the window to the composer as an explicit `--assets` subset. ~~**Rotation applies ONLY when a class's active set is non-empty** — crypto's is empty this phase, so nothing changes live.~~

    > **RIDER 2026-08-31 (P2-T4 as built + its review). The struck sentence above is WRONG and was not implemented.** "Active set non-empty" exempts only crypto; it leaves all four tradfi classes rotating, and `equity_etf`'s active set is **16 assets — ABOVE `ROTATION_SIZE` 12**. That rule would have windowed a live class 12-of-16 and broken the Phase 2 sweep freeze silently. The size-only variant ("whole set when `<= ROTATION_SIZE`") fails for the same reason and on the same class.
    >
    > **As built, the gate is two declarations, neither inferred from an asset count:**
    > 1. `loop.ROTATION_CLASSES = ("crypto",)` — which classes SHOULD rotate. Design s5 scopes D6 to crypto in its own words ("a *crypto* generation sweeps a rotating window of 12 assets… full coverage in 9 generations (100/12)"). Same convention as `LIVE_CLASSES`/`ACTIVE_CELLS`: a change to what a generation sweeps is a decision, never a side effect.
    > 2. `expander_for(cls) is not expand_family` — which classes CAN accept a window at all. `--assets` is a view onto active cells and the legacy pooled expander has none, so `composer.run` refuses it. **Both the loop's emit condition and the composer's accept condition read this same dispatch**, so rotation switches on in the same commit that makes it legal. Keyed on the string `"crypto"` instead, Phase 3 would have emitted a window into a composer guaranteed to exit 1 — `stage_failed` and a Sentinel FAIL on every crypto fire.
    >
    > Plus the small-set rule inside `rotation_window` itself: an active set of `<= size` is returned whole and the cursor never moves. `docs/2026-08-28-market-data-universe-design.md` s5 is stale on this point too; it was deliberately not edited (that doc is stale in this worktree on s4 and s7b as well).
  - Re-trial eligibility helper (pure, dated): burying verdict cutoff vs cell data end, `RETRIAL_WINDOW_DAYS = 183` with a comment citing D9 and the reason (the gauntlet is deterministic; a same-data re-test is a known answer bought at a higher BH bar for every live survivor).
  - `pipeline_status.json` gains queue depth per class so a parked queue is visible.
- [ ] **Step 4: Verify** targeted modules green; full suite green; **and prove the freeze**: `python -m pipeline.loop --dry-run` in the worktree reports the same decision it does in the live tree.
- [ ] **Step 5: Commit** `git add pipeline/composer.py pipeline/loop_state.py pipeline/loop.py <test files>`
  `feat(sp5): rotation, sweep queues, re-trial protocol (D6/D9/D10) (P2-T4)`

### Task 5: Governance documents (MAIN SESSION does the chain append)

**Files:** Create `docs/2026-08-29-sp5-crypto-grid-addendum.md`, `docs/notes/family-openness-v1.md`.

- [ ] **Step 1:** Write the Lane-B addendum following the 2a/2b structure exactly (read `docs/2026-08-27-sp4-track2b-addendum.md` first): `**Status:` line naming Coen's go and the date and stating it was written before implementation; class declaration with the literal field values and the asset count; honesty limits (numbered: survivorship, ragged histories, price-only benchmark basis, more cells = more trials); routing; build deltas file-by-file ending in "NOTHING else"; ship bar as the arrow chain ending in `activation (ACTIVE_CELLS crypto gains 100x1d) -> first real generation`.
- [ ] **Step 2:** Write `docs/notes/family-openness-v1.md` in the house protocol-note style (read `docs/notes/quarantine-live-protocol-v1.md` first): declares D9 re-trials and D10 queues, states the RATCHET POSITION (this LOOSENS what may be re-proposed, so it is declared BEFORE any re-trial registration exists), and the WHY.
- [ ] **Step 3:** Commit both docs (docs-only commit, per the Lane-B convention that the addendum lands before the code it describes — note in your report that this plan inverts that ordering because the code is behavior-frozen; the MAIN SESSION will decide whether to reorder before merge).
  `docs(sp5): crypto grid addendum + family-openness protocol note (P2-T5)`
- [ ] **Step 4:** DO NOT append the chain note. The main session appends `family-openness-v1` to `registry_log.jsonl` in the LIVE tree, under chain lock, at merge time.

### Task 6: The freeze proof

**Files:** Create `pipeline/test_phase2_freeze.py`.

- [ ] **Step 1: Write the tests** (these ARE the deliverable — they prove Phase 2 changed nothing live):

```python
def test_crypto_still_takes_the_legacy_pooled_path():
    # composer routes crypto to expand_family while ACTIVE_CELLS crypto is empty
def test_tradfi_expansion_is_identical_to_the_declared_grid():
    for cls in ("fx", "equity_etf", "bond_etf", "metal_etf"):
        assert cells.active_cells(cls) == cells.class_cells(cls)
def test_no_class_sweeps_a_cell_outside_its_active_set():
def test_rotation_is_inert_while_an_active_set_is_empty():
def test_declared_but_inactive_crypto_cells_never_reach_a_generation():
```

- [ ] **Step 2-4:** Run, implement nothing (they should PASS against Tasks 1-4's work; any failure is a real defect — report it, do not weaken the test).
- [ ] **Step 5: Commit** `git add pipeline/test_phase2_freeze.py`
  `test(sp5): phase 2 freeze proof - declaration changed nothing live (P2-T6)`

## Self-review notes

- Spec coverage: D4 -> T1/T2/T3, D5 -> T3 (declared 6-TF, active empty), D6 -> T4, D9/D10 -> T4/T5, governance -> T5, the phase invariant -> T6.
- NOT in this phase (spec s10): Phase 3 activation (Coen's commit), the `26_CryptoGridRefresh` task, intraday data/quarantine work, Morpheus UI numbering.
- Task order: 1, 2, 3, 4, 5, 6. Task 3 depends on 1 (gate must exist before crypto's grid widens, or the next live fire sweeps 600 cells). Task 6 depends on all.
- The riskiest step is Task 3's import-time disjointness assertion against 100 real tickers; the second riskiest is Task 4's queue persistence interacting with the loop's watermark bookkeeping.
