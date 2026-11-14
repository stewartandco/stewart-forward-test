"""2b-2 design s3.5: fetch only the intraday cells quarantined strategies owe;
a failed cell is a degraded item that persists until a fetch succeeds."""
from __future__ import annotations

import json
from pathlib import Path

from . import degraded as dg
from .quarantine import run as quarantine_run, fetch_owed, FETCH_SOURCE
from .test_gen3b import argv_for
from .test_intraday_statistics import _twin_spec
from .test_quarantine_isolation import ledger
from .test_quarantine_record_intraday import _twins, _register_quarantined


def _specs(*specs):
    return {s["strategy_id"]: s for s in specs}


def test_no_intraday_cell_owed_means_no_fetch(tmp_path):
    calls = []
    sd = _twin_spec("1d")
    items = fetch_owed([sd["strategy_id"]], _specs(sd), tmp_path,
                       fetch=lambda *a, **k: calls.append(a))
    assert calls == [] and items == []


def test_each_owed_cell_is_fetched_once(tmp_path):
    a = _twin_spec("1h")
    b = json.loads(json.dumps(a))
    b["strategy_id"] = "b" * 16
    calls = []
    _csv(tmp_path, "BTCUSD_1h.csv", ["2026-10-05 09:00:00", "2026-10-05 10:00:00"])
    items = fetch_owed([a["strategy_id"], b["strategy_id"]], _specs(a, b), tmp_path,
                       fetch=lambda sym, tf, d, **kw: calls.append((sym, tf)) or {},
                       sleep=lambda s: None, now_ms=NOW_MS)
    assert calls == [("BTCUSD", "1h")] and items == []


def test_a_failed_fetch_is_an_item_and_the_other_cells_still_fetch(tmp_path):
    a = _twin_spec("1h")
    b = json.loads(json.dumps(a))
    b["strategy_id"] = "b" * 16
    b["universe"]["assets"] = ["ETHUSD"]
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        if sym == "BTCUSD":
            raise RuntimeError("HTTP 451")
        return {}
    items = fetch_owed([a["strategy_id"], b["strategy_id"]], _specs(a, b),
                       tmp_path, fetch=fetch, sleep=lambda s: None,
                       now_ms=NOW_MS)
    items = [i for i in items if i["source"] == FETCH_SOURCE]
    assert sorted(set(calls)) == ["BTCUSD", "ETHUSD"]
    assert [(i["source"], i["key"]) for i in items] == [(FETCH_SOURCE, "BTCUSD@1h")]
    assert "HTTP 451" in items[0]["reason"]


def test_the_cli_exits_zero_and_records_the_item(tmp_path, monkeypatch):
    reg, data, daily, sd, sh = _twins(tmp_path)
    from . import crypto_data

    def boom(*a, **k):
        raise RuntimeError("down")
    from . import quarantine
    monkeypatch.setattr(crypto_data, "fetch_symbol", boom)
    monkeypatch.setattr(quarantine.time, "sleep", lambda s: None)
    assert quarantine_run(argv_for(reg, data, "--fetch-owed")) == 0
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [
        (FETCH_SOURCE, "BTCUSD@1h")]


def test_catch_up_never_drops_a_fetch_item(tmp_path, monkeypatch):
    """The catch-up must REACH every owed date (few owed days), so its write
    is the authoritative remove_unseen=True one; only keep_sources saves the
    fetch item."""
    from .test_intraday_statistics import _twin_bars
    reg, data, daily, sd, sh = _twins(tmp_path, end=_twin_bars()[0][153]["date"])
    p = reg.log_path.parent / "logs" / dg.QUARANTINE_LEDGER
    dg.record(p, "quarantine", [{"source": FETCH_SOURCE, "key": "BTCUSD@1h",
                                 "reason": "down"}], remove_unseen=False)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert any(i["source"] == FETCH_SOURCE for i in ledger(reg)["items"])


