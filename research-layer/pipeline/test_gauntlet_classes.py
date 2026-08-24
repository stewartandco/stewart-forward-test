"""Tests for SP4 Track 1 Task 5: mixed-class-safe gauntlet, fx era summaries.

Crypto-only behaviour must stay byte-identical (regression-covered by
test_gauntlet.py, unedited). These tests cover the new per-class machinery:
per-class cell comparability, intersection-aligned clustering across classes,
fx era summaries recorded (never gated), and the T3-review rider threading
periods_per_year into pipeline/quarantine.py's forward simulation.

Run: python -m pytest pipeline/test_gauntlet_classes.py -q
"""
from __future__ import annotations

import pytest

from . import cells
from .cluster import effective_trials
from .gauntlet import (check_aligned, daily_returns_with_dates,
                       era_summary, intersect_returns)
from .screen import assert_cells_comparable


# ---------------- Step 3: per-class cell comparability ---------------------

def test_cells_comparable_per_class():
    # crypto (Saturday close) vs fx (Friday fix), 1 calendar day apart across
    # classes -- passes under the <=3-day cross-class allowance.
    assert_cells_comparable(
        {"BTCUSDT_1d": "2026-08-22", "EUR_1d": "2026-08-21"},
        class_of={"BTCUSDT_1d": "crypto", "EUR_1d": "fx"})

    # two fx cells ending on different days: the WITHIN-class same-day rule
    # still applies, so this still raises even though both are fx.
    with pytest.raises(ValueError, match="within class 'fx'"):
        assert_cells_comparable(
            {"EUR_1d": "2026-08-21", "GBP_1d": "2026-08-19"},
            class_of={"EUR_1d": "fx", "GBP_1d": "fx"})

    # a zero-bar cell still raises, class_of supplied or not
    with pytest.raises(ValueError, match="no bars"):
        assert_cells_comparable(
            {"BTCUSDT_1d": "2026-08-22", "EUR_1d": ""},
            class_of={"BTCUSDT_1d": "crypto", "EUR_1d": "fx"})

    # cross-class ends more than 3 calendar days apart still raise
    with pytest.raises(ValueError, match="3 calendar days"):
        assert_cells_comparable(
            {"BTCUSDT_1d": "2026-08-22", "EUR_1d": "2026-08-10"},
            class_of={"BTCUSDT_1d": "crypto", "EUR_1d": "fx"})

    # class_of=None keeps today's exact single-class behaviour: same-day
    # everywhere, no cross-class allowance at all.
    with pytest.raises(ValueError):
        assert_cells_comparable(
            {"BTCUSDT_1d": "2026-08-22", "EUR_1d": "2026-08-21"})


# ---------------- Step 3: intersection-aligned clustering ------------------

def test_effective_trials_intersection_alignment():
    # A on a weekday-only calendar (skips 2026-01-04, a Sunday), B on an
    # all-days calendar over the same week.
    equity_a = [("2026-01-01", 1.00), ("2026-01-02", 1.01),
                ("2026-01-03", 1.02), ("2026-01-05", 1.03),
                ("2026-01-06", 1.04)]
    equity_b = [("2026-01-01", 1.00), ("2026-01-02", 1.02),
                ("2026-01-03", 1.01), ("2026-01-04", 1.03),
                ("2026-01-05", 1.02), ("2026-01-06", 1.05)]
    dated = {"A": daily_returns_with_dates(equity_a),
            "B": daily_returns_with_dates(equity_b)}

    aligned, common = intersect_returns(dated)

    # A never has a return dated 01-04 (no bar that day), so the intersection
    # is exactly A's date set, dropping B's 01-04 entry.
    assert common == ["2026-01-02", "2026-01-03", "2026-01-05", "2026-01-06"]
    assert len(aligned["A"]) == len(aligned["B"]) == len(common) == 4

    check_aligned(aligned)          # must not raise: lengths now agree
    trials_n, labels, var = effective_trials(aligned)
    assert trials_n >= 1
    assert set(labels) == {"A", "B"}


def test_intersect_returns_empty_when_no_shared_dates():
    dated = {"A": [("2026-01-01", 0.01)], "B": [("2026-02-01", 0.02)]}
    aligned, common = intersect_returns(dated)
    assert common == []
    assert aligned == {"A": [], "B": []}


