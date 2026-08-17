# Gate Standard (protocol-v4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the research layer's gauntlet up to the `trading-systems` SOP's gate standard — adding PBO via CSCV, a Sharpe floor, and neighbourhood/plateau selection that replaces point-winner selection — so a candidate faces the same named gates regardless of which pipeline found it.

**Architecture:** Five new pure-function modules (`pbo`, `plateau`, `walkforward`, `regime`, plus a haircut function in the existing `stats`) with no I/O and no registry access, wired into `gauntlet.py` at the end. Four dense twin block types are added to the grammar; `composer.validate_family` gains a rule that only dense types may be swept. A write-free diagnostic reports what v4 would have done to the existing 80 specs, before the protocol note is chained.

**Tech Stack:** Python 3, standard library only (the pipeline is deliberately dependency-free — no numpy, no pandas anywhere in `pipeline/`).

---

## Read before starting

**Spec:** `docs/2026-08-17-gate-standard-design.md`. Read it fully. This plan implements it and does not restate its reasoning.

**Scoped test command** — the scanner's `test_scanner.py` belongs to another session, never run it:

```bash
python -m pytest pipeline/test_pipeline.py pipeline/test_composer.py pipeline/test_screen.py pipeline/test_gauntlet.py pipeline/test_gen2.py pipeline/test_gen3.py pipeline/test_gen3b.py pipeline/test_pbo.py pipeline/test_plateau.py pipeline/test_gen4.py -q
```

Baseline before any change: **371 tests green**. Run from `E:\Users\Coen\Claude\stewart-forward-test\research-layer`.

**Concurrency — this has already bitten this repo.** A concurrent session shares this branch, working directory and git index. Every commit in this plan is `git add <explicit paths> && git commit -m "..."` as **one** command. Never `git add -A`. Never leave anything staged. Run `git show HEAD --stat` after every commit and confirm only your files are in it.

**Never use PowerShell here-strings (`@'...'@`) in the Bash tool** — it produced a commit whose subject line was literally `@`.

**This plan writes nothing to the chain.** No task appends to `registry_log.jsonl`. Task 10 drafts the protocol note text as a file; chaining it is a separate, Coen-gated live sequence outside this plan.

## Data shapes you will need

Read from `registry_log.jsonl` entries (verified 2026-08-17):

- `strategy_registered` → `payload.strategy_id`, `payload.blocks` (list of `{role, type, params}`), `payload.provenance.sibling_group_id`, `payload.universe.assets`.
- `state_change` → `payload.strategy_id`, `payload.to`, `payload.reason`, `payload.from`, `payload.buried_at`. A screen death on turnover is `to == "graveyard"`, `reason == "trade_count"`.
- `verdict` → `payload.strategy_id`, `payload.stage` (`"screened"` or `"gauntlet"`), `payload.verdict` (`"pass"`/`"fail"`), `payload.metrics`.
- Artifacts: `artifacts/<sid>/equity.csv` with header `date,combined_equity`, 2329 rows. **All 80 have one**, including screen deaths.

Entry-type names are `state_change` and `verdict` — **not** `state_changed` / `verdict_recorded`.

## File structure

| File | Responsibility |
|---|---|
| `pipeline/pbo.py` (new) | CSCV probability of backtest overfitting. Pure. |
| `pipeline/plateau.py` (new) | Objective, neighbour enumeration, qualification, selection. Pure. |
| `pipeline/walkforward.py` (new) | Purged walk-forward folds. Pure. |
| `pipeline/regime.py` (new) | Regime ruler and conditional trade split. Pure. |
| `pipeline/stats.py` (modify) | `harvey_liu_haircut` added. |
| `pipeline/blocks.py` (modify) | Four dense twin types. |
| `pipeline/composer.py` (modify) | Sweepable-axis rule; sibling cap 25 → 60. |
| `pipeline/gauntlet.py` (modify) | PROTOCOL bump, new gates, `FAIL_ORDER`, `select_survivors` rewrite. |
| `diagnose_protocol_v4.py` (new) | Write-free ratchet check over the existing 80. |
| `pipeline/test_pbo.py`, `test_plateau.py`, `test_gen4.py` (new) | Test suites. |

---

## Task 1: Dense twin block types

**Files:**
- Modify: `pipeline/blocks.py` (append to `BLOCK_TYPES`, before the closing `}`)
- Test: `pipeline/test_gen4.py` (create)

Adding new keys is safe: `composer.preflight_block_types` only compares types present in **both** code and chain, so new entries cannot conflict. The regression test below makes that evidence rather than argument.

- [ ] **Step 1: Write the failing tests**

Create `pipeline/test_gen4.py`:

```python
"""protocol-v4 tests: dense grammar, sweepable-axis rule, cap."""
import json
from pathlib import Path

from pipeline.blocks import BLOCK_TYPES
from pipeline.composer import composition_fingerprint

REGISTRY = Path(__file__).resolve().parent.parent / "registry_log.jsonl"

DENSE = [("entry", "channel_breakout_dense"), ("entry", "ma_cross_dense"),
         ("entry", "trend_scan_dense"), ("stop", "atr_stop_dense")]


def test_dense_types_exist_with_expected_grids():
    assert BLOCK_TYPES[("entry", "channel_breakout_dense")]["lookback"]["grid"] == [20, 35, 55, 75, 100]
    assert BLOCK_TYPES[("entry", "ma_cross_dense")]["fast"]["grid"] == [5, 8, 13, 20, 34]
    assert BLOCK_TYPES[("entry", "ma_cross_dense")]["slow"]["grid"] == [50, 80, 130, 200]
    assert BLOCK_TYPES[("entry", "trend_scan_dense")]["max_lookback"]["grid"] == [60, 75, 90, 105, 120]
    assert BLOCK_TYPES[("stop", "atr_stop_dense")]["mult"]["grid"] == [1.5, 2.0, 2.5, 3.0, 3.5]


def test_coarse_types_are_untouched():
    """The chained schemas must not move, or preflight_block_types aborts."""
    assert BLOCK_TYPES[("entry", "channel_breakout")]["lookback"]["grid"] == [20, 55, 100]
    assert BLOCK_TYPES[("stop", "atr_stop")]["mult"]["grid"] == [1.5, 2.0, 3.0]
    assert BLOCK_TYPES[("risk", "fixed_fraction")]["f"]["grid"] == [0.01, 0.02]


def test_all_80_existing_fingerprints_unchanged():
    """Adding dense block types must not perturb a single chained fingerprint.

    Proven by recomputing every chained fingerprint against a grammar with the
    dense types REMOVED and requiring identical output. Comparing
    composition_fingerprint(p) to itself would be a tautology that passes even
    if every fingerprint had drifted — mutation-checked: retyping one coarse
    grid literal (atr_stop.mult 2.0 -> 2) moves 46 of the 80.
    """
    payloads = []
    for line in REGISTRY.open(encoding="utf-8"):
        e = json.loads(line)
        if e.get("entry_type") == "strategy_registered":
            payloads.append(e["payload"])
    assert len(payloads) == 80
    with_dense = [composition_fingerprint(p) for p in payloads]
    original = dict(BLOCK_TYPES)
    try:
        BLOCK_TYPES.clear()
        BLOCK_TYPES.update({k: v for k, v in original.items() if k not in DENSE})
        without_dense = [composition_fingerprint(p) for p in payloads]
    finally:
        BLOCK_TYPES.clear()
        BLOCK_TYPES.update(original)
    assert with_dense == without_dense
    assert len(set(with_dense)) == 80, "fingerprint collision among chained specs"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `KeyError: ('entry', 'channel_breakout_dense')`.

If `test_all_80_existing_fingerprints_unchanged` fails on an import error for `composition_fingerprint`, run `grep -n "def composition_fingerprint" pipeline/composer.py` and correct the import to the real name before continuing. Do not weaken the test.

- [ ] **Step 3: Add the dense types**

In `pipeline/blocks.py`, insert immediately before the closing `}` of `BLOCK_TYPES`:

```python
    # --- protocol-v4 dense twins -------------------------------------------
    # Chained schemas are immutable (composer.preflight_block_types), so
    # plateau selection gets density through NEW types rather than by widening
    # the coarse ones. Only these may be swept; see composer.validate_family.
    ("entry", "channel_breakout_dense"): {
        "lookback": {"type": "int", "grid": [20, 35, 55, 75, 100]},
        "direction": {"type": "str", "grid": ["long", "both"]},
    },
    ("entry", "ma_cross_dense"): {
        "fast": {"type": "int", "grid": [5, 8, 13, 20, 34]},
        "slow": {"type": "int", "grid": [50, 80, 130, 200]},
        "direction": {"type": "str", "grid": ["long", "short", "both"]},
    },
    ("entry", "trend_scan_dense"): {
        "max_lookback": {"type": "int", "grid": [60, 75, 90, 105, 120]},
        "t_min": {"type": "float", "grid": [2.0, 2.5, 3.0]},
        "direction": {"type": "str", "grid": ["long", "short", "both"]},
    },
    ("stop", "atr_stop_dense"): {
        "atr_len": {"type": "int", "grid": [14]},
        "mult": {"type": "float", "grid": [1.5, 2.0, 2.5, 3.0, 3.5]},
    },
```

Then add the `fast < slow` constraint for the dense cross. In `CONSTRAINTS`, add:

```python
    ("entry", "ma_cross_dense"):
        lambda p: ["ma_cross_dense: fast must be < slow"] if p["fast"] >= p["slow"] else [],
