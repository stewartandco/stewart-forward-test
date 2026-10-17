from __future__ import annotations

import json
from unittest import mock

from tools import daily_parity as dp


def test_compare_identical_and_different(tmp_path):
    a = {"meta": {"seed": 1, "cutoff": "2023-12-31", "sample_n": 10, "registry_sha256": "abc",
                  "pipeline_file": "/p", "bars_module": False, "engine_rev": "v1", "registry_entries": 100},
         "sample": ["s1"], "results": {"s1": {"run_full": "x"}}, "errors": {}}
    b = json.loads(json.dumps(a))
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    b["results"]["s1"]["run_full"] = "y"
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1


def test_compare_meta_registry_sha256_difference(tmp_path, capsys):
    a = {"meta": {"seed": 1, "cutoff": "2023-12-31", "sample_n": 10, "registry_sha256": "abc",
                  "pipeline_file": "/p", "bars_module": False, "engine_rev": "v1", "registry_entries": 100},
         "sample": ["s1"], "results": {"s1": {"run_full": "x"}}, "errors": {}}
    b = json.loads(json.dumps(a))
    b["meta"]["registry_sha256"] = "xyz"
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
    captured = capsys.readouterr()
    assert "registry_sha256 differs" in captured.out


def test_compare_identical_errors_fails(tmp_path, capsys):
    """Both sides with identical errors must return 1 and print them."""
    a = {"meta": {"seed": 1, "cutoff": "2023-12-31", "sample_n": 10, "registry_sha256": "abc",
                  "pipeline_file": "/p", "bars_module": False, "engine_rev": "v1", "registry_entries": 100},
         "sample": ["s1"], "results": {}, "errors": {"s1": "ValueError: test"}}
    b = json.loads(json.dumps(a))
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
    captured = capsys.readouterr()
    assert "errors present" in captured.out
    assert "ValueError: test" in captured.out


def test_compare_empty_results_fails(tmp_path, capsys):
    """Both sides with empty results must return 1."""
    a = {"meta": {"seed": 1, "cutoff": "2023-12-31", "sample_n": 10, "registry_sha256": "abc",
                  "pipeline_file": "/p", "bars_module": False, "engine_rev": "v1", "registry_entries": 100},
         "sample": [], "results": {}, "errors": {}}
    b = json.loads(json.dumps(a))
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
    captured = capsys.readouterr()
    assert "empty results" in captured.out


def test_select_includes_special_specs_and_stratifies():
    def spec(sid, cls, special=False):
        blocks = [{"type": "vol_target" if special else "fixed_fraction"}]
        return {"strategy_id": sid, "blocks": blocks,
                "universe": {"asset_class": cls, "timeframe": "1d"}}
    specs = {f"v{i}": spec(f"v{i}", "crypto", True) for i in range(3)}
    specs.update({f"f{i}": spec(f"f{i}", "fx") for i in range(5)})
    specs.update({f"e{i}": spec(f"e{i}", "equity_etf") for i in range(5)})
    specs["h1"] = dict(spec("h1", "crypto"), universe={"asset_class": "crypto", "timeframe": "1h"})
    got = dp.select(specs, 7, 1)
    assert {"v0", "v1", "v2"} <= set(got) and "h1" not in got and len(got) == 7
    assert any(s.startswith("f") for s in got) and any(s.startswith("e") for s in got)


