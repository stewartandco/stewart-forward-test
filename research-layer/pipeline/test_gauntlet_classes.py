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
from .gauntlet import (MIN_TRIALS_COMMON_DAYS, _raise_too_short_intersection,
                       check_aligned, daily_returns_with_dates, era_summary,
                       intersect_returns)
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


def test_date_key_normalisation_fixes_the_real_run_mismatch():
    """Real-run finding (2026-08-24): a bar's `date` string is carried
    through verbatim from its CSV, and this repo's CSVs disagree on format
    -- the legacy BTCUSD_1d.csv (what real crypto specs register against,
    spec s10.9) is bare `YYYY-MM-DD`, while the fx snapshot adapter's CSVs
    (and the modern ...USDT grid) are `YYYY-MM-DD HH:MM:SS` (verified
    against the pinned data/ directory). Two overlapping calendars whose
    keys never compare equal intersect to an empty set: 455 real strategies
    produced "intersection ... is only 0 day(s)". Reproduced here at the
    smallest possible scale: the SAME calendar week, one series bare-dated,
    the other timestamped."""
    week = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]
    bare_equity = ([("2026-08-09", 1.0)]
                   + [(d, 1.0 + 0.001 * i) for i, d in enumerate(week, 1)])
    ts_equity = ([("2026-08-09 00:00:00", 1.0)]
                + [(f"{d} 00:00:00", 1.0 + 0.002 * i)
                   for i, d in enumerate(week, 1)])

    crypto_dated = daily_returns_with_dates(bare_equity)
    fx_dated = daily_returns_with_dates(ts_equity)
    # normalised to date-only by construction, regardless of the source format
    assert [d for d, _ in crypto_dated] == week
    assert [d for d, _ in fx_dated] == week

    aligned, common = intersect_returns({"crypto": crypto_dated, "fx": fx_dated})
    assert common == week
    assert len(common) == 5             # not 0 -- the pre-fix result


# ---------------- batch review rider: date-only cutoff comparison ----------
#
# Reviewer finding: split_trades/window_vol/_benchmark_relative compared
# `entry_date`/`date` to `cutoff` as RAW strings, while train_returns and the
# PBO family matrix (both slicing daily_returns_with_dates' already
# date-normalised series) effectively compared date-only. A time-suffixed bar
# (`YYYY-MM-DD HH:MM:SS`, what the fx snapshot adapter and the modern ...USDT
# grid write) landing exactly on the cutoff date therefore sorted OOS in some
# consumers and IS in others -- inconsistent, and specifically wrong for a
# boundary bar that should be train-side everywhere. No REGISTERED data hits
# this today (crypto is bare-dated; fx/equity have no 2023-12-31 bar), but the
# declared USDT-grid CSVs are suffixed AND do carry one.

from .gauntlet import _date_le, split_trades, window_vol


def test_date_le_matches_raw_compare_for_bare_dates():
    """Pinned equivalence: for any bare `YYYY-MM-DD` string (every real
    registered crypto date, spec s10.9), date-only comparison is
    byte-identical to the pre-rider raw string comparison -- this rider
    changes NO recorded number for data shaped like today's real chain."""
    for a, b in [("2023-12-31", "2023-12-31"), ("2023-12-30", "2023-12-31"),
                ("2024-01-01", "2023-12-31")]:
        assert _date_le(a, b) == (a <= b)


def test_date_le_ignores_time_suffix_on_the_boundary():
    cutoff = "2023-12-31"
    assert _date_le("2023-12-31 00:00:00", cutoff) is True   # ON cutoff -> IS
    assert _date_le("2023-12-31 23:59:59", cutoff) is True
    assert _date_le("2024-01-01 00:00:00", cutoff) is False  # day after -> OOS
    # the pre-rider raw compare got the boundary case backwards: the
    # timestamped string sorts AFTER the bare one.
    assert ("2023-12-31 00:00:00" <= cutoff) is False


def test_split_trades_suffixed_bar_on_cutoff_lands_train_side():
    trades = [{"entry_date": "2023-12-31 00:00:00", "return_net": 0.01,
              "notional_frac": 1.0},
             {"entry_date": "2024-01-01 00:00:00", "return_net": 0.02,
              "notional_frac": 1.0}]
    is_t, oos_t = split_trades(trades, "2023-12-31")
    assert [t["entry_date"] for t in is_t] == ["2023-12-31 00:00:00"]
    assert [t["entry_date"] for t in oos_t] == ["2024-01-01 00:00:00"]


def test_window_vol_suffixed_bar_on_cutoff_counts_train_side():
    """Rigged so the boundary bar is the THIRD is-window close -- window_vol
    needs >= 3 closes to compute anything at all. Before this rider, the raw
    compare sorted "2023-12-31 00:00:00" AFTER the bare "2023-12-31", so the
    boundary bar fell OOS, the IS side had only 2 closes, and window_vol
    silently returned 0.0 (a stale zero, never a loud failure) instead of a
    real number."""
    bars = [{"date": "2023-12-29 00:00:00", "close": 100.0},
           {"date": "2023-12-30 00:00:00", "close": 101.0},
           {"date": "2023-12-31 00:00:00", "close": 102.0},   # ON the cutoff
           {"date": "2024-01-02 00:00:00", "close": 103.0}]   # OOS
    is_vol = window_vol({"X": bars}, ["X"], "", "2023-12-31")
    assert is_vol > 0.0


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
    # --cutoff inside every fixture calendar (SP5 D3): fx now declares
    # benchmark "self", so its verdicts need OOS bars for the buy-and-hold
    # control -- the default cutoff (2023-12-31), which this test previously
    # rode on, sits past every fixture bar (they end 2020-12-31), and
    # _benchmark_relative loudly refuses a zero-bar OOS window.
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(art),
                       "--cutoff", "2020-06-30"])
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
    # in the review response, not hand-typed here as a magic number. The
    # clustering alignment runs on the FULL series, so the explicit --cutoff
    # above does not move this number.
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


def _with_time_suffix(bars: list[dict]) -> list[dict]:
    """Same bars, dated `YYYY-MM-DD HH:MM:SS` -- the EUR_1d.csv / modern
    ...USDT-grid convention, verified against the pinned data/ directory
    (2026-08-24) -- rather than _daily_bars/_weekday_bars's bare ISO dates."""
    return [dict(b, date=b["date"] + " 00:00:00") for b in bars]


