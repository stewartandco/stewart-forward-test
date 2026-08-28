"""Tests for tools_benchmark_backfill_report_crypto.py (SP5 Task 5).

Everything runs against a SYNTHETIC chain + artifacts in tmp_path -- never
the live registry_log.jsonl. The happy-path tests monkeypatch the module's
EXPECTED_N down to the fixture cohort size; one test deliberately leaves the
real EXPECTED_N in place to pin the refusal behaviour.
"""
import csv
import json

import pytest

from pipeline.registry import Registry

import tools_benchmark_backfill_report_crypto as tool


CRYPTO_ASSETS = ["BTCUSD", "ETHUSD"]
COST_MODEL = {"commission_per_side": 0.001, "slippage_ticks": 0.0005}
PER_SIDE = COST_MODEL["commission_per_side"] + COST_MODEL["slippage_ticks"]
CUTOFF = "2026-01-01"

TRADE_FIELDS = ["asset", "side", "entry_date", "entry_px", "exit_date",
                "exit_px", "exit_reason", "return_net", "notional_frac"]


# -- fixture builders ------------------------------------------------------

def register(reg, sid, asset_class="crypto", assets=None,
             sibling_group_id="fam-2026-08-22-gen5"):
    reg.append("strategy_registered", {
        "strategy_id": sid,
        "family": "fam",
        "universe": {"asset_class": asset_class,
                     "assets": list(CRYPTO_ASSETS) if assets is None
                     else list(assets),
                     "timeframe": "1d"},
        "provenance": {"sibling_group_id": sibling_group_id},
    })


def change_state(reg, sid, to):
    reg.append("state_change", {"strategy_id": sid, "from": "x", "to": to})


def write_bundle(artifacts_dir, sid, cutoff=CUTOFF, trades=None):
    bundle = artifacts_dir / sid / "gauntlet"
    bundle.mkdir(parents=True)
    (bundle / "config.json").write_text(json.dumps(
        {"cutoff": cutoff, "spec": {"cost_model": dict(COST_MODEL)}}),
        encoding="utf-8")
    if trades is None:
        trades = [{"asset": "BTCUSD", "side": "long",
                   "entry_date": "2026-01-02", "entry_px": "100",
                   "exit_date": "2026-01-03", "exit_px": "101",
                   "exit_reason": "target", "return_net": "0.01",
                   "notional_frac": "0.5"}]
    with (bundle / "oos_trades.csv").open("w", newline="",
                                          encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        w.writeheader()
        w.writerows(trades)


def write_bars(data_dir, asset, bars):
    """bars: list of (date, open, close)."""
    with (data_dir / f"{asset}_1d.csv").open("w", newline="",
                                             encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for date, o, c in bars:
            w.writerow([date, o, max(o, c), min(o, c), c, 1000])


def write_fixture_data(data_dir):
    """One pre-cutoff bar each, then the hand-pinned three-bar OOS window:
    btc closes [100, 110, 99] entered at open 100, eth closes
    [200, 210, 231] entered at open 200."""
    write_bars(data_dir, "BTCUSD", [
        (CUTOFF, 90, 95),
        ("2026-01-02", 100, 100),
        ("2026-01-03", 100, 110),
        ("2026-01-04", 110, 99),
    ])
    write_bars(data_dir, "ETHUSD", [
        (CUTOFF, 190, 195),
        ("2026-01-02", 200, 200),
        ("2026-01-03", 200, 210),
        ("2026-01-04", 210, 231),
    ])


def build_happy(tmp_path, sids=("sid-aaa", "sid-bbb")):
    """Complete synthetic world: registry with `sids` all crypto+quarantine,
    full artifact bundles, and the pinned data CSVs."""
    registry_path = tmp_path / "registry_log.jsonl"
    artifacts = tmp_path / "artifacts"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    reg = Registry(registry_path)
    for sid in sids:
        register(reg, sid)
        change_state(reg, sid, "quarantine")
        write_bundle(artifacts, sid)
    write_fixture_data(data_dir)
    out = tmp_path / "out" / "report.md"
    return registry_path, artifacts, data_dir, out


def run_main(registry_path, artifacts, data_dir, out):
    return tool.main(["--registry", str(registry_path),
                      "--artifacts", str(artifacts),
                      "--data-dir", str(data_dir),
                      "--out", str(out)])


# -- 1. cohort discovery ---------------------------------------------------

def test_cohort_last_state_and_class_filter(tmp_path):
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "in-crypto")
    change_state(reg, "in-crypto", "quarantine")
    register(reg, "out-equity", asset_class="equity_etf",
             assets=["SPY"])
    change_state(reg, "out-equity", "quarantine")
    register(reg, "out-grave")
    change_state(reg, "out-grave", "graveyard")
    register(reg, "out-was-quarantine")
    change_state(reg, "out-was-quarantine", "quarantine")
    change_state(reg, "out-was-quarantine", "graveyard")

    cohort = tool.find_crypto_quarantine_cohort(reg)
    assert sorted(cohort) == ["in-crypto"]


def test_cohort_hard_asserts_pooled_universe(tmp_path):
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "single-asset", assets=["BTCUSD"])
    change_state(reg, "single-asset", "quarantine")
    with pytest.raises(ValueError, match="single-asset"):
        tool.find_crypto_quarantine_cohort(reg)


# -- 2. cohort-count refusal ----------------------------------------------

