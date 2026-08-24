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
from .gauntlet import (MIN_TRIALS_COMMON_DAYS, check_aligned,
                       daily_returns_with_dates, era_summary, intersect_returns)
from .registry import Registry
from .screen import assert_cells_comparable


# ---------------- Step 3: per-class cell comparability ---------------------

def test_cells_comparable_per_class():
    # crypto (Saturday close) vs fx (Friday fix), 1 calendar day apart across
    # classes -- passes well within the allowance.
    assert_cells_comparable(
        {"BTCUSDT_1d": "2026-08-22", "EUR_1d": "2026-08-21"},
        class_of={"BTCUSDT_1d": "crypto", "EUR_1d": "fx"})

    # two fx cells ending on different days: the WITHIN-class same-day rule
    # still applies, so this still raises even though both are fx -- fx's
    # own declared max_end_lag_days never widens the WITHIN-class rule, only
    # the cross-class one.
    with pytest.raises(ValueError, match="within class 'fx'"):
        assert_cells_comparable(
            {"EUR_1d": "2026-08-21", "GBP_1d": "2026-08-19"},
            class_of={"EUR_1d": "fx", "GBP_1d": "fx"})

    # a zero-bar cell still raises, class_of supplied or not
    with pytest.raises(ValueError, match="no bars"):
        assert_cells_comparable(
            {"BTCUSDT_1d": "2026-08-22", "EUR_1d": ""},
            class_of={"BTCUSDT_1d": "crypto", "EUR_1d": "fx"})

    # Real-run finding (2026-08-24): FRED H.10 (the fx snapshot's source) is
    # a WEEKLY release, so a fetch just before its Monday post can leave fx
    # up to 9 calendar days behind a same-day crypto fetch. cells.CLASSES
    # declares fx's max_end_lag_days=10, so the allowance is 3+10=13 and a
    # 9-day gap PASSES -- this exact shape crashed before this fix.
    assert_cells_comparable(
        {"BTCUSD_1d": "2026-08-23", "AUD_1d": "2026-08-14"},
        class_of={"BTCUSD_1d": "crypto", "AUD_1d": "fx"})

    # A 14-day gap exceeds the 13-day allowance (3 base + fx's declared 10)
    # and still raises, naming the allowance.
    with pytest.raises(ValueError, match=r"13-day allowance"):
        assert_cells_comparable(
            {"BTCUSD_1d": "2026-08-23", "AUD_1d": "2026-08-09"},
            class_of={"BTCUSD_1d": "crypto", "AUD_1d": "fx"})

    # crypto-vs-crypto: same-class, so the WITHIN-class same-day rule applies
    # regardless of any class's declared max_end_lag_days -- unchanged from
    # before this field existed (regression).
    with pytest.raises(ValueError, match="within class 'crypto'"):
        assert_cells_comparable(
            {"BTCUSDT_1d": "2026-08-22", "ETHUSDT_1d": "2026-08-21"},
            class_of={"BTCUSDT_1d": "crypto", "ETHUSDT_1d": "crypto"})

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


# ---------------- end-to-end: mixed crypto+fx registry, unequal history ----
#
# Everything above exercises the per-class helpers in isolation. Nothing
# forced gauntlet.run() itself to walk its per-class wiring (the spec_class
# lookup, the eras fetch, the intersection recording at the metrics-append
# site) on any branch but the no-op single-class one -- this closes that gap
# with a real mixed registry through the real CLI entrypoint.
#
# The real dry-run generation (Task 7, 2026-08-24) hit a defect this earlier
# version of the test could not have caught: it used two EQUAL-length fx
# fixtures, so gauntlet.py:544's check_aligned(returns_by_id) never fired on
# a RAGGED same-class calendar. The real run registered 12 fx pairs whose
# genuine historical inception dates differ by decades (most 1971, EUR 1999,
# ZAR/SGD 1980/81, MXN 1993 -- verified against the pinned CSVs: every one of
# the 12 files' row count equals its unique-date count, so this is real
# history, not a duplicate-date bug), and check_aligned raised before
# intersection ever ran. This fixture reproduces that shape directly: two fx
# pairs with genuinely different history starts, plus a crypto spec, all in
# one registry.

import datetime

from .common import content_id


def _daily_bars(start: datetime.date, end: datetime.date,
               price: float = 100.0) -> list[dict]:
    """Every calendar day from start to end, flat close. fast==slow at every
    warm ma_cross bar on a flat series, so entries never fire -- zero trades,
    deliberately (evaluate_spec's empty-trades path is exercised elsewhere;
    these fixtures exist for their CALENDAR SHAPE only)."""
    bars, d = [], start
    while d <= end:
        bars.append({"date": d.isoformat(), "open": price, "high": price,
                    "low": price, "close": price, "volume": 0.0})
        d += datetime.timedelta(days=1)
    return bars


