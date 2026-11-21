"""Binance public-archive listing, checksum-verified download and kline parse.

Measurement code for the crypto survivorship study, never a pipeline stage.
Every network path takes an injected `fetch(url) -> bytes` so tests make no
network calls. Output CSVs use the pipeline's own layout (crypto_data.FIELDS,
DATE_FMT, UTC) so pipeline.screen.load_bars reads them unchanged.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from pipeline.crypto_data import DATE_FMT, FIELDS

S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL = "https://data.binance.vision"
_PACE_S = 0.2
_TIMEOUT_S = 60
_MONTH_RE = re.compile(r"-(\d{4}-\d{2})\.zip$")


class ChecksumMismatch(Exception):
    """Downloaded zip bytes do not match the published sha256."""


def _http_get(url: str) -> bytes:
    """GET with a 60 s timeout and 0.2 s pacing. 4xx is raised, never retried."""
    time.sleep(_PACE_S)
    with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as r:
        return r.read()


def _prefix(symbol: str, tf: str) -> str:
    return f"data/spot/monthly/klines/{symbol}/{tf}/"


def _list_entries(symbol: str, tf: str, fetch) -> list[tuple[str, int]]:
    """All (month, zip_size) entries under the prefix, paging on IsTruncated."""
    prefix = _prefix(symbol, tf)
    out: dict[str, int] = {}
    marker = ""
    while True:
        url = f"{S3_LIST}?prefix={urllib.parse.quote(prefix)}"
        if marker:
            url += f"&marker={urllib.parse.quote(marker)}"
        root = ElementTree.fromstring(fetch(url))
        last = ""
        truncated = False
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "IsTruncated":
                truncated = (el.text or "").strip().lower() == "true"
            elif tag == "Contents":
                key = size = None
                for c in el:
                    ctag = c.tag.rsplit("}", 1)[-1]
                    if ctag == "Key":
                        key = c.text or ""
                    elif ctag == "Size":
                        size = int(c.text or 0)
                if key is None:
                    continue
                last = key
                m = _MONTH_RE.search(key)
                if m:                       # .zip only; .CHECKSUM never matches
                    out[m.group(1)] = size or 0
        if not truncated or not last or last == marker:
            break
        marker = last
    return sorted(out.items())


def list_months(symbol: str, tf: str, fetch=_http_get) -> list[str]:
    return [m for m, _ in _list_entries(symbol, tf, fetch)]


def download_month(symbol: str, tf: str, month: str, dest: Path,
                   fetch=_http_get) -> Path:
    name = f"{symbol}-{tf}-{month}.zip"
    url = f"{DL}/{_prefix(symbol, tf)}{name}"
    data = fetch(url)
    check = fetch(url + ".CHECKSUM").decode("utf-8", "replace").split()
    want = check[0].lower() if check else ""
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise ChecksumMismatch(f"{name}: sha256 {got} != published {want!r}")
    folder = Path(dest) / symbol / tf
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    (folder / (name + ".CHECKSUM")).write_bytes(
        f"{want}  {name}\n".encode("utf-8"))
    return path


def parse_zip(path: Path) -> list[dict]:
    """Kline bars from a monthly zip; ms and us open_time both accepted."""
    bars: list[dict] = []
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            if not member.lower().endswith(".csv"):
                continue
            text = io.TextIOWrapper(z.open(member), encoding="utf-8", newline="")
            for n, row in enumerate(csv.reader(text), 1):
                if not row or not row[0].strip().isdigit():
                    continue                # header row (or blank line)
                try:
                    t = int(row[0])
                    if t >= 10**15:
                        t //= 1000          # microseconds -> milliseconds
                    date = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
                    bars.append({"date": date.strftime(DATE_FMT),
                                 "open": float(row[1]), "high": float(row[2]),
                                 "low": float(row[3]), "close": float(row[4]),
                                 "volume": float(row[5])})
                except (ValueError, IndexError, OverflowError, OSError) as exc:
                    raise ValueError(f"{path} member {member} row {n}: "
                                     f"{type(exc).__name__}: {exc}") from exc
    return bars


def write_pipeline_csv(bars: list[dict], path: Path) -> str:
    """Write pipeline-format CSV (LF); returns the sha256 of the file bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        for b in bars:
            w.writerow([b["date"]] + [repr(float(b[k])) for k in FIELDS[1:]])
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Plan(list):
    """List of (symbol, tf, month) that also carries zip sizes from the listing."""
    sizes: dict


def plan_downloads(symbols, tfs, fetch=_http_get) -> Plan:
    plan = Plan()
    plan.sizes = {}
    for sym in symbols:
        for tf in tfs:
            for month, size in _list_entries(sym, tf, fetch):
                plan.append((sym, tf, month))
                plan.sizes[(sym, tf, month)] = size
    return plan


def summarise(plan) -> dict:
    sizes = getattr(plan, "sizes", {})
    return {"files": len(plan),
            "est_bytes": sum(sizes.get(t, 0) for t in plan)}