def test_a_recovered_fetch_clears_its_item_only(tmp_path, monkeypatch):
    reg, data, daily, sd, sh = _twins(tmp_path)
    p = reg.log_path.parent / "logs" / dg.QUARANTINE_LEDGER
    dg.record(p, "quarantine", [
        {"source": FETCH_SOURCE, "key": "BTCUSD@1h", "reason": "down"},
        {"source": "restated", "key": "EFA", "reason": "x"}], remove_unseen=False)
    from . import crypto_data, feed_health
    monkeypatch.setattr(crypto_data, "fetch_symbol", lambda *a, **k: {})
    monkeypatch.setattr(feed_health, "scan", lambda *a, **k: [])
    assert quarantine_run(argv_for(reg, data, "--fetch-owed")) == 0
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [("restated", "EFA")]


def test_fetch_owed_is_exclusive_with_the_other_modes(tmp_path):
    reg, data, daily, sd, sh = _twins(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--fetch-owed", "--catch-up")) == 1


def test_the_wrapper_runs_fetch_owed_before_the_date():
    """The fetch step precedes the daily --date record (so the record sees the
    fresh bars) and its crash reaches the task's exit code through FETCH_RC."""
    bat = (Path(__file__).resolve().parent.parent / "tasks"
           / "run_quarantine.bat").read_text(encoding="utf-8").splitlines()
    cmds = [ln for ln in bat if not ln.lstrip().upper().startswith("REM")]
    fetch = [i for i, ln in enumerate(cmds) if "--fetch-owed" in ln]
    date = [i for i, ln in enumerate(cmds) if "--date" in ln]
    assert len(fetch) == 1 and len(date) == 1 and fetch[0] < date[0]
    assert 'if "%FETCH_RC%"=="1" goto :fail' in [ln.strip() for ln in cmds]


def test_a_binance_ban_stops_the_fetch_and_owes_every_remaining_cell(tmp_path):
    """HTTP 418 is an IP-wide ban shared by every Binance consumer on this
    box: one more call can extend it, so the loop must stop at once."""
    from .crypto_data import BinanceBanned
    a = _twin_spec("1h")
    b = json.loads(json.dumps(a))
    b["strategy_id"] = "b" * 16
    b["universe"]["assets"] = ["ETHUSD"]
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        raise BinanceBanned("418 from Binance: IP-wide ban")
    items = fetch_owed([a["strategy_id"], b["strategy_id"]], _specs(a, b),
                       tmp_path, fetch=fetch, sleep=lambda s: None,
                       now_ms=NOW_MS)
    items = [i for i in items if i["source"] == FETCH_SOURCE]
    assert calls == ["BTCUSD"]
    assert [(i["source"], i["key"]) for i in items] == [
        (FETCH_SOURCE, "BTCUSD@1h"), (FETCH_SOURCE, "ETHUSD@1h")]
    assert "ban" in items[1]["reason"].lower() and "418" in items[1]["reason"]


def test_nothing_owed_never_imports_crypto_data(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pipeline.crypto_data", None)
    sd = _twin_spec("1d")
    assert fetch_owed([sd["strategy_id"]], _specs(sd), tmp_path) == []


def test_a_crashed_fetch_step_still_lets_the_record_run_and_fails_the_task(
        tmp_path, monkeypatch):
    """Behavioural: a --fetch-owed that exits 1 must not skip --date or
    --catch-up or the commit, and the task's final exit is 1."""
    import os
    import pytest
    if os.name != "nt":
        pytest.skip("runs the .bat under cmd.exe")
    from . import test_chain_mirror_wrappers as w
    stub = (
        "import sys, pathlib\n"
        "L = pathlib.Path(__file__).resolve().parent.parent\n"
        "with open(L / 'logs' / 'calls.txt', 'a') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "with open(L / 'registry_log.jsonl', 'a') as f: f.write('{\"q\": 1}\\n')\n"
        "if '--fetch-owed' in sys.argv:\n"
        "    sys.exit(1)\n")
    repo, bat = w._scratch(tmp_path, "run_quarantine.bat")
    (repo / "research-layer/pipeline/quarantine.py").write_text(stub)
    code = w._run(bat)
    calls = (repo / "research-layer/logs/calls.txt").read_text().splitlines()
    assert calls[0].startswith("--fetch-owed")
    assert any(c.startswith("--date") for c in calls) and "--catch-up" in calls
    assert code == 1
    assert w._git(repo, "rev-list", "--count", "HEAD").strip() == "2"


def _two_cells():
    a = _twin_spec("1h")
    b = json.loads(json.dumps(a))
    b["strategy_id"] = "b" * 16
    b["universe"]["assets"] = ["ETHUSD"]
    return [a["strategy_id"], b["strategy_id"]], _specs(a, b)


def test_a_rate_limit_stops_the_fetch_and_owes_every_remaining_cell(
        tmp_path, monkeypatch):
    """crypto_data.main aborts its whole run on a RateLimited that reaches it
    (retries exhausted, or a Retry-After beyond the cap): another call keeps
    hammering the shared IP and invites a 418. The 08:20 task records and
    stops at once (no long sleep); the next day's run retries."""
    from . import quarantine
    from .crypto_data import RateLimited
    sleeps = []
    monkeypatch.setattr(quarantine.time, "sleep", sleeps.append)
    ids, specs = _two_cells()
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        raise RateLimited(120.0, retry_ok=False)
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, now_ms=NOW_MS)
    items = [i for i in items if i["source"] == FETCH_SOURCE]
    assert calls == ["BTCUSD"]
    assert [(i["source"], i["key"]) for i in items] == [
        (FETCH_SOURCE, "BTCUSD@1h"), (FETCH_SOURCE, "ETHUSD@1h")]
    assert all("rate limit" in i["reason"].lower() for i in items)
    assert sleeps == []


def test_cells_are_paced_like_crypto_data(tmp_path, monkeypatch):
    """The same 0.2 s pause crypto_data.main takes between cells: the IP
    budget is shared with every Binance consumer on this box. A failed cell
    is still followed by the pause before the next."""
    from . import quarantine
    events = []
    monkeypatch.setattr(quarantine.time, "sleep",
                        lambda s: events.append(("sleep", s)))
    ids, specs = _two_cells()

    def fetch(sym, tf, d, **kw):
        events.append(("fetch", sym))
        if sym == "BTCUSD":
            raise RuntimeError("HTTP 451")
        return {}
    fetch_owed(ids, specs, tmp_path, fetch=fetch, now_ms=NOW_MS)
    # pass 1 (paced, the failed cell is still followed by the pause), then the
    # two retry passes over the failed cell alone (120 s before each)
    assert events == [("fetch", "BTCUSD"), ("sleep", 0.2), ("fetch", "ETHUSD"),
                      ("sleep", 120), ("fetch", "BTCUSD"),
                      ("sleep", 120), ("fetch", "BTCUSD")]


# ---- 2b-3 C1, C2 + Coen 10-17: retry passes, hole and stale items ----------

NOW_MS = 1_791_201_600_000          # 2026-10-05 12:00:00 UTC


def _one_cell():
    a = _twin_spec("1h")
    return [a["strategy_id"]], _specs(a)


def _sleeps():
    got = []
    return got, got.append


def _csv(data_dir, name, dates):
    (Path(data_dir) / name).write_text(
        "date,open,high,low,close,volume\n"
        + "".join(f"{d},1,1,1,1,1\n" for d in dates), encoding="utf-8")


def test_a_failed_cell_is_retried_in_the_same_run_and_recovers(tmp_path):
    ids, specs = _one_cell()
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        if len(calls) == 1:
            raise RuntimeError("HTTP 451")
        return {}
    sleeps, sleep = _sleeps()
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=sleep,
                       now_ms=NOW_MS)
    assert calls == ["BTCUSD", "BTCUSD"]
    assert [i for i in items if i["source"] == FETCH_SOURCE] == []
    assert sleeps == [120]