def test_mixed_registry_e2e_divergent_date_key_formats_still_intersect(tmp_path):
    """Real-run finding (2026-08-24): 455 real strategies produced
    'intersection ... is only 0 day(s)' even though their calendars overlap
    1999-2026, because registered crypto specs load the legacy BTCUSD_1d.csv
    (bare `YYYY-MM-DD` dates -- production crypto uses this ticker per spec
    s10.9, verified against the pinned CSV) while fx specs load the
    snapshot adapter's CSVs (`YYYY-MM-DD HH:MM:SS`, also verified). Before
    daily_returns_with_dates normalised every key to date-only, those two
    string formats never compared equal over any date, no matter how much
    the actual calendars overlapped. The previous mixed e2e above could not
    have caught this: _daily_bars and _weekday_bars both emit bare dates, so
    its two fixtures happened to already share a format. This one pins the
    genuinely divergent pairing end to end."""
    from .gauntlet import run as gauntlet_run

    reg, crypto_spec, gbp_spec, eur_spec = mixed_class_gauntlet_registry(tmp_path)
    data = write_data_dir(tmp_path, {
        # bare dates, exactly _daily_bars's own format -- matches the real
        # legacy BTCUSD_1d.csv this crypto spec's ticker would really load
        "BTCUSD": _daily_bars(datetime.date(2020, 1, 1), datetime.date(2020, 12, 31)),
        # ` 00:00:00`-suffixed -- matches the real EUR_1d.csv/GBP_1d.csv
        "GBP": _with_time_suffix(_weekday_bars(
            datetime.date(2015, 1, 1), datetime.date(2020, 12, 31))),
        "EUR": _with_time_suffix(_weekday_bars(
            datetime.date(2020, 1, 1), datetime.date(2020, 12, 31))),
    })
    art = tmp_path / "art"
    # --cutoff inside every fixture calendar -- same SP5 D3 reason as the
    # unequal-history e2e above (fx benchmark "self" needs OOS bars).
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(art),
                       "--cutoff", "2020-06-30"])
    assert rc == 0          # pre-fix: ValueError, "intersection ... is only
                            # 0 day(s)" -- completing at all is the point

    verdicts = {e["payload"]["strategy_id"]: e["payload"]
               for e in reg.entries() if e["entry_type"] == "verdict"
               and e["payload"]["stage"] == "gauntlet"}
    assert len(verdicts) == 3
    for spec in (crypto_spec, gbp_spec, eur_spec):
        m = verdicts[spec["strategy_id"]]["metrics"]
        assert m["trials_alignment"] == "intersection"
        # the exact number the unequal-history test above derives
        # independently; the point here is ONLY that it is not 0
        assert m["trials_common_days"] > 0


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


def test_too_short_intersection_hints_key_format_mismatch_when_ranges_overlap():
    """Breadcrumb for the next person: if the actual intersection came up
    empty/short but each series' own [min, max] date span overlaps
    generously, that combination is the signature of a key-format mismatch
    (exactly the 2026-08-24 real-run shape) rather than a genuine data gap,
    so the error message should say so. Built by deliberately NOT calling
    daily_returns_with_dates -- simulating a hypothetical caller that
    bypassed the normalisation fix -- so this stays meaningful even though
    the fix makes it unreachable through the real run() path today."""
    days = [(datetime.date(2020, 1, 1) + datetime.timedelta(days=i)).isoformat()
           for i in range(150)]           # >> MIN_TRIALS_COMMON_DAYS
    mismatched = {
        "crypto1": [(d, 0.001) for d in days],           # bare, unsuffixed
        "fx1": [(f"{d} 00:00:00", 0.001) for d in days],  # timestamped
    }
    _, common = intersect_returns(mismatched)
    assert common == []                  # reproduces the real 0-day result

    with pytest.raises(ValueError, match="key-format mismatch") as exc:
        _raise_too_short_intersection(mismatched, common)
    msg = str(exc.value)
    assert "crypto1" in msg and "fx1" in msg
    assert "150" in msg or "149" in msg   # the overlap figure appears


def test_too_short_intersection_omits_hint_when_ranges_genuinely_disjoint():
    """A real data gap (one series ended, the other only just started) must
    not get misdiagnosed as a formatting bug -- no breadcrumb when the spans
    themselves barely overlap."""
    genuinely_disjoint = {
        "retired": [("2005-01-01", 0.001), ("2010-01-01", 0.001)],
        "new": [("2020-01-01", 0.001), ("2020-01-02", 0.001)],
    }
    with pytest.raises(ValueError) as exc:
        _raise_too_short_intersection(genuinely_disjoint, [])
    msg = str(exc.value)
    assert "key-format mismatch" not in msg


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


# ---------------- SP4 Task P2: parallel candidate evaluation ----------------
#
# gauntlet.py:876's registry-wide clustering loop stays serial (SP4 Task P1
# already covers its cost with the sim cache); Task P2 parallelises the
# PER-CANDIDATE gate battery + corroborating metrics (stress re-run,
# self-perturbation, walkforward, haircut, regime) with a ProcessPoolExecutor.
# protocol-v6's own founding principle -- every gate reads the strategy ALONE,
# never a sibling -- is exactly what makes evaluating candidates out of order
# or in different processes safe: nothing about one candidate's verdict can
# depend on another's, so nothing is lost by not doing them in registry order
# in real time (run() still MERGES results back into registry order, so a
# chained verdict's own content is unaffected either way -- this test proves
# the content, not just the order, survives the trip).

from . import gauntlet as gauntlet_mod