def test_fingerprint_changes_with_bar_change():
    """Prove that changing one bar changes the run_full fingerprint."""
    spec = {
        "strategy_id": "test_s",
        "blocks": [{"type": "fixed_fraction"}],
        "universe": {"asset_class": "crypto", "timeframe": "1d", "assets": ["BTC"]},
    }

    # Create mock data that will be returned by load_cell_data
    import numpy as np
    bars_data = {
        ("BTC", "1d"): np.array([
            (20230101, 40000.0), (20230102, 41000.0), (20230103, 42000.0),
        ], dtype=[("date", "i4"), ("close", "f8")])
    }
    hashes_data = {
        "BTC_1d": "hash123"
    }

    # Mock the necessary functions
    with mock.patch("tools.daily_parity.load_cell_data") as mock_load, \
         mock.patch("tools.daily_parity._spec_bars") as mock_spec_bars, \
         mock.patch("tools.daily_parity.run_spec") as mock_run, \
         mock.patch("tools.daily_parity.evaluate_standalone") as mock_eval, \
         mock.patch("tools.daily_parity.daily_returns_with_dates") as mock_drd, \
         mock.patch("tools.daily_parity.gauntlet.daily_returns_from_curve") as mock_cret, \
         mock.patch("tools.daily_parity.annualized_sharpe") as mock_sharpe, \
         mock.patch("tools.daily_parity.simcache.cache_key") as mock_ck, \
         mock.patch("tools.daily_parity.cells.cell_id") as mock_cid:

        # Setup return values
        mock_load.return_value = (bars_data, hashes_data, None)
        mock_spec_bars.return_value = bars_data
        mock_cid.return_value = "BTC_1d"
        mock_ck.return_value = "cache_key_1"
        mock_drd.return_value = [0.01, 0.02]
        mock_cret.return_value = [0.01, 0.02]
        mock_sharpe.return_value = 1.5

        # run_spec mock that includes the bars in the result (echoing the input)
        def run_spec_side_effect(spec_arg, bars):
            # Return a result that includes a hash of the bars, so different bars = different result
            bars_hash = dp._h(str(bars))[:16]  # Use first 16 chars of hash
            return {
                "equity": np.array([100.0, 101.0, 103.0]),
                "trades": [],
                "metrics": {"ret": 0.03, "bars_hash": bars_hash},
            }
        mock_run.side_effect = run_spec_side_effect

        mock_eval.return_value = {
            "passed": True,
            "reason": "ok",
            "metrics": {"sharpe": 1.5},
            "mc_summary": {},
        }

        # Get first fingerprint
        fp1 = dp.fingerprint(spec, "/data", "2023-12-31")
        run_full_1 = fp1["run_full"]

        # Change one bar's close price and get second fingerprint
        bars_data[("BTC", "1d")][2]["close"] = 43000.0  # Changed from 42000.0
        fp2 = dp.fingerprint(spec, "/data", "2023-12-31")
        run_full_2 = fp2["run_full"]

        # They should be different because the bar changed
        assert run_full_1 != run_full_2, "Fingerprint should change when a bar changes"


def test_fingerprint_completes_for_a_legacy_pooled_crypto_spec(tmp_path):
    """Switch 1: crypto's benchmark is "self", which raises for a 2-asset
    spec. daily_parity re-runs registered pooled BTCUSD+ETHUSD specs, so it
    must fingerprint them without the control (they were judged without one
    and the pre-switch code computes none), not record an error that the
    compare step then reports as a parity break. Real engine, real files."""
    import pytest
    from pipeline import gauntlet_core
    from pipeline.test_screen import (write_data_dir, dated_target_hit_bars,
                                      breakout_spec_blocks, COST)
    spec = {"strategy_id": "cd" * 8, "version": 1,
            "universe": {"assets": ["BTCUSD", "ETHUSD"], "asset_class": "crypto",
                         "timeframe": "1d", "session": "24x7"},
            "blocks": breakout_spec_blocks(), "cost_model": dict(COST)}
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars(),
                                     "ETHUSD": dated_target_hit_bars()})
    fp = dp.fingerprint(spec, data, "2023-01-21")
    assert {"run_full", "standalone", "passed", "reason"} <= set(fp)
    assert dp._standalone_kwargs(spec) == {"record_benchmark": False}
    # premise: without the keyword the same spec raises
    sb = gauntlet_core._spec_bars({("BTCUSD", "1d"): dated_target_hit_bars(),
                                   ("ETHUSD", "1d"): dated_target_hit_bars()}, spec)
    with pytest.raises(ValueError, match="exactly one asset per cell"):
        gauntlet_core.evaluate_standalone(spec, sb, "2023-01-21", perturb=False)
    # a single-asset crypto spec skips it too (the base computes none for crypto: A/B parity)
    one = dict(spec, universe=dict(spec["universe"], assets=["BTCUSD"]))
    assert dp._standalone_kwargs(one) == {"record_benchmark": False}
    # a non-crypto spec keeps the default (its control is computed on both sides)
    fx = dict(spec, universe=dict(spec["universe"], asset_class="fx", assets=["EURUSD"]))
    assert dp._standalone_kwargs(fx) == {}


