"""crypto_refresh: the daily top-up of active crypto cells (2b-3 Task 2).

Every test uses a fake `fetch` and `sleep` (no network, no real waiting), tmp
data/logs dirs, and a monkeypatched cells.ACTIVE_CELLS["crypto"] (restored by
monkeypatch, so the empty live gate is never left changed).
"""
from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import cells
from pipeline import crypto_data as cd
from pipeline import crypto_refresh as cr

NOW = "2026-10-17T00:02:00Z"                       # 08:02 Perth
NOW_MS = int(datetime(2026, 10, 17, 0, 2, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60_000


def _gate(monkeypatch, assets, tfs=("1d",)):
    monkeypatch.setitem(cells.ACTIVE_CELLS, "crypto",
                        {"assets": tuple(assets), "timeframes": tuple(tfs)})


def _write_csv(data_dir: Path, asset: str, first="2020-01-01 00:00:00", tf="1d"):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"{asset}_{tf}.csv").write_text(
        "open_time,open,high,low,close,volume\n"
        f"{first},1.0,2.0,0.5,1.5,10.0\n", encoding="utf-8")


class Fake:
    """A scripted fetch: per-symbol list of outcomes (exception or None).
    The last outcome repeats once the list is down to one entry."""

    def __init__(self, data_dir: Path, script=None, on_call=None):
        self.data_dir = data_dir
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []                            # (symbol, tf, now_ms, end_ms)
        self.on_call = on_call

    def __call__(self, symbol, tf, data_dir, now_ms=None, end_ms=None):
        self.calls.append((symbol, tf, now_ms, end_ms))
        if self.on_call:
            self.on_call(symbol)
        outcomes = self.script.get(symbol)
        if outcomes:
            out = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
            if out is not None:
                raise out
        _write_csv(Path(data_dir), symbol, tf=tf)
        return {"rows": 1, "sha256": "ab" * 32, "first_date": "2020-01-01 00:00:00",
                "last_date": "2020-01-01 00:00:00"}

    def symbols(self):
        return [c[0] for c in self.calls]


class Sleeps:
    def __init__(self):
        self.calls = []

    def __call__(self, s):
        self.calls.append(s)


def _run(tmp_path, fake, assets, sleep=None, deadline_ms=None, **kw):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    cell_list = [(a, "1d") for a in assets]
    return cr.refresh(data, cell_list, now_ms=NOW_MS, deadline_ms=deadline_ms,
                      fetch=fake, sleep=sleep or Sleeps(), **kw)


def _manifest(data_dir: Path) -> dict:
    p = data_dir / "crypto_snapshot_manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _ledger(logs: Path) -> dict:
    return json.loads((logs / cr.LEDGER).read_text(encoding="utf-8"))


def _main(tmp_path, monkeypatch, fake, extra=(), now=NOW):
    monkeypatch.setattr(cr, "fetch_symbol", fake)
    monkeypatch.setattr(cr.time, "sleep", lambda s: None)
    argv = ["--data-dir", str(tmp_path / "data"), "--logs-dir", str(tmp_path / "logs"),
            "--now", now, *extra]
    return cr.main(argv)


A, B, C = "AAAUSDT", "XUSDT", "CCCUSDT"


def test_refreshes_every_active_cell_with_the_end_cap(tmp_path):
    fake = Fake(tmp_path / "data")
    out = _run(tmp_path, fake, [A, B, C])
    assert fake.symbols() == [A, B, C]
    assert {c[3] for c in fake.calls} == {END_MS}
    assert {c[2] for c in fake.calls} == {NOW_MS}
    assert set(_manifest(tmp_path / "data")) == {f"{s}_1d" for s in (A, B, C)}
    assert set(out["fetched"]) == {f"{s}_1d" for s in (A, B, C)}
    assert out["items"] == [] and out["aborted"] is None


def test_paces_between_cells(tmp_path):
    sleeps = Sleeps()
    _run(tmp_path, Fake(tmp_path / "data"), [A, B, C], sleep=sleeps)
    assert sleeps.calls == [cr.FETCH_PACING_S] * 2


def test_refresh_end_matches_the_last_complete_utc_day(tmp_path, monkeypatch):
    # 02:00Z = 10:00 Perth, a late/hand run: no bar dated 2026-10-17 UTC may
    # be kept, so the cap is still 00:00 UTC of that date.
    _gate(monkeypatch, [A])
    fake = Fake(tmp_path / "data")
    (tmp_path / "data").mkdir()
    assert _main(tmp_path, monkeypatch, fake, now="2026-10-17T02:00:00Z") == 0
    assert [c[3] for c in fake.calls] == [END_MS]


def test_one_failed_cell_does_not_stop_the_others(tmp_path, monkeypatch):
    _gate(monkeypatch, [A, B, C])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom")]})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert A in fake.symbols() and C in fake.symbols()
    items = [i for i in _ledger(tmp_path / "logs")["items"]
             if i["source"] == cr.FETCH_SOURCE]
    assert [i["key"] for i in items] == ["XUSDT"]
    assert "XUSDT" in items[0]["reason"] and "1d" in items[0]["reason"]
    assert set(_manifest(tmp_path / "data")) == {f"{A}_1d", f"{C}_1d"}


def test_a_failed_cell_is_retried_and_recovers(tmp_path):
    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom"), None]})
    out = _run(tmp_path, fake, [A, B, C])
    assert fake.symbols().count(B) == 2
    assert out["items"] == []
    assert f"{B}_1d" in out["fetched"]
    assert f"{B}_1d" in _manifest(tmp_path / "data")


def test_a_failed_cell_gets_at_most_two_retry_passes(tmp_path):
    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom")]})
    out = _run(tmp_path, fake, [A, B])
    assert fake.symbols().count(B) == 3
    assert [i["key"] for i in out["items"]] == ["XUSDT"]


def test_no_attempt_starts_after_the_deadline(tmp_path):
    now = {"ms": NOW_MS}
    deadline = NOW_MS + 13 * MIN

    def tick(symbol):                              # each attempt takes 10 minutes
        now["ms"] += 10 * MIN

    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom")]}, on_call=tick)
    out = _run(tmp_path, fake, [A, B], deadline_ms=deadline, clock=lambda: now["ms"])
    # pass 1 = A, B (clock now +20m, past the deadline): no retry may start
    assert fake.symbols() == [A, B]
    assert [i["key"] for i in out["items"]] == ["XUSDT"]
    assert out["items"][0]["source"] == cr.FETCH_SOURCE


def test_a_retry_starts_while_before_the_deadline(tmp_path):
    now = {"ms": NOW_MS}
    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom"), None]},
                on_call=lambda s: now.update(ms=now["ms"] + MIN))
    out = _run(tmp_path, fake, [A, B], deadline_ms=NOW_MS + 13 * MIN,
               clock=lambda: now["ms"])
    assert fake.symbols().count(B) == 2 and out["items"] == []