def test_parallel_evaluation_matches_serial(tmp_path, monkeypatch):
    """The P2 same-answer proof: run() derives max_workers = cpu_count - 2
    (never below 1) and exposes no CLI override, so the two paths are forced
    by monkeypatching os.cpu_count() -- 3 gives max_workers=1 (the serial,
    in-process reference _run_candidates itself falls back to), 4 gives
    max_workers=2 (the real ProcessPoolExecutor path, Windows 'spawn'
    included). Two structurally identical tmp registries (the v4-sweep
    fixture: one real sibling family, 5 candidates, 3 live + 2 dead) are run
    one each way and every chained verdict's metrics dict is compared key for
    key -- MC bootstrap, the deflated Sharpe, PBO, self-perturbation and the
    haircut all included, since every seed here is content-derived
    (evaluate_spec's own seed=int(sid, 16) % 2**31) and none of it should be
    able to tell which process did the work."""
    from .gauntlet import run as gauntlet_run
    from .test_gauntlet import v4_sweep_registry, v4_bars, V4_CUTOFF, write_data_dir

    serial_dir, parallel_dir = tmp_path / "serial", tmp_path / "parallel"
    serial_dir.mkdir()
    parallel_dir.mkdir()
    reg1, by_lb1 = v4_sweep_registry(serial_dir)
    reg2, by_lb2 = v4_sweep_registry(parallel_dir)
    data1 = write_data_dir(serial_dir, {"BTCUSD": v4_bars()})
    data2 = write_data_dir(parallel_dir, {"BTCUSD": v4_bars()})

    monkeypatch.setattr(gauntlet_mod.os, "cpu_count", lambda: 3)      # -> 1
    rc1 = gauntlet_run(["--registry", str(reg1.log_path),
                       "--data-dir", str(data1),
                       "--artifacts-dir", str(serial_dir / "art"),
                       "--cutoff", V4_CUTOFF])
    assert rc1 == 0

    monkeypatch.setattr(gauntlet_mod.os, "cpu_count", lambda: 4)      # -> 2
    rc2 = gauntlet_run(["--registry", str(reg2.log_path),
                       "--data-dir", str(data2),
                       "--artifacts-dir", str(parallel_dir / "art"),
                       "--cutoff", V4_CUTOFF])
    assert rc2 == 0

    def verdicts_by_sid(reg):
        return {e["payload"]["strategy_id"]: e["payload"]
                for e in reg.entries() if e["entry_type"] == "verdict"
                and e["payload"].get("stage") == "gauntlet"}

    v1, v2 = verdicts_by_sid(reg1), verdicts_by_sid(reg2)
    assert set(by_lb1.values()) == set(v1)
    assert set(by_lb2.values()) == set(v2)
    assert len(v1) == 5
    for lb, sid1 in by_lb1.items():
        sid2 = by_lb2[lb]
        assert v1[sid1]["verdict"] == v2[sid2]["verdict"], lb
        assert v1[sid1]["metrics"] == v2[sid2]["metrics"], (
            f"lookback={lb}: max_workers=2 diverged from the serial "
            f"(max_workers=1) reference")


def test_run_candidates_serial_matches_pool_directly(tmp_path):
    """Same proof at the narrower unit level: call `_run_candidates` itself
    with max_workers=1 (in-process loop) and max_workers=2 (real pool) on the
    SAME payload list, bypassing run()'s CLI and cpu_count entirely."""
    from .gauntlet import (_run_candidates, _evaluate_candidate,
                           _candidate_payload, daily_returns_with_dates,
                           _spec_bars)
    from .engine import run_spec
    from .test_gauntlet import v4_sweep_registry, v4_bars, V4_CUTOFF, write_data_dir
    from .screen import load_cell_data
    from .cluster import effective_trials

    reg, by_lb = v4_sweep_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": v4_bars()})
    all_specs = [e["payload"] for e in reg.entries()
                if e["entry_type"] == "strategy_registered"]
    cells_needed = sorted({(a, s["universe"].get("timeframe", "1d"))
                           for s in all_specs for a in s["universe"]["assets"]})
    bars_by_cell, data_hashes, data_end = load_cell_data(
        data, cells_needed, "9999-12-31")

    results = {}
    returns_by_id = {}
    for s in all_specs:
        sid = s["strategy_id"]
        res = run_spec(s, _spec_bars(bars_by_cell, s))
        results[sid] = res
        dated = daily_returns_with_dates(res["equity"])
        returns_by_id[sid] = [r for _, r in dated]
    trials_n, _labels, trials_var = effective_trials(returns_by_id)

    payloads = []
    for s in all_specs:
        sid = s["strategy_id"]
        g = s["provenance"]["sibling_group_id"]
        family = [{"sid": sid2, "axes": {}, "score": 1.0,
                  "screen_trade_count_fail": False, "gauntlet_passed": False}
                 for sid2 in by_lb.values()]
        sibling = next(x for x in family if x["sid"] == sid)
        payloads.append(_candidate_payload(
            s, _spec_bars(bars_by_cell, s), results[sid], returns_by_id[sid],
            5, 5, 1.0, trials_n, trials_var, sibling, family, {},
            V4_CUTOFF, True, "native", None, 0, 0))

    serial = {p["spec"]["strategy_id"]: _evaluate_candidate(p) for p in payloads}
    parallel = _run_candidates(payloads, max_workers=2)
    assert set(serial) == set(parallel) == set(by_lb.values())
    for sid in serial:
        assert serial[sid]["passed"] == parallel[sid]["passed"], sid
        assert serial[sid]["metrics"] == parallel[sid]["metrics"], sid
        assert serial[sid]["mc_summary"] == parallel[sid]["mc_summary"], sid


# ---------------- SP4 Task P3: PBO null gated on live groups ----------------

from .test_gauntlet import COST as _V4_COST

# Same lookback for all five -- the sweep axis here is the TARGET, not the
# entry, deliberately: v4_sweep_registry's own lookback sweep (20/35/55/75/
# 100) only produces TWO distinct return series on v4_bars() (20, 35 and 55
# all catch the identical spikes and post byte-identical trades; 75 and 100
# catch none and are byte-identical to each other too), which is genuinely
# below PBO_MIN_DISTINCT and would make this fixture prove nothing about a
# null that actually RUNS. Every one of these five closes the SAME trades on
# the SAME dates (same entry/lookback/spike pattern) but at a DIFFERENT
# r-multiple target price, so the five return series are genuinely distinct
# while every gate that only cares about trade DATES (not magnitude) still
# treats them identically.
LIVE_RS = (0.3, 0.6, 0.9, 1.2, 1.5)


