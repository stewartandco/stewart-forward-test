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
from .test_screen import write_data_dir, dated_target_hit_bars, screening_registry


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


# ---------------- end-to-end: mixed crypto+fx registry ----------------------
#
# Everything above exercises the per-class helpers in isolation. Nothing
# forced gauntlet.run() itself to walk its per-class wiring (the spec_class
# lookup, the eras fetch, the intersection recording at the metrics-append
# site) on any branch but the no-op single-class one -- this closes that gap
# with a real mixed registry through the real CLI entrypoint.

import datetime

from .common import content_id


def eur_flat_bars():
    """15 weekday-only bars, 2023-01-02..2023-01-20 (a Monday through a
    Friday), flat close. fast==slow at every warm bar on a flat series, so
    ma_cross never crosses -- zero trades, deliberately: evaluate_spec's
    empty-trades path (contributions([]) == [], bootstrap_paths([], ...))
    is already exercised elsewhere, and the fx spec's only job here is its
    CALENDAR SHAPE, weekday-only against BTCUSD's every-day one."""
    d, end = datetime.date(2023, 1, 2), datetime.date(2023, 1, 20)
    bars = []
    while d <= end:
        if d.weekday() < 5:
            bars.append({"date": d.isoformat(), "open": 1.10, "high": 1.10,
                        "low": 1.10, "close": 1.10, "volume": 0.0})
        d += datetime.timedelta(days=1)
    return bars


def _fx_spec(card_id):
    return {
        "strategy_id": None, "version": 1,
        "created_utc": "2026-08-24T00:00:00Z",
        "name": "fx mixed-class test", "family": "fx_mixed_test",
        "universe": {"assets": ["EUR"], "asset_class": "fx",
                     "timeframe": "1d", "session": "fx_5d"},
        "blocks": [
            {"role": "entry", "type": "ma_cross", "params": {"fast": 3, "slow": 5}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "provenance": {"card_ids": [card_id], "parent_strategy_id": None,
                       "sibling_group_id": "fx-mixed-test-g", "generation": 0},
        "generator": {"agent": "composer", "model": "m",
                      "pipeline_version": "g1.0.0", "run_id": "t"},
        "cost_model": dict(cells.FX_COST_MODEL),
    }


def mixed_class_gauntlet_registry(tmp_path):
    """One crypto strategy (screening_registry's usual fixture) and one fx
    strategy, BOTH advanced to 'gauntlet' state in the SAME registry -- the
    only way a real run ever spans >1 class."""
    reg, crypto_spec = screening_registry(tmp_path)
    reg.append("note", {"text": "screen-protocol-v1: test anchor"})
    reg.record_state_change(crypto_spec["strategy_id"], "screened", "test")
    reg.record_verdict(crypto_spec["strategy_id"], "screened", "pass",
                       {"trades": 50, "net_pnl": 0.5, "win_rate": 0.5,
                        "max_dd": -0.1}, "0" * 64)
    reg.record_state_change(crypto_spec["strategy_id"], "gauntlet", None)

    fx_spec = _fx_spec(crypto_spec["provenance"]["card_ids"][0])
    fx_spec["strategy_id"] = content_id(fx_spec, "strategy_id")
    reg.register_strategy(fx_spec)
    reg.record_state_change(fx_spec["strategy_id"], "screened", "test")
    reg.record_verdict(fx_spec["strategy_id"], "screened", "pass",
                       {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0,
                        "max_dd": 0.0}, "0" * 64)
    reg.record_state_change(fx_spec["strategy_id"], "gauntlet", None)

    chain_gauntlet_note(reg)
    return reg, crypto_spec, fx_spec


def test_mixed_registry_e2e_intersection_and_era_summary(tmp_path):
    from .gauntlet import run as gauntlet_run

    reg, crypto_spec, fx_spec = mixed_class_gauntlet_registry(tmp_path)
    # BTCUSD: every calendar day 2023-01-01..2023-01-23 (dated_target_hit_bars).
    # EUR: weekdays only, 2023-01-02..2023-01-20, wholly inside BTC's range and
    # ending 3 calendar days before it -- right at the cross-class allowance.
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars(),
                                     "EUR": eur_flat_bars()})
    art = tmp_path / "art"
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(art)])
    assert rc == 0

    verdicts = {e["payload"]["strategy_id"]: e["payload"]
               for e in reg.entries() if e["entry_type"] == "verdict"
               and e["payload"]["stage"] == "gauntlet"}
    assert len(verdicts) == 2

    # Each series' own first bar drops out of its return-dated series
    # (daily_returns_with_dates), so EUR contributes 14 return-dates
    # (2023-01-03..2023-01-20, all weekdays) and every one of them falls
    # inside BTC's continuous 2023-01-02..2023-01-23 return-date range --
    # the intersection is exactly EUR's 14 dates.
    fx_m = verdicts[fx_spec["strategy_id"]]["metrics"]
    assert fx_m["trials_alignment"] == "intersection"
    assert fx_m["trials_common_days"] == 14
    assert set(fx_m["era_summary"]) == {"pre_gfc", "gfc_zirp", "tightening",
                                        "post_2022"}

    # The run-level alignment fields are recorded on EVERY verdict of a
    # mixed run, crypto included; only era_summary is per-spec-class.
    crypto_m = verdicts[crypto_spec["strategy_id"]]["metrics"]
    assert crypto_m["trials_alignment"] == "intersection"
    assert crypto_m["trials_common_days"] == 14
    assert "era_summary" not in crypto_m


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
