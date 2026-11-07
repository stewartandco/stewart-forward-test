"""intraday_prefetch (2b-3 plan 4 D-i1): the phase-2 history fetch with the
per-cell check. Every test injects a fake fetch that writes real-format CSVs
into tmp_path: no network, no real sleeping, never the live data directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import cells
from pipeline import crypto_data as cd
from tools import intraday_prefetch as ip

UTC = timezone.utc
PERTH = timezone(timedelta(hours=8))
MIN = 60_000
LAYER = Path(__file__).resolve().parent.parent


def _ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


NOW = _ms(2021, 7, 10, 10, 0)            # 18:00 Perth, a Saturday: outside the window
NOW_2023 = _ms(2023, 2, 10, 10, 0)


def _bar_times(first: str, end_ms: int, tf: str, skip_days=()) -> list[str]:
    t = datetime.strptime(first, cd.DATE_FMT).replace(tzinfo=UTC)
    step = timedelta(milliseconds=cd.TF_MS[tf])
    out = []
    while int(t.timestamp() * 1000) + cd.TF_MS[tf] <= end_ms:
        if t.date().isoformat() not in skip_days:
            out.append(t.strftime(cd.DATE_FMT))
        t += step
    return out


def _write(data_dir: Path, asset: str, tf: str, first: str, end_ms: int, skip_days=()) -> dict:
    """A real-format CSV (header `date,open,...`): every bar of `tf` from `first`
    up to the last one closing at or before `end_ms`; returns fetch_symbol's dict."""
    data_dir.mkdir(parents=True, exist_ok=True)
    dates = _bar_times(first, end_ms, tf, skip_days)
    path = data_dir / f"{asset}_{tf}.csv"
    path.write_text("date,open,high,low,close,volume\n"
                    + "".join(f"{d},1.0,2.0,0.5,1.5,10.0\n" for d in dates),
                    encoding="utf-8", newline="")
    return {"rows": len(dates), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "first_date": dates[0], "last_date": dates[-1]}


def _write_1d(data_dir: Path, asset: str, first="2021-06-18 00:00:00", now_ms=NOW):
    return _write(data_dir, asset, "1d", first, cd.last_complete_utc_day_end_ms(now_ms))


class Fake:
    """A scripted `fetch`: writes the cell's file from `starts[(asset, tf)]`
    (default `default_start`) to the end cap; `raises[(asset, tf)]` is raised
    instead; `on_call(asset, tf)` runs first."""

    def __init__(self, starts=None, default_start="2021-06-18 00:00:00", skip=None,
                 raises=None, on_call=None):
        self.starts = starts or {}
        self.default_start = default_start
        self.skip = skip or {}
        self.raises = raises or {}
        self.on_call = on_call
        self.calls = []

    def __call__(self, symbol, tf, data_dir, now_ms=None, end_ms=None):
        self.calls.append((symbol, tf, now_ms, end_ms))
        if self.on_call:
            self.on_call(symbol, tf)
        exc = self.raises.get((symbol, tf))
        if exc is not None:
            raise exc
        return _write(Path(data_dir), symbol, tf, self.starts.get((symbol, tf), self.default_start),
                      end_ms, self.skip.get((symbol, tf), ()))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(data_dir: Path) -> dict:
    return json.loads((data_dir / "crypto_snapshot_manifest.json").read_text(encoding="utf-8"))


def _run(tmp_path, fetch, timeframes=("1h",), assets=("BTCUSDT",), now_ms=NOW, sleeps=None,
         first_1d="2021-06-18 00:00:00", **kw):
    data = tmp_path / "data"
    for a in assets:
        _write_1d(data, a, first_1d, now_ms)
    sl = sleeps if sleeps is not None else []
    return ip.run(data, timeframes, now_ms=now_ms, assets=list(assets), fetch=fetch,
                  sleep=sl.append, **kw), data


# ---------------------------------------------------------------- the cell list

