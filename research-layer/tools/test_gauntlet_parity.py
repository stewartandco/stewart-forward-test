from pipeline import gauntlet_core
from tools.gauntlet_parity import truncate_bars, GATE_KEYS, chained_base_pass


def test_truncate_bars_cuts_each_cell_at_its_recorded_end():
    # data_end is keyed by cell id ("<asset>_<tf>") and holds the last bar's
    # date string verbatim: bare for legacy crypto, timestamped for fx/etf.
    bars = {("BTCUSD", "1d"): [{"date": "2026-09-01"}, {"date": "2026-09-02"},
                               {"date": "2026-09-03"}],
            ("EUR", "1d"): [{"date": "2026-09-01 00:00:00"},
                            {"date": "2026-09-02 00:00:00"},
                            {"date": "2026-09-03 00:00:00"}]}
    out = truncate_bars(bars, {"BTCUSD_1d": "2026-09-02",
                               "EUR_1d": "2026-09-02 00:00:00"})
    assert [b["date"] for b in out[("BTCUSD", "1d")]] == ["2026-09-01", "2026-09-02"]
    assert [b["date"] for b in out[("EUR", "1d")]] == [
        "2026-09-01 00:00:00", "2026-09-02 00:00:00"]


def test_truncate_bars_leaves_a_cell_with_no_recorded_end_whole():
    bars = {("SPY", "1d"): [{"date": "2026-09-01"}, {"date": "2026-09-09"}]}
    assert truncate_bars(bars, {}) == bars


def test_truncate_bars_is_the_gauntlet_core_one():
    # Re-exported, not re-implemented: a later task reuses the core's.
    assert truncate_bars is gauntlet_core.truncate_bars


def test_parity_compares_every_gate_metric():
    assert {"oos_edge_per_trade", "edge_decay_pct", "mc_p05_equity", "p_ruin",
            "cost_stress_net_pnl", "train_sharpe"} <= set(GATE_KEYS)


def test_base_pass_is_a_chained_pass_or_a_family_kill_burial():
    passed = {"verdict": "pass", "metrics": {}}
    assert chained_base_pass(passed, None) is True
    killed = {"verdict": "fail", "metrics": {"pbo_family_kill": True}}
    assert chained_base_pass(killed, "pbo_family_kill") is True


def test_the_family_kill_flag_alone_is_not_a_base_pass():
    # The chain flags EVERY member of a killed group, including strategies
    # that had already failed a gate; only the burial reason says why.
    flagged_but_failed = {"verdict": "fail", "metrics": {"pbo_family_kill": True}}
    assert chained_base_pass(flagged_but_failed, "sharpe_floor") is False
    assert chained_base_pass(flagged_but_failed, None) is False
    assert chained_base_pass({"verdict": "fail", "metrics": {}}, "sharpe_floor") is False


def test_a_legacy_pooled_crypto_verdict_reruns_without_error(tmp_path):
    """Switch 1 made crypto's benchmark "self", which RAISES for a 2-asset
    spec; every pre-switch crypto strategy (533 at 2026-10-17, all pooled
    BTCUSD+ETHUSD) was judged with no control, so parity must re-judge them without one (record_benchmark
    False) -- end to end through main(), not a crash on the first one."""
    import json
    from pipeline.test_screen import write_data_dir, dated_target_hit_bars
    from tools.gauntlet_parity import main, legacy_pooled_crypto
    from pipeline.test_screen import breakout_spec_blocks, COST

    sid = "ab" * 8
    spec = {"strategy_id": sid, "version": 1, "name": "pooled", "family": "f",
            "universe": {"assets": ["BTCUSD", "ETHUSD"], "asset_class": "crypto",
                         "timeframe": "1d", "session": "24x7"},
            "blocks": breakout_spec_blocks(), "cost_model": dict(COST)}
    assert legacy_pooled_crypto(spec)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars(),
                                     "ETHUSD": dated_target_hit_bars()})
    cutoff = "2023-01-21"
    sb = gauntlet_core._spec_bars(
        {("BTCUSD", "1d"): dated_target_hit_bars(),
         ("ETHUSD", "1d"): dated_target_hit_bars()}, spec)
    judged = gauntlet_core.evaluate_standalone(spec, sb, cutoff, perturb=False,
                                               record_benchmark=False)
    metrics = dict(judged["metrics"], protocol="gauntlet-protocol-v6")
    verdict = {"strategy_id": sid, "stage": "gauntlet",
               "verdict": "pass" if judged["passed"] else "fail", "metrics": metrics}
    reg = tmp_path / "registry_log.jsonl"
    reg.write_text("".join(json.dumps({"entry_type": t, "payload": p}) + "\n"
                           for t, p in (("strategy_registered", spec),
                                        ("verdict", verdict))), encoding="utf-8")
    cfg = tmp_path / "art" / sid / "gauntlet" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"cutoff": cutoff,
                               "data_end": {"BTCUSD_1d": "2023-01-23",
                                            "ETHUSD_1d": "2023-01-23"}}),
                   encoding="utf-8")
    out = tmp_path / "report.md"
    rc = main(["--registry", str(reg), "--data-dir", str(data),
               "--artifacts-dir", str(tmp_path / "art"), "--sids", sid,
               "--min-compared", "0", "--out", str(out)])
    assert rc == 0, out.read_text(encoding="utf-8")
    assert "compared 1, mismatches 0 (excused 0, unexcused 0), not comparable 0" in out.read_text(encoding="utf-8")