def _weekday_bars(start: datetime.date, end: datetime.date,
                  price: float = 1.10) -> list[dict]:
    """Weekdays only (the fx_5d session) from start to end, flat close."""
    return [b for b in _daily_bars(start, end, price)
            if datetime.date.fromisoformat(b["date"]).weekday() < 5]


def _fx_spec(asset: str, card_id: str, group: str):
    return {
        "strategy_id": None, "version": 1,
        "created_utc": "2026-08-24T00:00:00Z",
        "name": f"fx mixed-class test {asset}", "family": f"fx_mixed_test_{asset}",
        "universe": {"assets": [asset], "asset_class": "fx",
                     "timeframe": "1d", "session": "fx_5d"},
        "blocks": [
            {"role": "entry", "type": "ma_cross", "params": {"fast": 3, "slow": 5}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "provenance": {"card_ids": [card_id], "parent_strategy_id": None,
                       "sibling_group_id": group, "generation": 0},
        "generator": {"agent": "composer", "model": "m",
                      "pipeline_version": "g1.0.0", "run_id": "t"},
        "cost_model": dict(cells.FX_COST_MODEL),
    }


def _advance_to_gauntlet(reg, spec, trades=0, net_pnl=0.0):
    reg.record_state_change(spec["strategy_id"], "screened", "test")
    reg.record_verdict(spec["strategy_id"], "screened", "pass",
                       {"trades": trades, "net_pnl": net_pnl, "win_rate": 0.0,
                        "max_dd": 0.0}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "gauntlet", None)


def mixed_class_gauntlet_registry(tmp_path):
    """One crypto strategy (screening_registry's usual fixture) and TWO fx
    strategies with UNEQUAL history starts, all advanced to 'gauntlet' state
    in the SAME registry. GBP's calendar starts years before EUR's, exactly
    the same-class raggedness shape the real dry-run hit -- not just the
    cross-class one the previous version of this fixture covered."""
    reg, crypto_spec = screening_registry(tmp_path)
    reg.append("note", {"text": "screen-protocol-v1: test anchor"})
    _advance_to_gauntlet(reg, crypto_spec, trades=50, net_pnl=0.5)

    card_id = crypto_spec["provenance"]["card_ids"][0]
    gbp_spec = _fx_spec("GBP", card_id, "fx-mixed-test-gbp")
    gbp_spec["strategy_id"] = content_id(gbp_spec, "strategy_id")
    reg.register_strategy(gbp_spec)
    _advance_to_gauntlet(reg, gbp_spec)

    eur_spec = _fx_spec("EUR", card_id, "fx-mixed-test-eur")
    eur_spec["strategy_id"] = content_id(eur_spec, "strategy_id")
    reg.register_strategy(eur_spec)
    _advance_to_gauntlet(reg, eur_spec)

    chain_gauntlet_note(reg)
    return reg, crypto_spec, gbp_spec, eur_spec


def test_mixed_registry_e2e_unequal_history_intersects_before_check_aligned(tmp_path):
    from .gauntlet import run as gauntlet_run

    reg, crypto_spec, gbp_spec, eur_spec = mixed_class_gauntlet_registry(tmp_path)
    # BTCUSD: every calendar day of 2020 (366 days, a leap year).
    # GBP: weekdays only, 2015-01-01..2020-12-31 -- the LONG-history fx pair
    #      (analogous to the real pairs' 1971 starts).
    # EUR: weekdays only, 2020-01-01..2020-12-31 -- starts YEARS later than
    #      GBP (analogous to the real EUR pair's 1999 launch), so it is the
    #      binding constraint on the shared calendar.
    # All three END on the same day, so assert_cells_comparable's same-day
    # (crypto/crypto n/a here) and <=3-day cross-class rule are both trivially
    # satisfied; the interesting raggedness is entirely in the START dates,
    # which is exactly what tripped check_aligned in the real dry-run.
    data = write_data_dir(tmp_path, {
        "BTCUSD": _daily_bars(datetime.date(2020, 1, 1), datetime.date(2020, 12, 31)),
        "GBP": _weekday_bars(datetime.date(2015, 1, 1), datetime.date(2020, 12, 31)),
        "EUR": _weekday_bars(datetime.date(2020, 1, 1), datetime.date(2020, 12, 31)),
    })
    art = tmp_path / "art"
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(art)])
    assert rc == 0          # would have raised "cannot cluster ragged return
                            # series" pre-fix -- completing at all is the point

    verdicts = {e["payload"]["strategy_id"]: e["payload"]
               for e in reg.entries() if e["entry_type"] == "verdict"
               and e["payload"]["stage"] == "gauntlet"}
    assert len(verdicts) == 3

    # EUR is the shortest-lived series and is wholly contained within both
    # GBP's and BTCUSD's calendars, so the intersection is exactly EUR's own
    # return-dated series: 261 weekdays (2020-01-02..2020-12-31, EUR's own
    # first bar dropped by daily_returns_with_dates) -- computed independently
    # in the review response, not hand-typed here as a magic number.
    expected_common = len(_weekday_bars(
        datetime.date(2020, 1, 1), datetime.date(2020, 12, 31))) - 1

    for spec, sid_name in ((gbp_spec, "gbp"), (eur_spec, "eur"),
                           (crypto_spec, "crypto")):
        m = verdicts[spec["strategy_id"]]["metrics"]
        # run-level fields: identical across every verdict of one run
        assert m["trials_alignment"] == "intersection", sid_name
        assert m["trials_common_days"] == expected_common, sid_name

    fx_era_keys = {"pre_gfc", "gfc_zirp", "tightening", "post_2022"}
    assert set(verdicts[gbp_spec["strategy_id"]]["metrics"]["era_summary"]) == fx_era_keys
    assert set(verdicts[eur_spec["strategy_id"]]["metrics"]["era_summary"]) == fx_era_keys
    assert "era_summary" not in verdicts[crypto_spec["strategy_id"]]["metrics"]


