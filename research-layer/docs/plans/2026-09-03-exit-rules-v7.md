# Exit Rules v7 (D15) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire calendar exits and fixed-percent stops from the Composer grammar, add declared indicator-placed stops and indicator-event signal exits, stamp new registrations `version: 2`, keep the legacy engine path byte-identical for `version: 1`, and record why every trade closed.

**Architecture:** Retire-in-place (chained schemas are immutable): `RETIRED_TYPES` in `blocks.py`, refused for `version >= 2`. The engine branches once on `version`: v1 = today's code path untouched; v2 = barriers then declared signal exits, no deadline, no implicit exit. The Composer stamps `version: 2`, the fingerprint includes the version, the verifier enforces v2 after the chained note. A read-only tool classifies every family for the re-trial (firing is Coen-gated).

**Tech Stack:** Python 3.14 (`E:\Users\Coen\Claude\stewart-forward-test\research-layer` venv — run tests with `python -m pytest pipeline -q -p no:warnings`), jsonschema, the append-only hash-chained registry. **Spec:** `docs/2026-09-03-exit-rules-v7-design.md`. **Worktree:** `E:\Users\Coen\Claude\stewart-forward-test-d15`, branch `feat/d15-exit-rules`, base `920f9fc`.

**Conventions for every task:** scoped `git add <paths>`; never touch `E:\Users\Coen\Claude\stewart-forward-test` (the live tree — a cycle may be running and workers re-import `pipeline/*.py` from disk) or any other worktree; never write `registry_log.jsonl`; tests use tmp registries only; `pipeline/simcache.py`, `cluster.py`, and the gauntlet regions ~550-655, ~1098-1175, ~1222, ~1290-1320 belong to the peer session's branch — do not edit them. Record the baseline (`python -m pytest pipeline -q -p no:warnings`) before Task 1 and report every count.

---

## File structure

| File | Responsibility |
|---|---|
| `pipeline/blocks.py` (modify) | `RETIRED_TYPES`; new stop/exit types + `CONSTRAINTS`; `validate_block(..., version=1)`; `retired_reason()` |
| `pipeline/engine.py` (modify) | `version` threaded; `_stop_price()` (all stop types); `signal_exits()`; v2 branch; `exit_reason_counts()`; metrics `exit_reasons`, `open_at_end`, `stop_invalid` |
| `pipeline/quarantine.py` (modify) | pass `version` into `simulate_asset` |
| `pipeline/composer.py` (modify) | `SWEEPABLE_TYPES`, `RANGE_REQUIRING`; `validate_family` v2 rules; `version: 2` stamps; `composition_fingerprint` with version; `grammar_summary` retired line; prompt rules; `v7_compliant_as_is()` |
| `pipeline/cells.py` (modify) | `FX_EXCLUDED_BLOCK_TYPES` gains the range-requiring new types |
| `pipeline/gauntlet.py` (modify, `_evaluate_candidate` only) | `exit_reasons_is/oos`, `open_at_end` recorded |
| `verify_registry.py` (modify) | v7 rule after the chained note |
| `schemas/strategy_spec.schema.json` (modify) | `version` enum `[1, 2]` |
| `tools_retrial_families_v7.py` (create) | dry-run classification report |
| `docs/notes/exit-rules-v7.md` (create; chained by the main session, NOT by a task) | Lane A note |
| `SCHEMA.md`, `docs/2026-08-06-composer-design.md`, `docs/2026-08-13-screen-design.md` (modify) | documentation rows |
| tests: `pipeline/test_exit_rules_v7.py` (create), edits to `test_gen4.py`, `test_screen.py`, `test_composer_fx.py`, `test_verify_registry_d9.py` | pins |

---

### Task 1: grammar — retire in place, add the new types

**Files:** modify `pipeline/blocks.py`; modify `pipeline/test_gen4.py` (`EXPECTED_BLOCK_TYPES`, `TWINS` exemption); create `pipeline/test_exit_rules_v7.py`.

- [ ] **Step 1: Write the failing tests** — create `pipeline/test_exit_rules_v7.py`:

```python
"""D15 exit rules v7: grammar, engine, composer, verifier pins.
Run: python -m pytest pipeline/test_exit_rules_v7.py -q
"""
from __future__ import annotations

import pytest

from .blocks import BLOCK_TYPES, RETIRED_TYPES, validate_block, retired_reason

NEW_STOPS = {("stop", "swing_stop"), ("stop", "ma_stop"), ("stop", "channel_stop"), ("stop", "band_stop")}
NEW_EXITS = {("exit", "ma_crossunder"), ("exit", "channel_exit"), ("exit", "zscore_revert"),
             ("exit", "tstat_decay"), ("exit", "regime_flip")}


def test_retired_types_stay_in_the_grammar_with_their_chained_schema():
    # chained schemas are immutable (composer.preflight_block_types): retiring must not edit them
    assert BLOCK_TYPES[("exit", "time_stop")] == {"max_bars": {"type": "int", "grid": [10, 20, 40]}}
    assert BLOCK_TYPES[("stop", "pct_stop")] == {"pct": {"type": "float", "grid": [0.05, 0.10, 0.15]}}
    assert set(RETIRED_TYPES) == {("exit", "time_stop"), ("stop", "pct_stop")}
    assert "calendar" in retired_reason("exit", "time_stop")
    assert retired_reason("entry", "ma_cross") is None


def test_validate_block_refuses_retired_types_for_v2_only():
    assert validate_block("exit", "time_stop", {"max_bars": 20}) == []                 # legacy default
    assert validate_block("exit", "time_stop", {"max_bars": 20}, version=1) == []
    errs = validate_block("exit", "time_stop", {"max_bars": 20}, version=2)
    assert errs and "retired" in errs[0]
    errs = validate_block("stop", "pct_stop", {"pct": 0.05}, version=2)
    assert errs and "retired" in errs[0]
    assert validate_block("stop", "atr_stop", {"atr_len": 14, "mult": 2.0}, version=2) == []


@pytest.mark.parametrize("key", sorted(NEW_STOPS | NEW_EXITS))
def test_new_types_exist_and_every_grid_has_three_contiguous_values(key):
    schema = BLOCK_TYPES[key]
    assert schema, key
    for p, s in schema.items():
        assert len(s["grid"]) >= 3, (key, p)
        assert s["grid"] == sorted(s["grid"]), (key, p)


def test_new_type_grids_are_exactly_the_spec():
    assert BLOCK_TYPES[("stop", "swing_stop")] == {"lookback": {"type": "int", "grid": [10, 20, 40]}}
    assert BLOCK_TYPES[("stop", "ma_stop")] == {"ma_len": {"type": "int", "grid": [20, 50, 100]}}
    assert BLOCK_TYPES[("stop", "channel_stop")] == {"lookback": {"type": "int", "grid": [20, 55, 100]}}
    assert BLOCK_TYPES[("stop", "band_stop")] == {"lookback": {"type": "int", "grid": [20, 40, 60]},
                                                  "mult": {"type": "float", "grid": [1.5, 2.0, 2.5, 3.0]}}
    assert BLOCK_TYPES[("exit", "ma_crossunder")] == {"fast": {"type": "int", "grid": [5, 8, 13, 20, 34]},
                                                      "slow": {"type": "int", "grid": [50, 80, 130, 200]}}
    assert BLOCK_TYPES[("exit", "channel_exit")] == {"lookback": {"type": "int", "grid": [10, 20, 40]}}
    assert BLOCK_TYPES[("exit", "zscore_revert")] == {"lookback": {"type": "int", "grid": [20, 40, 60, 90]},
                                                      "z_exit": {"type": "float", "grid": [0.0, 0.5, 1.0]}}
    assert BLOCK_TYPES[("exit", "tstat_decay")] == {"max_lookback": {"type": "int", "grid": [60, 90, 120]},
                                                    "t_exit": {"type": "float", "grid": [0.0, 0.5, 1.0]}}
    assert BLOCK_TYPES[("exit", "regime_flip")] == {"ma_len": {"type": "int", "grid": [50, 100, 150, 200, 250]}}


def test_ma_crossunder_constraint_fast_lt_slow():
    assert validate_block("exit", "ma_crossunder", {"fast": 34, "slow": 50}, version=2) == []
    errs = validate_block("exit", "ma_crossunder", {"fast": 34, "slow": 50}, version=2)  # valid
    assert errs == []
    # the constraint only bites when fast >= slow; no grid pair satisfies that today, so
    # assert the constraint function directly
    from .blocks import CONSTRAINTS
    assert CONSTRAINTS[("exit", "ma_crossunder")]({"fast": 50, "slow": 50}) == ["ma_crossunder: fast must be < slow"]
```

