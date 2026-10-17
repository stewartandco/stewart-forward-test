"""Switch 1 (SP5 Phase 3): crypto per cell at 1d -- the activated-state pins.

Build 2b-3, plan 1, Task 5. One commit moved crypto off the legacy POOLED
path (one 2-asset BTCUSD+ETHUSD spec per sweep combo) onto the per-cell path
every other class already takes (one single-asset spec per ACTIVE cell),
activated 76 USDT cells at 1d, and flipped crypto's benchmark to "self" with
it. The routing and the benchmark are one fact and moved together; see the
comment block above CLASSES["crypto"] in cells.py.

These tests replace the Phase 2 freeze proof's crypto half
(test_phase2_freeze.py): that module pinned "nothing activated"; this one pins
what was activated, exactly.

Run: python -m pytest pipeline/test_switch1.py -q
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from . import cells, composer, loop, loop_state
from .gauntlet import _benchmark_relative

# Coen 2026-10-17 admission: at least 365 days of history before
# screen.DEFAULT_CUTOFF (2023-12-31), i.e. first daily bar on or before
# 2022-12-31. In cells.ASSETS declaration order (the import-time gate check
# requires it).
ADMITTED = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "TRXUSDT", "ZECUSDT",
    "DOGEUSDT", "LINKUSDT", "ADAUSDT", "XLMUSDT", "BCHUSDT", "LTCUSDT", "HBARUSDT",
    "AVAXUSDT", "SHIBUSDT", "UNIUSDT", "NEARUSDT", "PAXGUSDT", "AAVEUSDT", "DOTUSDT",
    "ICPUSDT", "ETCUSDT", "QNTUSDT", "NEXOUSDT", "ALGOUSDT", "ATOMUSDT", "JSTUSDT",
    "VETUSDT", "FILUSDT", "CAKEUSDT", "INJUSDT", "STXUSDT", "DASHUSDT", "CRVUSDT",
    "APTUSDT", "FETUSDT", "SUNUSDT", "GNOUSDT", "LDOUSDT", "LUNCUSDT", "XTZUSDT",
    "ENSUSDT", "CFXUSDT", "DCRUSDT", "JASMYUSDT", "RAYUSDT", "CVXUSDT", "OPUSDT",
    "IOTAUSDT", "COMPUSDT", "TWTUSDT", "GRTUSDT", "THETAUSDT", "RUNEUSDT", "AXSUSDT",
    "NEOUSDT", "CHZUSDT", "MANAUSDT", "APEUSDT", "XECUSDT", "ARUSDT", "SFPUSDT",
    "SNXUSDT", "1INCHUSDT", "SANDUSDT", "IMXUSDT", "EGLDUSDT", "BATUSDT", "DYDXUSDT",
    "ZENUSDT", "RSRUSDT", "GALAUSDT", "QTUMUSDT", "DGBUSDT", "YFIUSDT")

# First bar in 2024 (10), then first bar 2023-03-17..2023-12-15 (14).
EXCLUDED = tuple(f"{t}USDT" for t in (
    "ENA", "ETHFI", "JUP", "PYTH", "RENDER", "STRK", "TAO", "WIF", "ZK", "ZRO",
    "GAS", "GLM", "PROM", "ARB", "SUI", "FLOKI", "PEPE", "PENDLE", "WLD", "SEI",
    "TIA", "ORDI", "JTO", "BONK"))

# The live tree's bar cache, READ ONLY (a worktree carries no data/).
LIVE_DATA = Path(r"E:\Users\Coen\Claude\stewart-forward-test\research-layer\data")

CRYPTO_BASIS = "price returns, staking/funding yield excluded on both sides"

# The generation-history block of the PRE-switch crypto brief
# (composer.SYSTEM_PROMPT at a1205609b), captured before composer.py was
# edited. The new brief must carry it byte for byte: it is the measured
# history of the pooled BTCUSD+ETHUSD book, and rewording it would change a
# record, not a prompt. Its sha256 at capture is pinned alongside.
PRE_SWITCH_HISTORY = (
    "What happened so far, as measured, not as opinion:\n"
    "\n"
    "Generation 1 registered three families, all long-biased. 13 of its 22 specs\n"
    "passed the screen; all 13 then failed out of sample, because their per-trade\n"
    "edge scaled with a price drift and a volatility level that did not persist.\n"
    "Passive buy-and-hold decayed just as hard over the same window.\n"
    "\n"
    "Generation 2 corrected the long-only bias: all four families were\n"
    "short-capable or symmetric. It still passed nothing. 18 of its 34 specs\n"
    "cleared the screen and all 18 then failed the gauntlet. The detail:\n"
    "- Symmetric z-score reversion lost 46% to 66% over the training window and\n"
    "  failed the screen outright \u2014 the same way generation 1's reversion family\n"
    "  died, on net_negative.\n"
    "- A short-only trend family produced only 10-19 trades in six and a half years\n"
    "  and failed the screen's trade-count floor. Four of its eight specs were also\n"
    "  unprofitable, so the trade count is what it failed on, not the only thing\n"
    "  wrong with it.\n"
    "- Of the 18 that reached the gauntlet, 12 showed POSITIVE\n"
    "  volatility-normalized edge decay out of sample, from +1.8% to +54.3%: they\n"
    "  held or improved their edge per unit of available opportunity. The other 6\n"
    "  ran down to -69.8%, and both families that reached the gauntlet appear in\n"
    "  both groups.\n"
    "- All 18 used vol_target sizing, so nothing here compares sizing rules. Within\n"
    "  that single arm the four worst ruin and the four worst Monte Carlo outcomes\n"
    "  were vol_target by construction. The grammar's other sizing rule,\n"
    "  fixed_fraction, has been registered on 4 specs and has never reached the\n"
    "  gauntlet.\n"
    "\n"
    "Draw your own conclusions from those facts.")
PRE_SWITCH_HISTORY_SHA256 = "8308558603e9a16b76c1cc549de06e693886d45138aff236950bdb05f5ac6a17"

# expand_family_for_class over test_phase2_freeze.TRADFI_FAMILIES, dumped from
# the pre-switch code (a1205609b) before composer.py was edited: per class the
# spec count, the strategy_ids in order, and the sha256 of the canonical JSON
# (sort_keys, compact separators) of the whole expansion.
TRADFI_FIXTURE = Path(__file__).resolve().parent / "fixtures_switch1" / "tradfi_expansion.json"

_RUN, _MODEL, _CREATED = "switch1", "claude-opus-5", "2026-10-17T00:00:00Z"


def _crypto_family(**overrides):
    from .test_composer import good_family
    return good_family(**{"assets": ["BTCUSDT"], **overrides})


# ---------------- the gate ----------------

def test_crypto_active_set_is_the_76_admitted_assets_at_1d():
    assert len(ADMITTED) == 76 and len(EXCLUDED) == 24
    assert cells.active_cells("crypto") == [(a, "1d") for a in ADMITTED]
    assert cells.ACTIVE_CELLS["crypto"] == {"assets": ADMITTED, "timeframes": ("1d",)}
    active_assets = {a for a, _ in cells.active_cells("crypto")}
    assert not active_assets & set(EXCLUDED)
    # the 24 are still DECLARED (data work stays legal); they are just not swept
    assert set(EXCLUDED) <= set(cells.ASSETS)
    assert set(ADMITTED) | set(EXCLUDED) == set(cells.ASSETS)


@pytest.mark.parametrize("asset", ADMITTED)
def test_every_active_crypto_cell_meets_the_admission_floor_on_the_live_data(asset):
    path = LIVE_DATA / f"{asset}_1d.csv"
    if not path.exists():
        pytest.skip(f"{path} absent (a clone or a machine without the live bar cache)")
    with path.open(encoding="utf-8") as fh:
        fh.readline()                       # header
        first = fh.readline()
    assert first[:10] <= "2022-12-31", (
        f"{asset}: first daily bar {first[:10]} is after the 2022-12-31 admission "
        f"line (365 days before screen.DEFAULT_CUTOFF)")


# ---------------- routing + benchmark: one fact ----------------

def test_crypto_routes_per_cell_and_benchmarks_itself():
    """The coupling pin in its landed form. benchmark "self" needs exactly one
    asset per cell, which only the per-cell expander provides; a per-cell
    route with benchmark None would write no control on any crypto verdict
    (absence reads as "not applicable"), silently. Both halves, together."""
    assert composer.expander_for("crypto") is composer.expand_family_for_class
    assert cells.CLASSES["crypto"]["benchmark"] == "self"
    for cls in cells.CLASSES:
        assert composer.expander_for(cls) is composer.expand_family_for_class, cls


def test_the_pooled_expander_is_gone():
    for name in ("expand_family", "ALLOWED_ASSETS", "UNIVERSE_BASE", "COST_MODEL"):
        assert not hasattr(composer, name), f"composer.{name} survived the switch"
    assert not hasattr(loop, "expand_family")


def test_a_crypto_family_expands_one_single_asset_spec_per_window_cell():
    window = list(ADMITTED[:12])
    fam = _crypto_family()
    assert composer.validate_family(fam, set(fam["card_ids"]), 60) == []
    specs = composer.expand_family_for_class(fam, _RUN, _MODEL, _CREATED, "crypto",
                                             assets=window)
    n_combos = 9                                   # good_family: 3 x 3 sweep
    assert len(specs) == n_combos * 12
    for spec in specs:
        uni = spec["universe"]
        assert uni["assets"][0] in window
        assert uni == {"assets": [uni["assets"][0]], "asset_class": "crypto",
                       "timeframe": "1d", "session": "24x7"}
        assert "USDT" not in spec["name"] and "USD" not in spec["name"].split()[0]
        assert spec["name"].startswith(uni["assets"][0][:-4] + " 1d ")
        assert spec["cost_model"] == cells.CLASSES["crypto"]["cost_model"]
    assert len({s["strategy_id"] for s in specs}) == len(specs)
    per_asset = {a: sum(1 for s in specs if s["universe"]["assets"] == [a]) for a in window}
    assert set(per_asset.values()) == {n_combos}


def test_the_usdt_name_rule_does_not_touch_tradfi_or_legacy_names():
    assert composer._build_name(["SOLUSDT"], "f", [], "1d") == "SOL 1d f"
    assert composer._build_name(["1INCHUSDT"], "f", [], "1d") == "1INCH 1d f"
    assert composer._build_name(["BTCUSD", "ETHUSD"], "f", [], "1d") == "BTC+ETH 1d f"
    assert composer._build_name(["EUR"], "f", [], "1d") == "EUR 1d f"
    assert composer._build_name(["SPY"], "f", [], "1d") == "SPY 1d f"


def test_the_crypto_cap_counts_cells(tmp_path, monkeypatch):
    """The per-cycle bound is sibling_cap x the cells THIS invocation sweeps,
    for crypto exactly as for every other class (D10)."""
    from .test_composer import seeded_registry
    seen = []
    real_split = composer.split_for_cycle

    def _spy(specs, cap):
        seen.append(cap)
        return real_split(specs, cap)

    monkeypatch.setattr(composer, "split_for_cycle", _spy)
    reg_path, cid = seeded_registry(tmp_path)
    window = ",".join(ADMITTED[:12])
    rc = composer.run(["--registry", str(reg_path), "--run-id", "cap", "--dry-run",
                       "--sibling-cap", "25", "--assets", window,
                       "--loop-state", str(tmp_path / "loop_state.json")],
                      propose_fn=lambda cards: [_crypto_family(card_ids=[cid])])
    assert rc == 0
    assert seen == [25 * 12]


def test_rotation_windows_the_76_assets_twelve_at_a_time():
    """Rotation is live for crypto now (both gates open). The cursor walks
    12 at a time over the 76 and WRAPS: after 6 advances it sits at 72, so
    the 7th window is the last 4 assets followed by the first 8
    (loop_state.rotation_window as it is; no short window is ever emitted)."""
    state = {"classes": {}}
    assert loop._sweep_window(state, "crypto") == (list(ADMITTED[:12]), True)
    for _ in range(6):
        loop_state.advance_rotation(state, "crypto", len(ADMITTED), loop.ROTATION_SIZE)
    window, rotates = loop._sweep_window(state, "crypto")
    assert rotates is True
    assert window[:4] == list(ADMITTED[72:])
    assert window == list(ADMITTED[72:]) + list(ADMITTED[:8])
    # every asset is reached in the first 7 windows (76 <= 7 x 12)
    state = {"classes": {}}
    reached = set()
    for _ in range(7):
        reached |= set(loop._sweep_window(state, "crypto")[0])
        loop_state.advance_rotation(state, "crypto", len(ADMITTED), loop.ROTATION_SIZE)
    assert reached == set(ADMITTED)


def test_a_crypto_verdict_carries_the_self_benchmark():
    cutoff = "2023-12-31"
    bars = {"SOLUSDT": [
        {"date": "2023-12-31 00:00:00", "open": 9.0, "high": 9.0, "low": 9.0,
         "close": 9.0, "volume": 1.0},                       # on cutoff: IS
        {"date": "2024-01-01 00:00:00", "open": 100.0, "high": 100.0, "low": 100.0,
         "close": 100.0, "volume": 1.0},                     # first OOS bar
        {"date": "2024-06-01 00:00:00", "open": 105.0, "high": 125.0, "low": 104.0,
         "close": 120.0, "volume": 1.0},                     # last OOS bar
    ]}
    spec = {"strategy_id": "x",
            "universe": {"assets": ["SOLUSDT"], "asset_class": "crypto"}}
    result = _benchmark_relative(spec, bars, strategy_net=0.05, cutoff=cutoff)
    per_side = 0.001 + 0.0005                                # crypto cost_model
    expected = (120.0 / 100.0 - 1) - 2 * per_side
    assert result == {"window": "oos", "strategy_net": 0.05,
                      "buy_hold_net": pytest.approx(expected, abs=1e-12),
                      "excess": pytest.approx(0.05 - expected, abs=1e-12),
                      "basis": CRYPTO_BASIS}


# ---------------- the brief ----------------

def test_the_crypto_brief_names_the_active_assets_and_keeps_the_history():
    assert hashlib.sha256(PRE_SWITCH_HISTORY.encode("utf-8")).hexdigest() \
        == PRE_SWITCH_HISTORY_SHA256
    brief = composer.system_prompt_for("crypto")
    assert brief is composer.CRYPTO_SYSTEM_PROMPT
    assert not hasattr(composer, "SYSTEM_PROMPT")
    for ticker in ADMITTED:
        assert ticker in brief, ticker
    for ticker in EXCLUDED:
        assert ticker not in brief, ticker
    assert "76" in brief
    assert PRE_SWITCH_HISTORY in brief
    assert brief.count("What happened so far") == 1
    # introduced as the pooled book's history, immediately before it
    lead = brief[:brief.index(PRE_SWITCH_HISTORY)]
    assert "BTCUSD+ETHUSD" in lead.split("\n\n")[-2]
    assert "BTCUSD, ETHUSD" not in brief
    assert CRYPTO_BASIS.replace(" on both sides", "") in brief
    assert "today's top-100" in brief
    assert "implementable from daily OHLCV alone" in brief
    assert "financing" not in brief
    cost = cells.CLASSES["crypto"]["cost_model"]
    assert f"{cost['commission_per_side']:.5f}" in brief
    assert f"{cost['slippage_ticks']:.5f}" in brief


def test_the_crypto_schema_enum_is_the_active_assets():
    schema = composer.proposal_schema_for("crypto")
    enum = schema["properties"]["families"]["items"]["properties"]["assets"]["items"]["enum"]
    assert enum == list(ADMITTED)
    # the proposal schema's SHAPE is unchanged: only the enum is class-specific
    fx = composer.proposal_schema_for("fx")
    fx_enum = fx["properties"]["families"]["items"]["properties"]["assets"]["items"]
    crypto_enum = schema["properties"]["families"]["items"]["properties"]["assets"]["items"]
    assert fx.keys() == schema.keys() and fx_enum.keys() == crypto_enum.keys()


def test_validate_family_checks_crypto_against_the_declared_grid():
    """BTCUSD / ETHUSD are not declared crypto assets; a family naming them is
    refused like any other undeclared ticker."""
    fam = _crypto_family(assets=["BTCUSD"])
    errs = composer.validate_family(fam, set(fam["card_ids"]), 60)
    assert any("crypto assets" in e for e in errs), errs
    assert composer.validate_family(_crypto_family(), {"aaaaaaaaaaaaaaaa"}, 60) == []


# ---------------- tradfi untouched ----------------

@pytest.mark.parametrize("asset_class", ["fx", "equity_etf", "bond_etf", "metal_etf"])
def test_tradfi_expansion_is_byte_identical(asset_class):
    from .test_phase2_freeze import TRADFI_FAMILIES, _RUN_ID, _MODEL as M, _CREATED as C
    want = json.loads(TRADFI_FIXTURE.read_text(encoding="utf-8"))["classes"][asset_class]
    specs = composer.expand_family_for_class(TRADFI_FAMILIES[asset_class](),
                                             _RUN_ID, M, C, asset_class)
    canon = json.dumps(specs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert len(specs) == want["n_specs"]
    assert [s["strategy_id"] for s in specs] == want["strategy_ids"]
    assert hashlib.sha256(canon.encode("utf-8")).hexdigest() == want["sha256"]


# ---------------- the grid record note ----------------

GRID_NOTE = Path(__file__).resolve().parent.parent / "docs" / "notes" / "crypto-grid-record-v1.md"

# First daily bar of the 24 excluded assets (live data, 2026-10-17), as stated
# in the note.
EXCLUDED_FIRST_BAR = {
    "ARB": "2023-03-23", "BONK": "2023-12-15", "ENA": "2024-04-02",
    "ETHFI": "2024-03-18", "FLOKI": "2023-05-05", "GAS": "2023-03-17",
    "GLM": "2023-03-17", "JTO": "2023-12-07", "JUP": "2024-01-31",
    "ORDI": "2023-11-07", "PENDLE": "2023-07-03", "PEPE": "2023-05-05",
    "PROM": "2023-03-17", "PYTH": "2024-02-02", "RENDER": "2024-07-26",
    "SEI": "2023-08-15", "STRK": "2024-02-20", "SUI": "2023-05-03",
    "TAO": "2024-04-11", "TIA": "2023-10-31", "WIF": "2024-03-05",
    "WLD": "2023-07-24", "ZK": "2024-06-17", "ZRO": "2024-06-20",
}


def _note_line(text: str, label: str) -> str:
    """The text after `label` on the one line that starts with it."""
    hits = [ln for ln in text.split("\n") if ln.startswith(label)]
    assert len(hits) == 1, f"expected exactly one line starting {label!r}, got {len(hits)}"
    return hits[0][len(label):]


def test_the_grid_record_matches_the_gate():
    """docs/notes/crypto-grid-record-v1.md states the Switch 1 gate exactly.

    The note is prose, but four of its lines are machine-parseable, each one
    line starting with a fixed label:

      Phase 1 cells (active from this entry): timeframe 1d on assets: A, B, ...
      Phase 2 cells (inactive until their own Coen-gated commit): timeframes
          1h, 4h, 12h on the same assets
      Phase 3 cells (inactive until their own Coen-gated commit): timeframes
          15m, 30m on the same assets
      Excluded assets (24, not swept): TICKER first bar YYYY-MM-DD, ...

    (each phase line is a single line in the file; the wrap above is only
    this docstring's). Assets are comma-separated tickers in manifest order;
    timeframes are comma-separated.
    """
    raw = GRID_NOTE.read_bytes()
    assert b"\r" not in raw, "the note must be LF (the chain-note guard refuses CR)"
    text = raw.decode("utf-8")
    assert text.startswith("crypto-grid-record-v1: ")
    assert not text.startswith("#"), "no markdown heading: the first line is the prefix"

    # phase 1: the assets are the active assets, in order, and the timeframe is 1d
    p1 = _note_line(text, "Phase 1 cells (active from this entry): timeframe ")
    tf, assets_txt = p1.split(" on assets: ")
    assert (tf,) == cells.ACTIVE_CELLS["crypto"]["timeframes"] == ("1d",)
    assets = tuple(a.strip() for a in assets_txt.rstrip(".").split(","))
    assert len(assets) == 76
    assert assets == cells.ACTIVE_CELLS["crypto"]["assets"] == ADMITTED

    # phases 2 and 3: later timeframes on the same assets, nothing else
    p2 = _note_line(text, "Phase 2 cells (inactive until their own Coen-gated commit): timeframes ")
    assert p2.rstrip(".") == "1h, 4h, 12h on the same assets"
    p3 = _note_line(text, "Phase 3 cells (inactive until their own Coen-gated commit): timeframes ")
    assert p3.rstrip(".") == "15m, 30m on the same assets"

    # the excluded set is exactly the declared-but-not-admitted assets, each with its first bar
    ex = _note_line(text, "Excluded assets (24, not swept): ")
    got = {}
    for part in ex.rstrip(".").split(","):
        ticker, date = part.strip().split(" first bar ")
        got[ticker] = date
    assert {f"{t}USDT" for t in got} == set(cells.ASSETS) - set(ADMITTED)
    assert got == EXCLUDED_FIRST_BAR
    assert all(d > "2022-12-31" for d in got.values())          # every one fails the admission line
    assert {t for t, d in got.items() if d.startswith("2024")} == {
        "ENA", "ETHFI", "JUP", "PYTH", "RENDER", "STRK", "TAO", "WIF", "ZK", "ZRO"}
    assert min(got.values()) == "2023-03-17" and "2023-12-15" in got.values()

    # the statements the note must make
    for needle in ("2023-12-31", "365 days", "fixed by this note", "RETIRED",
                   "BTCUSD", "ETHUSD", "counted trial", "arithmetic", "never evidence of edge",
                   "the same 76 are used in every phase", "crypto_universe_manifest.json",
                   # per-cell exclusions in later phases (spec D-d1, s8.3: an
                   # intraday file can start later than its asset's 1d file)
                   "A phase 2 or phase 3 cell whose own first bar falls after "
                   "2022-12-31 is excluded when its phase is activated and is "
                   "named in that phase's note",
                   "an exclusion never adds a cell"):
        assert needle in text, needle
