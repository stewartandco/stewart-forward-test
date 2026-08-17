"""protocol-v4 tests: dense twin block types and their behavioural and
schema parity with the coarse grammar they shadow, plus the sweepable-axis
rule (only dense block types may carry a sweep axis) and the sibling cap
(raised from 25 to 60 so a family with two dense geometry axes plus a sizing
arm still fits)."""
import json
import math
from pathlib import Path

import pytest

from pipeline.blocks import BLOCK_TYPES
from pipeline.composer import composition_fingerprint

REGISTRY = Path(__file__).resolve().parent.parent / "registry_log.jsonl"

DENSE = [("entry", "channel_breakout_dense"), ("entry", "ma_cross_dense"),
         ("entry", "trend_scan_dense"), ("stop", "atr_stop_dense"),
         ("entry", "zscore_reversion_dense"), ("target", "r_multiple_dense"),
         ("filter", "vol_percentile_dense"), ("regime", "regime_ma_short_dense")]

# Single source of truth for "which (role, type) pairs exist" — imported by
# test_composer.py and test_gen2.py so a future grammar change fails with a
# readable diff naming what was added or removed, instead of a bare count.
EXPECTED_BLOCK_TYPES = frozenset({
    ("entry", "ma_cross"),
    ("entry", "channel_breakout"),
    ("entry", "zscore_reversion"),
    ("entry", "trend_scan"),
    ("regime", "regime_ma"),
    ("filter", "vol_percentile"),
    ("stop", "atr_stop"),
    ("stop", "pct_stop"),
    ("target", "r_multiple"),
    ("exit", "time_stop"),
    ("risk", "fixed_fraction"),
    ("risk", "vol_target"),
    ("entry", "trend_scan_ds"),
    ("entry", "ma_cross_ds"),
    ("regime", "regime_ma_short"),
    ("entry", "channel_breakout_dense"),
    ("entry", "ma_cross_dense"),
    ("entry", "trend_scan_dense"),
    ("stop", "atr_stop_dense"),
    ("entry", "zscore_reversion_dense"),
    ("target", "r_multiple_dense"),
    ("filter", "vol_percentile_dense"),
    ("regime", "regime_ma_short_dense"),
})

# Each dense entry twin next to the coarse/direction-capable twin it must
# stay behaviourally and schema-identical to (see composer.preflight_block_types
# for why the coarse side can never move).
TWINS = [
    (("entry", "channel_breakout"), ("entry", "channel_breakout_dense")),
    (("entry", "ma_cross_ds"), ("entry", "ma_cross_dense")),
    (("entry", "trend_scan_ds"), ("entry", "trend_scan_dense")),
    (("stop", "atr_stop"), ("stop", "atr_stop_dense")),
    (("entry", "zscore_reversion"), ("entry", "zscore_reversion_dense")),
    (("target", "r_multiple"), ("target", "r_multiple_dense")),
    (("filter", "vol_percentile"), ("filter", "vol_percentile_dense")),
    (("regime", "regime_ma_short"), ("regime", "regime_ma_short_dense")),
]


def test_dense_types_exist_with_expected_grids():
    assert BLOCK_TYPES[("entry", "channel_breakout_dense")]["lookback"]["grid"] == [20, 35, 55, 75, 100]
    assert BLOCK_TYPES[("entry", "ma_cross_dense")]["fast"]["grid"] == [5, 8, 13, 20, 34]
    assert BLOCK_TYPES[("entry", "ma_cross_dense")]["slow"]["grid"] == [50, 80, 130, 200]
    assert BLOCK_TYPES[("entry", "trend_scan_dense")]["max_lookback"]["grid"] == [60, 75, 90, 105, 120]
    assert BLOCK_TYPES[("stop", "atr_stop_dense")]["mult"]["grid"] == [1.5, 2.0, 2.5, 3.0, 3.5]
    assert BLOCK_TYPES[("target", "r_multiple_dense")]["r"]["grid"] == [1.0, 1.5, 2.0, 2.5, 3.0]
    assert BLOCK_TYPES[("filter", "vol_percentile_dense")]["lookback"]["grid"] == [90, 120, 150, 180]
    assert BLOCK_TYPES[("filter", "vol_percentile_dense")]["max_pctile"]["grid"] == [0.6, 0.7, 0.8, 0.9, 1.0]
    assert BLOCK_TYPES[("regime", "regime_ma_short_dense")]["ma_len"]["grid"] == [50, 100, 150, 200, 250]
    assert BLOCK_TYPES[("entry", "zscore_reversion_dense")]["lookback"]["grid"] == [20, 40, 60, 75, 90]
    assert BLOCK_TYPES[("entry", "zscore_reversion_dense")]["z_entry"]["grid"] == [1.5, 1.75, 2.0, 2.25, 2.5]