# ---- --twins-1h (engine-perf Task 6) ----

def _walk(n, step_hours, seed):
    """Seeded random-walk OHLCV rows, dated like the real CSVs."""
    import datetime as dt
    import random
    rng = random.Random(seed)
    px, t0, rows = 100.0, dt.datetime(2023, 1, 1), []
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rng.gauss(0.0002, 0.01)))
        hi, lo = max(o, px) * 1.004, min(o, px) * 0.996
        t = t0 + dt.timedelta(hours=step_hours * i)
        d = t.strftime("%Y-%m-%d") if step_hours == 24 else t.strftime("%Y-%m-%d %H:%M:%S")
        rows.append({"date": d, "open": o, "high": hi, "low": lo, "close": px, "volume": 1000.0})
    return rows


def _write_csv(path, rows):
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(rows)


def _crypto_spec(sid, entry_type, asset="BTCUSDT", asset_class="crypto"):
    return {"strategy_id": sid, "version": 2, "family": "f",
            "universe": {"assets": [asset], "asset_class": asset_class,
                         "timeframe": "1d", "session": "24x7"},
            "blocks": [{"role": "entry", "type": entry_type,
                        "params": ({"max_lookback": 60, "t_min": 2.0} if entry_type == "trend_scan"
                                   else {"lookback": 20, "direction": "long"})},
                       {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}},
                       (
                           {"role": "exit", "type": "tstat_decay",
                            "params": {"max_lookback": 60, "t_exit": 0.5}}
                           if entry_type == "trend_scan" else
                           {"role": "exit", "type": "regime_flip", "params": {"ma_len": 50}}),
                       {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}],
            "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005},
            "provenance": {"card_ids": ["c1"], "run_id": "r", "sibling_group_id": "g"}}