def test_cells_are_admitted_assets_by_phase_2_timeframes():
    assert ip.PHASE_2_TIMEFRAMES == ("1h", "4h", "12h")
    assert "1d" not in ip.PHASE_2_TIMEFRAMES
    admitted = cells.ACTIVE_CELLS["crypto"]["assets"]
    got = ip.cells_for(ip.PHASE_2_TIMEFRAMES)
    assert len(admitted) == 76 and len(got) == 228
    # manifest (declaration) order, coarsest timeframe first per asset
    assert got == [(a, tf) for a in admitted for tf in ("12h", "4h", "1h")]


def test_cells_for_subset_keeps_manifest_order_and_refuses_strangers():
    got = ip.cells_for(("1h", "12h"), assets=["ETHUSDT", "BTCUSDT"])
    assert got == [("BTCUSDT", "12h"), ("BTCUSDT", "1h"), ("ETHUSDT", "12h"), ("ETHUSDT", "1h")]
    with pytest.raises(ValueError):
        ip.cells_for(("1h",), assets=["NOTACOINUSDT"])
    with pytest.raises(ValueError):
        ip.cells_for(("1d",))              # 1d is the anchor, never a prefetch target


# ---------------------------------------------------------------- the clip

def test_an_intraday_file_never_starts_before_the_1d_file(tmp_path):
    fake = Fake(starts={("BTCUSDT", "1h"): "2021-06-14 00:00:00"})
    rep, data = _run(tmp_path, fake)
    path = data / "BTCUSDT_1h.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "date,open,high,low,close,volume"
    assert lines[1].startswith("2021-06-18 00:00:00,")
    assert all(ln.split(",")[0] >= "2021-06-18" for ln in lines[1:])
    assert rep["clipped"] == {"BTCUSDT@1h": 4 * 24}
    assert lines[-1].startswith("2021-07-09 23:00:00,")        # the tail is untouched
    row = next(r for r in rep["cells"] if r["cell"] == "BTCUSDT@1h")
    assert row["status"] == "fetched" and row["clipped"] == 96
    assert row["first_date"] == "2021-06-18 00:00:00"


def test_a_file_already_inside_the_1d_span_is_left_untouched(tmp_path):
    fake = Fake()                                   # 1h starts on the 1d start day
    rep, data = _run(tmp_path, fake)
    before = _manifest(data)["BTCUSDT_1h"]
    assert rep["clipped"] == {}
    assert ip.clip_to_daily_start(data, "BTCUSDT", "1h", sleep=lambda s: None) == 0
    assert _manifest(data)["BTCUSDT_1h"] == before          # not re-stamped


def test_the_manifest_matches_the_clipped_file(tmp_path):
    fake = Fake(starts={("BTCUSDT", "4h"): "2021-06-10 00:00:00"})
    rep, data = _run(tmp_path, fake, timeframes=("4h", "12h"))
    assert rep["clipped"] == {"BTCUSDT@4h": 8 * 6}
    path = data / "BTCUSDT_4h.csv"
    entry = _manifest(data)["BTCUSDT_4h"]
    lines = path.read_text(encoding="utf-8").splitlines()[1:]
    assert entry["sha256"] == _sha(path)
    assert entry["rows"] == len(lines)
    assert entry["first_date"] == lines[0].split(",")[0] == "2021-06-18 00:00:00"
    assert entry["last_date"] == lines[-1].split(",")[0]
    # the unclipped sibling keeps the entry refresh stamped
    assert _manifest(data)["BTCUSDT_12h"]["sha256"] == _sha(data / "BTCUSDT_12h.csv")


def test_clip_without_a_1d_anchor_is_a_failed_cell_and_the_file_stays(tmp_path):
    data = tmp_path / "data"                        # no BTCUSDT_1d.csv at all
    fake = Fake(starts={("BTCUSDT", "1h"): "2021-06-14 00:00:00"})
    rep = ip.run(data, ("1h",), now_ms=NOW, assets=["BTCUSDT"], fetch=fake, sleep=lambda s: None)
    assert [f["cell"] for f in rep["failed"]] == ["BTCUSDT@1h"]
    assert "1d" in rep["failed"][0]["reason"]
    assert (data / "BTCUSDT_1h.csv").read_text().splitlines()[1].startswith("2021-06-14 00:00:00")
    assert rep["clipped"] == {}


# ---------------------------------------------------------------- the checks