```

- [ ] **Step 4: Wire the dense types into the engine**

Each dense type is **behaviourally identical** to its coarse twin — only the grid differs. Widen the existing branch conditions rather than copying any handler body, so the two can never drift. Six exact edits in `pipeline/engine.py`:

| Line | From | To |
|---|---|---|
| 114 | `elif block["type"] == "channel_breakout":` | `elif block["type"] in ("channel_breakout", "channel_breakout_dense"):` |
| 143 | `elif block["type"] == "trend_scan_ds":` | `elif block["type"] in ("trend_scan_ds", "trend_scan_dense"):` |
| 155 | `elif block["type"] == "ma_cross_ds":` | `elif block["type"] in ("ma_cross_ds", "ma_cross_dense"):` |
| 211 | `elif s["type"] == "atr_stop":` | `elif s["type"] in ("atr_stop", "atr_stop_dense"):` |
| 253 | `if s["type"] == "atr_stop":` | `if s["type"] in ("atr_stop", "atr_stop_dense"):` |
| 283 | `elif (entry["type"] in ("ma_cross", "ma_cross_ds")` | `elif (entry["type"] in ("ma_cross", "ma_cross_ds", "ma_cross_dense")` |

The parameter names already match exactly: `channel_breakout_dense` takes `lookback`/`direction` like its twin, `trend_scan_dense` takes `max_lookback`/`t_min`/`direction`, `ma_cross_dense` takes `fast`/`slow`/`direction`, `atr_stop_dense` takes `atr_len`/`mult`. No handler body changes.

- [ ] **Step 5: Prove the twins are behaviourally identical**

Append to `pipeline/test_gen4.py`:

```python
from pipeline.engine import run_spec


def _spec(entry_type, stop_type):
    return {
        "strategy_id": "t" * 16,
        "universe": {"assets": ["BTCUSD"], "timeframe": "1d",
                     "session": "24x7", "asset_class": "crypto"},
        "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005},
        "blocks": [
            {"role": "entry", "type": entry_type,
             "params": {"lookback": 55, "direction": "both"}},
            {"role": "stop", "type": stop_type,
             "params": {"atr_len": 14, "mult": 2.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 20}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
    }


def test_dense_twin_reproduces_its_coarse_twin_exactly():
    """A shared grid point must give byte-identical trades, or the twins have
    drifted and every fingerprint argument in the spec is void."""
    from pipeline.screen import load_bars
    root = Path(__file__).resolve().parent.parent
    bars = {"BTCUSD": load_bars(root / "data", "BTCUSD", "9999-12-31")}
    coarse = run_spec(_spec("channel_breakout", "atr_stop"), bars)
    dense = run_spec(_spec("channel_breakout_dense", "atr_stop_dense"), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
```

If `load_bars` has a different signature, run `grep -n "def load_bars" -A 5 pipeline/screen.py` and adapt the call. Do not skip this test — it is the evidence for the aliasing claim.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: 4 passed.

- [ ] **Step 7: Verify preflight still returns zero conflicts against the live chain**

```bash
python -c "from pipeline.registry import Registry; from pipeline.composer import preflight_block_types; print(preflight_block_types(Registry('registry_log.jsonl')))"
```

Expected: `[]`. A non-empty list means a chained schema was mutated — revert and find what moved.

- [ ] **Step 8: Bump the hardcoded grammar-size assertions**

`BLOCK_TYPES` grows 15 → 19, and four existing tests assert the old count. All four must move to 19, each with a one-line comment saying why. There is repo precedent: one of them is still *named* `test_grammar_has_twelve_types_with_required_roles` while already asserting 15, from an earlier grammar growth.

- `pipeline/test_composer.py::test_grammar_has_twelve_types_with_required_roles` — `assert len(BLOCK_TYPES) == 15`
- `pipeline/test_composer.py::test_block_types_roundtrip` — `assert len(reg.block_types()) == 15`
- `pipeline/test_composer.py::test_run_registers_blocks_then_specs` — `assert len(reg.block_types()) == 15`
- `pipeline/test_gen2.py::test_grammar_has_fifteen_types` — `assert len(BLOCK_TYPES) == 15`

Bump the counts only. Do not rename the tests — the stale names are a separate cleanup and renaming them here would bury a real change inside a cosmetic one.

- [ ] **Step 9: Run the full scoped suite**

Run the scoped command from the header.
Expected: 371 baseline + 4 new = **375 passed**, zero failures.

**Known pre-existing flake, not caused by this task.** `pipeline/test_screen.py::test_serial_and_parallel_runs_produce_identical_verdicts` fails roughly 1 run in 5. `reader.build_card` (`pipeline/reader.py:157`) stamps `created_utc` from the wall clock at 1-second resolution; that timestamp is hashed into `card_id`, which flows into the content-addressed `strategy_id`; the test builds a separate registry per `workers` value and compares `strategy_id`s, so two iterations straddling a second boundary diverge. This is a defect in the test's assumption, not in production behaviour — two cards genuinely created a second apart *should* get different ids. This task makes the window marginally wider (19 block-type registrations per registry instead of 15, each doing a linear scan), but does not cause it. Do not fix it here: `test_screen.py` belongs to the concurrent session's fan-out work.

- [ ] **Step 10: Commit**

```bash
git add pipeline/blocks.py pipeline/engine.py pipeline/test_gen4.py pipeline/test_composer.py pipeline/test_gen2.py && git commit -m "feat(grammar): dense twin block types for plateau selection"
git show HEAD --stat
```

---

## Task 2: Only dense types may be swept; cap 25 to 60

**Files:**
- Modify: `pipeline/composer.py:34` (`SIBLING_CAP_DEFAULT`), `pipeline/composer.py:127-180` (`validate_family`)
- Test: `pipeline/test_gen4.py`

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_gen4.py`:

```python
from pipeline.composer import validate_family, SIBLING_CAP_DEFAULT, SWEEPABLE_TYPES


def _fam(sweep_type, sweep_param, values):
    return {
        "family": "t", "rationale": "t", "card_ids": [],
        "assets": ["BTCUSD"],
        "blocks": [
            {"role": "entry", "type": sweep_type,
             "params": {"lookback": 55, "direction": "both"}
             if "channel" in sweep_type else {"max_lookback": 60, "t_min": 2.0,
                                              "direction": "both"}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "sweep": [{"block": 0, "param": sweep_param, "values": values}],
    }


def test_sweeping_a_coarse_type_is_rejected():
    errs = validate_family(_fam("channel_breakout", "lookback", [20, 55]),
                           accepted_ids=set(), sibling_cap=60)
    assert any("not sweepable" in e for e in errs), errs


def test_sweeping_a_dense_type_is_accepted():
    errs = validate_family(_fam("channel_breakout_dense", "lookback", [35, 55, 75]),
                           accepted_ids=set(), sibling_cap=60)
    assert not [e for e in errs if "sweepable" in e], errs


def test_sweepable_set_is_exactly_the_dense_types():
    assert SWEEPABLE_TYPES == {("entry", "channel_breakout_dense"),
                               ("entry", "ma_cross_dense"),
                               ("entry", "trend_scan_dense"),
                               ("stop", "atr_stop_dense")}


def test_sibling_cap_is_sixty():
    assert SIBLING_CAP_DEFAULT == 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `ImportError: cannot import name 'SWEEPABLE_TYPES'`.

- [ ] **Step 3: Implement**

In `pipeline/composer.py`, change line 34:

```python
SIBLING_CAP_DEFAULT = 60
```

Add below it:

```python
# protocol-v4: a family may only sweep axes on a dense block type. Mixing a
# dense axis with a coarse one manufactures fake cliffs in plateau selection —
# channel_breakout lookback 55 -> 100 is a different strategy, not a
# perturbation. Coarse types stay usable at FIXED values.
SWEEPABLE_TYPES = {("entry", "channel_breakout_dense"),
                   ("entry", "ma_cross_dense"),
                   ("entry", "trend_scan_dense"),
                   ("stop", "atr_stop_dense")}
```

In `validate_family`, inside the existing `for ax in fam.get("sweep", []):` loop, after the block-index and param-name checks already there (so `key` is bound), add:

```python
        if key not in SWEEPABLE_TYPES:
            errors.append(
                f"{key[0]}/{key[1]} is not sweepable — protocol-v4 allows "
                f"sweep axes only on dense block types {sorted(SWEEPABLE_TYPES)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: 14 passed (10 from Task 1's hardened set, plus these 4).

- [ ] **Step 5: Fix any existing composer tests that sweep coarse types**

Run: `python -m pytest pipeline/test_composer.py -q`

Existing fixtures sweep coarse types and will now fail. For each failure, change the fixture's block type to the dense twin and its sweep values to dense-grid members. **Do not** relax the new rule to make old fixtures pass — the rule is the feature.

- [ ] **Step 6: Re-tighten the comments Task 1 softened**

Task 1 reworded two comments to say the sweepable-axis rule did not exist yet, because at that point it did not. It does now. Update both to describe the shipped behaviour:
- the dense-twins header comment in `pipeline/blocks.py`
- the module docstring in `pipeline/test_gen4.py`

- [ ] **Step 7: Run the full scoped suite**

Expected: **385 passed**, zero failures.

- [ ] **Step 8: Commit**

```bash
git add pipeline/composer.py pipeline/test_gen4.py pipeline/test_composer.py && git commit -m "feat(composer): only dense types are sweepable; sibling cap 60"
git show HEAD --stat
```

---

## Task 2c: The remaining four dense twins

**Files:**
- Modify: `pipeline/blocks.py`, `pipeline/engine.py`, `pipeline/composer.py` (`SWEEPABLE_TYPES`)
- Test: `pipeline/test_gen4.py`

**Why this task exists.** The spec claimed the first four dense twins "cover exactly the axes gen-1 through gen-3 actually swept." That claim was wrong. Enumerating every swept axis across all 12 chained families shows four non-risk axes with no twin: `r_multiple.r` (4 families), `vol_percentile.max_pctile` (2), `regime_ma_short.ma_len` (2), and `zscore_reversion.lookback`/`.z_entry` (1). Without twins for these, a v4 family cannot sweep target, filter, regime or mean-reversion geometry — and `tstat_trend_both_asymmetric_payoff`, one of the three strategies currently in quarantine, swept `r_multiple.r`. Coen chose to add all four (2026-08-18).

Risk axes (`fixed_fraction.f`, `vol_target.ann_vol`) stay twin-less deliberately — the spec excludes them from the plateau and handles sizing as labelled arms.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_gen4.py`. Extend the existing `TWINS` list to all eight pairs and `EXPECTED_BLOCK_TYPES` to 23 entries, then add:

```python
def test_zscore_dense_keeps_long_and_both_only():
    """The engine emits a zscore short ONLY when direction == 'both' (see the
    zscore branch in engine.entry_signals). A 'short' grid value would produce
    no signals at all rather than an error, so the dense twin must NOT offer
    one. Same reason channel_breakout_dense is long/both."""
    assert BLOCK_TYPES[("entry", "zscore_reversion_dense")]["direction"]["grid"] == ["long", "both"]


def test_r_multiple_dense_needs_no_engine_branch():
    """r_multiple is dispatched by ROLE (engine.simulate_asset reads
    by_role['target'][0]['params']['r']), not by type name, so the dense twin
    works with no engine change. Proven, not assumed."""
    coarse = _spec("channel_breakout_dense", {"lookback": 55, "direction": "both"})
    dense = _spec("channel_breakout_dense", {"lookback": 55, "direction": "both"})
    for b in coarse["blocks"]:
        if b["role"] == "target":
            b["type"] = "r_multiple"
    for b in dense["blocks"]:
        if b["role"] == "target":
            b["type"] = "r_multiple_dense"
    from pipeline.screen import load_bars
    root = Path(__file__).resolve().parent.parent
    bars = {"BTCUSD": load_bars(root / "data", "BTCUSD", "9999-12-31")}
    a, b = run_spec(coarse, bars), run_spec(dense, bars)
    assert len(a["trades"]) > 0
    assert a["trades"] == b["trades"]
```

`_spec` must be extended to accept target/exit blocks so the above can run; give it a `target_type="r_multiple"` parameter defaulting to the coarse type.

Add behavioural-equivalence tests for `zscore_reversion_dense`, `regime_ma_short_dense` and `vol_percentile_dense` against their coarse twins at shared grid points, each asserting a non-zero trade count.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `KeyError: ('target', 'r_multiple_dense')`.

- [ ] **Step 3: Add the four types to `BLOCK_TYPES`**

```python
    ("target", "r_multiple_dense"): {
        "r": {"type": "float", "grid": [1.0, 1.5, 2.0, 2.5, 3.0]},
    },
    ("filter", "vol_percentile_dense"): {
        "lookback": {"type": "int", "grid": [90, 120, 150, 180]},
        "max_pctile": {"type": "float", "grid": [0.6, 0.7, 0.8, 0.9, 1.0]},
    },
    ("regime", "regime_ma_short_dense"): {
        "ma_len": {"type": "int", "grid": [50, 100, 150, 200, 250]},
    },
    ("entry", "zscore_reversion_dense"): {
        "lookback": {"type": "int", "grid": [20, 40, 60, 75, 90]},
        "z_entry": {"type": "float", "grid": [1.5, 1.75, 2.0, 2.25, 2.5]},
        "direction": {"type": "str", "grid": ["long", "both"]},
    },
```

**Do not add `"short"` to the zscore direction grid.** The engine only emits a zscore short when `direction == "both"`; `"short"` would silently produce nothing.

- [ ] **Step 4: Wire THREE of them into the engine**

`r_multiple_dense` needs no engine change — targets are role-dispatched. The other three are type-dispatched and need their branch conditions widened:

| Site | From | To |
|---|---|---|
| `entry_signals`, ~line 124 | `elif block["type"] == "zscore_reversion":` | `elif block["type"] in ("zscore_reversion", "zscore_reversion_dense"):` |
| `gate_mask`, ~line 184 | `elif g["type"] == "regime_ma_short":` | `elif g["type"] in ("regime_ma_short", "regime_ma_short_dense"):` |
| `gate_mask`, ~line 189 | `elif g["type"] == "vol_percentile":` | `elif g["type"] in ("vol_percentile", "vol_percentile_dense"):` |

Verify the line text before editing; do not trust the numbers.

- [ ] **Step 5: Extend `SWEEPABLE_TYPES` to all eight**

```python
SWEEPABLE_TYPES = {("entry", "channel_breakout_dense"),
                   ("entry", "ma_cross_dense"),
                   ("entry", "trend_scan_dense"),
                   ("entry", "zscore_reversion_dense"),
                   ("stop", "atr_stop_dense"),
                   ("target", "r_multiple_dense"),
                   ("filter", "vol_percentile_dense"),
                   ("regime", "regime_ma_short_dense")}
```

Update `test_sweepable_set_is_exactly_the_dense_types` to match.

- [ ] **Step 6: Run tests, then the scoped suite**

Run: `python -m pytest pipeline/test_gen4.py -q`, then the scoped command.
Confirm `preflight_block_types(Registry('registry_log.jsonl'))` still returns `[]` — 23 in-code types against 15 chained, with none of the 15 mutated.

- [ ] **Step 7: Commit**

```bash
git add pipeline/blocks.py pipeline/engine.py pipeline/composer.py pipeline/test_gen4.py && git commit -m "feat(grammar): dense twins for target, filter, regime and zscore axes"
git show HEAD --stat
```

---

## Task 2b: Log the batch-gate drift

**Files:**
- Modify: `pipeline/composer.py`
- Test: `pipeline/test_gen4.py`

Spec decision D2: the drift between the approved dry-run batch and what the real run actually chains is currently invisible and is **mechanically observable**, so it gets logged. Gen-3's approved dry run was 20 specs; the real run chained 24, with a directional mirror dropped. Gen-1 was 33 → 22.

This is a file-based record, not a chain write. It is not a Bailey trial — nothing was scored — and the log must say so, so a later reader cannot mistake it for one.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_gen4.py`:

```python
from pipeline.composer import drift_record, drift_between


def test_drift_record_captures_what_a_run_emitted():
    rec = drift_record("2026-08-17-gen4", dry_run=True,
                       specs=[{"strategy_id": "a" * 16, "family": "f1"},
                              {"strategy_id": "b" * 16, "family": "f1"}])
    assert rec["run_id"] == "2026-08-17-gen4"
    assert rec["mode"] == "dry"
    assert rec["n_specs"] == 2
    assert rec["strategy_ids"] == ["a" * 16, "b" * 16]
    assert rec["families"] == ["f1"]


def test_drift_between_names_what_was_added_and_dropped():
    dry = drift_record("r", True, [{"strategy_id": "a" * 16, "family": "f1"},
                                   {"strategy_id": "b" * 16, "family": "f2"}])
    real = drift_record("r", False, [{"strategy_id": "a" * 16, "family": "f1"},
                                     {"strategy_id": "c" * 16, "family": "f3"}])
    d = drift_between(dry, real)
    assert d["dropped"] == ["b" * 16]
    assert d["added"] == ["c" * 16]
    assert d["dropped_families"] == ["f2"]
    assert d["n_dry"] == 2 and d["n_real"] == 2


def test_drift_is_not_a_trial_count_and_says_so():
    d = drift_between(drift_record("r", True, []), drift_record("r", False, []))
    assert "not a trial count" in d["note"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `ImportError: cannot import name 'drift_record'`.

- [ ] **Step 3: Implement**

Append to `pipeline/composer.py`:

```python
def drift_record(run_id: str, dry_run: bool, specs: list[dict]) -> dict:
    """What one Composer run emitted. Written for both the dry run and the real
    run so the gap between the batch Coen approved and the batch that got
    chained stops being invisible."""
    return {"run_id": run_id,
            "mode": "dry" if dry_run else "real",
            "n_specs": len(specs),
            "strategy_ids": [s["strategy_id"] for s in specs],
            "families": sorted({s["family"] for s in specs})}


def drift_between(dry: dict, real: dict) -> dict:
    """The batch-gate drift. NOT a multiple-testing trial count: nothing in a
    dry run was ever backtested, so none of it inflated any maximum Sharpe. It
    is recorded because it is a real, auditable record of search that the chain
    would otherwise lose."""
    d, r = set(dry["strategy_ids"]), set(real["strategy_ids"])
    return {"run_id": real["run_id"],
            "n_dry": dry["n_specs"], "n_real": real["n_specs"],
            "dropped": sorted(d - r), "added": sorted(r - d),
            "dropped_families": sorted(set(dry["families"]) - set(real["families"])),
            "added_families": sorted(set(real["families"]) - set(dry["families"])),
            "note": "batch-gate drift; not a trial count — dry-run specs were "
                    "never scored, so they inflated no maximum"}
```

Then persist it. In `composer.main()`, after the specs for the run are known and before it returns, append one line to `logs/batch_drift.jsonl`:

```python
    drift_path = Path(__file__).resolve().parent.parent / "logs" / "batch_drift.jsonl"
    drift_path.parent.mkdir(parents=True, exist_ok=True)
    with drift_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(drift_record(args.run_id, args.dry_run, specs),
                           sort_keys=True) + "\n")
```

Use whatever the run-id argument is actually called in `main()` — run `grep -n "run_id" pipeline/composer.py` and match it rather than assuming `args.run_id` exists.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: 11 passed.

- [ ] **Step 5: Run the full scoped suite**

Expected: **382 passed**, zero failures.

- [ ] **Step 6: Commit**

```bash
git add pipeline/composer.py pipeline/test_gen4.py && git commit -m "feat(composer): log batch-gate drift between dry and real runs"
git show HEAD --stat
```

---

## Task 3: CSCV probability of backtest overfitting

**Files:**
- Create: `pipeline/pbo.py`
- Test: `pipeline/test_pbo.py` (create)

**Performance matters here and the naive implementation is unusable.** C(16,8) = 12,870 combinations; recomputing a Sharpe over ~1,160 observations per config per combination is hundreds of millions of operations in pure Python. Instead precompute `(n, sum, sumsq)` **per block per config** once, then a subset Sharpe is an addition of 8 triples. That is ~2.6M additions for 25 configs and runs in under a second.

- [ ] **Step 1: Write the failing tests**

Create `pipeline/test_pbo.py`:

```python
"""CSCV / PBO tests."""
import math
import random

import pytest

from pipeline.pbo import block_stats, cscv_pbo


def test_block_stats_drops_the_remainder_so_blocks_are_equal():
    stats = block_stats(list(range(10)), s=3)
    assert len(stats) == 3
    assert [n for n, _, _ in stats] == [3, 3, 3]
    assert stats[0] == (3, 0 + 1 + 2, 0 + 1 + 4)


def test_dominant_config_gives_low_pbo():
    """A config that is genuinely better everywhere should rarely rank in the
    bottom half out of sample."""
    rng = random.Random(7)
    series = {f"noise{i}": [rng.gauss(0, 0.01) for _ in range(320)]
              for i in range(7)}
    series["real"] = [rng.gauss(0.004, 0.01) for _ in range(320)]
    out = cscv_pbo(series, s=8)
    assert out["pbo"] < 0.2, out


def test_pure_noise_averages_to_pbo_one_half():
    """With no real edge the in-sample winner is a coin flip out of sample.

    Asserted on the MEAN over 30 independent draws rather than a single one.
    At 8 configs and s=8 a single PBO estimate has sd ~0.23 — measured: a
    200-seed sweep gives mean 0.4946 with 65 of 200 outside (0.25, 0.75) — so
    a single-seed assertion is either flaky or pinned to a hand-picked seed
    and proves nothing about the estimator. The mean of 30 draws has sd ~0.042
    and does test unbiasedness. Measured: seeds 0-29 give 0.5257 in 0.25s, and
    an adversarial block-alternating matrix gives 0.7276, which this band
    rejects — so it fails when it should.
    """
    vals = []
    for seed in range(30):
        rng = random.Random(seed)
        series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(320)]
                  for i in range(8)}
        vals.append(cscv_pbo(series, s=8)["pbo"])
    mean = sum(vals) / len(vals)
    assert 0.40 < mean < 0.60, f"mean PBO {mean:.3f} over {len(vals)} draws"


def test_is_deterministic():
    rng = random.Random(3)
    series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(160)]
              for i in range(5)}
    assert cscv_pbo(series, s=6) == cscv_pbo(series, s=6)


def test_order_of_ids_does_not_change_the_answer():
    rng = random.Random(5)
    series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(160)]
              for i in range(5)}
    reversed_series = dict(reversed(list(series.items())))
    assert cscv_pbo(series, s=6)["pbo"] == cscv_pbo(reversed_series, s=6)["pbo"]


