"""Offline tests for the tradfi snapshot adapter (no network, tmp trees only).

Run: python -m pytest pipeline/test_tradfi_data.py -q
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from pipeline import tradfi_data as td


def _canon_sha(pairs):     # independent mirror of free_integrity.series_sha256 for fixtures
    canon = "\n".join(f"{d},{c:.6f}" for d, c in pairs)
    return hashlib.sha256(canon.encode()).hexdigest()


def _mk_source(tmp_path, closes, sha=None, verdict="ok"):
    """A minimal trading-systems tree: manifest + parquet + verdict for EUR."""
    root = tmp_path / "ts"
    (root / "data" / "tradfi").mkdir(parents=True)
    (root / "results" / "tradfi").mkdir(parents=True)
    idx = pd.to_datetime([d for d, _ in closes])
    df = pd.DataFrame({"open": float("nan"), "high": float("nan"), "low": float("nan"),
                       "close": [c for _, c in closes], "volume": float("nan")}, index=idx)
    df.index.name = "date"
    df.to_parquet(root / "data" / "tradfi" / "free_fx_EUR_1d.parquet")
    real_sha = sha or _canon_sha([(d, c) for d, c in closes if c == c])
    manifest = {"snapshot_utc": "2026-08-23", "label": "CTA-lite: financials + metals (free lane)",
                "selected": [{"id": "EUR", "lane": "fx", "sha256": real_sha,
                              "history_start": closes[0][0], "rows": len(closes)}],
                "excluded": []}
    (root / "results" / "tradfi" / "free_universe_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "data" / "tradfi" / "free_fx_EUR_1d.verdict.json").write_text(json.dumps({"verdict": verdict}), encoding="utf-8")
    return root


CLOSES = [("2026-08-18", 1.1701), ("2026-08-19", 1.1712), ("2026-08-20", float("nan")), ("2026-08-21", 1.1698)]


def test_snapshot_writes_filled_csv_and_manifest(tmp_path):
    root = _mk_source(tmp_path, CLOSES)
    out = tmp_path / "layer"
    written = td.snapshot(root, out, classes=("fx",), assets=("EUR",))
    assert written == ["EUR"]
    lines = (out / "data" / "EUR_1d.csv").read_text().splitlines()
    assert lines[0] == "date,open,high,low,close,volume"
    # NaN-close row dropped (matches the sha canon), o=h=l=c=fix, volume 0
    assert len(lines) == 1 + 3
    assert lines[1].split(",")[1:] == ["1.1701", "1.1701", "1.1701", "1.1701", "0"]
    man = json.loads((out / "data" / "tradfi_snapshot_manifest.json").read_text())
    assert man["source_snapshot_utc"] == "2026-08-23"
    assert man["series"]["EUR"]["bar_kind"] == "single_fix"
    assert man["series"]["EUR"]["sha256_verified"] is True


def test_snapshot_refuses_sha_mismatch_and_writes_nothing(tmp_path):
    root = _mk_source(tmp_path, CLOSES, sha="0" * 64)
    out = tmp_path / "layer"
    with pytest.raises(td.SnapshotRefused, match="EUR"):
        td.snapshot(root, out, classes=("fx",), assets=("EUR",))
    assert not (out / "data" / "EUR_1d.csv").exists()


def test_snapshot_refuses_fail_verdict_and_unpinned(tmp_path):
    root = _mk_source(tmp_path, CLOSES, verdict="fail")
    with pytest.raises(td.SnapshotRefused, match="verdict"):
        td.snapshot(root, tmp_path / "layer", classes=("fx",), assets=("EUR",))
    root2 = _mk_source(tmp_path / "b", CLOSES)
    with pytest.raises(td.SnapshotRefused, match="not pinned"):
        td.snapshot(root2, tmp_path / "layer2", classes=("fx",), assets=("GBP",))


def test_written_csv_loads_through_screen_load_bars(tmp_path):
    from pipeline.screen import load_bars
    root = _mk_source(tmp_path, CLOSES)
    out = tmp_path / "layer"
    td.snapshot(root, out, classes=("fx",), assets=("EUR",))
    bars = load_bars(out / "data", "EUR", cutoff="9999-12-31", timeframe="1d")
    assert len(bars) == 3 and bars[0]["open"] == bars[0]["close"] == 1.1701 and bars[0]["volume"] == 0.0
