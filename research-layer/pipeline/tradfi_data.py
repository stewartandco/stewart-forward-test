"""Pinned snapshot adapter: copy tradfi series into the research layer's data
convention, verified against the producer's own manifest.

Spec ss3 + s10.3. Trading-systems is a sibling repo, not a dependency: its
CLAUDE.md bans cross-repo *code* imports, so this module never calls
`tradfi/free_fetch.load` or `tradfi/free_integrity.series_sha256` directly.
Instead it follows the same precedent as `pipeline/data_import.py` -- read the
parquet directly with pandas -- and reimplements `series_sha256`'s ten-line
canonicalisation verbatim (see `series_sha256_canon` below) so the sha
comparison is an independent re-derivation, not a trust-the-caller shortcut.
Task 2 Step 4 pins this reimplementation against the live manifest: both the
real EUR and JPY series must reproduce the manifest's recorded sha256 exactly,
or the canon is wrong and must not be adjusted until it does.

The producer's `free_fetch.load` guarantees are preserved on this side of the
copy, not re-derived from scratch: a `fail` integrity verdict refuses the
series, an unreadable verdict file refuses (fail-closed), and a MISSING
verdict file passes -- exactly `free_fetch.load`'s behaviour. An id that is
not `selected` in the manifest (unpinned) also refuses. Every refusal is
collected before anything is raised, and nothing is written to the layer's
`data/` directory when any requested id refuses (verify-all-then-write-all,
never partial).

The verdict file lookup mirrors `tradfi/free_fetch.py:354-355`'s
`verdict_path` exactly -- `verdict_{ins_id}.json`, keyed by instrument id
alone, independent of lane (plan correction: an earlier draft of this module
guessed `free_{lane}_{id}_1d.verdict.json`, which does not exist in the real
tree and would have silently treated every real verdict as "missing"). A
MISSING file still passes, but the pass is no longer invisible: the snapshot
manifest's per-series entry records `"verdict"` as whatever the file's
`verdict` field said (e.g. `"ok"`, `"warn"`), or `"missing"` when no file was
found. `"fail"` never appears there because that id is refused outright and
gets no manifest entry at all.

FX OHLC honesty (spec s3): FRED H.10 series are one daily spot fix, so the
source parquet carries NaN open/high/low/volume -- only close is real. For any
class whose `bar_kind` is `"single_fix"` (fx today), the CSV this module
writes fills `open=high=low=close=fix` and `volume=0`; true range therefore
degenerates to |close - prev close|. That is declared here, not hidden -- see
`CLASSES["fx"]["bar_kind"]` in `pipeline/cells.py` and the composer's block
exclusions (spec s4) that keep range-dependent blocks off fx cells.

Run as an explicit, deliberate command -- never on a schedule, never
implicit -- because a generation pins to the snapshot it was bred on:

    python -m pipeline.tradfi_data snapshot --classes fx
    python -m pipeline.tradfi_data snapshot --classes fx --assets EUR,GBP
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import cells

COLUMNS = ("open", "high", "low", "close", "volume")

DEFAULT_TS_ROOT = Path(r"E:\Users\Coen\Claude\trading-systems")


class SnapshotRefused(RuntimeError):
    """One or more requested series failed verification; nothing was written."""


def _closes(df: pd.DataFrame) -> pd.Series:
    """Numeric close series, date-sorted. Mirrors `free_integrity._closes`."""
    close = pd.to_numeric(df["close"], errors="coerce")
    close.index = pd.DatetimeIndex(close.index)
    return close.sort_index()


def series_sha256_canon(df: pd.DataFrame) -> str:
    """Reimplementation of `tradfi/free_integrity.py:series_sha256`.

    sha256 over `"YYYY-MM-DD,{close:.6f}"` lines, NaN closes dropped,
    date-sorted, newline-joined, no trailing newline. Kept importable (not
    underscore-private) so Task 2 Step 4's parity script and this module's
    own tests can both call it directly.
    """
    import hashlib

    close = _closes(df).dropna()
    canon = "\n".join(f"{pd.Timestamp(d).date()},{c:.6f}" for d, c in close.items())
    return hashlib.sha256(canon.encode()).hexdigest()


def _cache_path(ts_root: Path, lane: str, asset_id: str) -> Path:
    return ts_root / "data" / "tradfi" / f"free_{lane}_{asset_id}_1d.parquet"


def _verdict_path(ts_root: Path, asset_id: str) -> Path:
    """`tradfi/free_fetch.py:354-355` `verdict_path` exactly: `verdict_{ins_id}.json`,
    keyed by instrument id alone -- NOT lane- or timeframe-qualified.
    """
    return ts_root / "data" / "tradfi" / f"verdict_{asset_id}.json"


def _check_verdict(ts_root: Path, asset_id: str) -> tuple[str | None, str]:
    """Return (refusal_reason_or_None, verdict_value_to_record).

    Mirrors `free_fetch.load`: a MISSING verdict file passes -- recorded here
    as `"missing"` so a silently absent verdict stays visible in the snapshot
    manifest instead of being indistinguishable from a genuine `"ok"`; an
    unreadable/malformed verdict file fails closed; verdict `"fail"` refuses
    (and therefore is never the recorded value -- a refused id gets no
    manifest entry at all).
    """
    vp = _verdict_path(ts_root, asset_id)
    if not vp.exists():
        return None, "missing"
    try:
        verdict = json.loads(vp.read_text(encoding="utf-8"))["verdict"]
    except Exception as e:
        return f"{asset_id}: verdict file unreadable ({e})", "unreadable"
    if verdict == "fail":
        return f"{asset_id}: last integrity verdict is fail", "fail"
    return None, verdict


def _write_series_csv(out_data_dir: Path, asset_id: str, df: pd.DataFrame, bar_kind: str) -> int:
    """Write `<asset_id>_1d.csv` in the `data_import.py` convention. Returns rows written."""
    close = _closes(df).dropna()
    out = out_data_dir / f"{asset_id}_1d.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(("date", *COLUMNS))
        for ts, c in close.items():
            c = float(c)
            if bar_kind == "single_fix":
                o = h = low = c
                v = 0
            else:
                row = df.loc[ts]
                o, h, low, v = float(row["open"]), float(row["high"]), float(row["low"]), float(row["volume"])
            w.writerow((pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), o, h, low, c, v))
    return len(close)


def snapshot(ts_root: Path, layer_root: Path, classes: tuple[str, ...],
             assets: tuple[str, ...] | None = None) -> list[str]:
    """Copy the requested classes' assets from a trading-systems tree into
    `layer_root/data/`, verified against `results/tradfi/free_universe_manifest.json`.

    Two-phase: every requested id is verified (pinned, verdict clear, sha
    match) before anything is written. Any refusal aborts the whole call with
    every refusal named; nothing partial is ever written.
    """
    ts_root = Path(ts_root)
    layer_root = Path(layer_root)

    manifest_path = ts_root / "results" / "tradfi" / "free_universe_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pinned = {row["id"]: row for row in manifest.get("selected", [])}

    requested: list[tuple[str, str]] = []
    for cls in classes:
        cls_assets = assets if assets is not None else cells.CLASSES[cls]["assets"]
        for asset_id in cls_assets:
            requested.append((cls, asset_id))

    refusals: list[str] = []
    verified: dict[str, tuple[pd.DataFrame, dict, str, str]] = {}
    for cls, asset_id in requested:
        row = pinned.get(asset_id)
        if row is None:
            refusals.append(f"{asset_id}: not pinned in {manifest_path}")
            continue
        lane = row["lane"]

        verdict_refusal, verdict_value = _check_verdict(ts_root, asset_id)
        if verdict_refusal is not None:
            refusals.append(verdict_refusal)
            continue

        parquet_path = _cache_path(ts_root, lane, asset_id)
        if not parquet_path.exists():
            refusals.append(f"{asset_id}: no cached parquet at {parquet_path}")
            continue
        df = pd.read_parquet(parquet_path)

        actual_sha = series_sha256_canon(df)
        if actual_sha != row["sha256"]:
            refusals.append(
                f"{asset_id}: sha256 mismatch (manifest {row['sha256']}, computed {actual_sha})")
            continue

        verified[asset_id] = (df, row, cls, verdict_value)

    if refusals:
        raise SnapshotRefused("; ".join(refusals))

    out_data_dir = layer_root / "data"
    out_data_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    series_manifest: dict[str, dict] = {}
    for asset_id, (df, row, cls, verdict_value) in verified.items():
        bar_kind = cells.CLASSES[cls]["bar_kind"]
        rows_written = _write_series_csv(out_data_dir, asset_id, df, bar_kind)
        written.append(asset_id)
        series_manifest[asset_id] = {**row, "bar_kind": bar_kind,
                                      "sha256_verified": True, "rows_written": rows_written,
                                      "verdict": verdict_value}

    snapshot_manifest = {
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "source_snapshot_utc": manifest.get("snapshot_utc"),
        "series": series_manifest,
    }
    (out_data_dir / "tradfi_snapshot_manifest.json").write_text(
        json.dumps(snapshot_manifest, indent=2), encoding="utf-8")

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.tradfi_data",
        description="Pinned snapshot adapter: copy verified tradfi series into research-layer/data/.")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="verify + copy one or more classes' series")
    snap.add_argument("--classes", required=True,
                       help="comma-separated class names declared in pipeline.cells.CLASSES, e.g. fx")
    snap.add_argument("--assets", default=None,
                       help="comma-separated asset ids; default = every declared asset in --classes")
    snap.add_argument("--ts-root", default=None,
                       help="trading-systems repo root (default: $TRADING_SYSTEMS_ROOT or "
                            fr"{DEFAULT_TS_ROOT}")
    snap.add_argument("--out", default=None,
                       help="research-layer root to write data/ into (default: parent of pipeline/)")

    args = parser.parse_args(argv)

    ts_root = Path(args.ts_root) if args.ts_root else Path(
        os.environ.get("TRADING_SYSTEMS_ROOT", DEFAULT_TS_ROOT))
    layer_root = Path(args.out) if args.out else Path(__file__).resolve().parent.parent
    classes = tuple(c.strip() for c in args.classes.split(",") if c.strip())
    requested_assets = tuple(a.strip() for a in args.assets.split(",") if a.strip()) if args.assets else None

    written = snapshot(ts_root, layer_root, classes=classes, assets=requested_assets)
    print(f"snapshot: wrote {len(written)} series to {layer_root / 'data'}: {', '.join(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