def test_ragged_series_refuse_rather_than_silently_misalign():
    with pytest.raises(ValueError, match="ragged"):
        cscv_pbo({"a": [0.1] * 100, "b": [0.1] * 99}, s=4)


def test_fewer_than_two_configs_is_uncomputable_not_zero():
    out = cscv_pbo({"only": [0.01] * 100}, s=4)
    assert out["pbo"] is None
    assert out["reason"] == "needs at least 2 configs"


def test_reports_its_own_shape():
    rng = random.Random(1)
    series = {f"n{i}": [rng.gauss(0, 0.01) for _ in range(160)]
              for i in range(4)}
    out = cscv_pbo(series, s=8)
    assert out["n_configs"] == 4
    assert out["s"] == 8
    assert out["n_combinations"] == 70   # C(8,4)
    assert out["block_size"] == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_pbo.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.pbo'`.

- [ ] **Step 3: Implement**

Create `pipeline/pbo.py`:

```python
"""CSCV: the probability of backtest overfitting.

Bailey, Borwein, Lopez de Prado & Zhu, J. Computational Finance 2017
(SSRN 2326253). The trials x time performance matrix is split into S equal
contiguous blocks; every way of choosing S/2 blocks as in-sample gives one
observation of where the in-sample winner ranks out of sample. PBO is the
share of those observations where the winner lands in the bottom half.

Blocks are treated as exchangeable — that is the method, not an oversight.

Performance note: subset Sharpes are reconstructed from per-block
(count, sum, sum of squares) so a combination costs S/2 additions per config
instead of a full pass over the observations. Without this, C(16,8) = 12,870
combinations is unusably slow in pure Python.
"""
from __future__ import annotations

import math
from itertools import combinations


def block_stats(series: list[float], s: int) -> list[tuple[int, float, float]]:
    """Per-block (count, sum, sum of squares). The trailing remainder is
    dropped so every block is the same length, as CSCV requires."""
    size = len(series) // s
    out = []
    for b in range(s):
        chunk = series[b * size:(b + 1) * size]
        out.append((len(chunk), sum(chunk), sum(x * x for x in chunk)))
    return out


def _sharpe_from(n: int, total: float, total_sq: float) -> float:
    """Sharpe of a block subset from its pooled moments. Degenerate subsets
    (fewer than 2 points, or zero variance) sort last rather than raising: a
    flat curve is a real outcome for a config that never traded."""
    if n < 2:
        return -math.inf
    mean = total / n
    var = (total_sq - total * total / n) / (n - 1)
    if var <= 0:
        return -math.inf
    return mean / math.sqrt(var)


def cscv_pbo(perf_by_id: dict[str, list[float]], s: int = 16) -> dict:
    """PBO over a {config_id: return series} matrix.

    Every series must share one calendar; ragged input is refused rather than
    silently misaligned (the same failure mode gauntlet.check_aligned guards).
    """
    ids = sorted(perf_by_id)
    lengths = {i: len(perf_by_id[i]) for i in ids}
    if len(set(lengths.values())) > 1:
        raise ValueError(
            "cannot compute PBO on ragged series: every config must share one "
            "calendar, got " + ", ".join(f"{i}={n}" for i, n in sorted(lengths.items())))
    n_configs = len(ids)
    size = (lengths[ids[0]] // s) if ids else 0
    shape = {"n_configs": n_configs, "s": s, "block_size": size,
             "n_combinations": 0}
    if n_configs < 2:
        return {"pbo": None, "reason": "needs at least 2 configs", **shape}
    if size < 2:
        return {"pbo": None, "reason": f"blocks of {size} are too short", **shape}

    stats = {i: block_stats(perf_by_id[i], s) for i in ids}
    blocks = range(s)
    half = s // 2
    below = 0
    total = 0
    for is_blocks in combinations(blocks, half):
        oos_blocks = [b for b in blocks if b not in is_blocks]
        is_sr, oos_sr = {}, {}
        for i in ids:
            st = stats[i]
            n = sum(st[b][0] for b in is_blocks)
            t = sum(st[b][1] for b in is_blocks)
            q = sum(st[b][2] for b in is_blocks)
            is_sr[i] = _sharpe_from(n, t, q)
            n = sum(st[b][0] for b in oos_blocks)
            t = sum(st[b][1] for b in oos_blocks)
            q = sum(st[b][2] for b in oos_blocks)
            oos_sr[i] = _sharpe_from(n, t, q)
        # in-sample winner; ties break on id so the result is reproducible
        winner = max(ids, key=lambda i: (is_sr[i], i))
        # ascending rank out of sample: 1 = worst, n_configs = best
        ranked = sorted(ids, key=lambda i: (oos_sr[i], i))
        rank = ranked.index(winner) + 1
        omega = rank / (n_configs + 1)
        # lambda = logit(omega); lambda <= 0 iff omega <= 0.5
        if omega <= 0.5:
            below += 1
        total += 1
    shape["n_combinations"] = total
    return {"pbo": below / total, "reason": None, **shape}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_pbo.py -q`
