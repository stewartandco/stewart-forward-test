"""crypto_refresh: the daily top-up of active crypto cells (2b-3 Task 2).

Every test uses a fake `fetch` and `sleep` (no network, no real waiting), tmp
data/logs dirs, and a monkeypatched cells.ACTIVE_CELLS["crypto"] (restored by
monkeypatch, so the empty live gate is never left changed).
"""
from __future__ import annotations

import json
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import cells
from pipeline import crypto_data as cd
from pipeline import crypto_refresh as cr
from pipeline import feed_health as fh

NOW = "2026-10-17T00:02:00Z"                       # 08:02 Perth
NOW_MS = int(datetime(2026, 10, 17, 0, 2, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60_000


def _gate(monkeypatch, assets, tfs=("1d",)):
    monkeypatch.setitem(cells.ACTIVE_CELLS, "crypto",
                        {"assets": tuple(assets), "timeframes": tuple(tfs)})


LAST_FRESH = "2026-10-16"          # the newest bar a 00:02Z 2026-10-17 run may hold


def _days(first: str, last: str, skip=()) -> list[str]:
    """Daily bar open times `first`..`last` (dates), minus any `skip` dates."""
    d, end, out = date.fromisoformat(first[:10]), date.fromisoformat(last[:10]), []
    while d <= end:
        if d.isoformat() not in skip:
            out.append(f"{d.isoformat()} 00:00:00")
        d += timedelta(days=1)
    return out


def _write_csv(data_dir: Path, asset: str, first="2020-01-01 00:00:00", tf="1d",
               last=LAST_FRESH, skip=()):
    """A real-format file (header `date,...`): one bar a day, first..last, no
    hole unless `skip` names one, so feed_health finds it healthy."""
    data_dir.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"{d},1.0,2.0,0.5,1.5,10.0\n"
                   for d in _days(first, last, skip))
    (data_dir / f"{asset}_{tf}.csv").write_text(
        "date,open,high,low,close,volume\n" + rows, encoding="utf-8")


class Fake:
    """A scripted fetch: per-symbol list of outcomes (exception or None).
    The last outcome repeats once the list is down to one entry."""

    def __init__(self, data_dir: Path, script=None, on_call=None, csv=None):
        self.data_dir = data_dir
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []                            # (symbol, tf, now_ms, end_ms)
        self.on_call = on_call
        self.csv = csv or {}                       # symbol -> _write_csv kwargs

    def __call__(self, symbol, tf, data_dir, now_ms=None, end_ms=None):
        self.calls.append((symbol, tf, now_ms, end_ms))
        if self.on_call:
            self.on_call(symbol)
        outcomes = self.script.get(symbol)
        if outcomes:
            out = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
            if out is not None:
                raise out
        _write_csv(Path(data_dir), symbol, tf=tf, **self.csv.get(symbol, {}))
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


def test_a_malformed_cell_is_a_refresh_failure_not_a_stop(tmp_path, monkeypatch):
    # fetch_symbol refuses to write a malformed CSV (MalformedData, a
    # ValueError): an ordinary cell failure, not a ban -- the run continues.
    _gate(monkeypatch, [A, B, C])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data",
                {B: [cd.MalformedData("XUSDT_1d.csv: row 7: close '0.0' is not positive")]})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert A in fake.symbols() and C in fake.symbols()
    items = [i for i in _ledger(tmp_path / "logs")["items"]
             if i["source"] == cr.FETCH_SOURCE]
    assert [i["key"] for i in items] == ["XUSDT"]
    assert "XUSDT" in items[0]["reason"] and "1d" in items[0]["reason"]
    assert "row 7" in items[0]["reason"]
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


def test_no_retry_starts_exactly_at_the_deadline(tmp_path):
    # "never start at or after the deadline": the boundary instant is past it.
    deadline = NOW_MS + 13 * MIN
    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom"), None]})
    out = _run(tmp_path, fake, [A, B], deadline_ms=deadline, clock=lambda: deadline)
    assert fake.symbols() == [A, B]                 # B was NOT retried
    assert [i["key"] for i in out["items"]] == ["XUSDT"]


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