def test_a_ban_stops_the_run_and_marks_the_rest(tmp_path, monkeypatch):
    _gate(monkeypatch, [A, B, C])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data", {B: [cd.BinanceBanned("418")]})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert fake.symbols() == [A, B]                # C never called, B not retried
    items = {i["key"]: i for i in _ledger(tmp_path / "logs")["items"]
             if i["source"] == cr.FETCH_SOURCE}
    assert set(items) == {B, C}
    assert items[C]["reason"].startswith("skipped: Binance IP ban (418) on XUSDT")
    assert "failed" in items[B]["reason"]
    assert set(_manifest(tmp_path / "data")) == {f"{A}_1d"}


def test_a_hard_rate_limit_stops_like_a_ban(tmp_path):
    fake = Fake(tmp_path / "data", {B: [cd.RateLimited(500, retry_ok=False)]})
    sleeps = Sleeps()
    out = _run(tmp_path, fake, [A, B, C], sleep=sleeps)
    assert fake.symbols() == [A, B]
    items = {i["key"]: i for i in out["items"]}
    assert set(items) == {B, C}
    assert items[C]["reason"].startswith("skipped: Binance rate limit (429) on XUSDT")
    assert 500 not in sleeps.calls
    assert out["aborted"]


def test_a_retryable_rate_limit_sleeps_and_retries_next_pass(tmp_path):
    fake = Fake(tmp_path / "data", {B: [cd.RateLimited(7, retry_ok=True), None]})
    sleeps = Sleeps()
    out = _run(tmp_path, fake, [A, B, C], sleep=sleeps)
    assert 7 in sleeps.calls
    assert fake.symbols().count(B) == 2 and C in fake.symbols()
    assert out["items"] == [] and out["aborted"] is None


