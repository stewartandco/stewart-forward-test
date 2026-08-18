"""Fetch Binance spot daily klines into committed CSVs.

Usage:
    python -m pipeline.data_fetch [--data-dir data]

Exchange symbols (BTCUSDT/ETHUSDT) map to the specs' universe names
(BTCUSD/ETHUSD); USDT is the pricing proxy. Committing the CSVs makes every
screen verdict reproducible from the public repo alone.
"""
from __future__ import annotations

import csv
import sys
import time
import json
import hashlib
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

API = "https://api.binance.com/api/v3/klines"
SYMBOLS = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
FIELDS = ["date", "open", "high", "low", "close", "volume"]


def klines_to_rows(klines: list[list]) -> list[dict]:
    rows = []
    for k in klines:
        rows.append({
            "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
        })
    return rows


def fetch_symbol(exchange_symbol: str) -> list[dict]:
    """Every COMPLETE daily kline for a symbol.

    Binance returns the currently-open kline as the last element of the last
    batch. Its close_time (index 6) is in the future, so it is a partial bar
    whose OHLCV describes however much of today has elapsed - on a 00:20 UTC
    fetch, about 1% of a day's volume. Writing it into the committed CSVs put a
    fabricated bar into every consumer that reads full history, which is the
    gauntlet's out-of-sample window. Completeness is taken from the exchange's
    own close_time rather than from a local date comparison, so it stays
    correct if this fetcher ever gains a non-daily interval.

    Pagination advances from the RAW batch, not the filtered one: a final batch
    consisting only of the open kline must still advance `start`, or the loop
    would re-request it forever.
    """
    now_ms = int(time.time() * 1000)
    rows, start = [], 0
    while True:
        url = (f"{API}?symbol={exchange_symbol}&interval=1d"
               f"&limit=1000&startTime={start}")
        with urllib.request.urlopen(url, timeout=30) as resp:
            batch = json.loads(resp.read())
        if not batch:
            return rows
        rows.extend(klines_to_rows([k for k in batch if k[6] <= now_ms]))
        start = batch[-1][0] + 1
        if len(batch) < 1000:
            return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data")
    args = ap.parse_args()
    for universe_name, exchange_symbol in SYMBOLS.items():
        rows = fetch_symbol(exchange_symbol)
        out = args.data_dir / f"{universe_name}_1d.csv"
        write_csv(rows, out)
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        print(f"{out.name}: {len(rows)} rows, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
