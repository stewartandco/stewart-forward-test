"""Import bars from the trading-systems parquet cache into the CSV format
pipeline/engine.py already reads.

Copying data across trees is fine; importing code across trees is not
(trading-systems CLAUDE.md). This reads files and writes files.

Determinism matters: the screen records a sha256 of each data file in its run
manifest, so two imports of the same source must be byte-identical or the
manifest stops meaning anything.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .cells import cell_id, validate_cell

COLUMNS = ("open", "high", "low", "close", "volume")


def import_cell(src_dir: Path, out_dir: Path, asset: str, timeframe: str) -> int:
    """Convert one cell's parquet to CSV. Returns the row count."""
    import pandas as pd

    validate_cell(asset, timeframe)
    src = Path(src_dir) / f"{cell_id(asset, timeframe)}.parquet"
    if not src.exists():
        raise FileNotFoundError(f"no cached bars for {cell_id(asset, timeframe)}: {src}")

    df = pd.read_parquet(src)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{src.name} is missing columns {missing}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{cell_id(asset, timeframe)}.csv"

    # newline="" + LF keeps the file byte-stable across platforms, so the
    # screen's data hash is reproducible.
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(("date", *COLUMNS))
        for ts, row in df.sort_index().iterrows():
            w.writerow((ts.strftime("%Y-%m-%d %H:%M:%S"),
                        *(row[c] for c in COLUMNS)))
    return len(df)