In `pipeline/test_gen4.py` add the nine new keys to `EXPECTED_BLOCK_TYPES` (keep both retired keys — they stay in the grammar) and add at the top of the `TWINS` block:

```python
# D15: the new indicator-placed stops and signal exits are dense-by-design
# (every grid >= 3 contiguous values) and have NO coarse twin; the twin
# invariant does not apply to them. Pinned by test_exit_rules_v7.py instead.
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest pipeline/test_exit_rules_v7.py pipeline/test_gen4.py -q -p no:warnings`
Expected: FAIL — `ImportError: RETIRED_TYPES`; `EXPECTED_BLOCK_TYPES` mismatch.

- [ ] **Step 3: Implement** — in `pipeline/blocks.py`:

Append to `BLOCK_TYPES` (after `zscore_reversion_dense`):

```python
    # --- D15 exit rules v7 (docs/2026-09-03-exit-rules-v7-design.md) -------
    # Indicator-placed stops (the stop LEVEL comes from an indicator at the
    # signal bar; fixed at entry, no trailing) and indicator-EVENT signal
    # exits (evaluated on close t, filled at open t+1, like entries). All are
    # dense-by-design (>= 3 contiguous grid values) and sweepable. The
    # retired time_stop / pct_stop entries above are UNCHANGED: chained
    # schemas are immutable, so they are refused by policy (RETIRED_TYPES),
    # never edited or deleted.
    ("stop", "swing_stop"): {
        "lookback": {"type": "int", "grid": [10, 20, 40]},
    },
    ("stop", "ma_stop"): {
        "ma_len": {"type": "int", "grid": [20, 50, 100]},
    },
    ("stop", "channel_stop"): {
        "lookback": {"type": "int", "grid": [20, 55, 100]},
    },
    ("stop", "band_stop"): {
        "lookback": {"type": "int", "grid": [20, 40, 60]},
        "mult": {"type": "float", "grid": [1.5, 2.0, 2.5, 3.0]},
    },
    ("exit", "ma_crossunder"): {
        "fast": {"type": "int", "grid": [5, 8, 13, 20, 34]},
        "slow": {"type": "int", "grid": [50, 80, 130, 200]},
    },
    ("exit", "channel_exit"): {
        "lookback": {"type": "int", "grid": [10, 20, 40]},
    },
    ("exit", "zscore_revert"): {
        "lookback": {"type": "int", "grid": [20, 40, 60, 90]},
        "z_exit": {"type": "float", "grid": [0.0, 0.5, 1.0]},
    },
    ("exit", "tstat_decay"): {
        "max_lookback": {"type": "int", "grid": [60, 90, 120]},
        "t_exit": {"type": "float", "grid": [0.0, 0.5, 1.0]},
    },
    ("exit", "regime_flip"): {
        "ma_len": {"type": "int", "grid": [50, 100, 150, 200, 250]},
    },
```

After `CONSTRAINTS` add the crossunder constraint and the retirement table:

```python
CONSTRAINTS[("exit", "ma_crossunder")] = (
    lambda p: ["ma_crossunder: fast must be < slow"] if p["fast"] >= p["slow"] else [])

# D15 (2026-09-03): retired for NEW registrations (version >= 2). The entries
# stay in BLOCK_TYPES because their chained block_type_registered schemas are
# immutable and ~5,000 legacy registrations cite them; the engine keeps
# executing them for version-1 specs. Reasons are the chained note's words.
RETIRED_TYPES: dict[tuple[str, str], str] = {
    ("exit", "time_stop"): "D15 exit-rules-v7: exits on the calendar, not the market",
    ("stop", "pct_stop"): "D15 exit-rules-v7: a fixed percent is not an indicator-placed stop",
}


def retired_reason(role: str, btype: str) -> str | None:
    return RETIRED_TYPES.get((role, btype))
```

Change `validate_block`'s signature and first check:

```python
def validate_block(role: str, btype: str, params: dict, *, version: int = 1) -> list[str]:
    """Return error strings; empty list = valid. `version` is the spec's
    registration version: retired types are errors for version >= 2 and
    valid (legacy) for version 1."""
    key = (role, btype)
    if key not in BLOCK_TYPES:
        return [f"unknown block type {role}/{btype}"]
    if version >= 2 and key in RETIRED_TYPES:
        return [f"{role}/{btype} retired: {RETIRED_TYPES[key]}"]
    schema = BLOCK_TYPES[key]
```