def test_the_manifest_is_still_written_when_a_fetch_crashes(tmp_path):
    fake = Fake(tmp_path / "data", {B: [KeyboardInterrupt()]})
    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, fake, [A, B])
    assert set(_manifest(tmp_path / "data")) == {f"{A}_1d"}


def test_admission_flags_a_cell_starting_after_the_floor(tmp_path):
    data = tmp_path / "data"
    _write_csv(data, "LATEUSDT", first="2023-01-01 00:00:00")
    _write_csv(data, "OKUSDT", first="2022-12-31 00:00:00")
    items = cr.admission_problems(data, [("LATEUSDT", "1d"), ("OKUSDT", "1d")])
    assert [(i["source"], i["key"]) for i in items] == [(cr.ADMISSION_SOURCE, "LATEUSDT")]
    assert "2023-01-01" in items[0]["reason"] and "2022-12-31" in items[0]["reason"]


def test_admission_flags_a_missing_or_empty_csv(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "EMPTYUSDT_1d.csv").write_text("open_time,open,high,low,close,volume\n",
                                           encoding="utf-8")
    items = cr.admission_problems(data, [("GONEUSDT", "1d"), ("EMPTYUSDT", "1d")])
    assert {i["key"] for i in items} == {"GONEUSDT", "EMPTYUSDT"}


def test_admission_floor_is_365_days_before_the_cutoff():
    assert cr.admission_floor() == "2022-12-31"
    assert cr.MIN_HISTORY_DAYS == 365


def test_admission_items_reach_the_ledger(tmp_path, monkeypatch):
    _gate(monkeypatch, [A])
    (tmp_path / "data").mkdir()
    _write_csv(tmp_path / "data", A, first="2023-06-01 00:00:00")

    def late(symbol, tf, data_dir, now_ms=None, end_ms=None):
        return {"rows": 1, "sha256": "cd" * 32, "first_date": "2023-06-01 00:00:00",
                "last_date": "2023-06-01 00:00:00"}

    assert _main(tmp_path, monkeypatch, late) == 0
    assert [(i["source"], i["key"]) for i in _ledger(tmp_path / "logs")["items"]] == [
        (cr.ADMISSION_SOURCE, A)]


def test_recovered_items_leave_the_ledger(tmp_path, monkeypatch):
    _gate(monkeypatch, [A, B])
    (tmp_path / "data").mkdir()
    bad = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom")]})
    assert _main(tmp_path, monkeypatch, bad) == 0
    fails = lambda: [i["key"] for i in _ledger(tmp_path / "logs")["items"]
                     if i["source"] == cr.FETCH_SOURCE]
    assert fails() == [B]
    good = Fake(tmp_path / "data")
    assert _main(tmp_path, monkeypatch, good) == 0
    assert _ledger(tmp_path / "logs")["items"] == []     # B now has a CSV: no admission item either
    assert _ledger(tmp_path / "logs")["writer"] == cr.WRITER


def test_no_active_cells_exits_0_and_writes_a_fresh_ledger(tmp_path, monkeypatch, capsys):
    _gate(monkeypatch, [], [])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data")
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert "crypto_refresh: no active crypto cells" in capsys.readouterr().out
    body = _ledger(tmp_path / "logs")
    assert body["items"] == [] and body["ts_utc"]
    assert fake.calls == []


def test_missing_data_dir_refuses_exit_2_writes_nothing(tmp_path, monkeypatch):
    _gate(monkeypatch, [A])
    fake = Fake(tmp_path / "data")
    assert _main(tmp_path, monkeypatch, fake) == 2
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs").exists()
    assert fake.calls == []


def test_deadline_local_is_today_in_utc_plus_8(tmp_path, monkeypatch):
    # 08:15 Perth on 2026-10-17 = 00:15Z.
    seen = {}
    real = cr.refresh

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    _gate(monkeypatch, [A])
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(cr, "refresh", spy)
    _main(tmp_path, monkeypatch, Fake(tmp_path / "data"), extra=["--deadline-local", "08:15"])
    assert seen["deadline_ms"] == END_MS + 15 * MIN
