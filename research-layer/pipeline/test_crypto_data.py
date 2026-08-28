"""Offline tests for the crypto grid fetcher (no network, tmp data dirs only).

Run: python -m pytest pipeline/test_crypto_data.py -q

`_http_get_json` is the single network boundary; every test either
monkeypatches it directly (pagination/resume/ban paths) or monkeypatches
`urllib.request.urlopen` beneath it (the 429/418 translation tests).
"""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import cells
from pipeline import crypto_data as cd

DAY = cd.TF_MS["1d"]
FAR_FUTURE_MS = 4_000_000_000_000   # year ~2096: every fixture kline is closed


def _kline(open_ms, o=1.0, h=2.0, low=0.5, c=1.5, v=10.0, close_ms=None):
    """Binance /api/v3/klines row shape: [open_ms, o, h, l, c, v, close_ms, ...]."""
    if close_ms is None:
        close_ms = open_ms + DAY - 1
    return [open_ms, str(o), str(h), str(low), str(c), str(v), close_ms,
            "0", 0, "0", "0", "0"]


def _page(start_ms, n):
    return [_kline(start_ms + i * DAY) for i in range(n)]


class FakePager:
    """Serves canned batches in order; records every requested URL."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.batches.pop(0)


def _start_time(url: str) -> int:
    return int(url.split("startTime=")[1].split("&")[0])


def _no_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr(cd.time, "sleep", calls.append)
    return calls


# ---------------------------------------------------------------- fetch_symbol

def test_pagination_two_full_pages_then_short(tmp_path, monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    pager = FakePager([_page(0, 1000), _page(1000 * DAY, 1000), _page(2000 * DAY, 5)])
    monkeypatch.setattr(cd, "_http_get_json", pager)
    res = cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)
    lines = (tmp_path / "BTCUSDT_1d.csv").read_text().splitlines()
    assert len(lines) == 1 + 2005
    assert res["rows"] == 2005
    # pagination advances from the RAW batch: last open of page 1 + 1
    assert _start_time(pager.urls[1]) == 999 * DAY + 1
    assert _start_time(pager.urls[2]) == 1999 * DAY + 1
    # ban-safety pacing between pages
    assert sleeps.count(0.2) == 2


def test_resume_starts_one_bar_after_last_row_and_merges(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    pager = FakePager([_page(0, 3)])
    monkeypatch.setattr(cd, "_http_get_json", pager)
    cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)

    last_ms = 2 * DAY
    # second fetch: server hands back the last existing bar again plus 2 new
    pager2 = FakePager([[_kline(last_ms), _kline(3 * DAY), _kline(4 * DAY)]])
    monkeypatch.setattr(cd, "_http_get_json", pager2)
    res = cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)
    assert _start_time(pager2.urls[0]) == last_ms + cd.TF_MS["1d"]
    lines = (tmp_path / "BTCUSDT_1d.csv").read_text().splitlines()
    assert len(lines) == 1 + 5           # 3 old + 2 new, duplicate deduped
    assert res["rows"] == 5
    dates = [ln.split(",")[0] for ln in lines[1:]]
    assert dates == sorted(dates) and len(set(dates)) == 5


def test_partial_bar_dropped(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    now_ms = 2 * DAY + 500               # third kline's close_time (3*DAY-1) >= now
    batch = [_kline(0), _kline(DAY), _kline(2 * DAY)]
    monkeypatch.setattr(cd, "_http_get_json", FakePager([batch]))
    res = cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=now_ms)
    assert res["rows"] == 2
    lines = (tmp_path / "BTCUSDT_1d.csv").read_text().splitlines()
    assert len(lines) == 1 + 2


def test_rate_limited_sleeps_retry_after_then_succeeds(tmp_path, monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise cd.RateLimited(retry_after=0.01)
        return _page(0, 2)

    monkeypatch.setattr(cd, "_http_get_json", flaky)
    res = cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)
    assert res["rows"] == 2
    assert calls["n"] == 2
    assert 0.01 in sleeps


def test_banned_never_retried(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def banned(url):
        calls["n"] += 1
        raise cd.BinanceBanned("418 from Binance: IP-wide ban - do NOT re-run")

    monkeypatch.setattr(cd, "_http_get_json", banned)
    with pytest.raises(cd.BinanceBanned):
        cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)
    assert calls["n"] == 1
    assert not (tmp_path / "BTCUSDT_1d.csv").exists()


def test_atomic_write_failure_leaves_original_intact(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(cd, "_http_get_json", FakePager([_page(0, 2)]))
    cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)
    original = (tmp_path / "BTCUSDT_1d.csv").read_bytes()

    monkeypatch.setattr(cd, "_http_get_json", FakePager([[_kline(2 * DAY)]]))
    monkeypatch.setattr(cd.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        cd.fetch_symbol("BTCUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)
    assert (tmp_path / "BTCUSDT_1d.csv").read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_tf_guard_refuses_undeclared_timeframe(tmp_path, monkeypatch):
    def never(url):     # guard must fire before any network attempt
        raise AssertionError("network boundary reached for a refused tf")

    monkeypatch.setattr(cd, "_http_get_json", never)
    assert "1w" not in cells.TIMEFRAMES
    with pytest.raises(ValueError):
        cd.fetch_symbol("BTCUSDT", "1w", tmp_path, now_ms=FAR_FUTURE_MS)


def test_csv_format_header_and_utc_dates(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    open_ms = 1_735_689_600_000          # 2025-01-01 00:00:00 UTC
    monkeypatch.setattr(cd, "_http_get_json",
                        FakePager([[_kline(open_ms, o=1.0, h=2.0, low=0.5, c=1.5, v=10.0)]]))
    res = cd.fetch_symbol("ETHUSDT", "1d", tmp_path, now_ms=FAR_FUTURE_MS)
    raw = (tmp_path / "ETHUSDT_1d.csv").read_bytes()
    assert b"\r" not in raw              # byte-deterministic LF terminator
    lines = raw.decode().splitlines()
    assert lines[0] == "date,open,high,low,close,volume"
    assert lines[1] == "2025-01-01 00:00:00,1.0,2.0,0.5,1.5,10.0"
    assert res["first_date"] == "2025-01-01 00:00:00"
    assert res["last_date"] == "2025-01-01 00:00:00"
    import hashlib
    assert res["sha256"] == hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------- http boundary

class _Headers:
    def __init__(self, d):
        self._d = d

    def get(self, key, default=None):
        return self._d.get(key, default)


def _http_error(code, headers=None):
    return urllib.error.HTTPError("http://x", code, "msg",
                                  _Headers(headers or {}), io.BytesIO(b""))


def test_http_429_raises_rate_limited_honoring_retry_after(monkeypatch):
    monkeypatch.setattr(cd.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(429, {"Retry-After": "7"})))
    with pytest.raises(cd.RateLimited) as ei:
        cd._http_get_json("http://x")
    assert ei.value.retry_after == 7.0


def test_http_429_retry_after_capped_and_defaulted(monkeypatch):
    monkeypatch.setattr(cd.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(429, {"Retry-After": "999"})))
    with pytest.raises(cd.RateLimited) as ei:
        cd._http_get_json("http://x")
    assert ei.value.retry_after == 120.0

    monkeypatch.setattr(cd.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(429)))
    with pytest.raises(cd.RateLimited) as ei:
        cd._http_get_json("http://x")
    assert ei.value.retry_after == 10.0


def test_http_418_raises_banned_with_do_not_rerun(monkeypatch):
    monkeypatch.setattr(cd.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(418)))
    with pytest.raises(cd.BinanceBanned) as ei:
        cd._http_get_json("http://x")
    assert "do NOT re-run" in str(ei.value)


def test_http_other_errors_propagate(monkeypatch):
    monkeypatch.setattr(cd.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(500)))
    with pytest.raises(urllib.error.HTTPError):
        cd._http_get_json("http://x")


def test_get_with_retry_transient_then_success(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("temporary")
        return [1]

    monkeypatch.setattr(cd, "_http_get_json", flaky)
    assert cd._get_with_retry("http://x", tries=3) == [1]
    assert calls["n"] == 3
    assert sleeps == [2, 4]              # 2*attempt backoff


def test_get_with_retry_exhausted_raises_last_error(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(cd, "_http_get_json",
                        lambda url: (_ for _ in ()).throw(urllib.error.URLError("down")))
    with pytest.raises(urllib.error.URLError):
        cd._get_with_retry("http://x", tries=2)


# ------------------------------------------------------------ snapshot manifest

def test_manifest_key_wise_merge_preserves_unrelated_keys(tmp_path):
    path = tmp_path / "crypto_snapshot_manifest.json"
    path.write_text(json.dumps({"OLDUSDT_4h": {"rows": 7, "sha256": "aa"}}), encoding="utf-8")
    cd.update_snapshot_manifest(tmp_path, {
        "BTCUSDT_1d": {"rows": 3, "sha256": "bb",
                       "first_date": "2025-01-01 00:00:00", "last_date": "2025-01-03 00:00:00"}})
    raw = path.read_text(encoding="utf-8")
    man = json.loads(raw)
    assert man["OLDUSDT_4h"] == {"rows": 7, "sha256": "aa"}
    assert man["BTCUSDT_1d"]["rows"] == 3
    assert "fetched_utc" in man["BTCUSDT_1d"]
    # indent=2, sort_keys=True, trailing newline -- byte-stable manifest
    assert raw == json.dumps(man, indent=2, sort_keys=True) + "\n"


# ------------------------------------------------------------------------ main

def _universe_manifest(path: Path, symbols):
    path.write_text(json.dumps(
        {"admitted": [{"rank": i + 1, "id": s.lower(), "symbol": s[:-4],
                       "binance_symbol": s} for i, s in enumerate(symbols)]}),
        encoding="utf-8")


def _serve_by_symbol(batches_by_symbol):
    def serve(url):
        sym = url.split("symbol=")[1].split("&")[0]
        out = batches_by_symbol[sym]
        if isinstance(out, Exception):
            raise out
        return out.pop(0) if out else []
    return serve


def test_main_default_assets_from_universe_manifest(tmp_path, monkeypatch, capsys):
    _no_sleep(monkeypatch)
    uni = tmp_path / "crypto_universe_manifest.json"
    _universe_manifest(uni, ["AAAUSDT", "BBBUSDT"])
    monkeypatch.setattr(cd, "_http_get_json", _serve_by_symbol(
        {"AAAUSDT": [_page(0, 2)], "BBBUSDT": [_page(0, 3)]}))
    rc = cd.main(["fetch", "--timeframes", "1d",
                  "--data-dir", str(tmp_path), "--manifest", str(uni)])
    assert rc == 0
    assert (tmp_path / "AAAUSDT_1d.csv").exists()
    assert (tmp_path / "BBBUSDT_1d.csv").exists()
    man = json.loads((tmp_path / "crypto_snapshot_manifest.json").read_text())
    assert set(man) == {"AAAUSDT_1d", "BBBUSDT_1d"}
    out = capsys.readouterr().out
    assert "AAAUSDT_1d" in out and "BBBUSDT_1d" in out


def test_main_missing_universe_manifest_names_task1_tool(tmp_path, capsys):
    rc = cd.main(["fetch", "--timeframes", "1d", "--data-dir", str(tmp_path),
                  "--manifest", str(tmp_path / "nope.json")])
    assert rc == 1
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "tools_select_crypto_universe.py" in out


def test_main_ban_aborts_run_but_keeps_completed_manifest_entries(tmp_path, monkeypatch, capsys):
    _no_sleep(monkeypatch)
    uni = tmp_path / "crypto_universe_manifest.json"
    _universe_manifest(uni, ["AAAUSDT", "BBBUSDT"])
    monkeypatch.setattr(cd, "_http_get_json", _serve_by_symbol(
        {"AAAUSDT": [_page(0, 2)],
         "BBBUSDT": cd.BinanceBanned("418 from Binance: IP-wide ban - do NOT re-run")}))
    rc = cd.main(["fetch", "--timeframes", "1d",
                  "--data-dir", str(tmp_path), "--manifest", str(uni)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "do NOT re-run" in out
    # AAAUSDT completed before the ban: its file is valid and its provenance recorded
    man = json.loads((tmp_path / "crypto_snapshot_manifest.json").read_text())
    assert "AAAUSDT_1d" in man
    assert "BBBUSDT_1d" not in man


def test_main_explicit_assets_and_new_row_count(tmp_path, monkeypatch, capsys):
    _no_sleep(monkeypatch)
    uni = tmp_path / "crypto_universe_manifest.json"
    _universe_manifest(uni, ["AAAUSDT", "BBBUSDT"])
    monkeypatch.setattr(cd, "_http_get_json", _serve_by_symbol({"AAAUSDT": [_page(0, 4)]}))
    rc = cd.main(["fetch", "--timeframes", "1d", "--assets", "AAAUSDT",
                  "--data-dir", str(tmp_path), "--manifest", str(uni)])
    assert rc == 0
    assert not (tmp_path / "BBBUSDT_1d.csv").exists()
    out = capsys.readouterr().out
    assert "4 rows (4 new)" in out