Expected: 8 passed, in under 5 seconds.

If `test_pure_noise_gives_pbo_near_one_half` fails, do **not** widen the band to make it pass. A noise matrix that does not produce PBO near 0.5 means the ranking or the winner selection is wrong — debug it.

- [ ] **Step 5: Sanity-check the real cost at S=16**

```bash
python -c "
import random, time
from pipeline.pbo import cscv_pbo
rng = random.Random(0)
series = {f'c{i}': [rng.gauss(0,0.01) for _ in range(2328)] for i in range(25)}
t = time.time(); out = cscv_pbo(series, s=16); print(out['pbo'], out['n_combinations'], f'{time.time()-t:.1f}s')
"
```

Expected: 12870 combinations, well under 60 seconds. If it takes minutes, the block-stats precomputation was not used — fix it before continuing.

- [ ] **Step 6: Commit**

```bash
git add pipeline/pbo.py pipeline/test_pbo.py && git commit -m "feat(pbo): CSCV probability of backtest overfitting"
git show HEAD --stat
```

---

## Task 4: Plateau qualification and neighbourhood selection

**Files:**
- Create: `pipeline/plateau.py`
- Test: `pipeline/test_plateau.py` (create)

This is the task that removes point-winner selection. The sibling contract is a plain dict so the module stays pure and testable without a registry:

```python
{"sid": str,
 "axes": dict[str, object],        # dense geometry axis name -> value
 "score": float | None,            # train-window annualized Sharpe
 "screen_trade_count_fail": bool,
 "gauntlet_passed": bool}
```

- [ ] **Step 1: Write the failing tests**

Create `pipeline/test_plateau.py`:

```python
"""Plateau qualification and neighbourhood selection."""
from pipeline.plateau import (annualized_sharpe, neighbours_of, plateau_members,
                              qualifies, select_survivor)

GRIDS = {"lookback": [20, 35, 55, 75, 100]}


def sib(sid, lookback, score, gauntlet_passed=True, tc_fail=False):
    return {"sid": sid, "axes": {"lookback": lookback}, "score": score,
            "screen_trade_count_fail": tc_fail,
            "gauntlet_passed": gauntlet_passed}


def test_annualized_sharpe_of_a_flat_curve_is_none():
    curve = [("2020-01-01", 1.0), ("2020-01-02", 1.0), ("2020-01-03", 1.0)]
    assert annualized_sharpe(curve) is None


def test_annualized_sharpe_uses_365_day_scaling():
    curve = [("2020-01-01", 1.0)]
    v = 1.0
    for i in range(400):
        v *= 1.001 if i % 2 == 0 else 0.9995
        curve.append((f"d{i}", v))
    sr = annualized_sharpe(curve)
    assert sr is not None and sr > 0


def test_neighbours_are_one_grid_step_on_exactly_one_axis():
    fam = [sib("a", 35, 1.0), sib("b", 55, 1.0), sib("c", 75, 1.0),
           sib("d", 100, 1.0)]
    got = {n["sid"] for n in neighbours_of(fam[1], fam, GRIDS)}
    assert got == {"a", "c"}


def test_absent_neighbours_are_simply_not_returned():
    fam = [sib("b", 55, 1.0), sib("d", 100, 1.0)]
    assert neighbours_of(fam[0], fam, GRIDS) == []


def test_plateau_is_ninety_percent_of_the_family_best():
    fam = [sib("a", 35, 1.00), sib("b", 55, 0.95), sib("c", 75, 0.80)]
    assert plateau_members(fam) == {"a", "b"}


def test_a_trade_count_neighbour_is_a_cliff():
    fam = [sib("a", 35, 1.0, gauntlet_passed=False, tc_fail=True),
           sib("b", 55, 1.0), sib("c", 75, 1.0)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "cliff_trade_count"


def test_a_neighbour_below_plateau_disqualifies():
    fam = [sib("a", 35, 0.50), sib("b", 55, 1.00), sib("c", 75, 1.00)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "neighbour_below_plateau"


def test_a_candidate_below_plateau_disqualifies_itself():
    fam = [sib("a", 35, 1.00), sib("b", 55, 0.50), sib("c", 75, 1.00)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "below_plateau"


def test_selection_prefers_the_best_worst_neighbour_not_the_point_winner():
    """'b' is the point winner but sits beside a weak neighbour; 'c' scores
    lower yet its whole neighbourhood is strong. Plateau selection takes 'c'.

    Floors: a=0.92, b=0.92, c=0.985, d=0.93, e=0.93 — 'c' wins outright, so
    the test does not lean on any tie-break. An earlier version of this
    fixture scored d and e at 0.98, which produced a three-way c/d/e tie at
    0.98 and asserted 'd', a value no floor-based rule can return.
    """
    fam = [sib("a", 20, 0.92), sib("b", 35, 1.00), sib("c", 55, 0.99),
           sib("d", 75, 0.985), sib("e", 100, 0.93)]
    winner, detail = select_survivor(fam, GRIDS)
    assert winner == "c", detail
    assert max(fam, key=lambda s: s["score"])["sid"] == "b"


def test_tie_break_picks_the_smallest_sid_even_at_differing_lengths():
    """Regression: the original tie-break key `[-ord(c) for c in sid]` compared
    LISTS, so "aa" -> [-97,-97] sorts below "aab" -> [-97,-97,-98] and max()
    returned the LONGER sid rather than the smallest. Every other fixture uses
    one-character sids, which is why nothing caught it; real strategy ids are
    16 hex characters.
    """
    fam = [sib("aab", 35, 1.0), sib("aa", 55, 1.0), sib("b", 75, 1.0)]
    winner, _ = select_survivor(fam, GRIDS)
    assert winner == "aa"


def test_only_gauntlet_passers_can_be_selected():
    fam = [sib("a", 35, 1.00, gauntlet_passed=False),
           sib("b", 55, 1.00, gauntlet_passed=False),
           sib("c", 75, 1.00, gauntlet_passed=False)]
    winner, detail = select_survivor(fam, GRIDS)
    assert winner is None


def test_ties_break_lexicographically_on_sid():
    fam = [sib("zz", 35, 1.0), sib("aa", 55, 1.0), sib("mm", 75, 1.0)]
    winner, _ = select_survivor(fam, GRIDS)
    assert winner == "aa"


def test_selection_is_order_independent():
    fam = [sib("a", 20, 0.92), sib("b", 35, 1.00), sib("c", 55, 0.99),
           sib("d", 75, 0.98), sib("e", 100, 0.98)]
    assert select_survivor(fam, GRIDS)[0] == select_survivor(list(reversed(fam)), GRIDS)[0]


def test_a_scoreless_sibling_is_below_plateau_but_not_a_cliff():
    fam = [sib("a", 35, None), sib("b", 55, 1.0), sib("c", 75, 1.0)]
    ok, reason = qualifies(fam[1], fam, GRIDS)
    assert ok is False and reason == "neighbour_below_plateau"


def test_a_family_with_no_swept_axis_fails_rather_than_passing_vacuously():
    """The bypass this guard exists to close: empty grids give no neighbours,
    so every other clause would pass and an unswept family would clear a
    robustness gate on no evidence at all."""
    fam = [sib("solo", 55, 1.0)]
    ok, reason = qualifies(fam[0], fam, {})
    assert ok is False and reason == "no_swept_axis"


def test_an_unswept_family_selects_nobody():
    fam = [sib("solo", 55, 1.0)]
    winner, detail = select_survivor(fam, {})
    assert winner is None
    assert detail["solo"]["reason"] == "no_swept_axis"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_plateau.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.plateau'`.

- [ ] **Step 3: Implement**