def test_a_cell_failing_admission_is_reported_not_dropped(tmp_path):
    fake = Fake(starts={("BTCUSDT", "1h"): "2023-02-01 00:00:00"})
    rep, data = _run(tmp_path, fake, now_ms=NOW_2023, first_1d="2020-01-01 00:00:00")
    assert [a["key"] for a in rep["admission"]] == ["BTCUSDT@1h"]
    assert rep["admission"][0]["source"] == "crypto_admission"
    assert (data / "BTCUSDT_1h.csv").read_text().splitlines()[1].startswith("2023-02-01 00:00:00")
    assert rep["failed"] == [] and rep["clipped"] == {}
    assert rep["fetched"].keys() == {"BTCUSDT@1h"}


def test_holes_are_reported(tmp_path):
    fake = Fake(skip={("BTCUSDT", "4h"): ("2021-06-25",)})
    rep, _ = _run(tmp_path, fake, timeframes=("4h", "12h"))
    holes = [i for i in rep["feed_health"] if i["source"] == "feed_hole"]
    assert [(i["key"], "2021-06-25" in i["reason"]) for i in holes] == [("BTCUSDT@4h", True)]
    assert rep["admission"] == []


def test_a_healthy_run_reports_clean_and_sizes_the_files(tmp_path):
    fake = Fake()
    rep, data = _run(tmp_path, fake, timeframes=("1h", "4h", "12h"), assets=("BTCUSDT", "ETHUSDT"))
    assert set(rep) == {"cells", "fetched", "failed", "clipped", "admission", "feed_health",
                        "aborted", "elapsed_s", "bytes_by_tf"}
    assert rep["failed"] == rep["aborted"] == rep["admission"] == rep["feed_health"] == []
    assert len(rep["cells"]) == 6 and len(rep["fetched"]) == 6
    assert [r["cell"] for r in rep["cells"]][:3] == ["BTCUSDT@12h", "BTCUSDT@4h", "BTCUSDT@1h"]
    assert all(r["attempts"] == 1 and r["status"] == "fetched" for r in rep["cells"])
    for tf in ("1h", "4h", "12h"):
        assert rep["bytes_by_tf"][tf] == sum((data / f"{a}_{tf}.csv").stat().st_size
                                             for a in ("BTCUSDT", "ETHUSDT"))
    assert isinstance(rep["elapsed_s"], float)
    json.dumps(rep)                                             # the report is JSON


# ---------------------------------------------------------------- failures

@pytest.mark.parametrize("exc", [cd.BinanceBanned("HTTP 418"), cd.RateLimited(30.0)])
def test_a_ban_stops_the_run_and_marks_the_rest(tmp_path, exc):
    fake = Fake(raises={("BTCUSDT", "4h"): exc})
    sleeps = []
    rep, _ = _run(tmp_path, fake, timeframes=("1h", "4h", "12h"), assets=("BTCUSDT", "ETHUSDT"),
                  sleeps=sleeps)
    assert [(c[0], c[1]) for c in fake.calls] == [("BTCUSDT", "12h"), ("BTCUSDT", "4h")]
    assert rep["fetched"].keys() == {"BTCUSDT@12h"}
    assert [f["cell"] for f in rep["failed"]] == ["BTCUSDT@4h"]
    assert [a["cell"] for a in rep["aborted"]] == ["BTCUSDT@1h", "ETHUSDT@12h", "ETHUSDT@4h", "ETHUSDT@1h"]
    assert all("ban" in a["reason"] or "rate limit" in a["reason"] for a in rep["aborted"])
    assert sorted(set(sleeps)) == [0.2]                         # one pacing sleep, no retry back-off
    statuses = {r["cell"]: r["status"] for r in rep["cells"]}
    assert statuses["BTCUSDT@4h"] == "failed" and statuses["ETHUSDT@1h"] == "aborted"


