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
