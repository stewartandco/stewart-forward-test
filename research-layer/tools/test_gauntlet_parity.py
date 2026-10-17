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
    spec; the ~155 registered pooled BTCUSD+ETHUSD specs were judged with no
    control, so parity must re-judge them without one (record_benchmark
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
    assert "compared 1, mismatches 0, not comparable 0" in out.read_text(encoding="utf-8")


def test_legacy_pooled_crypto_is_only_the_multi_asset_crypto_book():
    from tools.gauntlet_parity import legacy_pooled_crypto
    u = lambda assets, cls="crypto": {"universe": {"assets": assets, "asset_class": cls}}
    assert legacy_pooled_crypto(u(["BTCUSD", "ETHUSD"]))
    assert not legacy_pooled_crypto(u(["SOLUSDT"]))
    assert not legacy_pooled_crypto(u(["EUR"], "fx"))