def _flat_like(bars: list[dict], price: float = 100.0) -> list[dict]:
    """Same dates as `bars`, constant OHLC -- channel_breakout_dense can
    never close ABOVE a rolling max that never moves, so every sibling that
    sweeps its lookback over this series posts zero trades regardless of
    which lookback it uses. Built for a WHOLE FAMILY to die on its own
    evidence (oos_negative), deliberately, to exercise Task P3's dead-group
    gate."""
    return [{"date": b["date"], "open": price, "high": price, "low": price,
             "close": price, "volume": 1.0} for b in bars]


def _cb_spec(card_id: str, asset: str, group: str, lookback: int) -> dict:
    return {
        "strategy_id": None, "version": 1,
        "created_utc": "2026-08-26T00:00:00Z",
        "name": f"p3 dead-group test {asset} lb={lookback}",
        "family": f"p3_dead_test_{asset}",
        "universe": {"assets": [asset], "asset_class": "crypto",
                     "timeframe": "1d", "session": "24x7"},
        "blocks": [
            {"role": "entry", "type": "channel_breakout_dense",
             "params": {"lookback": lookback, "direction": "long"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 40}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "provenance": {"card_ids": [card_id], "parent_strategy_id": None,
                       "sibling_group_id": group, "generation": 0},
        "generator": {"agent": "composer", "model": "m",
                      "pipeline_version": "g1.0.0", "run_id": "t"},
        "cost_model": dict(_V4_COST),
    }


def _r_spec(card_id: str, group: str, r: float) -> dict:
    """Fixed lookback=20 (one of v4_bars()'s three live lookbacks), r swept
    instead -- see LIVE_RS's own comment for why."""
    return {
        "strategy_id": None, "version": 1,
        "created_utc": "2026-08-26T00:00:00Z",
        "name": f"p3 live-group test r={r}", "family": "p3_live_test",
        "universe": {"assets": ["BTCUSD"], "asset_class": "crypto",
                     "timeframe": "1d", "session": "24x7"},
        "blocks": [
            {"role": "entry", "type": "channel_breakout_dense",
             "params": {"lookback": 20, "direction": "long"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "target", "type": "r_multiple", "params": {"r": r}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 40}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "provenance": {"card_ids": [card_id], "parent_strategy_id": None,
                       "sibling_group_id": group, "generation": 0},
        "generator": {"agent": "composer", "model": "m",
                      "pipeline_version": "g1.0.0", "run_id": "t"},
        "cost_model": dict(_V4_COST),
    }


def dead_and_live_family_registry(tmp_path):
    """One real LIVE sibling family (5 r-multiples on BTCUSD's real spike
    pattern -- every one of them closes a real, winning trade on the SAME
    dates, at 5 genuinely DIFFERENT target prices, so every gate that reads
    trade dates treats them alike while their return series stay distinct)
    plus a second, wholly DEAD family (5 lookbacks of channel_breakout_dense
    over ETHUSD's perfectly flat series, so every one of them fails
    oos_negative on zero trades). One gauntlet pass, two families, only one
    of which has anything left to test -- exactly the shape Task P3's
    dead-group gate exists for."""
    from .test_gauntlet import V4_LOOKBACKS
    from .common import content_id as _content_id

    reg, card_id = _fresh_registry_with_card(tmp_path)
    reg.append("note", {"text": "screen-protocol-v1: test anchor"})

    live_group = "p3-live-test-group"
    live_sids = {}
    for r in LIVE_RS:
        spec = _r_spec(card_id, live_group, r)
        spec["strategy_id"] = _content_id(spec, "strategy_id")
        reg.register_strategy(spec)
        _advance_to_gauntlet(reg, spec)
        live_sids[r] = spec["strategy_id"]

    dead_group = "p3-dead-test-group"
    dead_sids = {}
    for lb in V4_LOOKBACKS:
        spec = _cb_spec(card_id, "ETHUSD", dead_group, lb)
        spec["strategy_id"] = _content_id(spec, "strategy_id")
        reg.register_strategy(spec)
        _advance_to_gauntlet(reg, spec)
        dead_sids[lb] = spec["strategy_id"]

    chain_gauntlet_note(reg)
    return reg, live_sids, dead_sids, live_group, dead_group


def test_pbo_dead_group_gets_new_label_live_group_still_gets_a_null(
        tmp_path, capsys):
    """The Task P3 assertion in one test: a group with zero gate-passing
    candidates this pass never has a null built for it (null_draws stays 0,
    the printed verdict is the new 'not_measured_dead_group' label, and
    'underpowered' -- which means something ELSE, a null that WAS attempted
    or considered -- never appears for it), while a real live group in the
    SAME run still gets one (null_draws == args.pbo_null_draws, a real
    pass/fail/kill verdict)."""
    from .gauntlet import run as gauntlet_run
    from .test_gauntlet import v4_bars, V4_CUTOFF, write_data_dir

    reg, live_sids, dead_sids, live_group, dead_group = \
        dead_and_live_family_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": v4_bars(),
                                     "ETHUSD": _flat_like(v4_bars())})
    art = tmp_path / "art"
    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(art), "--cutoff", V4_CUTOFF,
                       "--pbo-null-draws", "8"])
    assert rc == 0
    out = capsys.readouterr().out

    verdicts = {e["payload"]["strategy_id"]: e["payload"]
                for e in reg.entries() if e["entry_type"] == "verdict"
                and e["payload"].get("stage") == "gauntlet"}

    # every ETHUSD sibling died on its OWN evidence (oos_negative, zero
    # trades) -- confirms this really is a dead family, not a vacuous test.
    dead_reasons = {e["payload"]["strategy_id"]: e["payload"]["reason"]
                    for e in reg.entries() if e["entry_type"] == "state_change"
                    and e["payload"]["to"] == "graveyard"}
    for sid in dead_sids.values():
        assert dead_reasons[sid] == "oos_negative"
        m = verdicts[sid]["metrics"]
        assert m["pbo_null_draws"] == 0
        assert m["pbo_percentile"] is None
        assert m["pbo_family_kill"] is False
        # batch review rider: the label chains too, not just the printed line
        assert m["pbo_verdict"] == "not_measured_dead_group"

    dead_line = next(l for l in out.splitlines() if l.strip().startswith(
        f"PBO {dead_group}:"))
    assert dead_line.rstrip().endswith("-> not_measured_dead_group")
    assert "underpowered" not in dead_line

    # the live family: at least one r-multiple actually cleared every gate
    # (confirming this really is a LIVE group, not a vacuous test either),
    # and its PBO line ran the real permutation null, never the dead label.
    live_line = next(l for l in out.splitlines() if l.strip().startswith(
        f"PBO {live_group}:"))
    assert live_line.rstrip().split("-> ")[-1] in {"pass", "fail", "kill"}
    assert "not_measured_dead_group" not in live_line
    assert "underpowered" not in live_line

    passed_any = False
    for sid in live_sids.values():
        m = verdicts[sid]["metrics"]
        assert m["pbo_null_draws"] == 8, (
            "the live group's null must actually run at the requested "
            "--pbo-null-draws count")
        # batch review rider: the chained label matches the printed one, and
        # is never the dead-group label a live group can never earn.
        assert m["pbo_verdict"] in {"pass", "fail", "kill"}
        assert live_line.rstrip().endswith(f"-> {m['pbo_verdict']}")
        passed_any = passed_any or verdicts[sid]["verdict"] == "pass"
    assert passed_any, "fixture bug: the live group has no gate-passer"