Create `pipeline/plateau.py`:

```python
"""Neighbourhood/plateau selection — protocol-v4.

Replaces point-winner selection. The SOP forbids choosing the best single
configuration and requires selection by neighbourhood quality: a candidate is
only eligible if it AND every one of its one-step neighbours sit on the
plateau, and among eligible candidates the winner is the one whose worst
neighbour is strongest.

A neighbour that died at the screen on trade_count is a hard cliff. Turnover is
a structural property of a configuration, not a noisy metric — a 24-trade
sibling can post a flattering per-trade Sharpe while being untradeable.

Every function here is pure. The registry and the artifacts are read by the
caller (gauntlet.py / diagnose_protocol_v4.py), never here.
"""
from __future__ import annotations

import math

PLATEAU_RATIO = 0.9
TRADING_DAYS = 365   # crypto trades every day; matches the rest of the pipeline


def annualized_sharpe(equity: list[tuple[str, float]]) -> float | None:
    """Annualized Sharpe of a daily equity curve. None when it cannot be
    computed (too few steps, or no variance) — never a fabricated 0.0."""
    rets = [equity[i][1] / equity[i - 1][1] - 1
            for i in range(1, len(equity)) if equity[i - 1][1] > 0]
    if len(rets) < 30:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)


def neighbours_of(sibling: dict, family: list[dict],
                  grids: dict[str, list]) -> list[dict]:
    """Siblings differing from this one by exactly one grid step on exactly one
    axis. Absent grid points simply have no neighbour there."""
    out = []
    for axis, values in grids.items():
        if axis not in sibling["axes"]:
            continue
        here = values.index(sibling["axes"][axis])
        for step in (-1, 1):
            j = here + step
            if not 0 <= j < len(values):
                continue
            want = values[j]
            for other in family:
                if other["sid"] == sibling["sid"]:
                    continue
                if other["axes"].get(axis) != want:
                    continue
                if all(other["axes"].get(a) == v
                       for a, v in sibling["axes"].items() if a != axis):
                    out.append(other)
    return sorted(out, key=lambda s: s["sid"])


def plateau_members(family: list[dict],
                    ratio: float = PLATEAU_RATIO) -> set[str]:
    """Every sibling scoring at least `ratio` of the family's best score.
    A sibling with no computable score is never on the plateau."""
    scored = [s["score"] for s in family if s["score"] is not None]
    if not scored:
        return set()
    best = max(scored)
    if best <= 0:
        return set()
    return {s["sid"] for s in family
            if s["score"] is not None and s["score"] >= ratio * best}


def qualifies(sibling: dict, family: list[dict], grids: dict[str, list],
              ratio: float = PLATEAU_RATIO) -> tuple[bool, str | None]:
    """Plateau qualification. Returns (ok, reason_when_not).

    A family with no swept dense axis FAILS. Without a neighbourhood there is
    no robustness evidence, and every other clause here would pass vacuously:
    empty grids give no neighbours, no neighbours give nothing to fail on, and
    a lone sibling is trivially >= 0.9 of its own score. A gate that passes on
    the absence of evidence is not a gate.
    """
    if not grids:
        return False, "no_swept_axis"
    plat = plateau_members(family, ratio)
    if sibling["sid"] not in plat:
        return False, "below_plateau"
    nbrs = neighbours_of(sibling, family, grids)
    if any(n["screen_trade_count_fail"] for n in nbrs):
        return False, "cliff_trade_count"
    if any(n["sid"] not in plat for n in nbrs):
        return False, "neighbour_below_plateau"
    return True, None


def neighbourhood_floor(sibling: dict, family: list[dict],
                        grids: dict[str, list]) -> float:
    """The worst score across the candidate and its neighbours. This is the
    selection currency — never the candidate's own score alone."""
    scores = [sibling["score"]]
    scores += [n["score"] for n in neighbours_of(sibling, family, grids)]
    return min((s for s in scores if s is not None), default=-math.inf)


def select_survivor(family: list[dict], grids: dict[str, list],
                    ratio: float = PLATEAU_RATIO) -> tuple[str | None, dict]:
    """Pick the sibling with the strongest neighbourhood floor among
    gauntlet-passing, plateau-qualifying candidates. Ties break on sid."""
    detail = {}
    eligible = []
    for s in sorted(family, key=lambda x: x["sid"]):
        ok, reason = qualifies(s, family, grids, ratio)
        detail[s["sid"]] = {"qualifies": ok, "reason": reason,
                            "floor": neighbourhood_floor(s, family, grids),
                            "gauntlet_passed": s["gauntlet_passed"]}
        if ok and s["gauntlet_passed"]:
            eligible.append(s)
    if not eligible:
        return None, detail
    # Descending floor, then ascending sid. NOT max() with an ord-based key:
    # that compares lists, so "aa" -> [-97,-97] ranks below "aab" ->
    # [-97,-97,-98] and the LONGER sid wins. Strategy ids are 16 hex chars.
    winner = sorted(eligible,
                    key=lambda s: (-neighbourhood_floor(s, family, grids),
                                   s["sid"]))[0]
    return winner["sid"], detail
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_plateau.py -q`
Expected: 13 passed.

The tie-break key `[-ord(c) for c in s["sid"]]` inverts the string comparison so that `max` picks the lexicographically **smallest** sid, matching the existing `select_survivors` convention. If `test_ties_break_lexicographically_on_sid` fails, that is the line to fix — do not change the test.

- [ ] **Step 5: Commit**

```bash
git add pipeline/plateau.py pipeline/test_plateau.py && git commit -m "feat(plateau): neighbourhood qualification and selection"
git show HEAD --stat
```

---

## Task 5: Harvey-Liu haircut

**Files:**
- Modify: `pipeline/stats.py` (append)
- Test: `pipeline/test_gen4.py`

Recorded in the verdict, not a gate. Harvey & Liu present three multiple-testing adjustments; this uses Bonferroni, the most conservative. Because the number is reported rather than gating, conservatism costs nothing and needs no calibration argument.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_gen4.py`:

```python
from pipeline.stats import harvey_liu_haircut


def test_haircut_is_nonlinear_smaller_for_stronger_sharpes():
    weak = harvey_liu_haircut(0.6, t_years=6.4, n_trials=4)
    strong = harvey_liu_haircut(1.7, t_years=6.4, n_trials=4)
    assert strong["haircut_pct"] < weak["haircut_pct"]
    assert 0.0 <= strong["haircut_pct"] <= 100.0


def test_more_trials_means_a_bigger_haircut():
    few = harvey_liu_haircut(1.3, t_years=6.4, n_trials=4)
    many = harvey_liu_haircut(1.3, t_years=6.4, n_trials=80)
    assert many["haircut_pct"] > few["haircut_pct"]


def test_a_nonpositive_sharpe_is_fully_haircut_not_negative():
    out = harvey_liu_haircut(-0.4, t_years=6.4, n_trials=4)
    assert out["sr_haircut"] == 0.0
    assert out["haircut_pct"] == 100.0


def test_haircut_states_its_method():
    assert harvey_liu_haircut(1.0, 6.4, 4)["method"] == "bonferroni"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `ImportError: cannot import name 'harvey_liu_haircut'`.

- [ ] **Step 3: Implement**

Append to `pipeline/stats.py`:

```python
def harvey_liu_haircut(sr_annual: float, t_years: float,
                       n_trials: int) -> dict:
    """Multiple-testing haircut on an annualized Sharpe (Harvey & Liu, SSRN
    2345489). Nonlinear by construction — a strong Sharpe loses proportionally
    less than a weak one, which is why the SOP forbids a flat 50% haircut.

    Harvey & Liu give three adjustments; this uses Bonferroni, the most
    conservative. The haircut is RECORDED in the verdict, never gated on, so
    erring conservative costs nothing.
    """
    if sr_annual <= 0 or t_years <= 0:
        return {"sr_observed": sr_annual, "sr_haircut": 0.0,
                "haircut_pct": 100.0, "p_raw": None, "p_adjusted": None,
                "method": "bonferroni"}
    t_stat = sr_annual * math.sqrt(t_years)
    p_raw = 2.0 * (1.0 - normal_cdf(t_stat))
    p_adj = min(1.0, p_raw * max(1, n_trials))
    if p_adj >= 1.0:
        sr_haircut = 0.0
    else:
        t_adj = inv_normal_cdf(1.0 - p_adj / 2.0)
        sr_haircut = max(0.0, t_adj / math.sqrt(t_years))
    return {"sr_observed": sr_annual, "sr_haircut": sr_haircut,
            "haircut_pct": 100.0 * (1.0 - sr_haircut / sr_annual),
            "p_raw": p_raw, "p_adjusted": p_adj, "method": "bonferroni"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/stats.py pipeline/test_gen4.py && git commit -m "feat(stats): Harvey-Liu multiple-testing haircut"
git show HEAD --stat
```

---

## Task 6: Purged walk-forward folds

**Files:**
- Create: `pipeline/walkforward.py`
- Test: `pipeline/test_gen4.py`

**Recorded, not gating — and this diverges from the SOP.** The SOP's Phase 5 checklist treats "purged WF majority pass + no catastrophic veto" as a designation requirement. The approved spec places it among recorded numbers. Compute both flags and record them; promoting them to gates later is a one-line addition to `FAIL_ORDER`. This divergence is flagged for Coen in the handoff and must not be silently resolved either way.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_gen4.py`:

```python
from pipeline.walkforward import purged_folds, walkforward_report

PURGE = 200


def _trades(dates_and_returns):
    return [{"entry_date": d, "return_net": r, "notional_frac": 1.0}
            for d, r in dates_and_returns]


def test_three_folds_cover_the_window_without_overlapping():
    dates = [f"2020-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 15)]
    folds = purged_folds(dates, n_folds=3, purge_bars=2)
    assert len(folds) == 3
    spans = [set(f["test"]) for f in folds]
    assert spans[0] & spans[1] == set()
    assert spans[1] & spans[2] == set()


def test_purge_gap_removes_train_bars_adjacent_to_the_test_slice():
    dates = [f"d{i:03d}" for i in range(90)]
    folds = purged_folds(dates, n_folds=3, purge_bars=5)
    mid = folds[1]
    assert set(mid["train"]) & set(mid["test"]) == set()
    first_test = dates.index(mid["test"][0])
    for d in dates[max(0, first_test - 5):first_test]:
        assert d not in mid["train"]


def test_majority_pass_needs_two_of_three_positive_folds():
    dates = [f"d{i:03d}" for i in range(90)]
    trades = _trades([(d, 0.01 if i < 60 else -0.01)
                      for i, d in enumerate(dates)])
    rep = walkforward_report(trades, dates, n_folds=3, purge_bars=5)
    assert rep["folds_positive"] == 2
    assert rep["majority_pass"] is True


def test_a_fold_breaching_the_ruin_level_is_catastrophic():
    dates = [f"d{i:03d}" for i in range(90)]
    trades = _trades([(d, 0.001 if i < 60 else -0.30)
                      for i, d in enumerate(dates)])
    rep = walkforward_report(trades, dates, n_folds=3, purge_bars=5)
    assert rep["catastrophic"] is True


def test_report_records_every_fold_not_just_the_verdict():
    dates = [f"d{i:03d}" for i in range(90)]
    trades = _trades([(d, 0.01) for d in dates])
    rep = walkforward_report(trades, dates, n_folds=3, purge_bars=5)
    assert len(rep["folds"]) == 3
    assert all("net" in f and "min_equity" in f for f in rep["folds"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.walkforward'`.