def test_a_cell_failing_every_pass_is_one_item_after_three_attempts(tmp_path):
    ids, specs = _one_cell()
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(len(calls) + 1)
        raise RuntimeError(f"HTTP 5xx #{len(calls)}")
    sleeps, sleep = _sleeps()
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=sleep,
                       now_ms=NOW_MS)
    mine = [i for i in items if i["source"] == FETCH_SOURCE]
    assert len(calls) == 3 and sleeps == [120, 120]
    assert len(mine) == 1 and mine[0]["key"] == "BTCUSD@1h"
    assert "#3" in mine[0]["reason"]                 # the LAST failure
    assert mine[0]["reason"].endswith(" (after 3 attempts)")


def test_only_the_cells_that_failed_are_retried(tmp_path):
    ids, specs = _two_cells()
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        if sym == "BTCUSD" and calls.count("BTCUSD") == 1:
            raise RuntimeError("blip")
        return {}
    sleeps, sleep = _sleeps()
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=sleep,
                       now_ms=NOW_MS)
    assert calls == ["BTCUSD", "ETHUSD", "BTCUSD"]
    assert [i for i in items if i["source"] == FETCH_SOURCE] == []
    assert sleeps == [0.2, 120]            # pacing in pass 1, one retry wait


def test_nothing_failed_means_no_retry_sleep(tmp_path):
    ids, specs = _two_cells()
    sleeps, sleep = _sleeps()
    fetch_owed(ids, specs, tmp_path, fetch=lambda *a, **k: {}, sleep=sleep,
               now_ms=NOW_MS)
    assert sleeps == [0.2]