# ---------------- SP4 Task P5: per-stage progress output --------------------

def test_progress_lines_appear_for_every_stage(tmp_path, capsys):
    """A smoke test, not a numbers test: every stage P5 names must print AT
    LEAST one '[gauntlet] ...' line, unbuffered (flush=True at every call
    site -- not directly observable through capsys, but every print() in the
    new code paths passes it, so a hang mid-run would still show partial
    output rather than none)."""
    from .gauntlet import run as gauntlet_run
    from .test_gauntlet import v4_sweep_registry, v4_bars, V4_CUTOFF, write_data_dir

    reg, by_lb = v4_sweep_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": v4_bars()})
    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art"),
                       "--cutoff", V4_CUTOFF])
    assert rc == 0
    out = capsys.readouterr().out

    assert "[gauntlet] clustering done in" in out
    assert "cache " in out and "hits /" in out and "misses)" in out
    assert "[gauntlet] candidate evaluation done in" in out
    assert "[gauntlet] pbo group" in out
    assert "[gauntlet] stage timings:" in out
    assert "clustering" in out and "candidate eval" in out
    assert "pbo " in out and "artifacts" in out and "total" in out


def test_progress_lines_appear_in_dry_run_too(tmp_path, capsys):
    """--dry-run still evaluates every candidate (it only skips the chain
    write), so the same progress + timing lines must still appear."""
    from .gauntlet import run as gauntlet_run
    from .test_gauntlet import v4_sweep_registry, v4_bars, V4_CUTOFF, write_data_dir

    reg, by_lb = v4_sweep_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": v4_bars()})
    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art"),
                       "--cutoff", V4_CUTOFF, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "[gauntlet] clustering done in" in out
    assert "[gauntlet] candidate evaluation done in" in out
    assert "[gauntlet] stage timings:" in out


# ---------------- SP4 Task B1: benchmark-relative control -------------------
#
# Pre-registered docs/2026-08-24-sp4-track2a-addendum.md ("Pre-registration:
# benchmark-relative control (B1, Coen 2026-08-26)"): a class whose CLASSES
# entry declares `benchmark: "self"` (equity_etf, today) gets a
# `metrics["benchmark_relative"]` block on every gauntlet verdict -- a
# same-OOS-window buy-and-hold of the cell's own asset, RECORDED, NOT GATED.
# Classes with `benchmark: None` carry no key at all. SP5 D3
# (docs/2026-08-28-market-data-universe-design.md s7): fx flipped to "self"
# with a per-class-honest basis string; crypto stays None until Phase 3.

from .gauntlet import (BENCHMARK_BASIS, _benchmark_relative,
                       _candidate_payload, _evaluate_candidate)


def test_benchmark_relative_analytic_fixture():
    """Hand-computed against a two-bar OOS window: entry at the first OOS
    bar's OPEN, exit at the last OOS bar's CLOSE, net of one round trip
    (2 sides) of equity_etf's declared cost_model -- 0.00010 commission +
    0.00010 slippage per side (docs/2026-08-24-sp4-track2a-addendum.md).
    An IS-dated bar is included and must be ignored entirely (only the
    `date > cutoff` fence -- the same one split_trades applies to trades --
    decides what counts as the OOS window)."""
    cutoff = "2023-12-31"
    bars = {"SPY": [
        {"date": "2023-06-01", "open": 999.0, "high": 999.0, "low": 999.0,
         "close": 999.0, "volume": 1.0},          # IS bar: must be ignored
        {"date": "2024-01-01", "open": 100.0, "high": 100.0, "low": 100.0,
         "close": 100.0, "volume": 1.0},           # first OOS bar
        {"date": "2024-06-01", "open": 105.0, "high": 112.0, "low": 104.0,
         "close": 110.0, "volume": 1.0},            # last OOS bar
    ]}
    spec = {"strategy_id": "x",
            "universe": {"assets": ["SPY"], "asset_class": "equity_etf"}}

    result = _benchmark_relative(spec, bars, strategy_net=0.02, cutoff=cutoff)

    per_side = 0.00010 + 0.00010     # equity_etf cost_model, ONE side
    expected_buy_hold = (110.0 / 100.0 - 1) - 2 * per_side   # 0.10 - 0.0004
    expected_excess = 0.02 - expected_buy_hold
    assert result == {
        "window": "oos", "strategy_net": 0.02,
        "buy_hold_net": pytest.approx(expected_buy_hold, abs=1e-12),
        "excess": pytest.approx(expected_excess, abs=1e-12),
        "basis": "price returns, dividends excluded on both sides"}


def test_benchmark_relative_absent_for_none_classes():
    """crypto (the one remaining `benchmark: None` class -- fx flipped under
    SP5 D3) -- absence of the key, never a null placeholder (the addendum's
    own convention)."""
    bars = {"BTCUSDT": [{"date": "2024-01-01", "open": 1.0, "high": 1.0,
                         "low": 1.0, "close": 1.0, "volume": 1.0}]}
    crypto_spec = {"strategy_id": "x",
                   "universe": {"assets": ["BTCUSDT"], "asset_class": "crypto"}}
    assert _benchmark_relative(crypto_spec, bars, 0.01, "2023-12-31") is None


