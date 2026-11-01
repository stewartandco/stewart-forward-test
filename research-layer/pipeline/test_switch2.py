"""Switch 2 (SP5 Phase 3, build 2b-3 phase 2): crypto intraday 1h/4h/12h --
the activated-state pins.

One commit opened crypto's phase 2: ACTIVE_CELLS["crypto"]["timeframes"] moved
from ("1d",) to ("1h", "4h", "12h", "1d"), so a 12-asset rotation window is 48
cells (12 assets x 4 timeframes) and the active set is 76 x 4 = 304 cells. The
crypto brief gained its intraday text in the same commit. test_switch1.py keeps
the asset pins; its timeframe pins moved here.

The composer's Task 8 hold (test_switch2_guard.py) still keeps a run on 1d
cells until BOTH intraday notes are on the chain; tests here that drive
composer.run for crypto chain them on their tmp registry first.

Run: python -m pytest pipeline/test_switch2.py -q
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from . import cells, composer, loop, loop_state
from .registry import Registry
from .test_switch1 import (ADMITTED, EXCLUDED, LIVE_DATA, PRE_SWITCH_HISTORY,
                           _crypto_family)

SWITCH2_TIMEFRAMES = ("1h", "4h", "12h", "1d")
INTRADAY = ("1h", "4h", "12h")

TRADFI_FIXTURE = Path(__file__).resolve().parent / "fixtures_switch2" / "tradfi_unchanged.json"

# The Rules block of the crypto brief at BASE (6f29e438d, the Task 8 commit:
# the Switch 1 brief), captured before composer.py was edited. The new brief
# must carry it byte for byte: the Switch 2 brief changes the universe section
# only. The generation-history block is test_switch1.PRE_SWITCH_HISTORY (same
# rule, pinned by sha256 there).
BASE_RULES = (
    "Rules:\n"
    "- Use ONLY the block types and parameter grid values given in the grammar.\n"
    "- Every family must cite the card_ids that motivate it. Cite only cards that\n"
    "  genuinely inform the composition; do not decorate with irrelevant citations.\n"
    "- Exactly one entry block; at least one stop and one risk block per family.\n"
    "- EXITS (D15, 2026-09-03): a position closes ONLY through (a) a stop-loss whose\n"
    "  LEVEL is indicator-placed (atr_stop*, swing_stop, ma_stop, channel_stop,\n"
    "  band_stop), (b) an R-multiple target (optional), (c) declared indicator-EVENT\n"
    "  exit blocks (optional, zero or more: ma_crossunder, channel_exit,\n"
    "  zscore_revert, tstat_decay, regime_flip). NEVER a time stop of any kind --\n"
    "  exiting on the calendar is forbidden. An exit block MAY reuse the entry's\n"
    "  indicator (crossover in, crossunder out is the canonical example).\n"
    "- Every family must state a regime_hypothesis: which market conditions it\n"
    "  expects to work in, and why it is not merely levered exposure to an upward\n"
    "  drift. A family whose edge disappears when drift and volatility fall should\n"
    "  say so plainly.\n"
    "- Short-capable types exist: trend_scan_ds and ma_cross_ds take a direction\n"
    "  parameter (long, short, both), and regime_ma_short permits entries below a\n"
    "  moving average. channel_breakout and zscore_reversion already accept\n"
    "  direction: both.\n"
    "- regime_ma and regime_ma_short cannot appear in the same family — their\n"
    "  filters are mutually exclusive and the spec would never trade. Express\n"
    "  \"long in one regime, short in the other\" as two separate families.\n"
    "- Choose sweep axes ONLY where the cited research motivates exploring the\n"
    "  parameter, and sweep them on a DENSE block type (the *_dense variants).\n"
    "  Coarse types may be USED at fixed values but may NOT be swept.\n"
    "- Every swept axis must declare at least THREE values that are CONTIGUOUS on\n"
    "  that parameter's declared grid — e.g. [35, 55, 75], never [20, 55, 100].\n"
    "  Selection requires a registered sibling one step BELOW and one step ABOVE a\n"
    "  candidate on every swept axis, so a two-value sweep and a gapped sweep can\n"
    "  never produce a survivor and will be rejected.\n"
    "- Prefer ONE or TWO well-motivated axes at FIVE contiguous values over several\n"
    "  axes at three: five values leave three eligible candidates, three leave one.\n"
    "- Propose fewer, better-grounded families over many weak ones. If the cards\n"
    "  support only two good families, propose two."
)
BASE_RULES_SHA256 = "edf500d2f727f4861f24a9f98f1f44fee10514c9b6b072e66baa5e9f9c16b508"


# ---------------- the gate ----------------

def test_crypto_active_set_is_the_76_assets_at_four_timeframes():
    assert len(ADMITTED) == 76
    want = [(a, tf) for a in ADMITTED for tf in SWITCH2_TIMEFRAMES]
    assert cells.active_cells("crypto") == want
    assert len(want) == 304
    assert cells.ACTIVE_CELLS["crypto"] == {"assets": ADMITTED,
                                            "timeframes": SWITCH2_TIMEFRAMES}
    assert not {tf for _, tf in cells.active_cells("crypto")} & {"15m", "30m"}
    # phase 3 (15m, 30m) stays declared and unswept; so do the 24 excluded assets
    assert {"15m", "30m"} <= set(cells.CLASSES["crypto"]["timeframes"])
    assert not {a for a, _ in cells.active_cells("crypto")} & set(EXCLUDED)
    # Task 8 of plan 4: no phase 2 cell is below the admission line
    assert cells.EXCLUDED_CELLS == frozenset()


def _first_date(path: Path) -> str:
    with path.open(encoding="utf-8") as fh:
        fh.readline()                       # header
        return fh.readline()[:10]


@pytest.mark.parametrize("asset", ADMITTED)
def test_every_intraday_cell_meets_the_admission_floor_on_the_live_data(asset):
    daily = LIVE_DATA / f"{asset}_1d.csv"
    if not daily.exists():
        pytest.skip(f"{daily} absent (a clone or a machine without the live bar cache)")
    floor = _first_date(daily)
    for tf in INTRADAY:
        path = LIVE_DATA / f"{asset}_{tf}.csv"
        if not path.exists():
            pytest.skip(f"{path} absent (a clone or a machine without the live bar cache)")
        first = _first_date(path)
        assert first <= "2022-12-31", (
            f"{asset} {tf}: first bar {first} is after the 2022-12-31 admission line")
        assert first >= floor, (
            f"{asset} {tf}: first bar {first} precedes the asset's own first daily bar {floor}")


def test_a_window_is_48_cells_and_rotation_is_unchanged():
    state = {"classes": {}}
    window, rotates = loop._sweep_window(state, "crypto")
    assert (window, rotates) == (list(ADMITTED[:12]), True)
    swept = composer.sweep_cells("crypto", assets=window)
    assert len(swept) == 48
    assert swept == [(a, tf) for a in window for tf in SWITCH2_TIMEFRAMES]
    # the cursor still walks the 76 ASSETS twelve at a time and wraps
    for _ in range(6):
        loop_state.advance_rotation(state, "crypto", len(ADMITTED), loop.ROTATION_SIZE)
    window, rotates = loop._sweep_window(state, "crypto")
    assert rotates is True
    assert window == list(ADMITTED[72:]) + list(ADMITTED[:8])


def test_the_crypto_cap_counts_cells(tmp_path, monkeypatch):
    """The per-cycle bound is sibling_cap x the cells THIS invocation sweeps
    (D10): 48 for a 12-asset window once the intraday notes are chained (moved
    here from test_switch1.py, which pinned 12)."""
    from .test_composer import seeded_registry
    seen = []
    real_split = composer.split_for_cycle

    def _spy(specs, cap):
        seen.append(cap)
        return real_split(specs, cap)

    monkeypatch.setattr(composer, "split_for_cycle", _spy)
    reg_path, cid = seeded_registry(tmp_path)
    reg = Registry(reg_path)
    for prefix in composer.INTRADAY_NOTE_PREFIXES:      # Task 8's hold is lifted
        reg.append("note", {"text": prefix + " the verbatim body of the note"})
    window = ",".join(ADMITTED[:12])
    rc = composer.run(["--registry", str(reg_path), "--run-id", "cap", "--dry-run",
                       "--sibling-cap", "25", "--assets", window,
                       "--loop-state", str(tmp_path / "loop_state.json")],
                      propose_fn=lambda cards: [_crypto_family(card_ids=[cid])])
    assert rc == 0
    assert seen == [25 * 48]


# ---------------- tradfi untouched ----------------

@pytest.mark.parametrize("asset_class", ["fx", "equity_etf", "bond_etf", "metal_etf"])
def test_tradfi_is_unchanged(asset_class):
    """Active cells and the family expansion of every tradfi class equal a
    fixture dumped from BASE (6f29e438d) before the gate change; their gates
    stay "all" at 1d."""
    from .test_phase2_freeze import TRADFI_FAMILIES, _RUN_ID, _MODEL as M, _CREATED as C
    want = json.loads(TRADFI_FIXTURE.read_text(encoding="utf-8"))["classes"][asset_class]
    assert cells.ACTIVE_CELLS[asset_class] == {"assets": "all", "timeframes": "all"}
    assert [list(c) for c in cells.active_cells(asset_class)] == want["active_cells"]
    specs = composer.expand_family_for_class(TRADFI_FAMILIES[asset_class](),
                                             _RUN_ID, M, C, asset_class)
    canon = json.dumps(specs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert len(specs) == want["n_specs"]
    assert [s["strategy_id"] for s in specs] == want["strategy_ids"]
    assert hashlib.sha256(canon.encode("utf-8")).hexdigest() == want["sha256"]


# ---------------- the brief ----------------

def test_the_crypto_brief_keeps_rules_and_history():
    assert hashlib.sha256(BASE_RULES.encode("utf-8")).hexdigest() == BASE_RULES_SHA256
    brief = composer.system_prompt_for("crypto")
    assert brief is composer.CRYPTO_SYSTEM_PROMPT
    for ticker in ADMITTED:
        assert ticker in brief, ticker
    for ticker in EXCLUDED:
        assert ticker not in brief, ticker
    flat = " ".join(brief.split())        # the brief wraps lines; compare phrases
    assert "48 cells" in flat
    assert "about 8 days at 1h" in flat
    assert "on daily OHLCV bars:" not in flat
    # the new universe text (DRAFT pending Coen's approval, Task 12)
    assert "each on 1h, 4h, 12h and daily OHLCV bars:" in flat
    assert "every cell in the generation's window is its own strategy" in flat
    assert "every asset in the generation's window" not in flat
    assert ("A cell is one asset at one timeframe, so a window of 12 assets "
            "is 48 cells.") in flat
    assert ("Every parameter is in bars of the cell's own timeframe: a lookback of 200 "
            "is 200 days at 1d, 100 days at 12h and about 8 days at 1h.") in flat
    assert ("the volatility-percentile regime ranks against the last 365 days of bars "
            "at every timeframe.") in flat
    assert ("Each bar is a REAL OHLC bar of its timeframe on crypto's 24x7 calendar "
            "(every hour of every day, weekends included)") in flat
    assert "REAL daily OHLC bar" not in flat
    assert "Strategies must be implementable from the cell's own OHLCV bars alone." in flat
    assert "implementable from daily OHLCV alone" not in flat
    # byte-identical to BASE: the measured history and every Rules bullet
    assert brief.count("What happened so far") == 1
    assert PRE_SWITCH_HISTORY in brief
    assert brief.endswith(BASE_RULES)