def _twin_world(tmp_path, with_1h=True):
    """A raw chain (one trend_scan crypto spec + one channel_breakout crypto
    spec, both 1d) and a data dir with BTCUSDT 1d, and 1h unless told not."""
    reg = tmp_path / "reg.jsonl"
    specs = [_crypto_spec("a1" * 8, "trend_scan"), _crypto_spec("b2" * 8, "channel_breakout")]
    reg.write_text("".join(json.dumps({"entry_type": "strategy_registered", "payload": s}) + "\n"
                           for s in specs), encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    _write_csv(data / "BTCUSDT_1d.csv", _walk(400, 24, 1))
    if with_1h:
        _write_csv(data / "BTCUSDT_1h.csv", _walk(2880, 1, 2))
    return reg, data


def _dump(reg, data, out, *extra):
    return dp.main(["dump", "--registry", str(reg), "--data-dir", str(data), "--out", str(out),
                    "--sample", "2", "--cutoff", "2023-03-15", *extra])


def test_twins_1h_are_fingerprinted_and_compared(tmp_path, capsys):
    from tools.intraday_rehearsal import make_twin
    reg, data = _twin_world(tmp_path)
    assert _dump(reg, data, tmp_path / "a.json", "--twins-1h", "1") == 0
    assert _dump(reg, data, tmp_path / "b.json", "--twins-1h", "1") == 0
    a = json.loads((tmp_path / "a.json").read_text())
    # the twin is a 1h twin of the trend_scan original (the only eligible one), on the 1h file's asset
    orig = _crypto_spec("a1" * 8, "trend_scan")
    want = make_twin(orig, "BTCUSDT", "1h")["strategy_id"]
    assert a["twins"] == {want: {"original": "a1" * 8, "asset": "BTCUSDT", "tf": "1h"}}
    assert a["meta"]["twins_1h"] == 1
    assert want in a["results"] and not a["errors"]
    # a single-asset crypto twin must skip the Switch 1 benchmark control, or its
    # `standalone` hash differs from the base's (which computes none) by one key
    assert dp._standalone_kwargs(make_twin(orig, "BTCUSDT", "1h")) == {"record_benchmark": False}
    assert a["results"][want]["resolved_periods"] == 365 * 24          # a real 1h run
    assert set(a["sample"]) == {"a1" * 8, "b2" * 8}                      # 1d sample untouched
    assert dp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    assert "compared 3" in capsys.readouterr().out


def test_twins_1h_compare_catches_a_twin_mismatch_and_a_missing_twin(tmp_path, capsys):
    reg, data = _twin_world(tmp_path)
    _dump(reg, data, tmp_path / "a.json", "--twins-1h", "1")
    a = json.loads((tmp_path / "a.json").read_text())
    (twin,) = a["twins"]
    A, B = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    # a changed twin fingerprint
    b = json.loads(json.dumps(a))
    b["results"][twin]["run_full"] = "tampered"
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", A, B]) == 1
    assert twin in capsys.readouterr().out
    # twins requested but absent on one side (no vacuous pass on the 1d part)
    b = json.loads(json.dumps(a))
    b["twins"], b["results"] = {}, {k: v for k, v in a["results"].items() if k != twin}
    (tmp_path / "b.json").write_text(json.dumps(b))
    assert dp.main(["compare", A, B]) == 1
    out = capsys.readouterr().out
    assert "twins differ" in out
    # twins requested and absent on BOTH sides
    (tmp_path / "a.json").write_text(json.dumps(b))
    assert dp.main(["compare", A, B]) == 1
    assert "twins_1h" in capsys.readouterr().out
    # different twin counts requested
    b = json.loads(json.dumps(a))
    b["meta"]["twins_1h"] = 2
    (tmp_path / "b.json").write_text(json.dumps(b))
    (tmp_path / "a.json").write_text(json.dumps(a))
    assert dp.main(["compare", A, B]) == 1
    assert "twins_1h differs" in capsys.readouterr().out


def test_twins_1h_zero_produced_is_an_error(tmp_path, capsys):
    reg, data = _twin_world(tmp_path, with_1h=False)             # no *USDT_1h.csv
    assert _dump(reg, data, tmp_path / "a.json", "--twins-1h", "1") != 0
    assert not (tmp_path / "a.json").exists()
    assert "twin" in capsys.readouterr().err
    # fewer than asked is an error too (the proof wants all N): only one eligible original
    (tmp_path / "w2").mkdir()
    reg, data = _twin_world(tmp_path / "w2")
    assert _dump(reg, data, tmp_path / "w2" / "a.json", "--twins-1h", "2") != 0
    # no twins requested: unchanged output shape, no twins needed
    assert _dump(reg, data, tmp_path / "w2" / "c.json") == 0
    c = json.loads((tmp_path / "w2" / "c.json").read_text())
    assert c["twins"] == {} and c["meta"]["twins_1h"] == 0


def test_twin_originals_are_restricted_to_crypto_1d_changed_code_blocks():
    def sp(sid, cls="crypto", tf="1d", btype="channel_breakout", role="entry"):
        return {"strategy_id": sid, "blocks": [{"role": role, "type": btype}],
                "universe": {"asset_class": cls, "timeframe": tf, "assets": ["BTCUSDT"]}}
    specs = {s["strategy_id"]: s for s in [
        sp("ts", btype="trend_scan"), sp("tsd", btype="trend_scan_dense"),
        sp("td", btype="tstat_decay", role="exit"), sp("vp", btype="vol_percentile", role="filter"),
        sp("vpd", btype="vol_percentile_dense", role="filter"),
        sp("plain"), sp("fx_ts", cls="fx", btype="trend_scan"),
        sp("h1_ts", tf="1h", btype="trend_scan")]}
    got = dp.twin_candidates(specs)
    assert set(got) == {"ts", "tsd", "td", "vp", "vpd"}