- [ ] **Step 3: Implement**

Create `pipeline/walkforward.py`:

```python
"""Purged walk-forward, used as CORROBORATION only.

The literature this pipeline follows is explicit that walk-forward is the worst
out-of-sample scheme for preventing false discoveries and that CSCV is the
primary tool (see pipeline/pbo.py). It is computed here because the SOP asks
for it as a corroborating view, never as a selector.

Purging removes train bars adjacent to each test slice so an indicator's
lookback cannot straddle the boundary. The gap must be at least the longest
lookback in the grammar, which is 200 (ma_cross.slow, regime_ma.ma_len).
"""
from __future__ import annotations

RUIN_LEVEL = 0.5   # same constant the gauntlet's p_ruin gate uses


def purged_folds(dates: list[str], n_folds: int,
                 purge_bars: int) -> list[dict]:
    """Contiguous test slices with a purge gap carved out of train on both
    sides. Returns [{'test': [...], 'train': [...]}]."""
    n = len(dates)
    size = n // n_folds
    folds = []
    for k in range(n_folds):
        lo = k * size
        hi = n if k == n_folds - 1 else (k + 1) * size
        test = dates[lo:hi]
        keep_lo = max(0, lo - purge_bars)
        keep_hi = min(n, hi + purge_bars)
        train = dates[:keep_lo] + dates[keep_hi:]
        folds.append({"test": test, "train": train})
    return folds


def walkforward_report(trades: list[dict], dates: list[str], n_folds: int = 3,
                       purge_bars: int = 200) -> dict:
    """Per-fold net contribution and worst equity, plus the SOP's two summary
    flags. Both flags are RECORDED; neither gates under protocol-v4."""
    folds = purged_folds(dates, n_folds, purge_bars)
    out = []
    catastrophic = False
    for f in folds:
        window = set(f["test"])
        picked = [t for t in trades if t["entry_date"] in window]
        equity, min_equity, net = 1.0, 1.0, 0.0
        for t in picked:
            c = t["return_net"] * t.get("notional_frac", 1.0)
            net += c
            equity *= (1.0 + c)
            min_equity = min(min_equity, equity)
        if min_equity < RUIN_LEVEL:
            catastrophic = True
        out.append({"n_trades": len(picked), "net": net,
                    "min_equity": min_equity})
    positive = sum(1 for f in out if f["net"] > 0)
    return {"folds": out, "folds_positive": positive,
            "majority_pass": positive >= (n_folds // 2 + 1),
            "catastrophic": catastrophic,
            "purge_bars": purge_bars, "n_folds": n_folds}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/walkforward.py pipeline/test_gen4.py && git commit -m "feat(walkforward): purged folds recorded as corroboration"
git show HEAD --stat
```

---

## Task 7: Regime ruler and conditional split

**Files:**
- Create: `pipeline/regime.py`
- Test: `pipeline/test_gen4.py`

One ruler is declared for the whole protocol: BTC close versus its 200-day moving average, with a chop band on the spread. Recorded, not gating.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_gen4.py`:

```python
from pipeline.regime import regime_by_date, regime_split, CHOP_BAND


def _bars(closes):
    return [{"date": f"d{i:04d}", "close": c} for i, c in enumerate(closes)]


def test_a_strong_uptrend_labels_trend_up():
    bars = _bars([100.0] * 200 + [180.0] * 10)
    labels = regime_by_date(bars, ma_len=200)
    assert labels["d0205"] == "trend_up"


def test_a_strong_downtrend_labels_trend_down():
    bars = _bars([100.0] * 200 + [40.0] * 10)
    labels = regime_by_date(bars, ma_len=200)
    assert labels["d0205"] == "trend_down"


def test_price_near_the_average_labels_chop():
    bars = _bars([100.0] * 210)
    labels = regime_by_date(bars, ma_len=200)
    assert labels["d0205"] == "chop"


def test_bars_before_the_average_exists_are_unlabelled():
    bars = _bars([100.0] * 210)
    labels = regime_by_date(bars, ma_len=200)
    assert "d0100" not in labels


def test_chop_band_is_five_percent():
    assert CHOP_BAND == 0.05


def test_split_buckets_trades_and_reports_counts_and_net():
    labels = {"d0201": "trend_up", "d0202": "chop", "d0203": "trend_up"}
    trades = [{"entry_date": "d0201", "return_net": 0.10, "notional_frac": 1.0},
              {"entry_date": "d0202", "return_net": -0.04, "notional_frac": 1.0},
              {"entry_date": "d0203", "return_net": 0.02, "notional_frac": 1.0}]
    out = regime_split(trades, labels)
    assert out["trend_up"]["n"] == 2
    assert abs(out["trend_up"]["net"] - 0.12) < 1e-12
    assert out["chop"]["n"] == 1
    assert out["trend_down"]["n"] == 0