def test_a_retryable_rate_limit_that_reaches_us_stops_the_run(tmp_path):
    # fetch_symbol already retried it; retry_ok=True is still an exhausted 429.
    fake = Fake(tmp_path / "data", {B: [cd.RateLimited(7, retry_ok=True), None]})
    sleeps = Sleeps()
    out = _run(tmp_path, fake, [A, B, C], sleep=sleeps)
    assert fake.symbols() == [A, B]                # B not retried, C never called
    assert 7 not in sleeps.calls
    items = {i["key"]: i for i in out["items"]}
    assert set(items) == {B, C}
    assert items[C]["reason"].startswith("skipped: Binance rate limit (429) on XUSDT")
    assert out["aborted"]
    assert set(out["fetched"]) == {f"{A}_1d"}


def test_a_ban_in_a_retry_pass_stops_and_keeps_the_earlier_failures(tmp_path):
    # Pass 1: A and B fail, C completes. Retry pass: A is banned. B (failed,
    # still pending) is not retried and keeps its own failure item; A's item
    # now reports the ban; C stays fetched.
    fake = Fake(tmp_path / "data", {
        A: [urllib.error.URLError("a-boom"), cd.BinanceBanned("418")],
        B: [urllib.error.URLError("b-boom")]})
    out = _run(tmp_path, fake, [A, B, C])
    assert fake.symbols() == [A, B, C, A]
    items = {i["key"]: i for i in out["items"]}
    assert set(items) == {A, B}
    assert "BinanceBanned" in items[A]["reason"]
    assert "b-boom" in items[B]["reason"]
    assert out["aborted"]
    assert set(out["fetched"]) == {f"{C}_1d"}
    assert set(_manifest(tmp_path / "data")) == {f"{C}_1d"}


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


# ---- the hard stop (--stop-local; final review I1) --------------------------

def test_a_stop_mid_pass_one_skips_the_rest_and_still_writes_ledger_and_manifest(
        tmp_path, monkeypatch):
    # A timeout morning: every attempt takes 4 minutes. The guard would kill a
    # run that is still fetching at 08:15 before any ledger write; with
    # --stop-local 08:12 no attempt starts at or after 08:12, the rest are
    # named as skipped, and the ledger and manifest are written, exit 0.
    D = "DDDUSDT"
    _gate(monkeypatch, [A, B, C, D])
    (tmp_path / "data").mkdir()
    now = {"ms": NOW_MS}                           # 08:02 Perth

    def tick(symbol):
        now["ms"] += 4 * MIN

    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom")]}, on_call=tick)
    real = cr.refresh
    monkeypatch.setattr(cr, "refresh",
                        lambda *a, **kw: real(*a, clock=lambda: now["ms"], **kw))
    assert _main(tmp_path, monkeypatch, fake,
                 extra=["--deadline-local", "08:11", "--stop-local", "08:12"]) == 0
    # A 08:02, B 08:06 (fails), C 08:10; D would start at 08:14: not started,
    # and B is not retried either
    assert fake.symbols() == [A, B, C]
    items = {i["key"]: i for i in _ledger(tmp_path / "logs")["items"]
             if i["source"] == cr.FETCH_SOURCE}
    assert set(items) == {B, D}
    assert "boom" in items[B]["reason"]
    assert items[D]["reason"] == "skipped: past the stop time 08:12; not fetched this run"
    assert set(_manifest(tmp_path / "data")) == {f"{A}_1d", f"{C}_1d"}


def test_no_attempt_starts_exactly_at_the_stop(tmp_path):
    # ">= stop": the boundary instant is already past it, pass 1 included.
    stop = NOW_MS + 10 * MIN
    fake = Fake(tmp_path / "data")
    out = _run(tmp_path, fake, [A, B], stop_ms=stop, clock=lambda: stop)
    assert fake.calls == []
    assert [i["key"] for i in out["items"]] == [A, B]
    assert all(i["reason"].startswith("skipped: past the stop time 08:12")
               for i in out["items"])
    assert out["fetched"] == {}


def test_an_attempt_starts_one_ms_before_the_stop(tmp_path):
    stop = NOW_MS + 10 * MIN
    fake = Fake(tmp_path / "data")
    out = _run(tmp_path, fake, [A, B], stop_ms=stop, clock=lambda: stop - 1)
    assert fake.symbols() == [A, B] and out["items"] == []