# ---------------- Step 3: fx era summaries ----------------------------------

def trade(entry_date, ret, frac=1.0):
    return {"entry_date": entry_date, "return_net": ret, "notional_frac": frac}


def test_fx_era_summaries_recorded():
    trades = [
        trade("2005-06-01", 0.01),     # pre_gfc
        trade("2010-01-01", -0.02),    # gfc_zirp
        trade("2010-06-01", 0.015),    # gfc_zirp (second trade, same era)
        trade("2023-01-01", 0.03),     # post_2022
    ]
    summary = era_summary(trades, cells.FX_ERAS)

    assert set(summary) == {"pre_gfc", "gfc_zirp", "tightening", "post_2022"}
    assert summary["pre_gfc"] == {"n_trades": 1, "net_pnl": pytest.approx(0.01)}
    assert summary["gfc_zirp"]["n_trades"] == 2
    assert summary["gfc_zirp"]["net_pnl"] == pytest.approx(
        (1 - 0.02) * (1 + 0.015) - 1)
    assert summary["tightening"] == {"n_trades": 0, "net_pnl": pytest.approx(0.0)}
    assert summary["post_2022"] == {"n_trades": 1, "net_pnl": pytest.approx(0.03)}


def test_crypto_class_declares_no_eras():
    # cells.CLASSES["crypto"]["eras"] == () is the switch the gauntlet reads
    # to decide whether a verdict gets an era_summary key at all.
    assert cells.CLASSES["crypto"]["eras"] == ()


# ---------------- end-to-end: crypto path unaffected ------------------------

from .test_gauntlet import gauntlet_registry, chain_gauntlet_note
from .test_screen import write_data_dir, dated_target_hit_bars


def test_crypto_verdict_native_alignment_no_era_summary(tmp_path):
    """A single-class (crypto-only) real run through gauntlet.run(): the new
    metrics keys are present and correct, but nothing about the crypto
    numbers themselves is disturbed (test_gauntlet.py pins that)."""
    from .gauntlet import run as gauntlet_run
    from .registry import Registry

    reg, spec = gauntlet_registry(tmp_path)
    chain_gauntlet_note(reg)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    art = tmp_path / "art"
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(art)])
    assert rc == 0

    verdicts = [e for e in reg.entries() if e["entry_type"] == "verdict"
                and e["payload"]["stage"] == "gauntlet"]
    assert len(verdicts) == 1
    m = verdicts[0]["payload"]["metrics"]
    assert m["trials_alignment"] == "native"
    assert m["trials_common_days"] is None
    assert "era_summary" not in m


# ---------------- Step 2b (T3-review rider): quarantine threading ----------

from .quarantine import observe_day
from .engine import simulate_asset
from .test_engine_classes import mk_bars, short_closes, short_spec, MAX_BARS


def test_quarantine_threads_periods_per_year_for_fx_specs():
    """quarantine.py:235's simulate_asset call must derive periods_per_year
    from the spec's session exactly like run_spec does, or an fx spec's
    forward record would silently diverge from its screen/gauntlet numbers
    (spec s10.11)."""
    bars = mk_bars(short_closes())
    with_fin = {"commission_per_side": 0.00005, "slippage_ticks": 0.00010,
               "short_financing_per_year": -0.015}
    spec = short_spec(with_fin, session="fx_5d")
    bars_by_asset = {"EUR": bars}
    entered, date = bars[0]["date"], bars[-1]["date"]

    rows = observe_day(spec, bars_by_asset, date, entered)
    row = next(r for r in rows if r["asset"] == "EUR")

    # entered == the first bar, so the rebase baseline is exactly 1.0 and
    # row["equity"] is directly comparable to a raw simulate_asset() curve.
    direct_261 = simulate_asset(spec["blocks"], bars, spec["cost_model"],
                                periods_per_year=261)
    assert row["equity"] == pytest.approx(direct_261["equity"][-1])

    # Not vacuous: the un-threaded default (365) gives a DIFFERENT number, so
    # this test would have failed against the pre-fix call site.
    direct_365 = simulate_asset(spec["blocks"], bars, spec["cost_model"],
                                periods_per_year=365)
    assert direct_365["equity"][-1] != pytest.approx(row["equity"])
    assert cells.SESSION_PERIODS.get(spec["universe"]["session"]) == 261
