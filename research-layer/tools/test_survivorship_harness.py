import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pipeline.beats_hold import gauntlet_beats_hold  # noqa: F401
from pipeline.gauntlet_core import evaluate_standalone
from pipeline.parallel import CellError  # noqa: F401
from pipeline.screen import GATE_MIN_TRADES, SpecJob, load_bars
from tools import survivorship_harness as h
from tools.survivorship_archive import write_pipeline_csv
from tools.survivorship_universe import CUTOFF

PERTH = ZoneInfo("Australia/Perth")


def _bars(start: date, end: date):
    out, i, d = [], 0, start
    while d <= end:
        c = 100.0 + 20.0 * math.sin(i / 15.0) + i * 0.05
        out.append({"date": d.isoformat(), "open": c - 0.3, "high": c + 1.0,
                    "low": c - 1.0, "close": c, "volume": 1000.0})
        d += timedelta(days=1)
        i += 1
    return out


def _spec(pair="BTCUSDT"):
    return {
        "strategy_id": "0123456789abcdef", "version": 2, "name": "t", "family": "t",
        "universe": {"assets": [pair], "asset_class": "crypto",
                     "timeframe": "1d", "session": "24x7"},
        "blocks": [
            {"role": "entry", "type": "ma_cross", "params": {"fast": 10, "slow": 30}},
            {"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}},
        ],
        "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005},
    }


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    write_pipeline_csv(_bars(date(2022, 6, 1), date(2024, 12, 31)), d / "BTCUSDT_1d.csv")
    write_pipeline_csv(_bars(date(2022, 6, 1), date(2024, 1, 20)), d / "SHORTUSDT_1d.csv")
    return d


def test_train_job_matches_screen_specjob(data_dir):
    spec = _spec()
    got = h.train_job((spec, str(data_dir), "1d"))
    bars = load_bars(data_dir, "BTCUSDT", CUTOFF, "1d")
    res, passed, reason = SpecJob(GATE_MIN_TRADES)((spec, {"BTCUSDT": bars}))
    assert got["screen_pass"] == passed and got["reason"] == reason
    assert got["trades"] == res["metrics"]["trades"]
    assert got["net_pnl"] == res["metrics"]["net_pnl"]
    assert got["sid"] == spec["strategy_id"]


def test_full_job_equals_gauntlet_beats_hold_sharpe(data_dir):
    spec = _spec()
    got = h.full_job((spec, str(data_dir), "1d"))
    bars = load_bars(data_dir, "BTCUSDT", h.END, "1d")
    r = evaluate_standalone(spec, {"BTCUSDT": bars}, CUTOFF, False, battery="v6.2")
    bh = r["metrics"]["beats_hold"]
    assert bh["strategy_sharpe"] is not None
    assert got["m"] == bh["strategy_sharpe"]
    assert got["m_none"] is False
    assert got["hold_sharpe"] == bh["hold_sharpe"] and got["n_days"] == bh["n_days"]


def test_short_series_gives_zero_and_flag(data_dir):
    spec = _spec("SHORTUSDT")
    got = h.full_job((spec, str(data_dir), "1d"))
    assert got["m"] == 0.0 and got["m_none"] is True


def test_gauntlet_job_shape(data_dir):
    got = h.gauntlet_job((_spec(), str(data_dir), "1d"))
    assert {"sid", "passed", "reason", "beats_hold"} <= set(got)


def _rows(n):
    return [{"key": f"t{i}|P|1d", "stratum": "s", "tkey": f"t{i}", "pair": "P",
             "tf": "1d", "item": ({"i": i}, "d", "1d")} for i in range(n)]


def _job(item):
    return {"v": item[0]["i"]}


def _clock(times):
    it = iter(times)
    return lambda: next(it)


def _perth(hh, mm=0):
    return datetime(2026, 11, 9, hh, mm, tzinfo=PERTH)


def test_resume_exactly_once(tmp_path):
    out = tmp_path / "o.jsonl"
    rows = _rows(10)
    r1 = h.run_arm(rows, _job, out, 1, now=_clock([_perth(10), _perth(21)]), chunk=5)
    assert r1["done"] == 5 and r1["stopped_at_window"] is True
    r2 = h.run_arm(rows, _job, out, 1, now=lambda: _perth(10), chunk=5)
    assert r2["skipped"] == 5 and r2["done"] == 5 and r2["stopped_at_window"] is False
    keys = [json.loads(l)["key"] for l in out.read_text().splitlines()]
    assert sorted(keys) == sorted(r["key"] for r in rows) and len(keys) == 10
    first = json.loads(out.read_text().splitlines()[0])
    assert first["stratum"] == "s" and first["tkey"] == "t0" and first["v"] == 0


def test_window_uses_estimate(tmp_path):
    out = tmp_path / "o.jsonl"
    r = h.run_arm(_rows(5), _job, out, 1, now=lambda: _perth(19, 59), chunk=5,
                  est_s_per_item=30.0)
    assert r["done"] == 0 and r["stopped_at_window"] is True


def test_partial_last_line_ignored(tmp_path):
    out = tmp_path / "o.jsonl"
    rows = _rows(3)
    out.write_text(json.dumps({"key": rows[0]["key"]}) + "\n" + '{"key": "t1|P')
    r = h.run_arm(rows, _job, out, 1, now=lambda: _perth(10), chunk=5)
    assert r["skipped"] == 1 and r["done"] == 2


def _bad(item):
    if item[0]["i"] == 3:
        raise ValueError("boom")
    return {"v": item[0]["i"]}


def test_cell_error_not_written(tmp_path):
    out = tmp_path / "o.jsonl"
    rows = _rows(6)
    r = h.run_arm(rows, _bad, out, 1, now=lambda: _perth(10), chunk=10)
    assert len(r["errors"]) == 1 and r["errors"][0]["key"] == "t3|P|1d"
    keys = [json.loads(l)["key"] for l in out.read_text().splitlines()]
    assert "t3|P|1d" not in keys and len(keys) == 5