def test_zscore_reversion_dense_direction_excludes_short():
    """The engine's zscore short branch is `elif p["direction"] == "both" and
    z >= p["z_entry"]` — there is no standalone short-only branch, so a
    "short" value would match nothing and silently emit zero signals rather
    than error. Pin long/both only, same as the coarse twin and same as
    channel_breakout_dense for the identical reason."""
    assert BLOCK_TYPES[("entry", "zscore_reversion_dense")]["direction"]["grid"] == ["long", "both"]
    assert BLOCK_TYPES[("entry", "zscore_reversion")]["direction"]["grid"] == ["long", "both"]


def test_coarse_types_are_untouched():
    """The chained schemas must not move, or preflight_block_types aborts."""
    assert BLOCK_TYPES[("entry", "channel_breakout")]["lookback"]["grid"] == [20, 55, 100]
    assert BLOCK_TYPES[("stop", "atr_stop")]["mult"]["grid"] == [1.5, 2.0, 3.0]
    assert BLOCK_TYPES[("risk", "fixed_fraction")]["f"]["grid"] == [0.01, 0.02]
    assert BLOCK_TYPES[("entry", "zscore_reversion")]["z_entry"]["grid"] == [1.5, 2.0, 2.5]
    assert BLOCK_TYPES[("target", "r_multiple")]["r"]["grid"] == [1.0, 1.5, 2.0, 3.0]
    assert BLOCK_TYPES[("filter", "vol_percentile")]["max_pctile"]["grid"] == [0.8, 0.9, 1.0]
    assert BLOCK_TYPES[("regime", "regime_ma_short")]["ma_len"]["grid"] == [100, 200]


@pytest.mark.parametrize("coarse_key,dense_key", TWINS)
def test_dense_twin_has_same_param_keys_as_coarse(coarse_key, dense_key):
    """A param added to only one side of a twin would be silently inert in
    the shared engine handler rather than a crash — guard the key sets."""
    assert set(BLOCK_TYPES[coarse_key]) == set(BLOCK_TYPES[dense_key])


