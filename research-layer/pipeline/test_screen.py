"""Offline tests for the screening engine (no network, no API).

Run: python -m pytest pipeline/test_screen.py -q
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from .data_fetch import klines_to_rows, write_csv, SYMBOLS


# ---------------- data fetcher ----------------

# Two Binance kline records (list-of-lists API shape; only idx 0-5 used):
# [open_time_ms, open, high, low, close, volume, ...]
FAKE_KLINES = [
    [1502928000000, "4261.48", "4485.39", "4200.74", "4285.08", "795.15"],
    [1503014400000, "4285.08", "4371.52", "3938.77", "4108.37", "1199.88"],
]


def test_klines_to_rows_converts_and_dates():
    rows = klines_to_rows(FAKE_KLINES)
    assert rows[0] == {"date": "2017-08-17", "open": 4261.48, "high": 4485.39,
                       "low": 4200.74, "close": 4285.08, "volume": 795.15}
    assert rows[1]["date"] == "2017-08-18"


def test_write_csv_roundtrip(tmp_path):
    rows = klines_to_rows(FAKE_KLINES)
    out = tmp_path / "BTCUSD_1d.csv"
    write_csv(rows, out)
    back = list(csv.DictReader(out.open()))
    assert len(back) == 2
    assert back[0]["date"] == "2017-08-17"
    assert float(back[0]["close"]) == 4285.08


def test_symbol_mapping():
    assert SYMBOLS == {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}


def test_write_csv_bytes_are_lf_only(tmp_path):
    rows = klines_to_rows(FAKE_KLINES)
    out = tmp_path / "x.csv"
    write_csv(rows, out)
    raw = out.read_bytes()
    assert b"\r" not in raw


from .engine import (sma, stdev, atr_wilder, trend_tstat, realized_ann_vol,
                     percentile_rank)


# ---------------- indicators ----------------
# All indicator functions take aligned lists and return lists of the same
# length with None during warmup.

def test_sma_basic_and_warmup():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_stdev_is_sample_stdev():
    out = stdev([1, 2, 3], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(1.0)


def test_atr_wilder_flat_ranges():
    # every bar: high-low = 2, no gaps -> TR = 2 always; ATR converges to 2
    bars = [{"open": 10, "high": 11, "low": 9, "close": 10}] * 6
    out = atr_wilder(bars, 3)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(2.0)
    assert out[5] == pytest.approx(2.0)


def test_trend_tstat_perfect_line_is_infinite():
    assert trend_tstat([1.0, 2.0, 3.0, 4.0]) == float("inf")


def test_trend_tstat_flat_is_zero():
    assert trend_tstat([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_realized_ann_vol_flat_is_zero():
    out = realized_ann_vol([100.0] * 10, 5)
    assert out[-1] == pytest.approx(0.0)


def test_percentile_rank():
    # rank of current value among trailing window values (incl. current)
    assert percentile_rank([1, 2, 3], 2, 3) == pytest.approx(1.0)
    assert percentile_rank([3, 2, 1], 2, 3) == pytest.approx(1 / 3)


from .engine import entry_signals, gate_mask


def flat_bars(n, px=100.0):
    return [{"date": f"d{i}", "open": px, "high": px, "low": px,
             "close": px, "volume": 1.0} for i in range(n)]


def ramp_bars(n, start=100.0, step=1.0):
    out = []
    for i in range(n):
        c = start + i * step
        out.append({"date": f"d{i}", "open": c, "high": c, "low": c,
                    "close": c, "volume": 1.0})
    return out


# ---------------- entry signals ----------------

def test_ma_cross_signal_and_state():
    # 6 flat bars then a jump: fast sma(2) crosses above slow sma(4)
    bars = flat_bars(6) + ramp_bars(4, start=110.0, step=5.0)
    sig, state = entry_signals({"role": "entry", "type": "ma_cross",
                                "params": {"fast": 2, "slow": 4}}, bars)
    # state is +1 while fast>slow; the cross bar emits +1 in sig
    assert 1 in sig
    first = sig.index(1)
    assert state[first] == 1 and state[first - 1] in (0, None)


def test_channel_breakout_long_only_by_default():
    bars = flat_bars(6) + [{"date": "b", "open": 100, "high": 111, "low": 100,
                            "close": 111, "volume": 1.0}]
    spec = {"role": "entry", "type": "channel_breakout",
            "params": {"lookback": 5, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert sig[-1] == 1


def test_zscore_reversion_long_at_negative_z():
    bars = flat_bars(10) + [{"date": "b", "open": 90, "high": 90, "low": 90,
                             "close": 90, "volume": 1.0}]
    spec = {"role": "entry", "type": "zscore_reversion",
            "params": {"lookback": 5, "z_entry": 1.5, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert sig[-1] == 1          # 90 is far below the flat-100 mean


def test_trend_scan_long_on_strong_trend():
    bars = ramp_bars(130)
    spec = {"role": "entry", "type": "trend_scan",
            "params": {"max_lookback": 60, "t_min": 3.0}}
    sig, _ = entry_signals(spec, bars)
    assert sig[-1] == 1


def test_signals_none_during_warmup():
    bars = flat_bars(3)
    spec = {"role": "entry", "type": "channel_breakout",
            "params": {"lookback": 5, "direction": "long"}}
    sig, _ = entry_signals(spec, bars)
    assert all(s == 0 for s in sig)


# ---------------- gates ----------------

def test_regime_ma_gate_blocks_below_ma():
    down = [{"date": f"d{i}", "open": 100 - i, "high": 100 - i, "low": 100 - i,
             "close": 100 - i, "volume": 1.0} for i in range(120)]
    mask = gate_mask([{"role": "regime", "type": "regime_ma",
                       "params": {"ma_len": 100}}], down)
    assert mask[-1] is False     # falling market: close < sma(100)


def test_vol_percentile_gate_and_warmup():
    bars = flat_bars(500)
    mask = gate_mask([{"role": "filter", "type": "vol_percentile",
                       "params": {"lookback": 90, "max_pctile": 1.0}}], bars)
    assert mask[10] is False     # warmup (needs 365-bar percentile window)
    assert mask[-1] is True      # max_pctile 1.0 admits everything once warm


from .engine import simulate_asset


def breakout_spec_blocks(pct=0.05, r=1.0, max_bars=40, f=0.01):
    return [
        {"role": "entry", "type": "channel_breakout",
         "params": {"lookback": 5, "direction": "long"}},
        {"role": "stop", "type": "pct_stop", "params": {"pct": pct}},
        {"role": "target", "type": "r_multiple", "params": {"r": r}},
        {"role": "exit", "type": "time_stop", "params": {"max_bars": max_bars}},
        {"role": "risk", "type": "fixed_fraction", "params": {"f": f}},
    ]


COST = {"commission_per_side": 0.001, "slippage_ticks": 0.0005}


def target_hit_bars():
    bars = flat_bars(6)                                   # warmup, prior high 100
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})            # breakout close
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})            # entry at open 111
    bars.append({"date": "hit", "open": 112, "high": 120, "low": 112,
                 "close": 118, "volume": 1.0})            # target 116.55 hit
    return bars


# ---------------- simulator ----------------

def test_long_breakout_target_hit_math():
    book = simulate_asset(breakout_spec_blocks(), target_hit_bars(), COST)
    assert len(book["trades"]) == 1
    t = book["trades"][0]
    assert t["side"] == "long"
    assert t["entry_px"] == pytest.approx(111.0)
    # stop = 111*(1-0.05) = 105.45; distance 5.55; target = 116.55
    assert t["exit_px"] == pytest.approx(116.55)
    assert t["exit_reason"] == "target"
    gross = 116.55 / 111.0 - 1
    net = gross - 0.0015 * 2
    assert t["return_net"] == pytest.approx(net)
    # sizing: f=0.01, stop distance 5% -> notional = 0.2x equity
    assert book["equity"][-1] == pytest.approx(1 + 0.2 * net)


def test_same_bar_stop_and_target_stop_wins():
    bars = flat_bars(6)
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})
    bars.append({"date": "wide", "open": 111, "high": 120, "low": 100,
                 "close": 111, "volume": 1.0})            # touches both barriers
    book = simulate_asset(breakout_spec_blocks(), bars, COST)
    assert book["trades"][0]["exit_reason"] == "stop"
    assert book["trades"][0]["exit_px"] == pytest.approx(105.45)


def test_gap_through_stop_fills_at_open():
    bars = flat_bars(6)
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})
    bars.append({"date": "gap", "open": 90, "high": 95, "low": 88,
                 "close": 92, "volume": 1.0})             # opens far below stop
    book = simulate_asset(breakout_spec_blocks(), bars, COST)
    assert book["trades"][0]["exit_px"] == pytest.approx(90.0)
    assert book["trades"][0]["exit_reason"] == "stop"


def test_time_stop_exits_at_deadline_open():
    bars = flat_bars(6)
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})
    for i in range(4):                                    # drift, no barriers
        bars.append({"date": f"h{i}", "open": 112, "high": 113, "low": 111,
                     "close": 112, "volume": 1.0})
    book = simulate_asset(breakout_spec_blocks(max_bars=3), bars, COST)
    assert book["trades"][0]["exit_reason"] == "time"
    assert book["trades"][0]["exit_px"] == pytest.approx(112.0)


def test_one_position_at_a_time():
    # continuous new highs would re-signal every bar; only one open trade
    bars = flat_bars(6) + ramp_bars(10, start=101.0, step=2.0)
    blocks = breakout_spec_blocks(pct=0.05, r=3.0, max_bars=40)
    book = simulate_asset(blocks, bars, COST)
    assert len(book["trades"]) <= 1


def test_notional_cap_no_leverage():
    # tight stop 1% would imply 1.0/0.01 = 100x sizing at f=1.0 -> capped at 1x
    bars = target_hit_bars()
    blocks = breakout_spec_blocks(pct=0.01, r=1.0, f=1.0)
    book = simulate_asset(blocks, bars, COST)
    t = book["trades"][0]
    # stop 109.89, distance 1.11 -> target 112.11, hit on 'hit' bar
    gross = 112.11 / 111.0 - 1
    net = gross - 0.003
    assert book["equity"][-1] == pytest.approx(1 + 1.0 * net)  # notional 1x, not 100x


def test_signal_while_in_position_is_ignored_not_queued():
    # sig fires again at the fill bar's close (111 > rolling high 110);
    # it must not open a phantom same-bar position after the target exit
    book = simulate_asset(breakout_spec_blocks(), target_hit_bars(), COST)
    assert len(book["trades"]) == 1
    assert book["equity"][-1] == pytest.approx(
        1 + 0.2 * book["trades"][0]["return_net"])


def test_zero_stop_distance_skips_entry_no_crash(monkeypatch):
    import pipeline.engine as eng
    bars = flat_bars(30)
    sig = [0] * len(bars)
    sig[10] = 1
    monkeypatch.setattr(eng, "entry_signals", lambda block, b: (sig, [0] * len(b)))
    monkeypatch.setattr(eng, "_tightest_stop",
                        lambda stops, entry_px, side, atr_series, i: entry_px)  # dist 0
    blocks = [
        {"role": "entry", "type": "channel_breakout",
         "params": {"lookback": 5, "direction": "long"}},
        {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
        {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
    ]
    book = eng.simulate_asset(blocks, bars, COST)
    assert book["trades"] == []


from .engine import run_spec, max_drawdown


def make_screen_spec(assets=("BTCUSD",), **kw):
    return {
        "strategy_id": "aaaaaaaaaaaaaaaa",
        "universe": {"assets": list(assets), "asset_class": "crypto",
                     "timeframe": "1d", "session": "24x7"},
        "blocks": breakout_spec_blocks(**kw),
        "cost_model": COST,
    }


# ---------------- run_spec + metrics ----------------

def test_max_drawdown():
    assert max_drawdown([1.0, 1.2, 0.9, 1.1]) == pytest.approx(0.25)
    assert max_drawdown([1.0, 1.1, 1.2]) == pytest.approx(0.0)


def test_run_spec_single_asset_metrics():
    result = run_spec(make_screen_spec(), {"BTCUSD": target_hit_bars()})
    m = result["metrics"]
    assert m["trades"] == 1
    assert m["win_rate"] == pytest.approx(1.0)
    assert m["net_pnl"] == pytest.approx(result["equity"][-1][1] - 1)


def test_run_spec_two_assets_combined():
    btc = target_hit_bars()
    eth = flat_bars(9)
    for i, b in enumerate(eth):
        b["date"] = btc[i]["date"]
    bars = {"BTCUSD": btc, "ETHUSD": eth}
    result = run_spec(make_screen_spec(assets=("BTCUSD", "ETHUSD")), bars)
    # ETH book flat at 1.0; combined = mean -> half the BTC-only pnl
    solo = run_spec(make_screen_spec(), {"BTCUSD": target_hit_bars()})
    assert result["metrics"]["net_pnl"] == pytest.approx(solo["metrics"]["net_pnl"] / 2)
    assert result["metrics"]["trades"] == 1


def test_run_spec_deterministic():
    a = run_spec(make_screen_spec(), {"BTCUSD": target_hit_bars()})
    b = run_spec(make_screen_spec(), {"BTCUSD": target_hit_bars()})
    assert a == b


def test_run_spec_rejects_misaligned_calendars():
    btc = target_hit_bars()
    eth = flat_bars(9)
    for i, b in enumerate(eth):
        b["date"] = f"x{i}"          # same length, different calendar
    with pytest.raises(ValueError, match="calendar misalignment"):
        run_spec(make_screen_spec(assets=("BTCUSD", "ETHUSD")),
                 {"BTCUSD": btc, "ETHUSD": eth})


def test_max_drawdown_zero_peak_no_crash():
    assert max_drawdown([0.0, 0.0]) == 0.0


import subprocess
import sys

from .registry import Registry
from .screen import run as screen_run, PROTOCOL, GATE_MIN_TRADES
from .test_pipeline import make_card
from .blocks import BLOCK_TYPES, block_type_payload

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent


def run_verifier(log_path):
    return subprocess.run(
        [sys.executable, str(LAYER / "verify_registry.py"), str(log_path)],
        capture_output=True, text=True)


def write_data_dir(tmp_path, bars_by_asset):
    d = tmp_path / "data"
    d.mkdir()
    for asset, bars in bars_by_asset.items():
        with (d / f"{asset}_1d.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low",
                                              "close", "volume"])
            w.writeheader()
            w.writerows(bars)
    return d


def dated_target_hit_bars():
    """Like target_hit_bars but long enough to warm a lookback-20 breakout
    (23 bars) and carrying real pre-fence ISO dates."""
    bars = flat_bars(20)                                  # prior 20-bar high = 100
    bars.append({"date": "sig", "open": 100, "high": 110, "low": 100,
                 "close": 110, "volume": 1.0})
    bars.append({"date": "fill", "open": 111, "high": 111, "low": 111,
                 "close": 111, "volume": 1.0})
    bars.append({"date": "hit", "open": 112, "high": 120, "low": 112,
                 "close": 118, "volume": 1.0})
    for i, b in enumerate(bars):
        b["date"] = f"2023-01-{i + 1:02d}"
    return bars


def screening_registry(tmp_path, blocks=None):
    """Registry with grammar, an accepted card, and one proposed spec."""
    reg = Registry(tmp_path / "reg.jsonl")
    for key in BLOCK_TYPES:
        reg.register_block_type(block_type_payload(*key))
    # the test spec uses grammar-external param values, so register its shapes
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    spec = {
        "strategy_id": None, "version": 1,
        "created_utc": "2026-08-13T00:00:00Z",
        "name": "test breakout", "family": "breakout_test",
        "universe": {"assets": ["BTCUSD"], "asset_class": "crypto",
                     "timeframe": "1d", "session": "24x7"},
        "blocks": blocks if blocks is not None else [
            {"role": "entry", "type": "channel_breakout",
             "params": {"lookback": 20, "direction": "long"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.0}},
            {"role": "exit", "type": "time_stop", "params": {"max_bars": 40}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "provenance": {"card_ids": [card["card_id"]],
                       "parent_strategy_id": None,
                       "sibling_group_id": "g-test", "generation": 0},
        "generator": {"agent": "composer", "model": "m",
                      "pipeline_version": "g1.0.0", "run_id": "t"},
        "cost_model": dict(COST),
    }
    from .common import content_id
    spec["strategy_id"] = content_id(spec, "strategy_id")
    reg.register_strategy(spec)
    return reg, spec


def chain_protocol_note(reg):
    reg.append("note", {"text": f"{PROTOCOL}: test protocol anchor"})


# ---------------- screen CLI ----------------

def test_screen_refuses_real_run_without_protocol_note(tmp_path):
    reg, spec = screening_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data)])
    assert rc == 1


def test_screen_dry_run_allowed_without_note_and_writes_nothing(tmp_path, capsys):
    reg, spec = screening_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    n_before = sum(1 for _ in reg.entries())
    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--dry-run"])
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_before
    assert "DRY RUN" in capsys.readouterr().out


def test_screen_fence_excludes_post_cutoff_bars(tmp_path):
    # all signal bars dated AFTER the cutoff -> zero trades
    reg, spec = screening_registry(tmp_path)
    bars = dated_target_hit_bars()
    for i, b in enumerate(bars):
        b["date"] = f"2025-01-{i + 1:02d}"
    data = write_data_dir(tmp_path, {"BTCUSD": bars})
    chain_protocol_note(reg)
    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--artifacts-dir", str(tmp_path / "art")])
    assert rc == 0
    verdicts = [e for e in reg.entries() if e["entry_type"] == "verdict"]
    assert verdicts[0]["payload"]["metrics"]["trades"] == 0
    assert verdicts[0]["payload"]["verdict"] == "fail"


def test_screen_full_run_chains_and_verifies(tmp_path):
    reg, spec = screening_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    chain_protocol_note(reg)
    art = tmp_path / "art"
    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--artifacts-dir", str(art)])
    assert rc == 0
    states = reg.strategy_states()
    assert states[spec["strategy_id"]] == "graveyard"   # 1 trade < 40
    verdicts = [e for e in reg.entries() if e["entry_type"] == "verdict"]
    v = verdicts[0]["payload"]
    assert v["verdict"] == "fail" and v["metrics"]["trades"] == 1
    assert set(v["metrics"]) == {"trades", "net_pnl", "win_rate", "max_dd"}
    # graveyard reason is trade_count (pnl is positive)
    gy = [e["payload"] for e in reg.entries() if e["entry_type"] == "state_change"
          and e["payload"]["to"] == "graveyard"]
    assert gy[0]["reason"] == "trade_count"
    # artifacts exist and hash matches
    bundle = art / spec["strategy_id"]
    assert (bundle / "trades.csv").exists()
    assert (bundle / "equity.csv").exists()
    cfg = json.loads((bundle / "config.json").read_text())
    assert cfg["protocol"] == PROTOCOL
    from .screen import bundle_hash
    assert v["artifacts_hash"] == bundle_hash(bundle)
    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout


def test_screen_gate_pass_goes_to_gauntlet(tmp_path, monkeypatch):
    reg, spec = screening_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    chain_protocol_note(reg)
    monkeypatch.setattr("pipeline.screen.GATE_MIN_TRADES", 1)
    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--artifacts-dir", str(tmp_path / "art")])
    assert rc == 0
    assert reg.strategy_states()[spec["strategy_id"]] == "gauntlet"


def test_gate_min_trades_is_forty():
    assert GATE_MIN_TRADES == 40


def test_screen_reason_net_negative_when_trades_suffice(tmp_path, monkeypatch):
    # losing trade: same shape as dated_target_hit_bars but the trade stops out
    reg, spec = screening_registry(tmp_path)
    bars = dated_target_hit_bars()
    bars[-1] = {"date": bars[-1]["date"], "open": 108, "high": 108, "low": 100,
                "close": 101, "volume": 1.0}              # stop 105.45 hit
    data = write_data_dir(tmp_path, {"BTCUSD": bars})
    chain_protocol_note(reg)
    monkeypatch.setattr("pipeline.screen.GATE_MIN_TRADES", 1)
    rc = screen_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                     "--artifacts-dir", str(tmp_path / "art")])
    assert rc == 0
    gy = [e["payload"] for e in reg.entries() if e["entry_type"] == "state_change"
          and e["payload"]["to"] == "graveyard"]
    assert gy[0]["reason"] == "net_negative"