def test_a_failing_fetch_is_a_failed_cell_and_the_rest_go_on(tmp_path):
    fake = Fake(raises={("BTCUSDT", "4h"): PermissionError("locked")})
    rep, data = _run(tmp_path, fake, timeframes=("4h", "12h"), assets=("BTCUSDT", "ETHUSDT"))
    assert [f["cell"] for f in rep["failed"]] == ["BTCUSDT@4h"]
    assert "locked" in rep["failed"][0]["reason"]
    assert rep["fetched"].keys() == {"BTCUSDT@12h", "ETHUSDT@12h", "ETHUSDT@4h"}
    assert rep["aborted"] == []
    row = next(r for r in rep["cells"] if r["cell"] == "BTCUSDT@4h")
    assert row["status"] == "failed" and row["attempts"] == 3   # refresh's own two retry passes


def test_a_locked_file_is_a_failed_cell_and_stays_intact(tmp_path, monkeypatch):
    fake = Fake(starts={("BTCUSDT", "1h"): "2021-06-14 00:00:00"})
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT")
    real_replace = os.replace
    target = str(data / "BTCUSDT_1h.csv")
    tries = []

    def locked(src, dst):
        if str(dst) == target and str(src).endswith(".tmp"):
            tries.append(1)
            raise PermissionError("a reader holds the file")
        return real_replace(src, dst)

    # fetch (the fake) leaves the unclipped file; only the CLIP's replace is blocked
    sleeps = []
    monkeypatch.setattr(os, "replace", locked)
    rep = ip.run(data, ("1h",), now_ms=NOW, assets=["BTCUSDT"], fetch=fake, sleep=sleeps.append)
    path = data / "BTCUSDT_1h.csv"
    assert len(tries) == 9 and sleeps.count(0.25) == 8          # 1 + 8 retries, 0.25 s apart
    assert [f["cell"] for f in rep["failed"]] == ["BTCUSDT@1h"]
    assert "clip" in rep["failed"][0]["reason"]
    assert rep["clipped"] == {}
    # intact: still the full fetched file, no .tmp left, manifest still matches it
    assert path.read_text().splitlines()[1].startswith("2021-06-14 00:00:00")
    assert not list(data.glob("*.tmp"))
    assert _manifest(data)["BTCUSDT_1h"]["sha256"] == _sha(path)
    assert next(r for r in rep["cells"] if r["cell"] == "BTCUSDT@1h")["status"] == "clip_failed"


def test_a_transient_lock_on_the_clip_is_retried_then_clips(tmp_path, monkeypatch):
    fake = Fake(starts={("BTCUSDT", "1h"): "2021-06-14 00:00:00"})
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT")
    real_replace = os.replace
    left = [3]

    def flaky(src, dst):
        if str(src).endswith(".tmp") and left[0] > 0:
            left[0] -= 1
            raise PermissionError("busy")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    rep = ip.run(data, ("1h",), now_ms=NOW, assets=["BTCUSDT"], fetch=fake, sleep=lambda s: None)
    assert rep["failed"] == [] and rep["clipped"] == {"BTCUSDT@1h": 96}


# ---------------------------------------------------------------- the morning window

def test_stop_ms_is_the_earliest_of_2000_today_and_the_next_0758():
    """Final review M8: 07:58 as before, plus an evening stop at 20:00 Perth of
    the run day (plan Global Constraints: no live-tree write 20:00-07:00)."""
    def stop(y, mo, d, h, mi):
        now = int(datetime(y, mo, d, h, mi, tzinfo=PERTH).timestamp() * 1000)
        return datetime.fromtimestamp(ip.stop_ms_for(now) / 1000, tz=PERTH)

    assert stop(2026, 10, 29, 3, 0) == datetime(2026, 10, 29, 7, 58, tzinfo=PERTH)
    assert stop(2026, 10, 29, 8, 39) == datetime(2026, 10, 29, 7, 58, tzinfo=PERTH)
    assert stop(2026, 10, 29, 8, 40) == datetime(2026, 10, 29, 20, 0, tzinfo=PERTH)
    assert stop(2026, 10, 29, 16, 20) == datetime(2026, 10, 29, 20, 0, tzinfo=PERTH)
    assert stop(2026, 10, 29, 19, 59) == datetime(2026, 10, 29, 20, 0, tzinfo=PERTH)
    # a start at or after 20:00 has no window left: it stops at once (a clean
    # all-aborted report), never runs on into the night
    assert stop(2026, 10, 29, 20, 0) == datetime(2026, 10, 29, 20, 0, tzinfo=PERTH)
    assert stop(2026, 10, 29, 22, 30) == datetime(2026, 10, 29, 20, 0, tzinfo=PERTH)