def test_a_stop_in_a_retry_pass_keeps_the_failure_items(tmp_path):
    # Pass 1 ends at the stop: the failed cell keeps its own failure (it was
    # tried), it is not relabelled skipped, and nothing is retried.
    now = {"ms": NOW_MS}
    stop = NOW_MS + 10 * MIN
    fake = Fake(tmp_path / "data", {A: [urllib.error.URLError("a-boom"), None]},
                on_call=lambda s: now.update(ms=now["ms"] + 5 * MIN))
    out = _run(tmp_path, fake, [A, B], stop_ms=stop, clock=lambda: now["ms"])
    assert fake.symbols() == [A, B]
    assert [(i["key"], "a-boom" in i["reason"]) for i in out["items"]] == [(A, True)]


def test_no_stop_leaves_pass_one_ungated(tmp_path):
    # stop_ms None (the default, every hand run): a clock far past any stop
    # still attempts every cell in pass 1.
    fake = Fake(tmp_path / "data")
    out = _run(tmp_path, fake, [A, B, C], clock=lambda: NOW_MS + 24 * 60 * MIN)
    assert fake.symbols() == [A, B, C] and out["items"] == []


def test_stop_local_is_today_in_utc_plus_8_and_defaults_to_none(tmp_path, monkeypatch):
    seen = []
    real = cr.refresh

    def spy(*a, **kw):
        seen.append(kw.get("stop_ms", "absent"))
        return real(*a, **kw)

    _gate(monkeypatch, [A])
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(cr, "refresh", spy)
    _main(tmp_path, monkeypatch, Fake(tmp_path / "data"))
    _main(tmp_path, monkeypatch, Fake(tmp_path / "data"), extra=["--stop-local", "08:12"])
    assert seen == [None, END_MS + 12 * MIN]       # 08:12 Perth = 00:12Z


# ---- feed health in the ledger (2b-3 C1, C2) --------------------------------

def _health_items(tmp_path):
    return [(i["source"], i["key"]) for i in _ledger(tmp_path / "logs")["items"]
            if i["source"] in (fh.HOLE_SOURCE, fh.STALE_SOURCE)]


def test_a_hole_in_an_active_cell_is_a_ledger_item(tmp_path, monkeypatch):
    _gate(monkeypatch, [A, B])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data", csv={B: {"skip": ("2026-10-10",)}})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert _health_items(tmp_path) == [(fh.HOLE_SOURCE, B)]
    item = next(i for i in _ledger(tmp_path / "logs")["items"]
                if i["source"] == fh.HOLE_SOURCE)
    assert "2026-10-10" in item["reason"]


def test_a_cell_fetched_ok_but_unchanged_is_stale(tmp_path, monkeypatch):
    # B "fetches" fine but its last bar is still the one from before yesterday's
    # close: at 00:02Z 2026-10-17 the newest closed bar is 2026-10-16.
    _gate(monkeypatch, [A, B])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data", csv={B: {"last": "2026-10-15"}})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert _health_items(tmp_path) == [(fh.STALE_SOURCE, B)]
    assert f"{B}_1d" in _manifest(tmp_path / "data")


def test_the_newest_closed_bar_is_not_stale(tmp_path, monkeypatch):
    _gate(monkeypatch, [A])
    (tmp_path / "data").mkdir()
    assert _main(tmp_path, monkeypatch, Fake(tmp_path / "data")) == 0
    assert _health_items(tmp_path) == []


def test_a_failed_cell_is_not_also_stale(tmp_path, monkeypatch):
    # B's fetch fails; its file is still yesterday's. The failure item says so,
    # a feed_stale item would only repeat it.
    _gate(monkeypatch, [A, B])
    data = tmp_path / "data"
    data.mkdir()
    _write_csv(data, B, last="2026-10-15")
    fake = Fake(data, {B: [urllib.error.URLError("boom")]})
    assert _main(tmp_path, monkeypatch, fake) == 0
    items = _ledger(tmp_path / "logs")["items"]
    assert [(i["source"], i["key"]) for i in items] == [(cr.FETCH_SOURCE, B)]


def test_holes_are_scanned_for_every_active_cell_even_after_a_ban(tmp_path, monkeypatch):
    # A is banned (418): A fails, B and C are skipped. None is stale-checked,
    # but the file scan runs for all three, so every hole is still reported.
    _gate(monkeypatch, [A, B, C])
    data = tmp_path / "data"
    data.mkdir()
    _write_csv(data, A, last="2026-10-15", skip=("2026-03-03",))
    _write_csv(data, B, skip=("2026-04-04",))
    _write_csv(data, C)
    fake = Fake(data, {A: [cd.BinanceBanned("418")]})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert fake.symbols() == [A]
    assert sorted(_health_items(tmp_path)) == [(fh.HOLE_SOURCE, A), (fh.HOLE_SOURCE, B)]
    fetch_keys = {i["key"] for i in _ledger(tmp_path / "logs")["items"]
                  if i["source"] == cr.FETCH_SOURCE}
    assert fetch_keys == {A, B, C}