def test_ragged_same_class_intersection_below_minimum_raises(tmp_path):
    """Two fx pairs whose calendars barely overlap must not silently cluster
    on a handful of shared days -- that reads as a real trial count when it
    is nearly nothing. Kept as a fast unit-level check of the guard itself
    (not a full registry/CLI round trip, which the test above already
    covers) by calling the same alignment primitives run() uses."""
    long_lived = daily_returns_with_dates(
        [(b["date"], 1.0) for b in _weekday_bars(
            datetime.date(2000, 1, 1), datetime.date(2019, 12, 31))])
    barely_overlapping = daily_returns_with_dates(
        [(b["date"], 1.0) for b in _weekday_bars(
            datetime.date(2019, 12, 20), datetime.date(2020, 1, 10))])
    aligned, common = intersect_returns(
        {"long": long_lived, "short": barely_overlapping})
    assert 0 < len(common) < MIN_TRIALS_COMMON_DAYS


def _fresh_registry_with_card(tmp_path):
    """A registry with the block grammar and one accepted card, and nothing
    else -- unlike screening_registry, which also registers a crypto spec
    this test does not want in all_specs at all."""
    from .blocks import BLOCK_TYPES, block_type_payload
    from .test_pipeline import make_card
    reg = Registry(tmp_path / "reg.jsonl")
    for key in BLOCK_TYPES:
        reg.register_block_type(block_type_payload(*key))
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    return reg, card["card_id"]


def test_ragged_intersection_below_minimum_raises_through_run(tmp_path):
    """The loud guard fires through the real run() entrypoint, naming the
    offending series, rather than clustering on a handful of shared days."""
    from .gauntlet import run as gauntlet_run

    reg, card_id = _fresh_registry_with_card(tmp_path)
    reg.append("note", {"text": "screen-protocol-v1: test anchor"})

    long_lived = _fx_spec("GBP", card_id, "fx-ragged-long")
    long_lived["strategy_id"] = content_id(long_lived, "strategy_id")
    reg.register_strategy(long_lived)
    _advance_to_gauntlet(reg, long_lived)

    short_lived = _fx_spec("EUR", card_id, "fx-ragged-short")
    short_lived["strategy_id"] = content_id(short_lived, "strategy_id")
    reg.register_strategy(short_lived)
    _advance_to_gauntlet(reg, short_lived)

    chain_gauntlet_note(reg)

    # GBP: 20 years of weekday history. EUR: 16 weekdays, ending the same day
    # as GBP but starting barely three weeks earlier -- a real "two fx pairs,
    # unrelated history lengths" shape whose shared calendar is a handful of
    # days, not a trial count worth clustering on.
    data = write_data_dir(tmp_path, {
        "GBP": _weekday_bars(datetime.date(2000, 1, 1), datetime.date(2020, 1, 10)),
        "EUR": _weekday_bars(datetime.date(2019, 12, 20), datetime.date(2020, 1, 10)),
    })
    with pytest.raises(ValueError, match="too short to cluster") as exc:
        gauntlet_run(["--registry", str(reg.log_path),
                     "--data-dir", str(data),
                     "--artifacts-dir", str(tmp_path / "art")])
    msg = str(exc.value)
    assert long_lived["strategy_id"] in msg and short_lived["strategy_id"] in msg


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
