from pipeline import gauntlet_core
from tools.gauntlet_parity import truncate_bars, GATE_KEYS


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