def test_the_morning_window_is_0758_to_0840_perth():
    def inside(h, mi, s=0):
        return ip.in_morning_window(int(datetime(2026, 10, 29, h, mi, s, tzinfo=PERTH).timestamp() * 1000))

    assert not inside(7, 57, 59) and inside(7, 58) and inside(8, 2) and inside(8, 39, 59)
    assert not inside(8, 40) and not inside(16, 0)


def test_it_refuses_inside_the_morning_window(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    data.mkdir()
    out = tmp_path / "report.json"
    fake = Fake()
    monkeypatch.setattr(ip, "fetch_symbol", fake)
    monkeypatch.setattr(ip, "_wall_now_ms", lambda: _ms(2026, 10, 29, 0, 2))     # 08:02 Perth
    rc = ip.main(["--data-dir", str(data), "--out", str(out)])
    assert rc == 2 and not out.exists() and fake.calls == []
    assert list(data.iterdir()) == []
    assert "08:40" in capsys.readouterr().err


def test_now_skips_the_refusal_and_a_run_inside_the_window_stops_at_once(tmp_path, monkeypatch):
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT", now_ms=_ms(2026, 10, 29, 0, 2))
    out = tmp_path / "report.json"
    fake = Fake()
    monkeypatch.setattr(ip, "fetch_symbol", fake)
    rc = ip.main(["--data-dir", str(data), "--out", str(out), "--assets", "BTCUSDT",
                  "--now", "2026-10-29T00:02:00Z"])                        # 08:02 Perth
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and fake.calls == []
    assert [a["cell"] for a in rep["aborted"]] == ["BTCUSDT@12h", "BTCUSDT@4h", "BTCUSDT@1h"]
    assert all("07:58" in a["reason"] for a in rep["aborted"])


def test_a_run_reaching_0758_stops_and_reports_the_rest(tmp_path):
    t = {"ms": _ms(2021, 7, 10, 23, 50)}                  # 07:50 Perth, 2021-07-11

    def advance(asset, tf):
        t["ms"] += 4 * MIN

    fake = Fake(on_call=advance)
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT", now_ms=t["ms"])
    _write_1d(data, "ETHUSDT", now_ms=t["ms"])
    rep = ip.run(data, ("1h", "4h", "12h"), now_ms=t["ms"], assets=["BTCUSDT", "ETHUSDT"],
                 fetch=fake, sleep=lambda s: None, clock=lambda: t["ms"])
    # attempts start at 07:50 and 07:54; the third would start at 07:58 and does not
    assert [(c[0], c[1]) for c in fake.calls] == [("BTCUSDT", "12h"), ("BTCUSDT", "4h")]
    assert rep["fetched"].keys() == {"BTCUSDT@12h", "BTCUSDT@4h"}
    assert [a["cell"] for a in rep["aborted"]] == ["BTCUSDT@1h", "ETHUSDT@12h", "ETHUSDT@4h", "ETHUSDT@1h"]
    assert all("07:58" in a["reason"] for a in rep["aborted"])
    assert rep["failed"] == []


def test_a_run_reaching_2000_stops_and_reports_the_rest(tmp_path):
    t = {"ms": _ms(2021, 7, 10, 11, 50)}                  # 19:50 Perth, 2021-07-10

    def advance(asset, tf):
        t["ms"] += 6 * MIN

    fake = Fake(on_call=advance)
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT", now_ms=t["ms"])
    _write_1d(data, "ETHUSDT", now_ms=t["ms"])
    rep = ip.run(data, ("1h", "4h", "12h"), now_ms=t["ms"], assets=["BTCUSDT", "ETHUSDT"],
                 fetch=fake, sleep=lambda s: None, clock=lambda: t["ms"])
    # attempts start at 19:50 and 19:56; the third would start at 20:02 and does not
    assert [(c[0], c[1]) for c in fake.calls] == [("BTCUSDT", "12h"), ("BTCUSDT", "4h")]
    assert rep["fetched"].keys() == {"BTCUSDT@12h", "BTCUSDT@4h"}
    assert [a["cell"] for a in rep["aborted"]] == ["BTCUSDT@1h", "ETHUSDT@12h", "ETHUSDT@4h", "ETHUSDT@1h"]
    assert all("20:00" in a["reason"] for a in rep["aborted"])
    assert rep["failed"] == []


def test_no_clip_or_manifest_write_after_2000(tmp_path, monkeypatch):
    t = {"ms": _ms(2021, 7, 10, 11, 50)}                  # 19:50 Perth; stop 20:00
    fake = Fake(default_start="2021-06-10 00:00:00")       # both cells need a clip
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT", now_ms=t["ms"])
    real_clip, real_stamp = ip.clip_to_daily_start, ip.update_snapshot_manifest
    stamps = []

    def clip_then_time_passes(*a, **k):
        try:
            return real_clip(*a, **k)
        finally:
            t["ms"] += 10 * MIN                            # the first clip ends at 20:00

    def counting_stamp(d, results):
        stamps.append(sorted(results))
        return real_stamp(d, results)

    monkeypatch.setattr(ip, "clip_to_daily_start", clip_then_time_passes)
    monkeypatch.setattr(ip, "update_snapshot_manifest", counting_stamp)
    rep = ip.run(data, ("1h", "4h"), now_ms=t["ms"], assets=["BTCUSDT"], fetch=fake,
                 sleep=lambda s: None, clock=lambda: t["ms"])
    assert rep["clipped"] == {"BTCUSDT@4h": 8 * 6}
    assert stamps == [["BTCUSDT_4h"]]
    assert [f["cell"] for f in rep["failed"]] == ["BTCUSDT@1h"]
    assert "past the stop time 20:00" in rep["failed"][0]["reason"]
    assert not list(data.glob("*.tmp"))


# ---------------------------------------------------------------- what it never writes

def test_it_never_writes_the_refresh_ledger_or_the_chain(tmp_path, monkeypatch):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    logs.mkdir()
    out = tmp_path / "report.json"
    _write_1d(data, "BTCUSDT")
    fake = Fake(starts={("BTCUSDT", "1h"): "2021-06-14 00:00:00"})
    monkeypatch.setattr(ip, "fetch_symbol", fake)
    monkeypatch.setattr(ip, "_wall_now_ms", lambda: NOW)
    live_ledger = LAYER / "logs" / "degraded_crypto_refresh.json"
    before = live_ledger.exists() and live_ledger.stat().st_mtime_ns
    rc = ip.main(["--data-dir", str(data), "--out", str(out), "--assets", "BTCUSDT"])
    assert rc == 0 and out.exists()
    assert list(logs.iterdir()) == []
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == ["data/BTCUSDT_12h.csv", "data/BTCUSDT_1d.csv", "data/BTCUSDT_1h.csv",
                       "data/BTCUSDT_4h.csv", "data/crypto_snapshot_manifest.json", "report.json"]
    assert (live_ledger.exists() and live_ledger.stat().st_mtime_ns) == before


def test_a_missing_data_dir_exits_2_writing_nothing(tmp_path):
    out = tmp_path / "report.json"
    rc = ip.main(["--data-dir", str(tmp_path / "nope"), "--out", str(out),
                  "--now", "2021-07-10T10:00:00Z"])
    assert rc == 2 and not out.exists()


def test_the_cli_writes_the_report_and_prints_a_summary(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT")
    out = tmp_path / "sub" / "report.json"
    monkeypatch.setattr(ip, "fetch_symbol", Fake(starts={("BTCUSDT", "1h"): "2021-06-14 00:00:00"}))
    rc = ip.main(["--data-dir", str(data), "--out", str(out), "--assets", "BTCUSDT",
                  "--timeframes", "1h,4h", "--now", "2021-07-10T10:00:00Z"])
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0
    assert [r["cell"] for r in rep["cells"]] == ["BTCUSDT@4h", "BTCUSDT@1h"]
    assert rep["clipped"] == {"BTCUSDT@1h": 96}
    text = capsys.readouterr().out
    assert "2 cells" in text and "1 clipped" in text and str(out) in text


# ---------------------------------------------------------------- fix round 1

def test_no_clip_or_manifest_write_after_the_stop(tmp_path, monkeypatch):
    t = {"ms": _ms(2021, 7, 10, 23, 50)}                  # 07:50 Perth; stop 07:58
    fake = Fake(default_start="2021-06-10 00:00:00")       # both cells need a clip
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT", now_ms=t["ms"])
    real_clip, real_stamp = ip.clip_to_daily_start, ip.update_snapshot_manifest
    stamps = []

    def clip_then_time_passes(*a, **k):
        try:
            return real_clip(*a, **k)
        finally:
            t["ms"] += 10 * MIN                            # the first clip ends at 08:00

    def counting_stamp(d, results):
        stamps.append(sorted(results))
        return real_stamp(d, results)

    monkeypatch.setattr(ip, "clip_to_daily_start", clip_then_time_passes)
    monkeypatch.setattr(ip, "update_snapshot_manifest", counting_stamp)
    rep = ip.run(data, ("1h", "4h"), now_ms=t["ms"], assets=["BTCUSDT"], fetch=fake,
                 sleep=lambda s: None, clock=lambda: t["ms"])
    assert rep["clipped"] == {"BTCUSDT@4h": 8 * 6}
    assert stamps == [["BTCUSDT_4h"]]                      # no manifest write after the stop
    assert [f["cell"] for f in rep["failed"]] == ["BTCUSDT@1h"]
    assert "past the stop time 07:58" in rep["failed"][0]["reason"]
    row = next(r for r in rep["cells"] if r["cell"] == "BTCUSDT@1h")
    assert row["status"] == "clip_deferred" and row["clipped"] == 0
    path = data / "BTCUSDT_1h.csv"                          # untouched: unclipped, entry matches
    assert path.read_text().splitlines()[1].startswith("2021-06-10 00:00:00")
    assert not list(data.glob("*.tmp"))
    assert _manifest(data)["BTCUSDT_1h"]["sha256"] == _sha(path)


def test_a_restamp_failure_is_clipped_manifest_stale_with_the_true_row_count(tmp_path, monkeypatch):
    fake = Fake(starts={("BTCUSDT", "1h"): "2021-06-14 00:00:00"})
    data = tmp_path / "data"
    _write_1d(data, "BTCUSDT")

    def boom(d, results):
        raise OSError("manifest locked")

    monkeypatch.setattr(ip, "update_snapshot_manifest", boom)     # the clip's, not refresh's
    rep = ip.run(data, ("1h",), now_ms=NOW, assets=["BTCUSDT"], fetch=fake, sleep=lambda s: None)
    path = data / "BTCUSDT_1h.csv"
    lines = path.read_text().splitlines()[1:]
    assert lines[0].startswith("2021-06-18 00:00:00")             # the file IS clipped
    assert rep["clipped"] == {"BTCUSDT@1h": 96}
    row = next(r for r in rep["cells"] if r["cell"] == "BTCUSDT@1h")
    assert row["status"] == "clipped_manifest_stale"
    assert row["rows"] == len(lines) and row["first_date"] == "2021-06-18 00:00:00"
    assert rep["fetched"]["BTCUSDT@1h"]["rows"] == len(lines)
    assert [f["cell"] for f in rep["failed"]] == ["BTCUSDT@1h"]
    assert "clipped, manifest stale" in rep["failed"][0]["reason"]
    assert "manifest locked" in rep["failed"][0]["reason"]
    assert _manifest(data)["BTCUSDT_1h"]["sha256"] != _sha(path)  # stale, and reported as such


@pytest.mark.parametrize("flag,value", [("--timeframes", ""), ("--timeframes", " , ,"),
                                        ("--assets", ""), ("--assets", ",")])
def test_an_empty_timeframes_or_assets_argument_exits_2(tmp_path, monkeypatch, flag, value):
    data = tmp_path / "data"
    data.mkdir()
    out = tmp_path / "report.json"
    fake = Fake()
    monkeypatch.setattr(ip, "fetch_symbol", fake)
    rc = ip.main(["--data-dir", str(data), "--out", str(out), flag, value,
                  "--now", "2021-07-10T10:00:00Z"])
    assert rc == 2 and not out.exists() and fake.calls == []