def test_legacy_pooled_crypto_is_only_the_multi_asset_crypto_book():
    from tools.gauntlet_parity import legacy_pooled_crypto
    u = lambda assets, cls="crypto": {"universe": {"assets": assets, "asset_class": cls}}
    assert legacy_pooled_crypto(u(["BTCUSD", "ETHUSD"]))
    assert not legacy_pooled_crypto(u(["SOLUSDT"]))
    assert not legacy_pooled_crypto(u(["EUR"], "fx"))

# -- excused drift (Coen 2026-11-06): known causes are named, never hidden ----

from tools.gauntlet_parity import (classify_drift, load_repins, ulp_distance,
                                   ENGINE_CHANGE_UTC, ULP_MAX)

REPINS = [{"id": "EFA", "repinned_utc": "2026-10-04T11:53:18Z",
           "changed": [{"date": "2014-07-03", "ohlc": {"close": 69.22}}]},
          {"id": "EEM", "repinned_utc": "2026-10-25T00:52:20Z", "changed": []}]
CELLS = [("EFA", "1d", "2026-09-29T00:00:00Z")]
ULPS_3 = (0.1, 0.1 + 3 * 1.3877787807814457e-17)     # 3 ulps apart at 0.1


def snapshot_has_repin(asset, tf, d):
    """The --data-dir copy holds the re-pinned close (it has taken the re-pin)."""
    return {("EFA", "1d", "2014-07-03"): 69.22}.get((asset, tf, d))


def test_ulp_distance_counts_representable_steps():
    import math
    assert ulp_distance(1.0, 1.0) == 0
    assert ulp_distance(1.0, math.nextafter(1.0, 2.0)) == 1
    assert ulp_distance(-1.0, math.nextafter(-1.0, -2.0)) == 1
    assert ulp_distance(*ULPS_3) == 3
    # across zero: the smallest subnormals either side are 2 steps apart
    assert ulp_distance(-5e-324, 5e-324) == 2
    assert ulp_distance(-0.0, 0.0) == 0


def test_a_verdict_change_is_never_excused():
    ok, cause = classify_drift([("train_sharpe", 0.5, 0.6)], "2026-09-29T12:00:00Z",
                               CELLS, REPINS, verdict_match=False)
    assert not ok and "verdict" in cause


def test_drift_on_a_cell_revised_after_the_verdict_is_excused_and_named():
    ok, cause = classify_drift([("is_edge_per_trade", 0.0071, 0.0072)],
                               "2026-09-29T12:00:00Z", CELLS, REPINS,
                               verdict_match=True, bar_close=snapshot_has_repin)
    assert ok and "restated" in cause and "EFA 2014-07-03" in cause


