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