def test_a_ban_in_a_retry_pass_stops_without_sleeping(tmp_path):
    from .crypto_data import BinanceBanned
    ids, specs = _two_cells()
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        if sym == "BTCUSD":
            if calls.count("BTCUSD") == 1:
                raise RuntimeError("blip")
            raise BinanceBanned("418 from Binance: IP-wide ban")
        return {}
    sleeps, sleep = _sleeps()
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=sleep,
                       now_ms=NOW_MS)
    mine = [i for i in items if i["source"] == FETCH_SOURCE]
    assert calls == ["BTCUSD", "ETHUSD", "BTCUSD"]
    assert sleeps == [0.2, 120]            # no sleep after the ban
    assert [i["key"] for i in mine] == ["BTCUSD@1h"]
    assert "418" in mine[0]["reason"]


def test_a_rate_limit_in_a_retry_pass_stops_without_sleeping(tmp_path):
    from .crypto_data import RateLimited
    ids, specs = _two_cells()
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        if sym == "BTCUSD":
            if calls.count("BTCUSD") == 1:
                raise RuntimeError("blip")
            raise RateLimited(120.0, retry_ok=False)
        return {}
    sleeps, sleep = _sleeps()
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=sleep,
                       now_ms=NOW_MS)
    mine = [i for i in items if i["source"] == FETCH_SOURCE]
    assert calls == ["BTCUSD", "ETHUSD", "BTCUSD"] and sleeps == [0.2, 120]
    assert [i["key"] for i in mine] == ["BTCUSD@1h"]
    assert "rate limit" in mine[0]["reason"].lower()


def test_a_ban_in_pass_one_skips_the_retries_entirely(tmp_path):
    from .crypto_data import BinanceBanned
    ids, specs = _two_cells()
    calls = []

    def fetch(sym, tf, d, **kw):
        calls.append(sym)
        raise BinanceBanned("418 from Binance: IP-wide ban")
    sleeps, sleep = _sleeps()
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=sleep,
                       now_ms=NOW_MS)
    mine = [i for i in items if i["source"] == FETCH_SOURCE]
    assert calls == ["BTCUSD"] and sleeps == []
    assert [i["key"] for i in mine] == ["BTCUSD@1h", "ETHUSD@1h"]
    assert "418" in mine[1]["reason"] and "skipped" in mine[1]["reason"]


def test_holes_and_staleness_of_owed_cells_are_items(tmp_path):
    ids, specs = _two_cells()
    # BTCUSD: the fetch FAILS; its file has a hole (10-02) and an old last bar.
    _csv(tmp_path, "BTCUSD_1h.csv", ["2026-10-01 00:00:00", "2026-10-03 00:00:00"])
    # ETHUSD: fetch OK, no hole, but the last bar is days old: stale.
    _csv(tmp_path, "ETHUSD_1h.csv", [f"2026-10-01 {h:02d}:00:00" for h in range(24)])

    def fetch(sym, tf, d, **kw):
        if sym == "BTCUSD":
            raise RuntimeError("down")
        return {}
    sleeps, sleep = _sleeps()
    items = fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=sleep,
                       now_ms=NOW_MS)
    got = sorted((i["source"], i["key"]) for i in items)
    # no feed_stale for BTCUSD: its fetch failed, the fetch item says so
    assert got == [("feed_hole", "BTCUSD@1h"), ("feed_stale", "ETHUSD@1h"),
                   (FETCH_SOURCE, "BTCUSD@1h")]