def test_crypto_benchmark_and_the_legacy_pooled_path_flip_together():
    """SP5 P2-T3: the deferral is BOTH-OR-NEITHER, pinned so it cannot be
    half-shipped.

    Phase 2 declares crypto's 100-asset grid and its cost_model but leaves
    `benchmark` at None. It has to: while crypto is routed to the legacy
    pooled path, its specs are named from composer.ALLOWED_ASSETS
    (BTCUSD/ETHUSD -- tickers that are not declared crypto cell assets at
    all) and are the 2-asset pooled book. All 155 crypto strategies on the
    chain are that book. `_benchmark_relative` REQUIRES exactly one asset
    per cell for a benchmark:"self" class, so flipping the declaration on
    its own turns every crypto verdict into a ValueError -- which is exactly
    what the first cut of this task did, against a green-looking cells.py.

    The flip belongs to the PHASE 3 ACTIVATION COMMIT, which in the same
    commit populates ACTIVE_CELLS["crypto"], routes crypto through
    expand_family_for_class (single-asset per-cell specs sourced from the
    DECLARED grid), and deletes the legacy branch. This test fails in EITHER
    direction: flip the benchmark alone and the equality below breaks (and
    the pooled spec below starts raising); switch the routing alone and the
    equality breaks the other way.
    """
    from . import composer

    # The crypto path still sources its assets from the legacy pooled list,
    # NOT from the declared grid. That is the fact the benchmark hangs on.
    crypto_path_is_legacy_pooled = (
        set(composer.ALLOWED_ASSETS) != set(cells.CLASSES["crypto"]["assets"]))
    assert (cells.CLASSES["crypto"]["benchmark"] is None) == crypto_path_is_legacy_pooled, (
        "crypto's benchmark and its composer routing must flip in the SAME "
        "commit (SP5 Phase 3): benchmark 'self' needs one asset per cell, "
        "and the legacy pooled path cannot give it one")

    # And prove it behaviourally on a real spec off that path, so the pin is
    # not merely a restatement of the rule: a pooled spec must survive
    # _benchmark_relative today (no key, no raise).
    fam = {"family": "coupling_pin", "card_ids": ["aaaaaaaaaaaaaaaa"],
           "assets": list(composer.ALLOWED_ASSETS),
           "blocks": [
               {"role": "entry", "type": "ma_cross", "params": {"fast": 5, "slow": 50}},
               {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}},
               {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
               {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
           ]}
    pooled = composer.expand_family(fam, "run-coupling", "claude-opus-5",
                                    "2026-08-29T00:00:00Z")[0]
    assert len(pooled["universe"]["assets"]) == 2, pooled["universe"]
    assert pooled["universe"]["asset_class"] == "crypto"
    # Empty bars on purpose: with benchmark None this returns before touching
    # them. If someone flips the declaration alone, this raises instead.
    assert _benchmark_relative(pooled, {}, 0.0, "2023-12-31") is None


def test_benchmark_relative_suffixed_bar_exactly_on_cutoff_is_train_side():
    """Batch review rider: a time-suffixed bar dated exactly on the cutoff
    (the declared USDT-grid CSVs are suffixed AND carry a 2023-12-31 bar)
    must land TRAIN-side, the same rule split_trades/window_vol now apply --
    never misread as the first OOS bar because a raw string compare sorted
    the timestamped boundary string after the bare cutoff."""
    cutoff = "2023-12-31"
    bars = {"SPY": [
        {"date": "2023-12-31 00:00:00", "open": 999.0, "high": 999.0,
         "low": 999.0, "close": 999.0, "volume": 1.0},   # ON cutoff -> IS
        {"date": "2024-01-01 00:00:00", "open": 100.0, "high": 100.0,
         "low": 100.0, "close": 100.0, "volume": 1.0},    # first OOS bar
        {"date": "2024-06-01 00:00:00", "open": 105.0, "high": 112.0,
         "low": 104.0, "close": 110.0, "volume": 1.0},     # last OOS bar
    ]}
    spec = {"strategy_id": "x",
            "universe": {"assets": ["SPY"], "asset_class": "equity_etf"}}

    result = _benchmark_relative(spec, bars, strategy_net=0.02, cutoff=cutoff)

    per_side = 0.00010 + 0.00010
    expected_buy_hold = (110.0 / 100.0 - 1) - 2 * per_side
    assert result["buy_hold_net"] == pytest.approx(expected_buy_hold, abs=1e-12)


def test_crypto_verdicts_carry_no_benchmark_key(tmp_path):
    """End to end through gauntlet.run(): the mixed crypto+fx registry
    already exercised elsewhere in this file (era summaries, intersection
    alignment) proves the negative here for free -- crypto (the one
    remaining `benchmark: None` class since SP5 D3 flipped fx) never gets
    the key on its verdict's metrics dict. The fx verdicts of the SAME run
    DO carry it now, with the carry-excluded basis -- asserted here so the
    flip is pinned end to end, not only at the worker level. The explicit
    --cutoff sits INSIDE every fixture calendar (all three end 2020-12-31);
    the pre-flip default (2023-12-31) would leave fx with zero OOS bars,
    which _benchmark_relative loudly refuses."""
    from .gauntlet import run as gauntlet_run

    reg, crypto_spec, gbp_spec, eur_spec = mixed_class_gauntlet_registry(tmp_path)
    data = write_data_dir(tmp_path, {
        "BTCUSD": _daily_bars(datetime.date(2020, 1, 1), datetime.date(2020, 12, 31)),
        "GBP": _weekday_bars(datetime.date(2015, 1, 1), datetime.date(2020, 12, 31)),
        "EUR": _weekday_bars(datetime.date(2020, 1, 1), datetime.date(2020, 12, 31)),
    })
    rc = gauntlet_run(["--registry", str(reg.log_path),
                       "--data-dir", str(data),
                       "--artifacts-dir", str(tmp_path / "art"),
                       "--cutoff", "2020-06-30"])
    assert rc == 0

    verdicts = {e["payload"]["strategy_id"]: e["payload"]
               for e in reg.entries() if e["entry_type"] == "verdict"
               and e["payload"]["stage"] == "gauntlet"}
    assert len(verdicts) == 3
    assert "benchmark_relative" not in verdicts[crypto_spec["strategy_id"]]["metrics"]
    for fx_spec in (gbp_spec, eur_spec):
        b = verdicts[fx_spec["strategy_id"]]["metrics"]["benchmark_relative"]
        assert b["basis"] == "price returns, carry excluded on both sides"


# ---- a real equity_etf candidate, through the worker path -----------------
#
# A staircase price series: each cycle breaks out +10% from the current
# base, the strategy's r_multiple=1.0 target against a 5% stop closes the
# trade at +5% (a real, small, POSITION-SIZED win -- fixed_fraction f=0.01
# against a 5% stop distance sizes the trade at notional_frac=0.2, so the
# portfolio-level contribution is ~0.99% per trade), while the underlying
# asset keeps climbing to a NEW base 10% above the old one every cycle --
# the strategy takes its profit and the market keeps going. Two cycles
# land in-sample, two land out-of-sample, so IS and OOS edge are identical
# by construction (edge_decay passes trivially) while a same-window
# buy-and-hold captures the WHOLE OOS climb no strategy trade discipline
# ever gets: this is the ordinary, expected shape of "excess < 0 but the
# strategy still passes" (position sizing alone makes it common), not a
# contrived corner case.

def _staircase_bars(n_cycles: int, start: datetime.date, base0: float,
                    growth: float = 1.10, flat_len: int = 60) -> tuple[list[dict], datetime.date, float]:
    bars: list[dict] = []
    base = base0
    d = start
    def add(o, h, l, c):
        nonlocal d
        bars.append({"date": d.isoformat(), "open": o, "high": h, "low": l,
                    "close": c, "volume": 1.0})
        d += datetime.timedelta(days=1)
    for _ in range(n_cycles):
        for _ in range(flat_len):
            add(base, base, base, base)
        entry_px = base * 1.10
        add(base, entry_px, base, entry_px)            # breakout signal bar
        add(entry_px, entry_px, entry_px, entry_px)    # entry fires next open
        new_base = base * growth                       # market keeps climbing
        spike_high = max(new_base, entry_px * 1.06) + 1.0
        add(entry_px, spike_high, entry_px, new_base)   # target hit, new base
        base = new_base
    for _ in range(flat_len):
        add(base, base, base, base)
    return bars, d, base


_B1_CUTOFF = "2023-12-31"
_B1_SID = "b1" + "0" * 62


def _equity_benchmark_candidate():
    """One equity_etf candidate: 2 IS + 2 OOS winning trades, staircase
    underlying. Returns (spec, spec_bars)."""
    cutoff_dt = datetime.datetime.strptime(_B1_CUTOFF, "%Y-%m-%d").date()
    is_bars, _, base_after_is = _staircase_bars(
        2, cutoff_dt - datetime.timedelta(days=400), base0=100.0)
    is_bars = [b for b in is_bars if b["date"] <= _B1_CUTOFF]
    oos_bars, _, _ = _staircase_bars(
        2, cutoff_dt + datetime.timedelta(days=1), base0=base_after_is)
    bars = sorted(is_bars + oos_bars, key=lambda b: b["date"])

    spec = {
        "strategy_id": _B1_SID, "version": 1,
        "created_utc": "2026-08-26T00:00:00Z",
        "name": "b1 benchmark test", "family": "b1_benchmark_test",
        "universe": {"assets": ["SPY"], "asset_class": "equity_etf",
                    "timeframe": "1d", "session": "us_equity_5d"},
        "blocks": [
            {"role": "entry", "type": "channel_breakout_dense",
             "params": {"lookback": 20, "direction": "long"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 40}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "provenance": {"card_ids": ["c1"], "parent_strategy_id": None,
                       "sibling_group_id": "b1-benchmark-group", "generation": 0},
        "generator": {"agent": "composer", "model": "m",
                      "pipeline_version": "g1.0.0", "run_id": "t"},
        "cost_model": dict(cells.CLASSES["equity_etf"]["cost_model"]),
    }
    return spec, {"SPY": bars}


def _evaluate_equity_benchmark_candidate():
    from .engine import run_spec
    spec, spec_bars = _equity_benchmark_candidate()
    res = run_spec(spec, spec_bars)
    rets = [r for _, r in daily_returns_with_dates(res["equity"])]
    trials_n, _labels, trials_var = effective_trials({_B1_SID: rets})
    family = [{"sid": _B1_SID, "axes": {}, "score": 1.0,
              "screen_trade_count_fail": False, "gauntlet_passed": False}]
    payload = _candidate_payload(
        spec, spec_bars, res, rets, 1, 1, None, trials_n, trials_var,
        family[0], family, {}, _B1_CUTOFF, False, "native", None, 0, 0)
    return _evaluate_candidate(payload)


def test_equity_verdict_carries_the_full_benchmark_block():
    result = _evaluate_equity_benchmark_candidate()
    m = result["metrics"]
    assert set(m["benchmark_relative"]) == {
        "window", "strategy_net", "buy_hold_net", "excess", "basis"}
    assert m["benchmark_relative"]["window"] == "oos"
    assert m["benchmark_relative"]["basis"] == (
        "price returns, dividends excluded on both sides")
    # strategy_net is the SAME figure the oos_negative gate read (2 winning
    # OOS trades, ~0.99% portfolio contribution each, compounded to ~2%) --
    # small and positive, dwarfed by the asset's own ~21% OOS climb.
    assert 0 < m["benchmark_relative"]["strategy_net"] < 0.05
    assert m["benchmark_relative"]["buy_hold_net"] > 0.15
    assert m["benchmark_relative"]["excess"] == pytest.approx(
        m["benchmark_relative"]["strategy_net"]
        - m["benchmark_relative"]["buy_hold_net"], abs=1e-12)


def test_benchmark_relative_recorded_not_gated():
    """The exact case B1 exists to record: a real candidate whose OOS trades
    clear all six gates on their own evidence (protocol-v6) while its
    buy-and-hold excess is NEGATIVE -- the strategy's own gate battery never
    reads `benchmark_relative`, so a negative excess must not change the
    verdict at all."""
    result = _evaluate_equity_benchmark_candidate()
    assert result["passed"] is True
    assert result["reason"] is None
    assert result["metrics"]["benchmark_relative"]["excess"] < 0


# ---- SP5 D3: fx benchmark flip -- recorded, with a carry-honest basis -----

_FX_B_SID = "fb" + "0" * 62


def _fx_benchmark_candidate():
    """One fx candidate on single-fix bars (open==high==low==close, the
    bar_kind fx declares -- tradfi_data.py's FRED H.10 fixes), weekdays
    spanning both sides of the cutoff. A triangle-wave price so ma_cross
    genuinely crosses (real trades, nonzero window vol) instead of the flat
    fixtures the mixed e2e tests use for calendar shape alone. Returns
    (spec, spec_bars)."""
    bars = []
    d, i = datetime.date(2022, 1, 3), 0
    while d <= datetime.date(2024, 12, 31):
        if d.weekday() < 5:
            j = i % 20
            px = round(1.10 + 0.004 * (j if j < 10 else 20 - j), 6)
            bars.append({"date": d.isoformat(), "open": px, "high": px,
                        "low": px, "close": px, "volume": 0.0})
            i += 1
        d += datetime.timedelta(days=1)
    spec = _fx_spec("EUR", "c1", "fx-benchmark-basis-group")
    spec["strategy_id"] = _FX_B_SID
    return spec, {"EUR": bars}


def test_fx_benchmark_recorded_with_carry_basis():
    """SP5 D3 (docs/2026-08-28-market-data-universe-design.md s7): fx
    declares benchmark "self", so a real fx candidate evaluated through the
    same worker path as the equity B1 tests above gets the full
    benchmark_relative block -- and its basis string declares the fx-specific
    limitation (a USD-per-foreign hold's true driver is carry, which a
    price-only control cannot see), not the ETF dividends wording.

    single_fix note: open==close==the daily fix on every fx bar, so the
    control here is fix-to-fix by construction -- the entry-open/exit-close
    endpoint choice is exercised where the endpoints actually differ, by the
    equity OHLC fixtures above."""
    from .engine import run_spec

    spec, spec_bars = _fx_benchmark_candidate()
    res = run_spec(spec, spec_bars)
    # the fixture must stay a REAL candidate: if the triangle wave ever stops
    # producing ma_cross trades, this test would silently stop exercising the
    # strategy_net side of the block.
    assert res["trades"], "fixture degraded: fx benchmark candidate has no trades"
    rets = [r for _, r in daily_returns_with_dates(res["equity"])]
    trials_n, _labels, trials_var = effective_trials({_FX_B_SID: rets})
    family = [{"sid": _FX_B_SID, "axes": {}, "score": 1.0,
              "screen_trade_count_fail": False, "gauntlet_passed": False}]
    payload = _candidate_payload(
        spec, spec_bars, res, rets, 1, 1, None, trials_n, trials_var,
        family[0], family, {}, _B1_CUTOFF, False, "native", None, 0, 0)
    result = _evaluate_candidate(payload)

    b = result["metrics"]["benchmark_relative"]
    assert set(b) == {"window", "strategy_net", "buy_hold_net", "excess",
                      "basis"}
    assert b["window"] == "oos"
    assert b["basis"] == "price returns, carry excluded on both sides"
    assert b["excess"] == pytest.approx(
        b["strategy_net"] - b["buy_hold_net"], abs=1e-12)
    # buy_hold_net independently: first OOS bar's open to last OOS bar's
    # close, net of ONE round trip of fx's own cost model.
    oos = [x for x in spec_bars["EUR"] if x["date"] > _B1_CUTOFF]
    per_side = (cells.FX_COST_MODEL["commission_per_side"]
                + cells.FX_COST_MODEL["slippage_ticks"])
    assert b["buy_hold_net"] == pytest.approx(
        (oos[-1]["close"] / oos[0]["open"] - 1) - 2 * per_side, abs=1e-12)


def test_benchmark_basis_is_per_class():
    """The fx flip must not touch anyone else's wording: an equity_etf block
    still carries the dividends-excluded basis (bond/metal share that same
    default -- only fx, and later crypto, get their own entry)."""
    bars = {"SPY": [
        {"date": "2024-01-01", "open": 100.0, "high": 100.0, "low": 100.0,
         "close": 100.0, "volume": 1.0},
        {"date": "2024-06-01", "open": 110.0, "high": 110.0, "low": 110.0,
         "close": 110.0, "volume": 1.0}]}
    spec = {"strategy_id": "x",
            "universe": {"assets": ["SPY"], "asset_class": "equity_etf"}}
    result = _benchmark_relative(spec, bars, 0.02, "2023-12-31")
    assert result["basis"] == "price returns, dividends excluded on both sides"


# The classes that DELIBERATELY ride _DEFAULT_BASIS's dividends wording --
# pinned here, not inferred, so falling through to the default is a decision
# with a name on it, never an accident.
_DEFAULT_BASIS_CLASSES = frozenset({"equity_etf", "bond_etf", "metal_etf"})


def test_every_self_benchmark_class_made_a_basis_decision():
    """Completeness guard (rules-as-pipeline-stages: a convention that lives
    only in prose is not a guard): every class declaring `benchmark: "self"`
    must either carry its own BENCHMARK_BASIS entry or be a pinned member of
    _DEFAULT_BASIS_CLASSES above. A NEW class flipping to "self" therefore
    fails HERE, loudly, until its author makes a deliberate basis decision --
    in a test, never a runtime raise, because recorded-not-gated must never
    abort a live gauntlet pass."""
    for cls, spec in cells.CLASSES.items():
        if spec.get("benchmark") != "self":
            continue
        assert cls in BENCHMARK_BASIS or cls in _DEFAULT_BASIS_CLASSES, (
            f"class {cls!r} declares benchmark 'self' but has neither its own "
            f"BENCHMARK_BASIS entry nor a pinned seat in "
            f"_DEFAULT_BASIS_CLASSES -- decide its basis wording deliberately")