(rest unchanged).

- [ ] **Step 4: Run** `python -m pytest pipeline/test_exit_rules_v7.py pipeline/test_gen4.py pipeline/test_composer.py pipeline/test_gen2.py -q -p no:warnings` → PASS (the fingerprint-stability test in test_gen4 must still pass: additions never perturb existing fingerprints).

- [ ] **Step 5: Commit** `git add pipeline/blocks.py pipeline/test_gen4.py pipeline/test_exit_rules_v7.py && git commit -m "feat(grammar): D15 - retire time_stop/pct_stop in place, add indicator-placed stops and signal exits (v7 T1)"`

---

### Task 2: engine — version branch, new stops, signal exits, exit-reason metrics

**Files:** modify `pipeline/engine.py`, `pipeline/quarantine.py`; extend `pipeline/test_exit_rules_v7.py`; modify `pipeline/test_screen.py:512` (exact-set assertion).

- [ ] **Step 1: Capture the legacy golden BEFORE editing.** Write `pipeline/test_exit_rules_v7.py` helpers + a parity test that replays three legacy fixtures through the CURRENT engine and pins their outputs. Capture the numbers with a scratch script run at HEAD (before Step 3), the way `test_engine_classes.py` did (`CAPTURED_TRADES`, captured from git `875e1f7`):

```python
from .engine import run_spec, simulate_asset, exit_reason_counts
from .test_engine_classes import mk_bars, crypto_closes
from .test_screen import breakout_spec_blocks, COST, ramp_bars


def legacy_specs():
    """Three version-less (=> version 1) specs exercising every legacy exit path:
    time stop, pct stop, and the implicit ma_cross crossunder."""
    base = {"universe": {"assets": ["X"], "asset_class": "crypto", "timeframe": "1d", "session": "24x7"},
            "cost_model": COST}
    return [
        {**base, "strategy_id": "legacy-breakout", "blocks": breakout_spec_blocks(max_bars=5)},
        {**base, "strategy_id": "legacy-macross", "blocks": [
            {"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 20}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.10}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}}]},
        {**base, "strategy_id": "legacy-macross-ds", "blocks": [
            {"role": "entry", "type": "ma_cross_ds", "params": {"fast": 5, "slow": 20, "direction": "both"}},
            {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 10}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}}]},
    ]


def legacy_bars():
    return {"X": mk_bars(crypto_closes(400))}


# Captured 2026-09-03 at git 96f65de (pre-Task-2 engine) by
# scratch/capture_v7_baseline.py: for each legacy spec, the full trades list
# and the last 5 equity points. The legacy path must reproduce these exactly.
LEGACY_GOLDEN = {  # filled in by the capture script; keys = strategy_id
}


@pytest.mark.parametrize("spec", legacy_specs(), ids=lambda s: s["strategy_id"])
def test_legacy_path_is_byte_identical(spec):
    out = run_spec(spec, legacy_bars())
    g = LEGACY_GOLDEN[spec["strategy_id"]]
    assert out["trades"] == g["trades"]
    assert [e for _, e in out["equity"][-5:]] == g["equity_tail"]
    assert out["metrics"]["trades"] == g["metrics"]["trades"]
    assert out["metrics"]["net_pnl"] == g["metrics"]["net_pnl"]
```

The capture script (scratch, NOT committed) is:

```python
import json, sys
sys.path.insert(0, ".")
from pipeline.test_exit_rules_v7 import legacy_specs, legacy_bars
from pipeline.engine import run_spec
g = {}
for s in legacy_specs():
    o = run_spec(s, legacy_bars())
    g[s["strategy_id"]] = {"trades": o["trades"], "equity_tail": [e for _, e in o["equity"][-5:]],
                           "metrics": {"trades": o["metrics"]["trades"], "net_pnl": o["metrics"]["net_pnl"]}}
print(json.dumps(g, indent=1))
```

Paste its output into `LEGACY_GOLDEN` verbatim (floats at full repr). It must contain at least one trade with `exit_reason == "time"`, one `"stop"`, and one `"signal"` — assert that in a test `test_legacy_golden_covers_every_legacy_exit_path`.

Then the v2 behaviour tests:

```python
def v2(spec):
    return {**spec, "version": 2}


def test_v2_refuses_retired_types_in_the_engine():
    s = v2({**legacy_specs()[2]})              # carries exit/time_stop
    with pytest.raises(ValueError, match="time_stop"):
        run_spec(s, legacy_bars())
    s = v2({**legacy_specs()[1]})              # carries stop/pct_stop
    with pytest.raises(ValueError, match="pct_stop"):
        run_spec(s, legacy_bars())


def macross_v2(exit_blocks, stop=None):
    return {"version": 2, "strategy_id": "v2-macross",
            "universe": {"assets": ["X"], "asset_class": "crypto", "timeframe": "1d", "session": "24x7"},
            "cost_model": COST,
            "blocks": [{"role": "entry", "type": "ma_cross_ds", "params": {"fast": 5, "slow": 20, "direction": "long"}},
                       stop or {"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}},
                       *exit_blocks,
                       {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}}]}


def test_v2_has_no_implicit_crossunder_exit():
    out = run_spec(macross_v2([]), legacy_bars())
    assert all(t["exit_reason"] in ("stop", "target") for t in out["trades"]), out["trades"]
    assert "signal" not in "".join(t["exit_reason"] for t in out["trades"])


def test_v2_declared_ma_crossunder_exits_at_next_open_with_reason():
    out = run_spec(macross_v2([{"role": "exit", "type": "ma_crossunder", "params": {"fast": 5, "slow": 50}}]),
                   legacy_bars())
    sig = [t for t in out["trades"] if t["exit_reason"] == "signal:ma_crossunder"]
    assert sig, out["trades"]
    bars = legacy_bars()["X"]
    idx = {b["date"]: i for i, b in enumerate(bars)}
    for t in sig:
        assert t["exit_px"] == bars[idx[t["exit_date"]]]["open"]       # filled at open t+1


def test_v2_barriers_take_precedence_over_signal_exits_on_the_same_bar():
    # a stop that is hit on the same bar a signal exit would fire records "stop"
    out = run_spec(macross_v2([{"role": "exit", "type": "regime_flip", "params": {"ma_len": 50}}],
                              stop={"role": "stop", "type": "band_stop", "params": {"lookback": 20, "mult": 1.5}}),
                   legacy_bars())
    reasons = {t["exit_reason"] for t in out["trades"]}
    assert reasons <= {"stop", "target", "signal:regime_flip"}


def test_v2_metrics_record_exit_reasons_open_at_end_and_stop_invalid():
    out = run_spec(macross_v2([{"role": "exit", "type": "ma_crossunder", "params": {"fast": 5, "slow": 50}}]),
                   legacy_bars())
    m = out["metrics"]
    assert set(m) >= {"trades", "net_pnl", "win_rate", "max_dd", "exit_reasons", "open_at_end", "stop_invalid"}
    assert sum(m["exit_reasons"].values()) == m["trades"]
    assert isinstance(m["open_at_end"], bool) and isinstance(m["stop_invalid"], int)
    assert exit_reason_counts(out["trades"]) == m["exit_reasons"]


def test_v2_ma_stop_on_wrong_side_makes_the_signal_ineligible():
    # a long whose SMA(20) is ABOVE the entry price has no adverse-side stop: no trade, counted
    closes = [100.0 - i * 0.5 for i in range(60)] + [90.0 + i * 3.0 for i in range(60)]   # V shape
    bars = {"X": mk_bars(closes)}
    spec = macross_v2([], stop={"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}})
    out = run_spec(spec, bars)
    assert out["metrics"]["stop_invalid"] >= 0
    for t in out["trades"]:
        assert t["side"] != "long" or True   # every recorded long had a stop below entry by construction


@pytest.mark.parametrize("stop", [
    {"role": "stop", "type": "swing_stop", "params": {"lookback": 10}},
    {"role": "stop", "type": "channel_stop", "params": {"lookback": 20}},
    {"role": "stop", "type": "band_stop", "params": {"lookback": 20, "mult": 2.0}},
    {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
])
def test_v2_every_new_stop_type_places_the_stop_on_the_adverse_side(stop):
    out = run_spec(macross_v2([], stop=stop), legacy_bars())
    for t in out["trades"]:
        if t["exit_reason"] == "stop":
            assert (t["exit_px"] < t["entry_px"]) if t["side"] == "long" else (t["exit_px"] > t["entry_px"])


@pytest.mark.parametrize("exit_block", [
    {"role": "exit", "type": "channel_exit", "params": {"lookback": 10}},
    {"role": "exit", "type": "zscore_revert", "params": {"lookback": 20, "z_exit": 0.0}},
    {"role": "exit", "type": "tstat_decay", "params": {"max_lookback": 60, "t_exit": 0.0}},
    {"role": "exit", "type": "regime_flip", "params": {"ma_len": 50}},
])
def test_v2_every_new_exit_type_runs_and_labels_its_reason(exit_block):
    out = run_spec(macross_v2([exit_block]), legacy_bars())
    for t in out["trades"]:
        assert t["exit_reason"] in ("stop", "target", f"signal:{exit_block['type']}")


def test_two_declared_exits_both_fire_first_wins_in_declaration_order():
    out = run_spec(macross_v2([{"role": "exit", "type": "regime_flip", "params": {"ma_len": 50}},
                               {"role": "exit", "type": "ma_crossunder", "params": {"fast": 5, "slow": 50}}]),
                   legacy_bars())
    assert {t["exit_reason"] for t in out["trades"]} <= {"stop", "target", "signal:regime_flip", "signal:ma_crossunder"}


def test_open_at_end_is_marked_to_market_never_a_trade():
    closes = [100.0 + i for i in range(80)]           # one long that never exits
    out = run_spec(macross_v2([], stop={"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}}),
                   {"X": mk_bars(closes)})
    assert out["metrics"]["open_at_end"] is True
    assert out["equity"][-1][1] > 1.0                  # unrealised gain marked
    assert all(t["exit_date"] for t in out["trades"])  # no open trade in the list
```

In `pipeline/test_screen.py:512` change the exact-set assertion to
`assert set(v["metrics"]) == {"trades", "net_pnl", "win_rate", "max_dd", "exit_reasons", "open_at_end", "stop_invalid"}` and add `assert v["metrics"]["exit_reasons"] == {"target": 1}` if that fixture's one trade exits on target (check the fixture: `breakout_spec_blocks` with `ramp_bars` — read the test to see which reason its one trade has and pin that).

- [ ] **Step 2: Run** `python -m pytest pipeline/test_exit_rules_v7.py -q -p no:warnings` → the golden test PASSES already (it pins current behaviour); every v2 test FAILS (`KeyError: version` / no such stop type / `exit_reason_counts` import).

- [ ] **Step 3: Implement** `pipeline/engine.py`:

Add after `_tightest_stop`:

```python
def _indicator_stop(s: dict, bars: list[dict], closes: list[float], i: int,
                    side: int, series: dict) -> float | None:
    """D15 indicator-placed stop LEVEL at signal bar i (fixed at entry).
    Returns None while the indicator is warming up. The caller decides
    eligibility (the level must be on the adverse side of the entry)."""
    p = s["params"]
    t = s["type"]
    if t == "swing_stop":
        lb = p["lookback"]
        if i < lb:
            return None
        window = bars[i - lb:i]
        return min(b["low"] for b in window) if side == 1 else max(b["high"] for b in window)
    if t == "channel_stop":
        lb = p["lookback"]
        if i < lb:
            return None
        window = bars[i - lb:i]
        return min(b["low"] for b in window) if side == 1 else max(b["high"] for b in window)
    if t == "ma_stop":
        ma = series[("sma", p["ma_len"])][i]
        return ma
    if t == "band_stop":
        ma = series[("sma", p["lookback"])][i]
        sd = series[("stdev", p["lookback"])][i]
        if ma is None or sd is None:
            return None
        return ma - p["mult"] * sd if side == 1 else ma + p["mult"] * sd
    raise ValueError(f"no executor for stop type {t!r}")


def _stop_price(stops: list[dict], bars: list[dict], closes: list[float],
                entry_px: float, side: int, atr_series: dict, ind_series: dict,
                i: int, version: int) -> float | None | str:
    """Stop price for a position entered at entry_px from signal bar i.
    version 1: today's _tightest_stop (pct/atr) unchanged.
    version 2: every stop type; the tightest ADVERSE-side level wins; a level
    that is not on the adverse side makes the signal ineligible -> returns
    the sentinel "invalid" so the caller can count it."""
    if version < 2:
        return _tightest_stop(stops, entry_px, side, atr_series, i)
    levels = []
    for s in stops:
        if s["type"] in RETIRED_STOP_TYPES:
            raise ValueError(f"stop type {s['type']!r} is retired under exit-rules-v7 (version >= 2)")
        if s["type"] in ("atr_stop", "atr_stop_dense"):
            atr = atr_series[(s["params"]["atr_len"],)][i]
            if atr is None:
                return None
            levels.append(entry_px - side * s["params"]["mult"] * atr)
        else:
            lvl = _indicator_stop(s, bars, closes, i, side, ind_series)
            if lvl is None:
                return None
            levels.append(lvl)
    adverse = [l for l in levels if side * (entry_px - l) > 0]
    if len(adverse) != len(levels):
        return "invalid"
    return max(adverse) if side == 1 else min(adverse)      # tightest


RETIRED_STOP_TYPES = {"pct_stop"}
RETIRED_EXIT_TYPES = {"time_stop"}


def signal_exit(block: dict, bars: list[dict], closes: list[float], i: int,
                side: int, series: dict) -> bool:
    """D15 indicator-EVENT exit evaluated on close i for a position of `side`.
    True means: exit at the open of bar i+1 (the caller fills it)."""
    p = block["params"]
    t = block["type"]
    if t == "ma_crossunder":
        f = series[("sma", p["fast"])][i]
        s = series[("sma", p["slow"])][i]
        if f is None or s is None:
            return False
        return f < s if side == 1 else f > s
    if t == "channel_exit":
        lb = p["lookback"]
        if i < lb:
            return False
        window = bars[i - lb:i]
        return (closes[i] < min(b["low"] for b in window)) if side == 1 \
            else (closes[i] > max(b["high"] for b in window))
    if t == "zscore_revert":
        ma = series[("sma", p["lookback"])][i]
        sd = series[("stdev", p["lookback"])][i]
        if ma is None or sd is None or sd == 0:
            return False
        z = (closes[i] - ma) / sd
        return z >= -p["z_exit"] if side == 1 else z <= p["z_exit"]
    if t == "tstat_decay":
        windows = list(range(20, p["max_lookback"] + 1, 10))
        if i < max(windows) - 1:
            return False
        best = max((trend_tstat(closes[i - w + 1:i + 1]) for w in windows), key=abs)
        return (best <= p["t_exit"]) if side == 1 else (best >= -p["t_exit"])
    if t == "regime_flip":
        ma = series[("sma", p["ma_len"])][i]
        if ma is None:
            return False
        return closes[i] < ma if side == 1 else closes[i] > ma
    raise ValueError(f"no executor for exit type {t!r}")


def exit_reason_counts(trades: list[dict]) -> dict[str, int]:
    """{exit_reason: n} over closed trades; empty dict for none. RECORDED, never gated."""
    out: dict[str, int] = {}
    for t in trades:
        out[t["exit_reason"]] = out.get(t["exit_reason"], 0) + 1
    return dict(sorted(out.items()))
```

In `simulate_asset`, add the keyword `version: int = 1` to the signature and change the body:

```python
    by_role: dict[str, list[dict]] = {}
    for b in blocks:
        by_role.setdefault(b["role"], []).append(b)
    entry = by_role["entry"][0]
    gates = by_role.get("regime", []) + by_role.get("filter", [])
    stops = by_role["stop"]
    targets = by_role.get("target", [])
    exits = by_role.get("exit", [])
    risk = by_role["risk"][0]
    legacy = version < 2
    if legacy:
        time_stops = exits                       # today's semantics, untouched
        signal_exits: list[dict] = []
    else:
        for x in exits:
            if x["type"] in RETIRED_EXIT_TYPES:
                raise ValueError(f"exit type {x['type']!r} is retired under exit-rules-v7 (version >= 2)")
        time_stops = []
        signal_exits = exits

    sig, state = entry_signals(entry, bars)
    mask = gate_mask(gates, bars) if gates else [True] * len(bars)
    closes = [b["close"] for b in bars]
    atr_series = {}
    for s in stops:
        if s["type"] in ("atr_stop", "atr_stop_dense"):
            atr_series[(s["params"]["atr_len"],)] = atr_wilder(bars, s["params"]["atr_len"])
    # D15: indicator series the new stops/exits read, computed once per spec
    ind_series: dict = {}
    if not legacy:
        for s in stops + signal_exits:
            p = s["params"]
            for key, n in (("sma", p.get("ma_len")), ("sma", p.get("fast")), ("sma", p.get("slow")),
                           ("sma", p.get("lookback") if s["type"] in ("band_stop", "zscore_revert") else None),
                           ("stdev", p.get("lookback") if s["type"] in ("band_stop", "zscore_revert") else None)):
                if n is not None and (key, n) not in ind_series:
                    ind_series[(key, n)] = sma(closes, n) if key == "sma" else stdev(closes, n)
    stop_invalid = 0
```

In the exit block of the bar loop, replace the legacy `elif (entry["type"] in ("ma_cross", ...)` clause with:

```python
            elif legacy and entry["type"] in ("ma_cross", "ma_cross_ds", "ma_cross_dense") \
                    and state[i - 1] != pos["side"]:
                exit_px, exit_reason = b["open"], "signal"       # legacy implicit cross-down exit
            elif not legacy and pos.get("exit_pending"):
                exit_px, exit_reason = b["open"], pos["exit_pending"]   # declared signal exit, filled at open t+1
```

After the mark-to-market block at the end of the loop body (still inside `for i, b`), add the signal-exit evaluation on close i:

```python
        # D15: declared signal exits are evaluated on THIS close and filled at
        # the next open, exactly like entries; first declared exit that fires wins
        if not legacy and pos is not None and i >= pos["entry_i"]:
            pos["exit_pending"] = None
            for x in signal_exits:
                if signal_exit(x, bars, closes, i, pos["side"], ind_series):
                    pos["exit_pending"] = f"signal:{x['type']}"
                    break
```

Replace the entry's stop computation:

```python
            stop = _stop_price(stops, bars, closes, entry_px, side, atr_series, ind_series, i - 1, version)
            if stop == "invalid":
                stop_invalid += 1
                stop = None
            if stop is not None and abs(entry_px - stop) > 0:
```

(`deadline` line unchanged — `time_stops` is `[]` under v2 so it is `None`.) The returned dict gains `"stop_invalid": stop_invalid`.

`run_spec` passes `version=spec.get("version", 1)` into every `simulate_asset` call and extends metrics:

```python
    metrics = {
        "trades": len(trades),
        "net_pnl": combined[-1] - 1 if combined else 0.0,
        "win_rate": wins / len(trades) if trades else 0.0,
        "max_dd": -max_drawdown(combined) if combined else 0.0,
        # D15: RECORDED, never gated -- why trades closed, whether the book
        # ended with a position still open (marked to market in equity, never
        # a closed trade), and how many signals were dropped because the
        # indicator-placed stop was not on the adverse side of the entry.
        "exit_reasons": exit_reason_counts(trades),
        "open_at_end": any(books[a]["position"] is not None for a in books),
        "stop_invalid": sum(books[a]["stop_invalid"] for a in books),
    }
```

`pipeline/quarantine.py` `observe_day`: `simulate_asset(spec["blocks"], bars, spec["cost_model"], periods_per_year, version=spec.get("version", 1))`. Import `RETIRED_STOP_TYPES` etc. are module-level in engine. Keep `ENGINE_REV = "e2"` (the legacy path is byte-identical; the sim-cache key gains the spec version on the peer's branch).

- [ ] **Step 4: Run** `python -m pytest pipeline/test_exit_rules_v7.py pipeline/test_engine_classes.py pipeline/test_screen.py pipeline/test_gen2.py pipeline/test_gauntlet_classes.py -q -p no:warnings` → PASS, including the golden parity test and `test_engine_classes`' captured trades (legacy path). Then the full suite.

- [ ] **Step 5: Commit** `git add pipeline/engine.py pipeline/quarantine.py pipeline/test_exit_rules_v7.py pipeline/test_screen.py && git commit -m "feat(engine): D15 - version-2 path with indicator-placed stops and declared signal exits; legacy path byte-identical; exit reasons recorded (v7 T2)"`

---

### Task 3: composer — v2 stamps, rules, fingerprint, prompt, fx exclusions

**Files:** modify `pipeline/composer.py`, `pipeline/cells.py`, `schemas/strategy_spec.schema.json`; extend `pipeline/test_exit_rules_v7.py`; modify `pipeline/test_gen4.py:300-306` (`SWEEPABLE_TYPES` pin), `pipeline/test_composer_fx.py` (`RANGE_REQUIRING` pin).

- [ ] **Step 1: Write the failing tests** — append to `test_exit_rules_v7.py`:

```python
from .composer import (SWEEPABLE_TYPES, RANGE_REQUIRING, composition_fingerprint, validate_family,
                       grammar_summary, v7_compliant_as_is, expand_family)
from . import cells


def test_sweepable_and_range_requiring_include_the_new_types():
    assert {("stop", "swing_stop"), ("stop", "ma_stop"), ("stop", "channel_stop"), ("stop", "band_stop"),
            ("exit", "ma_crossunder"), ("exit", "channel_exit"), ("exit", "zscore_revert"),
            ("exit", "tstat_decay"), ("exit", "regime_flip")} <= SWEEPABLE_TYPES
    assert {"swing_stop", "channel_stop", "channel_exit"} <= RANGE_REQUIRING
    assert RANGE_REQUIRING == set(cells.FX_EXCLUDED_BLOCK_TYPES)         # still pinned equal
    assert {"ma_stop", "band_stop", "ma_crossunder", "zscore_revert", "tstat_decay", "regime_flip"}.isdisjoint(RANGE_REQUIRING)


def fam(blocks, sweep=None):
    return {"family": "d15_fam", "assets": ["BTCUSD"], "card_ids": ["c1"], "regime_hypothesis": "x",
            "blocks": blocks, "sweep": sweep or []}


def test_validate_family_refuses_retired_types_and_allows_zero_or_many_exits():
    ok = fam([{"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}},
              {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
              {"role": "exit", "type": "ma_crossunder", "params": {"fast": 8, "slow": 80}},
              {"role": "exit", "type": "regime_flip", "params": {"ma_len": 100}},
              {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}])
    assert validate_family(ok, {"c1"}, 60) == []
    bad = fam([{"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}},
               {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
               {"role": "exit", "type": "time_stop", "params": {"max_bars": 20}},
               {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}])
    errs = validate_family(bad, {"c1"}, 60)
    assert sum("retired" in e for e in errs) == 2, errs


def test_expanded_specs_are_version_2():
    f = fam([{"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}},
             {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
             {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}])
    specs = expand_family(f, "2026-09-03-test", "m", "2026-09-03T00:00:00Z")
    assert specs and all(s["version"] == 2 for s in specs)


def test_fingerprint_includes_version_but_is_unchanged_for_version_1():
    s1 = {"version": 1, "universe": {"assets": ["BTCUSD"], "timeframe": "1d", "asset_class": "crypto", "session": "24x7"},
          "blocks": [{"role": "entry", "type": "ma_cross", "params": {"fast": 10, "slow": 50}},
                     {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}},
                     {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}]}
    legacy_shape = {k: v for k, v in s1.items() if k != "version"}       # every chained spec has version 1
    assert composition_fingerprint(legacy_shape) == composition_fingerprint(s1)
    assert composition_fingerprint({**s1, "version": 2}) != composition_fingerprint(s1)


def test_v7_compliant_as_is_classifies_legacy_specs():
    base = {"version": 1, "universe": {"assets": ["BTCUSD"], "timeframe": "1d"},
            "blocks": [{"role": "entry", "type": "trend_scan_dense", "params": {"max_lookback": 60, "t_min": 2.0, "direction": "long"}},
                       {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 2.0}},
                       {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}]}
    assert v7_compliant_as_is(base) is True
    assert v7_compliant_as_is({**base, "blocks": base["blocks"] + [{"role": "exit", "type": "time_stop", "params": {"max_bars": 40}}]}) is False
    assert v7_compliant_as_is({**base, "blocks": [{"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}}] + base["blocks"][1:]}) is False


def test_grammar_summary_omits_retired_types_and_names_them():
    text = grammar_summary()
    assert "- exit/time_stop:" not in text and "- stop/pct_stop:" not in text
    assert "retired" in text and "time_stop" in text and "pct_stop" in text
    assert "- exit/ma_crossunder:" in text and "- stop/ma_stop:" in text
```

- [ ] **Step 2: Run** → FAIL (imports, sets, version 1 stamps).

- [ ] **Step 3: Implement** in `pipeline/composer.py`:

```python
SWEEPABLE_TYPES = {("entry", "channel_breakout_dense"),
                   ("entry", "ma_cross_dense"),
                   ("entry", "trend_scan_dense"),
                   ("entry", "zscore_reversion_dense"),
                   ("stop", "atr_stop_dense"),
                   ("target", "r_multiple_dense"),
                   ("filter", "vol_percentile_dense"),
                   ("regime", "regime_ma_short_dense"),
                   # D15 exit rules v7: dense-by-design, no coarse twin
                   ("stop", "swing_stop"), ("stop", "ma_stop"), ("stop", "channel_stop"), ("stop", "band_stop"),
                   ("exit", "ma_crossunder"), ("exit", "channel_exit"), ("exit", "zscore_revert"),
                   ("exit", "tstat_decay"), ("exit", "regime_flip")}

RANGE_REQUIRING = {"channel_breakout", "channel_breakout_dense",
                   "atr_stop", "atr_stop_dense",
                   # D15: read highs/lows, so single-fix (o=h=l=c) fx bars cannot feed them
                   "swing_stop", "channel_stop", "channel_exit"}

SPEC_VERSION = 2   # D15 exit rules v7; every spec the composer builds from 2026-09-03
```

`validate_family`: after the `roles`/`stop`/`risk` checks add:

```python
    for b in blocks:
        r = retired_reason(b.get("role"), b.get("type"))
        if r:
            errors.append(f"{b.get('role')}/{b.get('type')} retired: {r}")
```

and pass `version=SPEC_VERSION` into every `validate_block(...)` call inside `validate_family` (both the block loop and the sweep-axis loop). Both expanders stamp `"version": SPEC_VERSION`. `composition_fingerprint`: add `"version": spec.get("version", 1)` to `core` ONLY when it is not 1 (so every chained v1 fingerprint is unchanged):

```python
    core = {"assets": sorted(u["assets"]), "timeframe": u["timeframe"],
            "asset_class": u.get("asset_class"), "session": u.get("session"), "blocks": blocks}
    if spec.get("version", 1) != 1:
        core["version"] = spec["version"]      # D15: same blocks, different engine = different trial
```

Add:

```python
def v7_compliant_as_is(spec: dict) -> bool:
    """D15(b): a legacy (version-1) composition whose engine behaviour is
    UNCHANGED under exit-rules-v7 -- no retired block type and an entry that
    never had the implicit crossunder exit -- is compliant as registered and
    must NOT be re-registered as a version-2 trial (it would be a duplicate)."""
    from .blocks import RETIRED_TYPES
    if any((b["role"], b["type"]) in RETIRED_TYPES for b in spec["blocks"]):
        return False
    entry = next(b for b in spec["blocks"] if b["role"] == "entry")
    return not entry["type"].startswith("ma_cross")
```

`grammar_summary`:

```python
def grammar_summary() -> str:
    from .blocks import RETIRED_TYPES
    lines = []
    for (role, btype), schema in BLOCK_TYPES.items():
        if (role, btype) in RETIRED_TYPES:
            continue
        params = ", ".join(f"{p} in {s['grid']}" for p, s in schema.items())
        lines.append(f"- {role}/{btype}: {params or '(no params)'}")
    lines.append("- retired (never use): " + ", ".join(
        f"{r}/{t} ({why})" for (r, t), why in RETIRED_TYPES.items()))
    return "\n".join(lines)
```

`SYSTEM_PROMPT` rule line `- Exactly one entry block; at least one stop and one risk block per family.` becomes:

```
- Exactly one entry block; at least one stop and one risk block per family.
- EXITS (D15, 2026-09-03): a position closes ONLY through (a) a stop-loss whose
  LEVEL is indicator-placed (atr_stop*, swing_stop, ma_stop, channel_stop,
  band_stop), (b) an R-multiple target (optional), (c) declared indicator-EVENT
  exit blocks (optional, zero or more: ma_crossunder, channel_exit,
  zscore_revert, tstat_decay, regime_flip). NEVER a time stop of any kind --
  exiting on the calendar is forbidden. An exit block MAY reuse the entry's
  indicator (crossover in, crossunder out is the canonical example).
```

Add the same paragraph to the three per-class prompts (`_equity_etf_system_prompt`, `_bond_etf_system_prompt`, `_metal_etf_system_prompt`) at the equivalent rule line. `pipeline/cells.py`: `FX_EXCLUDED_BLOCK_TYPES = frozenset({"channel_breakout", "channel_breakout_dense", "atr_stop", "atr_stop_dense", "swing_stop", "channel_stop", "channel_exit"})` with a D15 comment (fx families now use `ma_stop`/`band_stop`). `schemas/strategy_spec.schema.json`: `"version": {"enum": [1, 2]}`. Update `test_gen4.py`'s `test_sweepable_set_is_exactly_the_dense_types` to the new set and `test_composer_fx.py`'s `RANGE_REQUIRING` pin to the seven values.

- [ ] **Step 4: Run** the full suite → PASS. Grep: `grep -n '"version": 1' pipeline/composer.py` → 0 hits.
- [ ] **Step 5: Commit** `git add pipeline/composer.py pipeline/cells.py schemas/strategy_spec.schema.json pipeline/test_exit_rules_v7.py pipeline/test_gen4.py pipeline/test_composer_fx.py && git commit -m "feat(composer): D15 - version-2 specs, retired types refused, new types sweepable, fx-safe stops, fingerprint carries version (v7 T3)"`

---

### Task 4: gauntlet metrics + verifier rule + schema docs

**Files:** modify `pipeline/gauntlet.py` (`_evaluate_candidate` only, at the `era_summary` pattern ~:838), `verify_registry.py`, `SCHEMA.md`; extend `pipeline/test_exit_rules_v7.py`, `pipeline/test_verify_registry_d9.py`.

- [ ] **Step 1: Tests**

```python
from .gauntlet import _evaluate_candidate   # if not importable in isolation, test through the existing gauntlet fixture in test_gauntlet_classes.py: run one candidate and assert the metrics keys
def test_gauntlet_verdict_metrics_record_exit_reasons_is_and_oos(...):
    # use test_gauntlet_classes.py's smallest end-to-end fixture; after main() assert every gauntlet
    # verdict's metrics carry "exit_reasons_is", "exit_reasons_oos" (dicts) and "open_at_end" (bool)
```

Verifier (in `test_verify_registry_d9.py`'s style, tmp chain via the fixture helper there): a chain with the `exit-rules-v7` note followed by a `version: 1` registration → INVALID (`"version 1 after exit-rules-v7"`); a `version: 2` registration carrying `exit/time_stop` after the note → INVALID (`"retired block type"`); a `version: 2` clean registration after the note → VALID; a `version: 1` registration BEFORE the note → VALID (unchanged history).

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `gauntlet.py` after the `era_summary` block:

```python
    # D15: RECORDED, NOT GATED -- why the IS and OOS trades closed, and whether
    # the OOS book ended with a position still open (marked to market, never a
    # closed trade). exit_reason_counts is the engine's own helper.
    metrics["exit_reasons_is"] = exit_reason_counts(payload["is_trades"])
    metrics["exit_reasons_oos"] = exit_reason_counts(payload["oos_trades"])
    metrics["open_at_end"] = bool(payload.get("oos_open_at_end", False))
```

(read `_evaluate_candidate`'s payload keys for the IS/OOS trade lists — the map names `res_trades`; use the keys that actually hold the split lists, and thread `open_at_end` from `run_spec`'s metrics where the payload is built. If the payload does not carry the split, record `exit_reasons` over `res_trades` and say so in the SCHEMA row.) `verify_registry.py`: detect the note (`entry_type == "note"` whose `payload["text"]` starts with `exit-rules-v7:`); after it, every `strategy_registered` must have `payload.get("version") == 2` and no block in `RETIRED_TYPES` (import from `pipeline.blocks`); `fail(lineno, ...)` in the existing style. `SCHEMA.md`: `screened` row gains `exit_reasons, open_at_end, stop_invalid`; `gauntlet` row gains `exit_reasons_is, exit_reasons_oos, open_at_end` with "(D15, RECORDED, NOT GATED)"; add a "protocol amendment 2026-09-03 (exit-rules-v7)" paragraph next to the v6 amendment naming `version: 2`, the retired types, and the verifier rule.

- [ ] **Step 4: Run** full suite → PASS; run `python verify_registry.py --log <copy of the live chain in tmp>` → VALID (copy the live `registry_log.jsonl` to the scratchpad first; never point at the live file).
- [ ] **Step 5: Commit** `git add pipeline/gauntlet.py verify_registry.py SCHEMA.md pipeline/test_exit_rules_v7.py pipeline/test_verify_registry_d9.py && git commit -m "feat(chain): D15 - exit reasons on verdicts, verifier enforces version 2 after the exit-rules-v7 note (v7 T4)"`

---

### Task 5: re-trial classification tool (dry run) + docs + the Lane A note text

**Files:** create `tools_retrial_families_v7.py`; create `docs/notes/exit-rules-v7.md` (TEXT ONLY — chaining is the main session's step); modify `docs/2026-08-06-composer-design.md` (grammar table rows), `docs/2026-08-13-screen-design.md` (exit semantics lines); tests in `pipeline/test_exit_rules_v7.py`.

- [ ] **Step 1: Tests** — `tools_retrial_families_v7.classify(entries)` over a tmp chain of four registrations (compliant-as-is; time_stop; pct_stop; ma_cross entry) returns `{family: {"compliant": [sids], "retrial": [sids], "reasons": {sid: [..]}}}`; `--dry-run` writes `docs/runs/<date>-exit-rules-v7-retrial-plan.md` with one table row per family and the totals, and writes NOTHING else (assert the registry file is byte-identical before/after).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the tool: read the chain via `Registry.entries()` (read-only), classify with `v7_compliant_as_is`, render the report; `--fire` is NOT implemented in this task (it raises `SystemExit("firing is Coen-gated; see docs/2026-09-03-exit-rules-v7-design.md §6")`). Write `docs/notes/exit-rules-v7.md` in the register of `family-openness-v1.md`: first line `exit-rules-v7: ...`; state what is on the chain already (grammar v1 chained time_stop/pct_stop at entries 342/344; 5,021 registrations carry a time stop; the implicit crossunder was never chained); what is untouched (every verdict stands; legacy sids keep executing under their registered semantics; nothing is re-judged); the rule (D15 wording verbatim from the design doc §0); the disposition (D15(b)); the ratchet position: **TIGHTENS** (a class of exits is forbidden, nothing loosened); the expected consequence (trade counts fall; the trade_count gate stays). Update the two design docs' rows.
- [ ] **Step 4: Run** full suite → PASS; run `python tools_retrial_families_v7.py --dry-run --log <tmp copy of the live chain>` and keep the report.
- [ ] **Step 5: Commit** `git add tools_retrial_families_v7.py docs/notes/exit-rules-v7.md docs/2026-08-06-composer-design.md docs/2026-08-13-screen-design.md pipeline/test_exit_rules_v7.py docs/runs/*exit-rules-v7-retrial-plan.md && git commit -m "feat(tools): D15 - re-trial classification (dry run only), exit-rules-v7 note text, docs (v7 T5)"`

---

### Task 6 (main session): chain, rebase, merge

- [ ] Whole-branch review (read-only) against the design doc.
- [ ] Rebase `feat/d15-exit-rules` onto the peer session's merged `claude/ai-agent-business-automation-0lzfd9` head; full suite green again.
- [ ] Chain `docs/notes/exit-rules-v7.md` under `ChainLock` from the LIVE tree (the only chain write), outside a cycle window, via the `chain_note.py` pattern used for family-openness-v1; verify the entry round-trips.
- [ ] Merge (fast-forward) outside a cycle window; `verify_registry.py` VALID on the live chain; tell the peer session; vault + MEMORY.md.

---

## Self-review
- **Spec coverage:** §1 grammar → T1; §2 marker/fingerprint/verifier → T3 + T4; §3 engine (legacy path, v2 path, open-at-end, metrics) → T2; §4 gauntlet/screen → T2 (screen via run_spec) + T4; §5 prompt/rules → T3; §6 tool → T5; §7 chain order → T5 (text) + T6 (chaining); §8 ship bar → T2 golden, T4 verifier, T6.
- **Placeholders:** the T4 gauntlet test body says "use the smallest fixture" — the implementer must write the concrete test against `test_gauntlet_classes.py`'s fixture; the T2 `test_v2_ma_stop_on_wrong_side...` test as written asserts weakly — tighten it to count `stop_invalid > 0` on a constructed V-shaped series where SMA(20) sits above the entry on the first long signal.
- **Type consistency:** `validate_block(..., version=)`, `RETIRED_TYPES`, `retired_reason`, `SPEC_VERSION`, `v7_compliant_as_is`, `_stop_price` (returns float | None | "invalid"), `signal_exit`, `exit_reason_counts`, `simulate_asset(..., version=)`, `run_spec` reading `spec.get("version", 1)`, metrics keys `exit_reasons` / `open_at_end` / `stop_invalid` (screen) and `exit_reasons_is` / `exit_reasons_oos` / `open_at_end` (gauntlet) — used consistently across tasks.