def test_an_unreadable_ack_file_is_one_item(tmp_path):
    ids, specs = _one_cell()
    _csv(tmp_path, "BTCUSD_1h.csv", ["2026-10-05 10:00:00", "2026-10-05 11:00:00"])
    (tmp_path / "intraday_holes_ack.json").write_text("{nope", encoding="utf-8")
    items = fetch_owed(ids, specs, tmp_path, fetch=lambda *a, **k: {},
                       sleep=lambda s: None, now_ms=NOW_MS)
    assert [(i["source"], i["key"]) for i in items] == [
        ("feed_hole", "intraday_holes_ack.json")]
    assert "ack file unreadable" in items[0]["reason"]


def test_a_detector_that_raises_never_crashes_the_step(tmp_path, monkeypatch):
    from . import feed_health
    ids, specs = _one_cell()

    def boom(*a, **k):
        raise ValueError("kaboom")
    monkeypatch.setattr(feed_health, "scan", boom)
    items = fetch_owed(ids, specs, tmp_path, fetch=lambda *a, **k: {},
                       sleep=lambda s: None, now_ms=NOW_MS)
    assert len(items) == 1
    assert items[0]["source"] == "feed_hole" and items[0]["key"] == "feed_health"
    assert items[0]["reason"] == f"detector failed: {ValueError('kaboom')!r}"


def test_a_failing_ack_loader_never_crashes_the_step(tmp_path, monkeypatch):
    from . import feed_health

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(feed_health, "load_acks", boom)
    ids, specs = _one_cell()
    items = fetch_owed(ids, specs, tmp_path, fetch=lambda *a, **k: {},
                       sleep=lambda s: None, now_ms=NOW_MS)
    assert [(i["source"], i["key"]) for i in items] == [("feed_hole", "feed_health")]


def test_the_step_sources_match_feed_health():
    """quarantine.py cannot import feed_health at module load (it imports
    crypto_data, which is imported lazily on purpose), so the constant holds
    literals; this pins them to the real names."""
    from . import feed_health
    from .quarantine import FETCH_STEP_SOURCES
    assert FETCH_STEP_SOURCES == (FETCH_SOURCE, feed_health.HOLE_SOURCE,
                                  feed_health.STALE_SOURCE)


def test_the_fetch_step_owns_its_detector_items_and_catch_up_keeps_them(tmp_path):
    from .quarantine import _write_fetch_ledger
    from .test_intraday_statistics import _twin_bars
    reg, data, daily, sd, sh = _twins(tmp_path, end=_twin_bars()[0][153]["date"])
    hole = {"source": "feed_hole", "key": "BTCUSD@1h", "reason": "1 day(s) with no bars"}
    stale = {"source": "feed_stale", "key": "BTCUSD@1h", "reason": "old"}
    other = {"source": "restated", "key": "EFA", "reason": "x"}
    p = reg.log_path.parent / "logs" / dg.QUARANTINE_LEDGER
    _write_fetch_ledger(reg.log_path, [hole, stale])
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert {i["source"] for i in ledger(reg)["items"]} >= {"feed_hole", "feed_stale"}
    # a later fetch run that no longer sees them removes both, keeps the rest
    dg.record(p, "quarantine", [other], remove_unseen=False)
    _write_fetch_ledger(reg.log_path, [])
    assert [(i["source"], i["key"]) for i in ledger(reg)["items"]] == [
        ("restated", "EFA")]


def test_a_dated_record_keeps_the_detector_items(tmp_path):
    from .quarantine import _write_fetch_ledger
    reg, data, daily, sd, sh = _twins(tmp_path)
    hole = {"source": "feed_hole", "key": "BTCUSD@1h", "reason": "x"}
    _write_fetch_ledger(reg.log_path, [hole])
    assert quarantine_run(argv_for(reg, data, "--date", daily[151]["date"])) == 0
    assert any(i["source"] == "feed_hole" for i in ledger(reg)["items"])


