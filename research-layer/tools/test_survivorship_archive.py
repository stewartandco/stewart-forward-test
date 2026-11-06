"""Tests for tools.survivorship_archive. No network: every path takes `fetch`."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from pipeline import screen
from tools import survivorship_archive as sa

DAY_MS = 86_400_000
T0_MS = 1735689600000  # 2025-01-01 00:00:00 UTC


def _rows(scale: int, header: bool = False) -> str:
    lines = []
    if header:
        lines.append("open_time,open,high,low,close,volume,close_time,"
                     "quote_volume,trades,taker_base,taker_quote,ignore")
    for i in range(3):
        t = (T0_MS + i * DAY_MS) * scale
        lines.append(f"{t},{100+i}.5,{110+i}.25,{90+i},{105+i}.125,{1000+i}.5,"
                     f"{t+1},1,1,1,1,0")
    return "\n".join(lines) + "\n"


def _zip_bytes(csv_text: str, name: str = "X-1d-2025-01.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, csv_text)
    return buf.getvalue()


def _zip_file(tmp_path: Path, csv_text: str, fname: str) -> Path:
    p = tmp_path / fname
    p.write_bytes(_zip_bytes(csv_text))
    return p


def test_parse_ms_and_us_timestamps_agree(tmp_path):
    a = sa.parse_zip(_zip_file(tmp_path, _rows(1), "ms.zip"))
    b = sa.parse_zip(_zip_file(tmp_path, _rows(1000), "us.zip"))
    assert a == b
    assert len(a) == 3
    assert a[0]["date"] == "2025-01-01 00:00:00"
    assert a[0]["open"] == 100.5 and a[0]["volume"] == 1000.5


def test_parse_skips_a_header_row(tmp_path):
    plain = sa.parse_zip(_zip_file(tmp_path, _rows(1), "p.zip"))
    hdr = sa.parse_zip(_zip_file(tmp_path, _rows(1, header=True), "h.zip"))
    assert plain == hdr


def test_checksum_mismatch_raises_and_deletes(tmp_path):
    zb = _zip_bytes(_rows(1))
    bad = ("0" * 64) + "  X-1d-2025-01.zip\n"

    def fetch(url: str) -> bytes:
        return bad.encode() if url.endswith(".CHECKSUM") else zb

    with pytest.raises(sa.ChecksumMismatch):
        sa.download_month("X", "1d", "2025-01", tmp_path, fetch=fetch)
    assert not list(tmp_path.rglob("*.zip"))


def test_download_month_ok_writes_zip_and_checksum(tmp_path):
    zb = _zip_bytes(_rows(1))
    good = f"{hashlib.sha256(zb).hexdigest()}  X-1d-2025-01.zip\n"
    seen = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        return good.encode() if url.endswith(".CHECKSUM") else zb

    p = sa.download_month("X", "1d", "2025-01", tmp_path, fetch=fetch)
    assert p == tmp_path / "X" / "1d" / "X-1d-2025-01.zip"
    assert p.read_bytes() == zb
    assert (p.parent / "X-1d-2025-01.zip.CHECKSUM").exists()
    assert seen[0] == ("https://data.binance.vision/data/spot/monthly/klines/"
                       "X/1d/X-1d-2025-01.zip")


def _page(keys, truncated):
    body = "".join(f"<Contents><Key>{k}</Key><Size>10</Size></Contents>"
                   for k in keys)
    return (f"<ListBucketResult><IsTruncated>{'true' if truncated else 'false'}"
            f"</IsTruncated>{body}</ListBucketResult>").encode()


def test_list_months_pages():
    p = "data/spot/monthly/klines/X/1d/"
    p1 = _page([p + "X-1d-2024-03.zip", p + "X-1d-2024-03.zip.CHECKSUM",
                p + "X-1d-2024-01.zip"], True)
    p2 = _page([p + "X-1d-2024-02.zip"], False)
    urls = []

    def fetch(url: str) -> bytes:
        urls.append(url)
        return p2 if "marker=" in url else p1

    assert sa.list_months("X", "1d", fetch=fetch) == [
        "2024-01", "2024-02", "2024-03"]
    assert len(urls) == 2 and "marker=" in urls[1]


def test_write_pipeline_csv_round_trips_through_load_bars(tmp_path):
    bars = sa.parse_zip(_zip_file(tmp_path, _rows(1), "r.zip"))
    path = tmp_path / "SYM_1d.csv"
    digest = sa.write_pipeline_csv(bars, path)
    raw = path.read_bytes()
    assert digest == hashlib.sha256(raw).hexdigest()
    assert b"\r" not in raw
    assert raw.splitlines()[0] == b"date,open,high,low,close,volume"
    got = screen.load_bars(tmp_path, "SYM", "9999-12-31")
    assert got == bars


def test_plan_and_summarise():
    p = "data/spot/monthly/klines/"
    def fetch(url):
        sym = "A" if "/A/" in url else "B"
        return _page([f"{p}{sym}/1d/{sym}-1d-2024-01.zip",
                      f"{p}{sym}/1d/{sym}-1d-2024-02.zip"], False)
    plan = sa.plan_downloads(["A", "B"], ["1d"], fetch)
    assert plan == [("A", "1d", "2024-01"), ("A", "1d", "2024-02"),
                    ("B", "1d", "2024-01"), ("B", "1d", "2024-02")]
    s = sa.summarise(plan)
    assert s["files"] == 4 and s["est_bytes"] == 40