def test_refuses_on_count_mismatch_and_writes_nothing(tmp_path, capsys):
    # Real EXPECTED_N (20) left in place; fixture has 2 -> refuse.
    registry_path, artifacts, data_dir, out = build_happy(tmp_path)
    assert tool.EXPECTED_N == 20
    with pytest.raises(SystemExit) as exc:
        run_main(registry_path, artifacts, data_dir, out)
    assert exc.value.code == 2
    assert not out.exists()
    assert not out.parent.exists()  # not even the parent dir was created
    err = capsys.readouterr().err
    assert "20" in err and "2" in err  # actual vs expected in the message


# -- 3. completeness refusal ----------------------------------------------

def test_refuses_on_missing_artifact_and_writes_nothing(
        tmp_path, monkeypatch, capsys):
    registry_path, artifacts, data_dir, out = build_happy(tmp_path)
    monkeypatch.setattr(tool, "EXPECTED_N", 2)
    (artifacts / "sid-bbb" / "gauntlet" / "oos_trades.csv").unlink()
    with pytest.raises(SystemExit) as exc:
        run_main(registry_path, artifacts, data_dir, out)
    assert exc.value.code == 2
    assert not out.exists()
    assert "sid-bbb" in capsys.readouterr().err


def test_refuses_on_missing_data_csv(tmp_path, monkeypatch):
    registry_path, artifacts, data_dir, out = build_happy(tmp_path)
    monkeypatch.setattr(tool, "EXPECTED_N", 2)
    (data_dir / "ETHUSD_1d.csv").unlink()
    with pytest.raises(SystemExit) as exc:
        run_main(registry_path, artifacts, data_dir, out)
    assert exc.value.code == 2
    assert not out.exists()


# -- 4 + 5. control math pins ---------------------------------------------

def _one_row(tmp_path):
    registry_path, artifacts, data_dir, _ = build_happy(
        tmp_path, sids=("sid-aaa",))
    reg = Registry(registry_path)
    cohort = tool.find_crypto_quarantine_cohort(reg)
    return tool.benchmark_row("sid-aaa", cohort["sid-aaa"],
                              artifacts, data_dir)


def test_basket_hold_pin_hand_computed(tmp_path):
    # r_btc = [0.0, +0.10, -0.10], r_eth = [0.0, +0.05, +0.10]
    # basket day returns [0.0, +0.075, 0.0]; gross = 1.075 - 1 = 0.075
    row = _one_row(tmp_path)
    assert row["basket_hold"] == pytest.approx(
        0.075 - 2 * PER_SIDE, abs=1e-12)


def test_per_asset_hold_pins(tmp_path):
    row = _one_row(tmp_path)
    assert row["btc_hold"] == pytest.approx(
        (99 / 100 - 1) - 2 * PER_SIDE, abs=1e-12)
    assert row["eth_hold"] == pytest.approx(
        (231 / 200 - 1) - 2 * PER_SIDE, abs=1e-12)


def test_strategy_net_and_excess(tmp_path):
    row = _one_row(tmp_path)
    # single committed trade: 0.01 return_net x 0.5 notional_frac
    assert row["strategy_net"] == pytest.approx(0.005, abs=1e-12)
    assert row["excess_btc"] == pytest.approx(
        row["strategy_net"] - row["btc_hold"], abs=1e-12)
    assert row["excess_eth"] == pytest.approx(
        row["strategy_net"] - row["eth_hold"], abs=1e-12)
    assert row["excess_basket"] == pytest.approx(
        row["strategy_net"] - row["basket_hold"], abs=1e-12)


# -- 6. date normalization ------------------------------------------------

def test_time_suffixed_cutoff_bar_is_not_oos(tmp_path):
    # A bar dated "2026-08-01 00:00:00" with cutoff "2026-08-01" is ON the
    # cutoff date, so it is NOT after the cutoff ([:10] compare).
    bars = [
        {"date": "2026-08-01 00:00:00", "open": 1.0, "close": 2.0},
        {"date": "2026-08-02 00:00:00", "open": 3.0, "close": 4.0},
    ]
    oos = tool.oos_bars(bars, "2026-08-01")
    assert [b["date"] for b in oos] == ["2026-08-02 00:00:00"]


# -- 7. edge numbers in the report ----------------------------------------

def test_edge_labels_and_columns_in_report(tmp_path, monkeypatch):
    registry_path, artifacts, data_dir, out = build_happy(tmp_path)
    monkeypatch.setattr(tool, "EXPECTED_N", 2)
    assert run_main(registry_path, artifacts, data_dir, out) == 0
    text = out.read_text(encoding="utf-8")
    assert "#0001" in text and "#0002" in text
    assert "sid-aaa" in text and "sid-bbb" in text
    assert "fam-2026-08-22-gen5" in text          # sibling_group_id column
    assert "READ ONLY" in text
    for col in ("btc_hold", "eth_hold", "basket_hold"):
        assert col in text


# -- 8. read-only ----------------------------------------------------------

def test_read_only_registry_untouched_only_report_written(
        tmp_path, monkeypatch):
    registry_path, artifacts, data_dir, out = build_happy(tmp_path)
    monkeypatch.setattr(tool, "EXPECTED_N", 2)
    registry_bytes = registry_path.read_bytes()
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert run_main(registry_path, artifacts, data_dir, out) == 0
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert registry_path.read_bytes() == registry_bytes
    assert after - before == {out}