def test_no_owed_intraday_cell_means_no_scan_and_no_sleep(tmp_path, monkeypatch):
    from . import feed_health

    def no_scan(*a, **k):
        raise AssertionError("scan must not run")
    monkeypatch.setattr(feed_health, "scan", no_scan)
    sd = _twin_spec("1d")
    sleeps, sleep = _sleeps()
    assert fetch_owed([sd["strategy_id"]], _specs(sd), tmp_path, sleep=sleep,
                      now_ms=NOW_MS) == []
    assert sleeps == []


# ---- 2b-3 plan 5 carry-over 1: fetch-owed fetches with the refresh's cap ----

# 2026-10-05 07:00 UTC = 15:00 Perth: a late (afternoon) run, long after 00:00 UTC
LATE_MS = 1_791_183_600_000


def test_fetch_owed_passes_the_end_cap(tmp_path):
    from .crypto_data import last_complete_utc_day_end_ms
    ids, specs = _two_cells()
    seen = []

    def fetch(sym, tf, d, now_ms=None, end_ms=None):
        seen.append((sym, tf, now_ms, end_ms))
        return {}
    fetch_owed(ids, specs, tmp_path, fetch=fetch, sleep=lambda s: None,
               now_ms=LATE_MS)
    cap = last_complete_utc_day_end_ms(LATE_MS)
    assert cap == LATE_MS - 7 * 3_600_000          # 00:00 UTC of the same day
    assert seen == [("BTCUSD", "1h", LATE_MS, cap), ("ETHUSD", "1h", LATE_MS, cap)]


def _canned_klines(tf, first_open_ms, now_ms):
    """Every kline of `tf` from `first_open_ms` up to and including the bar
    still open at `now_ms` (its close_time is in the future)."""
    from .crypto_data import TF_MS
    step, out, t = TF_MS[tf], [], first_open_ms
    while t <= now_ms:
        out.append([t, "1", "2", "0.5", "1.5", "10", t + step - 1,
                    "0", 0, "0", "0", "0"])
        t += step
    return out


def _late_run(tmp_path, monkeypatch, tf):
    """A real `fetch_symbol` run through the module's fake-HTTP seam."""
    from . import crypto_data as cd
    day0 = LATE_MS - LATE_MS % 86_400_000 - 86_400_000       # 2026-10-04 00:00 UTC
    (tmp_path / "BTCUSD_1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-10-04 00:00:00,1,2,0.5,1.5,10\n",
        encoding="utf-8")
    klines = _canned_klines(tf, day0, LATE_MS)

    def http(url):
        start = int(url.split("startTime=")[1].split("&")[0])
        assert f"interval={tf}" in url
        return [k for k in klines if k[0] >= start][:1000]
    monkeypatch.setattr(cd, "_http_get_json", http)
    monkeypatch.setattr(cd.time, "sleep", lambda s: None)
    spec = _twin_spec(tf)
    items = fetch_owed([spec["strategy_id"]], _specs(spec), tmp_path,
                       sleep=lambda s: None, now_ms=LATE_MS)
    path = tmp_path / f"BTCUSD_{tf}.csv"
    rows = path.read_text(encoding="utf-8").splitlines()[1:]
    return items, rows


def test_fetch_owed_never_writes_todays_bars(tmp_path, monkeypatch):
    # (tf, last row): 1h 23:00 and 4h 20:00 of the previous UTC day, 12h 12:00
    # (today's 12h bar is still open at 07:00 UTC, the closed-bar rule drops it)
    for tf, last in (("1h", "2026-10-04 23:00:00"), ("4h", "2026-10-04 20:00:00"),
                     ("12h", "2026-10-04 12:00:00")):
        d = tmp_path / tf
        d.mkdir()
        items, rows = _late_run(d, monkeypatch, tf)
        assert rows[-1].split(",")[0] == last, tf
        assert not [i for i in items if i["source"] == FETCH_SOURCE], tf
        assert all(r.split(",")[0] < "2026-10-05" for r in rows), tf


def test_fetch_owed_late_run_raises_no_stale_item(tmp_path, monkeypatch):
    for tf in ("1h", "4h"):
        d = tmp_path / tf
        d.mkdir()
        items, _ = _late_run(d, monkeypatch, tf)
        assert [i for i in items if i["source"] == "feed_stale"] == [], tf
        assert items == [], tf