def test_a_revision_the_snapshot_has_not_taken_excuses_nothing():
    # the manifest says re-pinned, but the data the re-run read still holds
    # the old bar (or no bar), so the revision cannot be what moved the metrics
    for close in (66.48, None):
        ok, _ = classify_drift([("is_edge_per_trade", 0.0071, 0.0072)],
                               "2026-09-29T12:00:00Z", CELLS, REPINS,
                               verdict_match=True,
                               bar_close=lambda a, tf, d, c=close: c)
        assert not ok
    ok, _ = classify_drift([("is_edge_per_trade", 0.0071, 0.0072)],
                           "2026-09-29T12:00:00Z", CELLS, REPINS, verdict_match=True)
    assert not ok                       # no snapshot reader: fail closed


def test_a_revision_before_the_verdict_excuses_nothing():
    ok, _ = classify_drift([("is_edge_per_trade", 0.0071, 0.0072)],
                           "2026-10-05T00:00:00Z", CELLS, REPINS, verdict_match=True,
                           bar_close=snapshot_has_repin)
    assert not ok


def test_a_revised_bar_after_the_recorded_data_end_excuses_nothing():
    cells = [("EFA", "1d", "2014-07-02T00:00:00Z")]
    ok, _ = classify_drift([("is_edge_per_trade", 0.0071, 0.0072)],
                           "2014-07-02T12:00:00Z", cells, REPINS, verdict_match=True,
                           bar_close=snapshot_has_repin)
    assert not ok


def test_a_revision_with_no_changed_bar_excuses_nothing():
    cells = [("EEM", "1d", "2026-09-29T00:00:00Z")]
    ok, _ = classify_drift([("is_edge_per_trade", 0.0071, 0.0072)],
                           "2026-09-29T12:00:00Z", cells, REPINS, verdict_match=True)
    assert not ok


def test_pre_engine_change_rounding_is_excused_up_to_ulp_max():
    before = "2026-08-21T00:00:00Z"
    assert before < ENGINE_CHANGE_UTC
    cells = [("EWZ", "1d", "2026-08-21T00:00:00Z")]
    ok, cause = classify_drift([("train_sharpe", *ULPS_3)], before, cells, REPINS,
                               verdict_match=True)
    assert ok and "rounding" in cause and "3 ulps" in cause
    import math
    far = 0.1
    for _ in range(ULP_MAX + 1):
        far = math.nextafter(far, 1.0)
    ok, _ = classify_drift([("train_sharpe", 0.1, far)], before, cells, REPINS,
                           verdict_match=True)
    assert not ok


def test_rounding_after_the_engine_change_is_not_excused():
    cells = [("EWZ", "1d", "2026-09-29T00:00:00Z")]
    ok, _ = classify_drift([("train_sharpe", *ULPS_3)], "2026-09-29T12:00:00Z",
                           cells, REPINS, verdict_match=True)
    assert not ok


def test_a_verdict_without_a_timestamp_excuses_nothing():
    for ts in (None, ""):
        ok, cause = classify_drift([("train_sharpe", *ULPS_3)], ts, CELLS, REPINS,
                                   verdict_match=True)
        assert not ok and "timestamp" in cause


def test_a_missing_or_non_float_metric_is_not_rounding():
    cells = [("EWZ", "1d", "2026-08-21T00:00:00Z")]
    for o, n in ((None, 0.1), (0.1, None), (True, False)):
        ok, _ = classify_drift([("p_ruin", o, n)], "2026-08-21T00:00:00Z", cells,
                               REPINS, verdict_match=True)
        assert not ok


def test_load_repins_reads_the_manifest_and_tolerates_its_absence(tmp_path):
    import json
    assert load_repins(tmp_path / "nope.json") is None
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"series": [], "repins": REPINS}), encoding="utf-8")
    assert load_repins(p) == REPINS
    p.write_text(json.dumps({"series": []}), encoding="utf-8")
    assert load_repins(p) == []


def test_csv_close_reads_one_dated_bar(tmp_path):
    from tools.gauntlet_parity import csv_close
    rows = ["date,open,high,low,close,volume",
            "2014-07-02 00:00:00,1,1,1,68.68,1",
            "2014-07-03 00:00:00,69.06,69.29,69.03,69.22,7"]
    (tmp_path / "EFA_1d.csv").write_text(chr(10).join(rows) + chr(10), encoding="utf-8")
    assert csv_close(tmp_path, "EFA_1d", "2014-07-03") == 69.22
    assert csv_close(tmp_path, "EFA_1d", "2014-07-04") is None
    assert csv_close(tmp_path, "NOPE_1d", "2014-07-03") is None
