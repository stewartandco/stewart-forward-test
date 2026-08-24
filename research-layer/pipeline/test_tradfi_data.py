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


def _mk_source(tmp_path, closes, sha=None, verdict="ok", write_verdict=True):
    """A minimal trading-systems tree: manifest + parquet + verdict for EUR.

    The verdict file is `verdict_EUR.json` -- the real producer naming
    (`tradfi/free_fetch.py:354-355` `verdict_path`, id-keyed, lane-independent),
    not the `free_fx_EUR_1d.verdict.json` guess an earlier draft used.
    write_verdict=False omits the file entirely (the MISSING-verdict path).
    """
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
    if write_verdict:
        (root / "data" / "tradfi" / "verdict_EUR.json").write_text(json.dumps({"verdict": verdict}), encoding="utf-8")
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
    assert man["series"]["EUR"]["verdict"] == "ok"


def test_snapshot_records_missing_verdict_and_still_writes(tmp_path):
    root = _mk_source(tmp_path, CLOSES, write_verdict=False)
    assert not (root / "data" / "tradfi" / "verdict_EUR.json").exists()
    out = tmp_path / "layer"
    written = td.snapshot(root, out, classes=("fx",), assets=("EUR",))
    assert written == ["EUR"]
    man = json.loads((out / "data" / "tradfi_snapshot_manifest.json").read_text())
    assert man["series"]["EUR"]["verdict"] == "missing"


def test_snapshot_refuses_explicit_asset_outside_class(tmp_path):
    """SPY is pinned, verified, and sha-clean -- but it is not a declared fx
    asset. Without a membership check, --classes fx --assets SPY would pass
    a real equities OHLC series through fx's single_fix flattening and record
    a verified-looking lie (bar_kind: single_fix, sha256_verified: true) for
    data that was never fx at all."""
    root = tmp_path / "ts"
    (root / "data" / "tradfi").mkdir(parents=True)
    (root / "results" / "tradfi").mkdir(parents=True)
    closes = [("2026-08-18", 410.0), ("2026-08-19", 411.5)]
    idx = pd.to_datetime([d for d, _ in closes])
    df = pd.DataFrame({"open": [c - 1 for _, c in closes], "high": [c + 1 for _, c in closes],
                       "low": [c - 2 for _, c in closes], "close": [c for _, c in closes],
                       "volume": [1000.0, 1200.0]}, index=idx)
    df.index.name = "date"
    df.to_parquet(root / "data" / "tradfi" / "free_equities_SPY_1d.parquet")
    sha = _canon_sha(closes)
    manifest = {"snapshot_utc": "2026-08-23",
                "selected": [{"id": "SPY", "lane": "equities", "sha256": sha,
                              "history_start": closes[0][0], "rows": len(closes)}],
                "excluded": []}
    (root / "results" / "tradfi" / "free_universe_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "data" / "tradfi" / "verdict_SPY.json").write_text(json.dumps({"verdict": "ok"}), encoding="utf-8")

    out = tmp_path / "layer"
    with pytest.raises(td.SnapshotRefused) as excinfo:
        td.snapshot(root, out, classes=("fx",), assets=("SPY",))
    message = str(excinfo.value)
    assert "SPY" in message and "fx" in message
    assert not (out / "data" / "SPY_1d.csv").exists()


def test_snapshot_refuses_unknown_class(tmp_path):
    root = _mk_source(tmp_path, CLOSES)
    with pytest.raises(ValueError, match="not a declared class"):
        td.snapshot(root, tmp_path / "layer", classes=("bonds",), assets=("EUR",))


def test_snapshot_manifest_merges_across_subset_reruns(tmp_path):
    """A subset re-run (--assets EUR after a full fx snapshot) must not blow
    away GBP's provenance: GBP's CSV stays on disk, so its manifest entry
    must stay too, untouched, while EUR's entry updates."""
    root = tmp_path / "ts"
    (root / "data" / "tradfi").mkdir(parents=True)
    (root / "results" / "tradfi").mkdir(parents=True)

    def _write_asset(asset_id, closes):
        idx = pd.to_datetime([d for d, _ in closes])
        df = pd.DataFrame({"open": float("nan"), "high": float("nan"), "low": float("nan"),
                           "close": [c for _, c in closes], "volume": float("nan")}, index=idx)
        df.index.name = "date"
        df.to_parquet(root / "data" / "tradfi" / f"free_fx_{asset_id}_1d.parquet")
        (root / "data" / "tradfi" / f"verdict_{asset_id}.json").write_text(
            json.dumps({"verdict": "ok"}), encoding="utf-8")
        return _canon_sha([(d, c) for d, c in closes if c == c])

    eur_closes = CLOSES
    gbp_closes = [("2026-08-18", 0.8501), ("2026-08-19", 0.8512)]
    eur_sha = _write_asset("EUR", eur_closes)
    gbp_sha = _write_asset("GBP", gbp_closes)

    manifest = {"snapshot_utc": "2026-08-23", "selected": [
        {"id": "EUR", "lane": "fx", "sha256": eur_sha, "history_start": eur_closes[0][0], "rows": len(eur_closes)},
        {"id": "GBP", "lane": "fx", "sha256": gbp_sha, "history_start": gbp_closes[0][0], "rows": len(gbp_closes)},
    ], "excluded": []}
    (root / "results" / "tradfi" / "free_universe_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    out = tmp_path / "layer"
    written_full = td.snapshot(root, out, classes=("fx",), assets=("EUR", "GBP"))
    assert set(written_full) == {"EUR", "GBP"}
    man1 = json.loads((out / "data" / "tradfi_snapshot_manifest.json").read_text())
    gbp_entry_v1 = man1["series"]["GBP"]
    assert gbp_entry_v1["rows_written"] == 2

    written_subset = td.snapshot(root, out, classes=("fx",), assets=("EUR",))
    assert written_subset == ["EUR"]
    assert (out / "data" / "GBP_1d.csv").exists()          # never touched, never deleted

    man2 = json.loads((out / "data" / "tradfi_snapshot_manifest.json").read_text())
    assert man2["series"]["GBP"] == gbp_entry_v1            # carried forward, byte-identical
    assert man2["series"]["EUR"]["rows_written"] == 3
    assert man2["previous_snapshot_utc"] == man1["snapshot_utc"]


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