def test_trades_outside_the_labelled_window_are_counted_as_unlabelled():
    labels = {"d0201": "trend_up"}
    trades = [{"entry_date": "d0001", "return_net": 0.1, "notional_frac": 1.0}]
    out = regime_split(trades, labels)
    assert out["unlabelled"]["n"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.regime'`.

- [ ] **Step 3: Implement**

Create `pipeline/regime.py`:

```python
"""The protocol's single declared regime ruler.

One ruler for the whole protocol, declared before any verdict, so a
regime-conditional report means the same thing across every strategy: BTC close
versus its 200-day moving average, with a band around the average that counts
as chop rather than a weak trend.

Recorded, not gating. The SOP asks for the split so that a strategy whose
losses cluster in one regime is visible at the gate.
"""
from __future__ import annotations

CHOP_BAND = 0.05
BUCKETS = ("trend_up", "trend_down", "chop", "unlabelled")


def regime_by_date(bars: list[dict], ma_len: int = 200) -> dict[str, str]:
    """date -> regime label. Bars before the average exists are omitted
    entirely rather than given a fabricated label."""
    labels = {}
    closes = [b["close"] for b in bars]
    running = 0.0
    for i, b in enumerate(bars):
        running += closes[i]
        if i >= ma_len:
            running -= closes[i - ma_len]
        if i < ma_len - 1:
            continue
        ma = running / ma_len
        if ma <= 0:
            continue
        spread = (closes[i] - ma) / ma
        if abs(spread) <= CHOP_BAND:
            labels[b["date"]] = "chop"
        else:
            labels[b["date"]] = "trend_up" if spread > 0 else "trend_down"
    return labels


def regime_split(trades: list[dict], labels: dict[str, str]) -> dict:
    """Per-bucket trade count, net contribution and win rate."""
    out = {b: {"n": 0, "net": 0.0, "wins": 0} for b in BUCKETS}
    for t in trades:
        bucket = labels.get(t["entry_date"], "unlabelled")
        c = t["return_net"] * t.get("notional_frac", 1.0)
        out[bucket]["n"] += 1
        out[bucket]["net"] += c
        out[bucket]["wins"] += 1 if c > 0 else 0
    for b in out.values():
        b["win_rate"] = (b["wins"] / b["n"]) if b["n"] else None
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: 27 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/regime.py pipeline/test_gen4.py && git commit -m "feat(regime): declared ruler and conditional trade split"
git show HEAD --stat
```

---

## Task 8: Wire protocol-v4 into the gauntlet

**Files:**
- Modify: `pipeline/gauntlet.py:28` (`PROTOCOL`), `:48` (`FAIL_ORDER`), `:85-176` (`evaluate_spec`), `:178-191` (`select_survivors`), `:280-400` (`main`)
- Test: `pipeline/test_gauntlet.py`, `pipeline/test_gen4.py`

This is the largest task and the one where a mistake is most expensive. Work through it in order and run the suite between sub-steps.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/test_gen4.py`:

```python
from pipeline.gauntlet import FAIL_ORDER, PROTOCOL, SR_FLOOR, PBO_PASS, PBO_KILL


def test_protocol_is_v4():
    assert PROTOCOL == "gauntlet-protocol-v4"


def test_fail_order_puts_the_cheap_reject_first_and_family_gates_last():
    assert FAIL_ORDER == ("sharpe_floor", "oos_negative", "edge_decay",
                          "mc_p05", "p_ruin", "cost_stress", "pbo", "plateau")


def test_dsr_is_still_absent_from_the_gate_order():
    assert "dsr" not in FAIL_ORDER


def test_thresholds_match_the_sop():
    assert SR_FLOOR == 0.4
    assert PBO_PASS == 0.20
    assert PBO_KILL == 0.50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/test_gen4.py -q`
Expected: FAIL with `ImportError: cannot import name 'SR_FLOOR'`.

- [ ] **Step 3: Update constants**

In `pipeline/gauntlet.py`, change line 28 and add the new thresholds beneath the existing ones:

```python
PROTOCOL = "gauntlet-protocol-v4"
```

```python
# protocol-v4 additions. SR_FLOOR is knowingly non-binding today — every one of
# the 43 strategies that has ever reached this stage scored at least 0.577 on
# the train window, and all 24 sub-0.4 specs died at the screen. It is adopted
# so the two pipelines read identically, and it will bite if the screen is ever
# loosened.
SR_FLOOR = 0.4
PBO_PASS = 0.20      # < this passes
PBO_KILL = 0.50      # > this kills the whole sibling group
CSCV_SPLITS = 16
PURGE_BARS = 200     # >= the grammar's longest lookback (ma_cross.slow = 200)
```

Replace `FAIL_ORDER` (line 48):

```python
FAIL_ORDER = ("sharpe_floor", "oos_negative", "edge_decay", "mc_p05",
              "p_ruin", "cost_stress", "pbo", "plateau")
```

- [ ] **Step 4: Add the imports**

Near the existing `from .cluster import effective_trials`:

```python
from .pbo import cscv_pbo
from .plateau import annualized_sharpe, select_survivor, qualifies
from .walkforward import walkforward_report
from .regime import regime_by_date, regime_split
from .stats import harvey_liu_haircut
```

Note `harvey_liu_haircut` joins the existing `from .stats import (...)` block rather than forming a second import from the same module.

- [ ] **Step 5: Add the per-spec gates to `evaluate_spec`**

`evaluate_spec` currently takes the five robustness gates. Add two parameters at the end, both keyword with defaults so existing tests keep working:

```python
def evaluate_spec(is_trades, oos_trades, stress_oos_trades, daily_returns,
                  is_vol, oos_vol, trials_n, trials_sr_var, seed,
                  group_n=None, registered_n=None,
                  train_sharpe=None, pbo_value=None, plateau_ok=None):
```

Inside, where the existing `checks` dict is built (around line 164), add the three new entries:

```python
    checks = {"sharpe_floor": train_sharpe is None or train_sharpe >= SR_FLOOR,
              "oos_negative": ...,          # unchanged
              "edge_decay": ...,            # unchanged
              "mc_p05": ...,                # unchanged
              "p_ruin": ...,                # unchanged
              "cost_stress": ...,           # unchanged
              "pbo": pbo_value is None or pbo_value < PBO_PASS,
              "plateau": plateau_ok is not False}
```

`None` means "not supplied by this caller" and passes, so the existing gauntlet tests that call `evaluate_spec` directly keep their meaning. The real `main()` always supplies all three.

Add to the returned `metrics` dict:

```python
        "train_sharpe": train_sharpe,
        "pbo": pbo_value,
```

- [ ] **Step 6: Replace `select_survivors`**

Delete the body of `select_survivors` (lines 178-191) and replace the whole function:

```python
def select_survivors(rows: list[dict], grids_by_group: dict,
                     family_by_group: dict) -> tuple[set[str], set[str]]:
    """protocol-v4 selection: per sibling group, the candidate with the
    strongest NEIGHBOURHOOD FLOOR among plateau-qualifying gauntlet passers.

    This function no longer reads any point metric. Under protocol-v3 it sorted
    on -dsr and took the winner, which is precisely the point-winner selection
    the SOP forbids.
    """
    quarantine, not_selected = set(), set()
    for group, family in sorted(family_by_group.items()):
        grids = grids_by_group.get(group, {})
        winner, _detail = select_survivor(family, grids)
        passers = {s["sid"] for s in family if s["gauntlet_passed"]}
        if winner is not None:
            quarantine.add(winner)
        not_selected.update(passers - {winner})
    return quarantine, not_selected
```

- [ ] **Step 7: Build the family view in `main()`**

In `main()`, after `full_results` and `returns_by_id` are populated and before `rows` is built, add:

```python
    # protocol-v4: plateau selection needs every sibling's train-window score,
    # its dense-axis coordinates, and whether it died at the screen on
    # turnover. Screen deaths are read from the chain, not re-derived.
    # `gauntlet_passed` starts False for every sibling and is filled in at
    # Step 9, once this run's verdicts exist.
    screen_tc_fail = {
        e["payload"]["strategy_id"]
        for e in registry.entries()
        if e["entry_type"] == "state_change"
        and e["payload"].get("to") == "graveyard"
        and e["payload"].get("reason") == "trade_count"}

    def train_curve(sid):
        return [(d, v) for d, v in full_results[sid]["equity"]
                if d <= args.cutoff]

    train_sharpe = {s["strategy_id"]: annualized_sharpe(
        train_curve(s["strategy_id"])) for s in all_specs}

    # dense-axis coordinates, and the grid each axis actually varies over
    from .composer import SWEEPABLE_TYPES
    from .blocks import BLOCK_TYPES
    family_by_group, grids_by_group = {}, {}
    for s in all_specs:
        sid, g = s["strategy_id"], s["provenance"]["sibling_group_id"]
        axes = {}
        for b in s["blocks"]:
            key = (b["role"], b["type"])
            if key not in SWEEPABLE_TYPES:
                continue
            for p, v in b["params"].items():
                if isinstance(BLOCK_TYPES[key].get(p, {}).get("grid"), list):
                    axes[f"{b['type']}.{p}"] = v
                    grids_by_group.setdefault(g, {})[f"{b['type']}.{p}"] = \
                        BLOCK_TYPES[key][p]["grid"]
        family_by_group.setdefault(g, []).append(
            {"sid": sid, "axes": axes, "score": train_sharpe[sid],
             "screen_trade_count_fail": sid in screen_tc_fail,
             "gauntlet_passed": False})
```

An axis whose value is identical across every sibling in the group is not a swept axis. Prune those, or a fixed parameter would generate phantom neighbours:

```python
    for g, fam in family_by_group.items():
        varying = {a for a in grids_by_group.get(g, {})
                   if len({s["axes"].get(a) for s in fam}) > 1}
        grids_by_group[g] = {a: v for a, v in grids_by_group.get(g, {}).items()
                             if a in varying}
        for s in fam:
            s["axes"] = {a: v for a, v in s["axes"].items() if a in varying}
```

- [ ] **Step 8: Compute PBO per group, then evaluate**

Before the `for s in candidates:` loop:

```python
    # PBO over the TRAIN window only — the 2024+ holdout has been consumed
    # three times already and protocol-v4 does not consume it a fourth. The
    # matrix includes EVERY sibling, screen deaths included; computing it over
    # passers only would filter on performance and understate overfitting.
    n_train = len([d for d, _ in full_results[all_specs[0]["strategy_id"]]["equity"]
                   if d <= args.cutoff])
    pbo_by_group = {}
    for g, fam in family_by_group.items():
        series = {s["sid"]: daily_returns_from_curve(train_curve(s["sid"]))
                  for s in fam}
        pbo_by_group[g] = cscv_pbo(series, s=CSCV_SPLITS)
        v = pbo_by_group[g]["pbo"]
        print(f"  PBO {g}: "
              f"{'n/a — ' + pbo_by_group[g]['reason'] if v is None else f'{v:.3f}'}"
              f"  ({pbo_by_group[g]['n_configs']} configs)")
    killed_groups = {g for g, r in pbo_by_group.items()
                     if r["pbo"] is not None and r["pbo"] > PBO_KILL}
    for g in sorted(killed_groups):
        print(f"  PBO FAMILY KILL: {g} at {pbo_by_group[g]['pbo']:.3f} > {PBO_KILL}")
```

In the `for s in candidates:` loop, pass the new arguments to `evaluate_spec`:

```python
        ok_plateau, _reason = qualifies(
            next(x for x in family_by_group[g] if x["sid"] == sid),
            family_by_group[g], grids_by_group.get(g, {}))
        passed, reason, metrics, mc_summary = evaluate_spec(
            is_t, oos_t, stress_oos, rets, is_vol, oos_vol,
            trials_n, trials_var, seed=int(sid, 16) % (2 ** 31),
            group_n=group_n[g], registered_n=registered_n,
            train_sharpe=train_sharpe[sid],
            pbo_value=pbo_by_group[g]["pbo"],
            plateau_ok=ok_plateau)
        if g in killed_groups:
            passed, reason = False, "pbo_family_kill"
```

Then record the corroborating numbers into `metrics` right after:

```python
        metrics["haircut"] = harvey_liu_haircut(
            train_sharpe[sid] or 0.0,
            t_years=n_train / 365.0, n_trials=trials_n)
        train_dates = [d for d, _ in full_results[sid]["equity"]
                       if d <= args.cutoff]
        metrics["walkforward"] = walkforward_report(
            [t for t in res["trades"] if t["entry_date"] <= args.cutoff],
            train_dates, n_folds=3, purge_bars=PURGE_BARS)
        btc = bars_by_asset.get("BTCUSD") or bars_by_asset[sorted(bars_by_asset)[0]]
        metrics["regime"] = regime_split(oos_t, regime_by_date(btc))
```

- [ ] **Step 9: Mark passers and call the new selector**

Replace the `quarantine, not_selected = select_survivors(rows)` call:

```python
    passed_by_sid = {r["sid"]: r["passed"] for r in rows}
    for fam in family_by_group.values():
        for s in fam:
            s["gauntlet_passed"] = passed_by_sid.get(s["sid"], False)
    quarantine, not_selected = select_survivors(
        rows, grids_by_group, family_by_group)
```

- [ ] **Step 10: Run the gauntlet's own suite and fix fallout**

Run: `python -m pytest pipeline/test_gauntlet.py pipeline/test_gen4.py -q`

Existing `select_survivors` tests will fail — its signature changed. Rewrite each to build the family/grids view rather than deleting it, and keep at least one test asserting the **old** behaviour is gone:

```python
def test_selection_no_longer_follows_dsr():
    """The v3 rule would take 'hi'; the v4 rule takes the better neighbourhood."""
    grids = {"lookback": [20, 35, 55, 75, 100]}
    fam = [{"sid": "lo", "axes": {"lookback": 55}, "score": 0.98,
            "screen_trade_count_fail": False, "gauntlet_passed": True},
           {"sid": "hi", "axes": {"lookback": 20}, "score": 1.00,
            "screen_trade_count_fail": False, "gauntlet_passed": True},
           {"sid": "nb", "axes": {"lookback": 35}, "score": 0.95,
            "screen_trade_count_fail": False, "gauntlet_passed": False},
           {"sid": "rt", "axes": {"lookback": 75}, "score": 0.99,
            "screen_trade_count_fail": False, "gauntlet_passed": False}]
    q, _ = select_survivors([{"sid": s["sid"], "group": "g",
                              "passed": s["gauntlet_passed"], "dsr": s["score"]}
                             for s in fam],
                            {"g": grids}, {"g": fam})
    assert q == {"lo"}
```

- [ ] **Step 11: Dry-run against the live registry, writing nothing**

```bash
python -m pipeline.gauntlet --dry-run
```

Expected: it prints per-group PBO lines and a `DRY RUN — ... nothing written.` summary. It **must** refuse a real run, because no `gauntlet-protocol-v4` note is chained yet — that refusal is correct and is the pre-declaration guard working.

Confirm nothing was written:

```bash
git status --short registry_log.jsonl
```

Expected: no output.

- [ ] **Step 12: Run the full scoped suite**

Expected: all green, zero failures. Report the exact count.

- [ ] **Step 13: Commit**

```bash
git add pipeline/gauntlet.py pipeline/test_gauntlet.py pipeline/test_gen4.py && git commit -m "feat(gauntlet): protocol-v4 gates and neighbourhood selection"
git show HEAD --stat
```

---

## Task 9: The write-free ratchet diagnostic

**Files:**
- Create: `diagnose_protocol_v4.py` (repo root of `research-layer/`, beside `verify_registry.py`)
- Test: `pipeline/test_gen4.py`

Follows the `diagnose_protocol_v2.py` precedent. Reports what v4 **would** have done to the existing 80. Changes no verdict, writes nothing, re-judges nothing.

- [ ] **Step 1: Write the failing test**

Append to `pipeline/test_gen4.py`:

```python
import subprocess
import sys
import hashlib


def test_the_diagnostic_writes_nothing():
    """Asserted mechanically. A diagnostic that quietly touched the chain would
    be the worst possible bug in this repo."""
    root = Path(__file__).resolve().parent.parent
    before = hashlib.sha256((root / "registry_log.jsonl").read_bytes()).hexdigest()
    r = subprocess.run([sys.executable, "diagnose_protocol_v4.py"],
                       cwd=root, capture_output=True, text=True, timeout=1800)
    after = hashlib.sha256((root / "registry_log.jsonl").read_bytes()).hexdigest()
    assert r.returncode == 0, r.stderr[-2000:]
    assert before == after, "diagnostic mutated registry_log.jsonl"
    assert "WOULD" in r.stdout
```

Note: the scanner appends to `registry_log.jsonl` continuously, so this test can fail spuriously if a scanner cycle lands mid-run. If it fails, re-run once and confirm the hash difference is a scanner append (`git diff --stat registry_log.jsonl`) before treating it as a real defect.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/test_gen4.py -k diagnostic -q`
Expected: FAIL — `diagnose_protocol_v4.py` does not exist, returncode non-zero.

- [ ] **Step 3: Implement**

Create `research-layer/diagnose_protocol_v4.py`:

```python
"""Ratchet check: what protocol-v4 WOULD have done to the existing 80.

Write-free by construction — it opens the registry read-only and never
constructs a Registry writer. It changes no verdict and re-judges nothing; the
77 buried strategies stay buried and gen-1/2/3 verdicts stand. Its only job is
to answer, BEFORE the v4 note is chained, whether the new standard has teeth.

If a majority of already-buried strategies would now pass, v4 is too loose and
must be tightened before gen-4 runs — the ratchet permits tightening freely and
requires evidence on-chain for any loosening.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from pipeline.gauntlet import SR_FLOOR, PBO_PASS, PBO_KILL, CSCV_SPLITS
from pipeline.pbo import cscv_pbo
from pipeline.plateau import annualized_sharpe, qualifies, select_survivor
from pipeline.blocks import BLOCK_TYPES
from pipeline.composer import SWEEPABLE_TYPES

ROOT = Path(__file__).resolve().parent
CUTOFF = "2023-12-31"


def load_chain():
    """Specs, terminal states, screen turnover deaths, and who actually PASSED
    the gauntlet. The last one must come from the gauntlet verdict, not from the
    state: a gauntlet failure and a sibling_not_selected both land in graveyard,
    so state alone cannot tell a passer from a failure."""
    specs, states, screen_tc, gauntlet_pass = [], {}, set(), set()
    for line in (ROOT / "registry_log.jsonl").open(encoding="utf-8"):
        e = json.loads(line)
        p = e.get("payload", {})
        if e["entry_type"] == "strategy_registered":
            specs.append(p)
        elif e["entry_type"] == "state_change":
            states[p["strategy_id"]] = p["to"]
            if p.get("to") == "graveyard" and p.get("reason") == "trade_count":
                screen_tc.add(p["strategy_id"])
        elif e["entry_type"] == "verdict":
            if p.get("stage") == "gauntlet" and p.get("verdict") == "pass":
                gauntlet_pass.add(p["strategy_id"])
    return specs, states, screen_tc, gauntlet_pass


def train_curve(sid):
    path = ROOT / "artifacts" / sid / "equity.csv"
    if not path.exists():
        return []
    return [(r["date"], float(r["combined_equity"]))
            for r in csv.DictReader(path.open(encoding="utf-8"))
            if r["date"] <= CUTOFF]


def returns(curve):
    return [curve[i][1] / curve[i - 1][1] - 1
            for i in range(1, len(curve)) if curve[i - 1][1] > 0]


def main() -> int:
    specs, states, screen_tc, gauntlet_pass = load_chain()
    print(f"{len(specs)} registered strategies; "
          f"{sum(1 for v in states.values() if v == 'graveyard')} buried, "
          f"{sum(1 for v in states.values() if v == 'quarantine')} quarantined")

    fam_by_group, grids_by_group = {}, {}
    for s in specs:
        sid, g = s["strategy_id"], s["provenance"]["sibling_group_id"]
        axes = {}
        for b in s["blocks"]:
            key = (b["role"], b["type"])
            if key not in SWEEPABLE_TYPES:
                continue
            for p, v in b["params"].items():
                if isinstance(BLOCK_TYPES[key].get(p, {}).get("grid"), list):
                    axes[f"{b['type']}.{p}"] = v
                    grids_by_group.setdefault(g, {})[f"{b['type']}.{p}"] = \
                        BLOCK_TYPES[key][p]["grid"]
        fam_by_group.setdefault(g, []).append(
            {"sid": sid, "axes": axes, "score": annualized_sharpe(train_curve(sid)),
             "screen_trade_count_fail": sid in screen_tc,
             "gauntlet_passed": sid in gauntlet_pass})

    n_sr_fail = n_plateau_fail = 0
    for g in sorted(fam_by_group):
        fam = fam_by_group[g]
        grids = grids_by_group.get(g, {})
        series = {s["sid"]: returns(train_curve(s["sid"])) for s in fam}
        series = {k: v for k, v in series.items() if v}
        lengths = {len(v) for v in series.values()}
        pbo = ({"pbo": None, "reason": "ragged series", "n_configs": len(series)}
               if len(lengths) > 1 else cscv_pbo(series, s=CSCV_SPLITS))
        v = pbo["pbo"]
        verdict = ("n/a — " + str(pbo["reason"]) if v is None
                   else f"{v:.3f} {'PASS' if v < PBO_PASS else 'FAMILY KILL' if v > PBO_KILL else 'FAIL'}")
        print(f"\n{g}  ({len(fam)} siblings)")
        print(f"  WOULD PBO: {verdict}")
        if not grids:
            print("  WOULD PLATEAU: n/a — no dense swept axis "
                  "(this family predates the v4 grammar)")
        else:
            winner, detail = select_survivor(fam, grids)
            print(f"  WOULD SELECT: {winner}")
            for sid in sorted(detail):
                d = detail[sid]
                if not d["qualifies"]:
                    n_plateau_fail += 1
                    print(f"    {sid}  would fail plateau: {d['reason']}")
        for s in fam:
            if s["score"] is not None and s["score"] < SR_FLOOR:
                n_sr_fail += 1
                print(f"    {s['sid']}  WOULD fail sharpe_floor: {s['score']:.3f}")

    print(f"\nSUMMARY — WOULD fail sharpe_floor: {n_sr_fail}; "
          f"WOULD fail plateau: {n_plateau_fail}")
    print("This changes nothing. No verdict was re-judged and nothing was "
          "written. Buried strategies stay buried.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the diagnostic and read the output**

```bash
python diagnose_protocol_v4.py
```

**Do not treat this as a pass/fail step.** Read the numbers and report them. The judgement — whether v4 has teeth or needs tightening before it is chained — is Coen's and must be surfaced, not decided here.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest pipeline/test_gen4.py -k diagnostic -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add diagnose_protocol_v4.py pipeline/test_gen4.py && git commit -m "feat(diagnose): write-free ratchet check for protocol-v4"
git show HEAD --stat
```

---

## Task 10: Documentation and the protocol note draft

**Files:**
- Create: `docs/notes/gauntlet-protocol-v4.md` (the text to be chained later)
- Modify: `SCHEMA.md`, `README.md`
- Modify: `C:\Users\Coen\.claude\projects\E--Users-Coen-Claude\memory\SOPs\sop-trading-system-build.md`

**This task chains nothing.** It writes the note's text to a file. Chaining it is the Coen-gated live sequence, outside this plan.

- [ ] **Step 1: Draft the protocol note**

Create `docs/notes/gauntlet-protocol-v4.md`. It must contain, in this order:

1. The line `gauntlet-protocol-v4` as the first token (the gauntlet's guard checks `startswith(PROTOCOL)`).
2. The three added gates with thresholds: `sharpe_floor` SR ≥ 0.4; `pbo` < 20% pass / > 50% family kill, CSCV S=16 on the train window over every sibling including screen deaths; `plateau` qualification and neighbourhood selection stated in full.
3. The statement that `select_survivors` no longer reads a point metric.
4. Harvey-Liu haircut, purged walk-forward (3 folds, 200-bar purge) and the regime split as **recorded, not gating** — and that the walk-forward flags diverge from the SOP's Phase 5, which treats them as binding.
5. **Ratchet position: v4 only tightens.** Three gates added, none removed, no threshold loosened; it therefore carries none of the evidence-and-argument burden protocol-v3 discharged.
6. A cross-reference to the `quarantine-standard-asymmetry` note (entry 2308, commit `1b5da5e`) for retroactivity. **Cross-reference it; do not restate it.**
7. The three surviving differences from the SOP: DSR stage, N deflation, sample-size floor 40 vs ~100.
8. That the Composer's prior-knowledge leakage is real, unmeasured and **uncorrectable by DSR at any N** — declared as a permanent limitation rather than folded into a number.
9. That this raises the chance of a zero-survivor gen-4, and that zero is an acceptable outcome — stated before any gen-4 number exists.

- [ ] **Step 2: Update SCHEMA.md**

`gauntlet.py:37` records that SCHEMA.md's gauntlet criterion (d) still lists the deflated Sharpe as a gate, amended by the chained v3 note. Update the criteria list to the v4 gate set and keep the historical note that (d) was retired by v3.

- [ ] **Step 3: Update the vault SOP**

In `sop-trading-system-build.md`, add to the Phase 4 header:

```
> **Shared gate standard (2026-08-17):** these gates are also the research
> layer's, via the chained `gauntlet-protocol-v4` note in
> `stewart-forward-test/research-layer`. Three differences survive and are
> argued on-chain in that note: DSR is applied at designation here and at
> quarantine->live there; N is raw here and deflated to effective clusters
> there; the sample-size floor is ~100 trades here and 40 at the research
> layer's screen. See `research-layer/docs/2026-08-17-gate-standard-design.md`.
```

- [ ] **Step 4: Verify the chain still validates**

```bash
python verify_registry.py registry_log.jsonl
```

Expected: VALID. This task touched no chain file; the check confirms it.

- [ ] **Step 5: Commit**

```bash
git add docs/notes/gauntlet-protocol-v4.md SCHEMA.md README.md && git commit -m "docs: protocol-v4 note text and shared gate standard pointers"
git show HEAD --stat
```

The vault SOP lives outside this repo and is committed separately by whatever process owns the vault — do not add it to this repo's commit.

---

## Task 11: Full-suite verification before shipping

**Files:** none modified.

- [ ] **Step 1: Run the research layer's scoped suite**

Run the scoped command from the header. Record the exact pass count.

- [ ] **Step 2: Run the `trading-systems` suite**

```bash
cd /e/Users/Coen/Claude/trading-systems && python -m pytest -q
```

**This takes ~45 minutes.** Expected: 907 passed / 1 skipped. This plan should not have touched that repo — run it because "should not" is not evidence.

- [ ] **Step 3: Verify both chains**

```bash
cd /e/Users/Coen/Claude/stewart-forward-test/research-layer && python verify_registry.py registry_log.jsonl
```

Expected: VALID.

- [ ] **Step 4: Confirm no stray files are staged or modified**

```bash
git status --short
```

Expected: only the concurrent session's known files (`sources/discovery_queue.jsonl`, `sources/verified_sources.json`, the 20 untracked `data/*USDT*.csv`, `reextract_test.py`). Nothing of yours uncommitted, nothing staged.

- [ ] **Step 5: Report, do not push**

Report the three counts (research-layer tests, trading-systems tests, chain status) and stop. Pushing and the live gen-4 sequence are Coen-gated and outside this plan.

---

## Open questions for Coen — surface, do not decide

1. **Purged walk-forward is recorded here, binding in the SOP's Phase 5.** Task 6 computes `majority_pass` and `catastrophic` and records both. Promoting them to gates is one line in `FAIL_ORDER`. This is either a fourth justified difference or a gap to close.
2. **The plateau ratio is 0.9 and untested against a real family.** Task 9's diagnostic is what reveals whether it is brutal. The number is pre-declared, so changing it after seeing the diagnostic is a tightening (safe) or a loosening (needs the argument on-chain).
3. **Families predating the v4 grammar have no dense swept axis**, so plateau selection cannot apply to them. Gen-4 onward is fine. The diagnostic will report this as `n/a` for all 12 existing groups — which means the plateau half of the ratchet check has no historical evidence to run against, and only the PBO and Sharpe-floor halves do.