def test_a_cell_with_no_file_is_one_cannot_scan_hole_item(tmp_path, monkeypatch):
    _gate(monkeypatch, [A, B])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom")]})
    assert _main(tmp_path, monkeypatch, fake) == 0
    holes = [i for i in _ledger(tmp_path / "logs")["items"] if i["source"] == fh.HOLE_SOURCE]
    assert [i["key"] for i in holes] == [B]
    assert holes[0]["reason"].startswith("cannot scan: ")


def test_detector_items_leave_the_ledger_when_fixed(tmp_path, monkeypatch):
    _gate(monkeypatch, [A, B])
    (tmp_path / "data").mkdir()
    bad = Fake(tmp_path / "data", csv={A: {"skip": ("2026-10-10",)},
                                       B: {"last": "2026-10-15"}})
    assert _main(tmp_path, monkeypatch, bad) == 0
    assert sorted(_health_items(tmp_path)) == [(fh.HOLE_SOURCE, A), (fh.STALE_SOURCE, B)]
    assert _main(tmp_path, monkeypatch, Fake(tmp_path / "data")) == 0
    assert _ledger(tmp_path / "logs")["items"] == []


def test_the_exit_code_is_unchanged_by_detector_items(tmp_path, monkeypatch, capsys):
    _gate(monkeypatch, [A, B, C])
    (tmp_path / "data").mkdir()
    fake = Fake(tmp_path / "data", {C: [urllib.error.URLError("boom")]},
                csv={A: {"skip": ("2026-10-10",)}, B: {"last": "2026-10-15"}})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert len(_health_items(tmp_path)) == 3                 # hole A, stale B, no file C
    assert "degraded item(s)" in capsys.readouterr().out


def test_an_acknowledged_hole_is_not_an_item(tmp_path, monkeypatch):
    _gate(monkeypatch, [A, B])
    data = tmp_path / "data"
    data.mkdir()
    (data / fh.ACK_FILE).write_text(json.dumps(
        {A: {"dates": ["2026-10-10"], "reason": "exchange maintenance"}}), encoding="utf-8")
    fake = Fake(data, csv={A: {"skip": ("2026-10-10",)}, B: {"skip": ("2026-10-11",)}})
    assert _main(tmp_path, monkeypatch, fake) == 0
    assert _health_items(tmp_path) == [(fh.HOLE_SOURCE, B)]


def test_an_unreadable_ack_file_is_one_item_and_honours_nothing(tmp_path, monkeypatch):
    _gate(monkeypatch, [A])
    data = tmp_path / "data"
    data.mkdir()
    (data / fh.ACK_FILE).write_text("{not json", encoding="utf-8")
    fake = Fake(data, csv={A: {"skip": ("2026-10-10",)}})
    assert _main(tmp_path, monkeypatch, fake) == 0
    items = [i for i in _ledger(tmp_path / "logs")["items"] if i["source"] == fh.HOLE_SOURCE]
    ack = [i for i in items if i["key"] == fh.ACK_FILE]
    assert len(ack) == 1 and ack[0]["reason"].startswith("ack file unreadable: ")
    assert [i["key"] for i in items if i["key"] == A]          # the hole still reports


def test_the_scan_gets_the_refresh_end_cap_and_only_fetched_cells_as_stale(
        tmp_path, monkeypatch):
    # A hand run at 02:00Z must not expect a bar the fetch was forbidden to write.
    _gate(monkeypatch, [A, B])
    (tmp_path / "data").mkdir()
    seen = {}
    real = fh.scan

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(cr.feed_health, "scan", spy)
    fake = Fake(tmp_path / "data", {B: [urllib.error.URLError("boom")]})
    assert _main(tmp_path, monkeypatch, fake, now="2026-10-17T02:00:00Z") == 0
    assert seen["end_ms"] == END_MS
    assert seen["stale_keys"] == {A}
    assert seen["now_ms"] == NOW_MS + 118 * MIN