def test_all_80_existing_fingerprints_unchanged():
    """Adding dense block types must not perturb a single chained fingerprint.

    Proven by recomputing every chained fingerprint against a grammar with the
    dense types REMOVED and requiring identical output. Comparing
    composition_fingerprint(p) to itself would be a tautology that passes even
    if every fingerprint had drifted.
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


from pipeline.engine import run_spec


def _spec(entry_type, entry_params, stop_type="atr_stop", stop_params=None,
         target=None, filter_block=None, regime_block=None):
    """target/filter_block/regime_block are optional (type, params) pairs,
    so a test can vary just the block under comparison and leave the rest of
    the spec (entry, stop, exit, risk) identical between the coarse and
    dense run."""
    blocks = [
        {"role": "entry", "type": entry_type, "params": entry_params},
        {"role": "stop", "type": stop_type,
         "params": stop_params or {"atr_len": 14, "mult": 2.0}},
        {"role": "exit", "type": "time_stop", "params": {"max_bars": 20}},
        {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
    ]
    if regime_block is not None:
        rtype, rparams = regime_block
        blocks.append({"role": "regime", "type": rtype, "params": rparams})
    if filter_block is not None:
        ftype, fparams = filter_block
        blocks.append({"role": "filter", "type": ftype, "params": fparams})
    if target is not None:
        ttype, tparams = target
        blocks.append({"role": "target", "type": ttype, "params": tparams})
    return {
        "strategy_id": "t" * 16,
        "universe": {"assets": ["BTCUSD"], "timeframe": "1d",
                     "session": "24x7", "asset_class": "crypto"},
        "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005},
        "blocks": blocks,
    }


def _load_bars():
    from pipeline.screen import load_bars
    root = Path(__file__).resolve().parent.parent
    return {"BTCUSD": load_bars(root / "data", "BTCUSD", "9999-12-31")}


def test_channel_breakout_dense_twin_reproduces_its_coarse_twin_exactly():
    """A shared grid point must give byte-identical trades, or the twins have
    drifted and every fingerprint argument in the spec is void."""
    bars = _load_bars()
    entry_params = {"lookback": 55, "direction": "both"}
    coarse = run_spec(_spec("channel_breakout", entry_params,
                            "atr_stop", {"atr_len": 14, "mult": 2.0}), bars)
    dense = run_spec(_spec("channel_breakout_dense", entry_params,
                           "atr_stop_dense", {"atr_len": 14, "mult": 2.0}), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_ma_cross_dense_twin_reproduces_its_coarse_twin_exactly():
    bars = _load_bars()
    entry_params = {"fast": 5, "slow": 50, "direction": "both"}
    coarse = run_spec(_spec("ma_cross_ds", entry_params), bars)
    dense = run_spec(_spec("ma_cross_dense", entry_params), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_trend_scan_dense_twin_reproduces_its_coarse_twin_exactly():
    bars = _load_bars()
    entry_params = {"max_lookback": 60, "t_min": 2.0, "direction": "both"}
    coarse = run_spec(_spec("trend_scan_ds", entry_params), bars)
    dense = run_spec(_spec("trend_scan_dense", entry_params), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_zscore_reversion_dense_twin_reproduces_its_coarse_twin_exactly():
    bars = _load_bars()
    entry_params = {"lookback": 60, "z_entry": 2.0, "direction": "both"}
    coarse = run_spec(_spec("zscore_reversion", entry_params), bars)
    dense = run_spec(_spec("zscore_reversion_dense", entry_params), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_r_multiple_dense_target_reproduces_its_coarse_twin_exactly():
    """Task 2c claim under test: targets dispatch by ROLE, not by type name
    — simulate_asset reads by_role.get('target') then
    targets[0]['params']['r'], never switching on the type string — so
    r_multiple_dense needs no engine change. Proven here rather than trusted:
    same entry/stop/exit/risk, only the target block's type differs."""
    bars = _load_bars()
    entry_params = {"lookback": 55, "direction": "both"}
    coarse = run_spec(_spec("channel_breakout", entry_params,
                            target=("r_multiple", {"r": 1.5})), bars)
    dense = run_spec(_spec("channel_breakout", entry_params,
                           target=("r_multiple_dense", {"r": 1.5})), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_vol_percentile_dense_filter_reproduces_its_coarse_twin_exactly():
    bars = _load_bars()
    entry_params = {"lookback": 55, "direction": "both"}
    filt_params = {"lookback": 90, "max_pctile": 0.9}
    coarse = run_spec(_spec("channel_breakout", entry_params,
                            filter_block=("vol_percentile", filt_params)), bars)
    dense = run_spec(_spec("channel_breakout", entry_params,
                           filter_block=("vol_percentile_dense", filt_params)), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


def test_regime_ma_short_dense_gate_reproduces_its_coarse_twin_exactly():
    bars = _load_bars()
    entry_params = {"lookback": 55, "direction": "both"}
    regime_params = {"ma_len": 100}
    coarse = run_spec(_spec("channel_breakout", entry_params,
                            regime_block=("regime_ma_short", regime_params)), bars)
    dense = run_spec(_spec("channel_breakout", entry_params,
                           regime_block=("regime_ma_short_dense", regime_params)), bars)
    assert coarse["trades"] == dense["trades"]
    assert coarse["equity"] == dense["equity"]
    assert len(coarse["trades"]) > 0


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
                               ("entry", "zscore_reversion_dense"),
                               ("stop", "atr_stop_dense"),
                               ("target", "r_multiple_dense"),
                               ("filter", "vol_percentile_dense"),
                               ("regime", "regime_ma_short_dense")}


def test_sibling_cap_is_sixty():
    assert SIBLING_CAP_DEFAULT == 60


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


def test_saturated_normal_cdf_does_not_break_the_haircut():
    # t_stat = 4.0 * sqrt(6.4) ~= 10.1 -- large enough that normal_cdf(t_stat)
    # saturates to exactly 1.0 in float64, making p_raw exactly 0.0. This has
    # broken this repo before (inv_normal_cdf raises on p<=0 or p>=1); confirm
    # the haircut handles it without crashing and without a negative/NaN result.
    out = harvey_liu_haircut(4.0, t_years=6.4, n_trials=4)
    assert out["p_raw"] == 0.0
    assert out["p_adjusted"] == 0.0
    assert 0.0 <= out["haircut_pct"] <= 100.0
    assert out["sr_haircut"] >= 0.0
    assert not math.isnan(out["haircut_pct"])


from pipeline.walkforward import purged_folds, walkforward_report


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


def test_regime_ma_uses_exactly_ma_len_bars_not_off_by_one():
    # Non-constant (linear ramp) prices so the moving-average window width
    # actually matters -- constant-price fixtures can't distinguish a
    # correctly ma_len-wide window from one that is ma_len-1 or ma_len+1
    # wide, since the mean is the same regardless of width there.
    #
    # close[i] = 100 + 5*i, ma_len=5. At i=12 the correct 5-wide window is
    # closes[8:13] = [140, 145, 150, 155, 160], mean=150.0, close=160,
    # spread = (160-150)/150 = 6.67% -> trend_up.
    #
    # A window narrowed to 4 bars by an off-by-one (closes[9:13] =
    # [145, 150, 155, 160]) would instead average 152.5, giving spread
    # (160-152.5)/152.5 = 4.92%, which is inside the 5% chop band -- a
    # different label. So this test would catch that specific bug.
    bars = [{"date": f"d{i:04d}", "close": 100 + 5 * i} for i in range(45)]
    labels = regime_by_date(bars, ma_len=5)
    assert labels["d0012"] == "trend_up"


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
