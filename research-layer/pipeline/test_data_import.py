"""Import trading-systems parquet bars into the CSV format the engine reads.

Copying data across trees is allowed; importing code across trees is not
(trading-systems CLAUDE.md), so this reads files and writes files, nothing more.
"""
import pandas as pd
import pytest

from pipeline import data_import as di


@pytest.fixture
def parquet(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    idx = pd.to_datetime(["2023-12-30 00:00:00", "2023-12-30 04:00:00",
                          "2023-12-30 08:00:00"])
    pd.DataFrame({"open": [1.0, 2.0, 3.0], "high": [2.0, 3.0, 4.0],
                  "low": [0.5, 1.5, 2.5], "close": [1.5, 2.5, 3.5],
                  "volume": [10.0, 20.0, 30.0]}, index=idx
                 ).rename_axis("ts").to_parquet(src / "BTCUSDT_4h.parquet")
    return src


def test_import_writes_the_engine_csv_format(parquet, tmp_path):
    out = tmp_path / "out"
    n = di.import_cell(parquet, out, "BTCUSDT", "4h")
    assert n == 3
    text = (out / "BTCUSDT_4h.csv").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "date,open,high,low,close,volume"
    assert text.splitlines()[1].startswith("2023-12-30 00:00:00,1.0,2.0,0.5,1.5")


def test_intraday_timestamps_keep_their_time_component(parquet, tmp_path):
    """Truncating to a date would collapse every intraday bar of a day onto one
    key and break the engine's ordering."""
    out = tmp_path / "out"
    di.import_cell(parquet, out, "BTCUSDT", "4h")
    dates = [l.split(",")[0] for l in
             (out / "BTCUSDT_4h.csv").read_text(encoding="utf-8").splitlines()[1:]]
    assert dates == ["2023-12-30 00:00:00", "2023-12-30 04:00:00",
                     "2023-12-30 08:00:00"]
    assert len(set(dates)) == 3


def test_import_is_deterministic_so_the_data_hash_is_stable(parquet, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    di.import_cell(parquet, a, "BTCUSDT", "4h")
    di.import_cell(parquet, b, "BTCUSDT", "4h")
    assert (a / "BTCUSDT_4h.csv").read_bytes() == (b / "BTCUSDT_4h.csv").read_bytes()


def test_a_missing_source_cell_is_refused_not_skipped(parquet, tmp_path):
    with pytest.raises(FileNotFoundError):
        di.import_cell(parquet, tmp_path / "out", "SOLUSDT", "30m")


def test_undeclared_cells_are_refused(parquet, tmp_path):
    with pytest.raises(ValueError):
        di.import_cell(parquet, tmp_path / "out", "DOGEUSDT", "4h")
