"""2b-2: quarantine records supported intraday specs; only a NON-24x7
intraday spec is refused (degraded intraday_unsupported). The supported path
is tested in test_quarantine_record_intraday.py."""
from __future__ import annotations

import copy
import json
import os

import pytest

from . import quarantine as quarantine_mod
from .common import content_id
from .qcells import complete_days
from .quarantine import run as quarantine_run
from .screen import load_bars as _real_load_bars
from .test_gen3b import argv_for, decisions
from .test_intraday_statistics import _twin_bars, _twin_spec
from .test_quarantine_isolation import _quarantine, btc_and_eth_solo, ledger
from .test_quarantine_record_intraday import _register_quarantined, _write


def _unsupported_clone(spec):
    one = json.loads(json.dumps(spec))
    one["universe"] = dict(one["universe"], timeframe="1h", session="us_equity")
    one["strategy_id"] = None
    one["strategy_id"] = content_id(one, "strategy_id")
    return one


def test_a_non_24x7_intraday_spec_is_skipped_and_reported(tmp_path, capsys):
    reg, a, b, data = btc_and_eth_solo(tmp_path)
    h = _unsupported_clone(a)
    try:
        reg.register_strategy(h)
    except ValueError:
        reg.append("strategy_registered", h)
    _quarantine(reg, h)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    sids = {r["strategy_id"] for r in decisions(reg)}
    assert h["strategy_id"] not in sids and a["strategy_id"] in sids
    out = capsys.readouterr().out
    assert f"{h['strategy_id']}  skipped: intraday timeframe '1h' in session 'us_equity'" in out
    assert any(i["source"] == "intraday_unsupported" and i["key"] == h["strategy_id"]
               for i in ledger(reg)["items"])


# ---- 2b-3 D-i Task 4: each intraday file is parsed once per process ----


@pytest.fixture(autouse=True)
def _fresh_full_bars_memo():
    """The memo is module state; a test must start and leave it empty so test
    order cannot leak a hit (or a miss) between tests."""
    quarantine_mod._reset_full_bars_memo()
    yield
    quarantine_mod._reset_full_bars_memo()


def _count_loads(monkeypatch):
    """Wrap quarantine's load_bars; returns the list of (file name, timeframe)
    it was called with."""
    calls = []

    def counting(data_dir, asset, cutoff, timeframe="1d"):
        calls.append((f"{asset}_{timeframe}.csv", timeframe))
        return _real_load_bars(data_dir, asset, cutoff, timeframe=timeframe)

    monkeypatch.setattr(quarantine_mod, "load_bars", counting)
    return calls


def _hour_rows(day0, n_days, price=100.0):
    import datetime as _dt
    d0 = _dt.date.fromisoformat(day0)
    rows = []
    for k in range(n_days):
        d = (d0 + _dt.timedelta(days=k)).isoformat()
        for h in range(24):
            rows.append({"date": f"{d} {h:02d}:00:00", "open": price,
                         "high": price, "low": price, "close": price,
                         "volume": 1.0})
    return rows


def test_each_intraday_file_is_parsed_once_per_process(tmp_path, monkeypatch):
    """A 3-date catch-up of two 1h strategies on one cell parses the file once
    (before: once per strategy per owed-date pass plus once per date)."""
    from .test_gauntlet import gauntlet_registry
    reg, _ = gauntlet_registry(tmp_path)
    daily, hourly, _ = _twin_bars()
    end = daily[153]["date"]                      # owed: 151, 152, 153
    data = tmp_path / "data"
    data.mkdir()
    _write(data, "BTCUSD_1h.csv", [b for b in hourly if b["date"][:10] <= end])
    entered = daily[150]["date"]
    specs = []
    for tag in ("a", "b"):
        s = _twin_spec("1h")
        s["provenance"] = {"sibling_group_id": "twin-1h-" + tag}
        specs.append(_register_quarantined(reg, s, entered))
    assert specs[0]["strategy_id"] != specs[1]["strategy_id"]
    calls = _count_loads(monkeypatch)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert {r["strategy_id"] for r in decisions(reg)} == \
        {s["strategy_id"] for s in specs}
    assert len(decisions(reg)) == 6
    assert calls == [("BTCUSD_1h.csv", "1h")], calls
    st = quarantine_mod.full_bars_memo_stats()
    assert st["misses"] == 1 and st["hits"] > 3, st


def test_prefix_by_bisect_equals_the_list_comprehension(tmp_path):
    daily, hourly, _ = _twin_bars()
    data = tmp_path
    _write(data, "BTCUSD_1h.csv", hourly)
    full = _real_load_bars(data, "BTCUSD", "9999-12-31", timeframe="1h")
    days = [d["date"] for d in daily]
    probes = ["2021-12-31"] + days + ["9999-12-31"]
    for date in probes:
        upto, complete = quarantine_mod._load_cell_bars(data, "BTCUSD@1h", date)
        assert upto == [b for b in full if b["date"][:10] <= date], date
        assert complete == (date in complete_days(full, "1h")), date


def test_the_prefix_is_a_new_list_over_shared_bar_dicts(tmp_path):
    _write(tmp_path, "BTCUSD_1h.csv", _hour_rows("2023-01-01", 3))
    a, _ = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "2023-01-03")
    b, _ = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "2023-01-03")
    assert a == b and a is not b
    a.append({"date": "junk"})
    c, _ = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "2023-01-03")
    assert len(c) == 72, "appending to a returned list must not reach the memo"


def test_a_replaced_file_is_reparsed(tmp_path, monkeypatch):
    """Review Focus 4: a rewritten CSV (new mtime/size) is seen by the next
    call, not served from the memo."""
    _write(tmp_path, "BTCUSD_1h.csv", _hour_rows("2023-01-01", 3))
    calls = _count_loads(monkeypatch)
    up, comp = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "9999-12-31")
    assert len(up) == 72 and calls == [("BTCUSD_1h.csv", "1h")]
    quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "9999-12-31")
    assert len(calls) == 1
    _write(tmp_path, "BTCUSD_1h.csv", _hour_rows("2023-01-01", 4))
    up, comp = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "2023-01-04")
    assert len(up) == 96 and len(calls) == 2 and comp is True   # the new day


def test_a_same_size_rewrite_with_a_new_mtime_is_reparsed(tmp_path, monkeypatch):
    """The key carries st_mtime_ns, not just the size: a restated price of the
    same length is still a different file."""
    p = tmp_path / "BTCUSD_1h.csv"
    _write(tmp_path, "BTCUSD_1h.csv", _hour_rows("2023-01-01", 2, price=100.0))
    calls = _count_loads(monkeypatch)
    quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "9999-12-31")
    size = p.stat().st_size
    _write(tmp_path, "BTCUSD_1h.csv", _hour_rows("2023-01-01", 2, price=200.0))
    assert p.stat().st_size == size
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    up, _ = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "9999-12-31")
    assert up[0]["close"] == 200.0 and len(calls) == 2


def test_a_file_changing_during_the_read_is_not_memoised(tmp_path, monkeypatch):
    _write(tmp_path, "BTCUSD_1h.csv", _hour_rows("2023-01-01", 3))
    calls = []

    def racing(data_dir, asset, cutoff, timeframe="1d"):
        bars = _real_load_bars(data_dir, asset, cutoff, timeframe=timeframe)
        calls.append(len(bars))
        if len(calls) == 1:                       # the writer lands mid-read
            _write(tmp_path, "BTCUSD_1h.csv", _hour_rows("2023-01-01", 4))
        return bars

    monkeypatch.setattr(quarantine_mod, "load_bars", racing)
    up, _ = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "9999-12-31")
    assert len(up) == 72                          # the parse is still returned
    assert quarantine_mod.full_bars_memo_stats()["bars"] == 0
    up2, _ = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "9999-12-31")
    assert len(up2) == 96 and calls == [72, 96]   # reparsed, now memoised
    quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", "9999-12-31")
    assert calls == [72, 96]


def test_a_missing_file_behaves_as_before_and_is_not_memoised(tmp_path):
    with pytest.raises(FileNotFoundError):
        quarantine_mod._load_cell_bars(tmp_path, "NOPE@1h", "2023-01-01")
    with pytest.raises(FileNotFoundError):
        quarantine_mod._full_bars(tmp_path, "NOPE", "1h")
    assert quarantine_mod._cell_days(tmp_path, "NOPE@1h", "9999-12-31") is None
    st = quarantine_mod.full_bars_memo_stats()
    assert st["bars"] == 0 and st["hits"] == 0 and st["misses"] == 0


def test_cyclic_access_over_24_cells_hits_within_the_bound(tmp_path, monkeypatch):
    """Strategy order then date order, twice (the shape of _last_bar_date /
    _owed_by_date, then _load_eligible_bars per date): each file is parsed
    once because the bound holds all 24 cells."""
    keys = [f"A{i:02d}@1h" for i in range(24)]
    for k in keys:
        _write(tmp_path, f"{k.split('@')[0]}_1h.csv", _hour_rows("2023-01-01", 3))
    calls = _count_loads(monkeypatch)
    dates = ["2023-01-01", "2023-01-02", "2023-01-03"]
    for _round in range(2):
        for k in keys:                                   # per-strategy pass
            assert quarantine_mod._cell_days(tmp_path, k, "9999-12-31") == dates
        for d in dates:                                  # per-date pass
            bars, missing, comp = quarantine_mod._load_eligible_bars(
                tmp_path, keys, d)
            assert not missing and all(comp.values()) and len(bars) == 24
    assert len(calls) == 24 and len({c[0] for c in calls}) == 24
    st = quarantine_mod.full_bars_memo_stats()
    assert st["misses"] == 24 and st["evictions"] == 0 and st["bars"] == 24 * 72


def test_the_bars_bound_evicts_least_recent(tmp_path, monkeypatch):
    for n in "ABC":
        _write(tmp_path, f"{n}_1h.csv", _hour_rows("2023-01-01", 3))   # 72 bars
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 72 * 2 + 10)
    calls = _count_loads(monkeypatch)
    for n in "AB":
        quarantine_mod._full_bars(tmp_path, n, "1h")
    quarantine_mod._full_bars(tmp_path, "A", "1h")      # A is now most recent
    quarantine_mod._full_bars(tmp_path, "C", "1h")      # 216 > 154: evict B
    st = quarantine_mod.full_bars_memo_stats()
    assert st["evictions"] == 1 and st["bars"] == 144, st
    n0 = len(calls)
    quarantine_mod._full_bars(tmp_path, "A", "1h")
    quarantine_mod._full_bars(tmp_path, "C", "1h")
    assert len(calls) == n0                             # A and C survived
    quarantine_mod._full_bars(tmp_path, "B", "1h")
    assert len(calls) == n0 + 1                         # B was the one evicted


def test_one_file_over_the_bound_is_still_served_and_held(tmp_path, monkeypatch):
    _write(tmp_path, "BIG_1h.csv", _hour_rows("2023-01-01", 3))
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 10)
    calls = _count_loads(monkeypatch)
    assert len(quarantine_mod._full_bars(tmp_path, "BIG", "1h")) == 72
    quarantine_mod._full_bars(tmp_path, "BIG", "1h")
    assert len(calls) == 1
    assert quarantine_mod.full_bars_memo_stats()["bars"] == 72


def test_the_memo_counts_hits_misses_evictions_and_bars_held(tmp_path, monkeypatch):
    _write(tmp_path, "A_1h.csv", _hour_rows("2023-01-01", 1))
    _write(tmp_path, "B_1h.csv", _hour_rows("2023-01-01", 1))
    assert quarantine_mod.full_bars_memo_stats() == {
        "hits": 0, "misses": 0, "evictions": 0, "bars": 0, "files": 0}
    quarantine_mod._full_bars(tmp_path, "A", "1h")
    quarantine_mod._full_bars(tmp_path, "A", "1h")
    quarantine_mod._full_bars(tmp_path, "A", "1h")
    assert quarantine_mod.full_bars_memo_stats() == {
        "hits": 2, "misses": 1, "evictions": 0, "bars": 24, "files": 1}
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 24)
    quarantine_mod._full_bars(tmp_path, "B", "1h")
    assert quarantine_mod.full_bars_memo_stats() == {
        "hits": 2, "misses": 2, "evictions": 1, "bars": 24, "files": 1}
    monkeypatch.undo()
    assert quarantine_mod.FULL_BARS_MEMO_MAX_BARS == 2_000_000


def test_bars_are_not_mutated_by_a_recorded_day(tmp_path):
    """The memo hands out SHARED bar dicts, so no intraday path may mutate
    them: observe_day over two dates leaves the memoised bars equal to a deep
    copy taken before."""
    daily, hourly, _ = _twin_bars()
    _write(tmp_path, "BTCUSD_1h.csv", hourly)
    full = quarantine_mod._full_bars(tmp_path, "BTCUSD", "1h")
    before = copy.deepcopy(full)
    spec = _twin_spec("1h")
    entered = daily[150]["date"]
    for day in (daily[151]["date"], daily[152]["date"]):
        upto, complete = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD@1h", day)
        assert complete
        rows = quarantine_mod.observe_day(spec, {"BTCUSD@1h": upto}, day, entered)
        assert rows and rows[0]["date"] == day
    assert full == before
    assert quarantine_mod._full_bars(tmp_path, "BTCUSD", "1h") is full


def test_daily_cells_are_untouched(tmp_path, monkeypatch):
    """The 1d path never reaches _full_bars: _daily_bars still parses fresh
    and normalises the date part, so daily cells are never memoised."""
    daily, _, _ = _twin_bars()
    _write(tmp_path, "BTCUSD_1d.csv", daily)

    def boom(*a, **k):
        raise AssertionError("a daily cell must not use _full_bars")

    monkeypatch.setattr(quarantine_mod, "_full_bars", boom)
    calls = _count_loads(monkeypatch)
    day = daily[10]["date"]
    for _ in range(2):
        bars, comp = quarantine_mod._load_cell_bars(tmp_path, "BTCUSD", day)
        assert comp and bars[-1]["date"] == day
        assert quarantine_mod._cell_days(tmp_path, "BTCUSD", day)[-1] == day
    assert len(calls) == 4                              # parsed every time
    assert quarantine_mod.full_bars_memo_stats()["files"] == 0


# ---- 2b-3 plan 5 Task 5: the memo is sized to the cells a run records ----


def _rows_file(data, name, n_rows):
    """A tiny intraday-shaped file of exactly n_rows data rows (the sizing
    counts bytes, never parses)."""
    import datetime as _dt
    t0 = _dt.datetime(2023, 1, 1)
    with (data / name).open("w", newline="", encoding="utf-8") as f:
        f.write("date,open,high,low,close,volume\n")
        for k in range(n_rows):
            ts = (t0 + _dt.timedelta(hours=k)).strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{ts},1,1,1,1,1\n")


def test_the_bound_covers_the_touched_cells(tmp_path, monkeypatch):
    for n, rows in (("A", 1000), ("B", 2000), ("C", 3000)):
        _rows_file(tmp_path, f"{n}_1h.csv", rows)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 2500)
    keys = ["A@1h", "B@1h", "C@1h"]
    assert quarantine_mod.size_full_bars_memo(tmp_path, keys) == 6000
    assert quarantine_mod.FULL_BARS_MEMO_MAX_BARS == 2500     # never mutated
    for _ in range(3):                                         # cyclic access
        for n in "ABC":
            quarantine_mod._full_bars(tmp_path, n, "1h")
    st = quarantine_mod.full_bars_memo_stats()
    assert st["misses"] == 3 and st["evictions"] == 0 and st["hits"] == 6, st
    assert st["bars"] == 6000


def test_the_bound_never_drops_below_the_default(tmp_path, monkeypatch):
    _rows_file(tmp_path, "A_1h.csv", 100)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 2500)
    assert quarantine_mod.size_full_bars_memo(tmp_path, ["A@1h"]) == 2500
    assert quarantine_mod.size_full_bars_memo(tmp_path, []) == 2500
    # a missing file and a daily key count nothing
    assert quarantine_mod.size_full_bars_memo(
        tmp_path, ["NOPE@1h", "BTCUSD"]) == 2500


def test_the_bound_stops_at_the_ceiling(tmp_path, monkeypatch):
    _rows_file(tmp_path, "A_1h.csv", 4000)
    _rows_file(tmp_path, "B_1h.csv", 4000)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 2500)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", 5000)
    assert quarantine_mod.size_full_bars_memo(tmp_path, ["A@1h", "B@1h"]) == 5000
    assert quarantine_mod.FULL_BARS_MEMO_CEILING_BARS == 5000
    assert quarantine_mod.FULL_BARS_MEMO_MAX_BARS == 2500
    monkeypatch.undo()
    assert quarantine_mod.FULL_BARS_MEMO_CEILING_BARS == 6_000_000


def test_a_reset_restores_the_default_bound(tmp_path, monkeypatch):
    _rows_file(tmp_path, "A_1h.csv", 9000)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 2500)
    quarantine_mod.size_full_bars_memo(tmp_path, ["A@1h"])
    quarantine_mod._reset_full_bars_memo()
    _rows_file(tmp_path, "B_1h.csv", 3000)
    _rows_file(tmp_path, "C_1h.csv", 3000)
    quarantine_mod._full_bars(tmp_path, "B", "1h")
    quarantine_mod._full_bars(tmp_path, "C", "1h")      # 6000 > the 2500 default
    assert quarantine_mod.full_bars_memo_stats()["evictions"] == 1


def _two_cell_chain(root):
    """Two 1h strategies on two cells (BTCUSD, ETHUSD), quarantined on day
    150, with the price files truncated so a catch-up owes 151, 152, 153."""
    from .test_gauntlet import gauntlet_registry
    root.mkdir(parents=True, exist_ok=True)
    reg, _ = gauntlet_registry(root)
    daily, hourly, _ = _twin_bars()
    end = daily[153]["date"]
    data = root / "data"
    data.mkdir()
    rows = [b for b in hourly if b["date"][:10] <= end]
    _write(data, "BTCUSD_1h.csv", rows)
    _write(data, "ETHUSD_1h.csv", rows)
    entered = daily[150]["date"]
    for asset in ("BTCUSD", "ETHUSD"):
        s = _twin_spec("1h")
        s["universe"] = dict(s["universe"], assets=[asset])
        _register_quarantined(reg, s, entered)
    return reg, data


def _rows_of(reg):
    return sorted(json.dumps(r, sort_keys=True) for r in decisions(reg))


def test_rows_are_identical_above_the_ceiling(tmp_path, monkeypatch):
    """Review Focus 4: a memo too small for the touched set (LRU churn, every
    file re-parsed) records exactly what an unbounded one records."""
    big = 10 ** 9
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", big)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", big)
    reg_a, data_a = _two_cell_chain(tmp_path / "unbounded")
    assert quarantine_run(argv_for(reg_a, data_a, "--catch-up")) == 0
    st_a = quarantine_mod.full_bars_memo_stats()
    assert st_a["evictions"] == 0 and st_a["misses"] == 2, st_a
    quarantine_mod._reset_full_bars_memo()

    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 10)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", 100)
    reg_b, data_b = _two_cell_chain(tmp_path / "bounded")
    assert quarantine_run(argv_for(reg_b, data_b, "--catch-up")) == 0
    st_b = quarantine_mod.full_bars_memo_stats()
    assert st_b["evictions"] > 0 and st_b["misses"] > 2, st_b
    rows_a, rows_b = _rows_of(reg_a), _rows_of(reg_b)
    assert len(rows_a) == 6
    assert rows_a == rows_b


def test_run_prints_the_memo_line(tmp_path, capsys):
    reg, data = _two_cell_chain(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date",
                                   _twin_bars()[0][151]["date"])) == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("full-bars memo:")]
    st = quarantine_mod.full_bars_memo_stats()
    assert lines == [f"full-bars memo: hits {st['hits']}, misses {st['misses']}, "
                     f"evictions {st['evictions']}, bound "
                     f"{quarantine_mod._current_full_bars_bound()} bars"], lines
    assert st["misses"] == 2 and st["evictions"] == 0


def test_a_catch_up_sizes_and_prints_once(tmp_path, monkeypatch, capsys):
    reg, data = _two_cell_chain(tmp_path)
    calls = []
    real = quarantine_mod.size_full_bars_memo

    def spy(data_dir, cell_keys):
        keys = list(cell_keys)
        calls.append(keys)
        return real(data_dir, keys)

    monkeypatch.setattr(quarantine_mod, "size_full_bars_memo", spy)
    assert quarantine_run(argv_for(reg, data, "--catch-up")) == 0
    assert len(decisions(reg)) == 6                  # three dates recorded
    assert len(calls) == 1 and sorted(calls[0]) == ["BTCUSD@1h", "ETHUSD@1h"]
    out = capsys.readouterr().out
    assert out.count("full-bars memo:") == 1
    assert out.index("full-bars memo:") > out.index("catch-up: --date 2022-")
    assert out.rstrip().splitlines()[-1].startswith("full-bars memo:")


def test_fetch_owed_and_review_do_not_size_the_memo(tmp_path, monkeypatch):
    reg, data = _two_cell_chain(tmp_path)
    called = []
    monkeypatch.setattr(quarantine_mod, "size_full_bars_memo",
                        lambda *a, **k: called.append(a))
    quarantine_run(argv_for(reg, data, "--review"))
    assert called == []
    # the --fetch-owed leg: the fetch itself is faked (never the network)
    from . import crypto_data, feed_health
    fetched = []
    monkeypatch.setattr(crypto_data, "fetch_symbol",
                        lambda sym, tf, *a, **k: fetched.append((sym, tf)) or {})
    monkeypatch.setattr(feed_health, "scan", lambda *a, **k: [])
    monkeypatch.setattr(quarantine_mod.time, "sleep", lambda s: None)
    assert quarantine_run(argv_for(reg, data, "--fetch-owed")) == 0
    assert sorted(fetched) == [("BTCUSD", "1h"), ("ETHUSD", "1h")]   # it did run
    assert called == []                              # and sized nothing


def test_the_bound_excludes_an_untouched_large_intraday_file(tmp_path, monkeypatch):
    """Sizing counts only the cells the run records: a large intraday file in
    the data dir that no quarantined strategy trades adds nothing."""
    _rows_file(tmp_path, "A_1h.csv", 1000)
    _rows_file(tmp_path, "UNTOUCHED_1h.csv", 9000)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 500)
    assert quarantine_mod.size_full_bars_memo(tmp_path, ["A@1h"]) == 1000
    assert quarantine_mod.size_full_bars_memo(
        tmp_path, ["A@1h", "UNTOUCHED@1h"]) == 10_000     # the control: counted if touched


def test_a_touched_total_above_the_ceiling_prints_one_warn(tmp_path, monkeypatch, capsys):
    _rows_file(tmp_path, "A_1h.csv", 4000)
    _rows_file(tmp_path, "B_1h.csv", 4000)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 2500)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", 7000)
    quarantine_mod.size_full_bars_memo(tmp_path, ["A@1h", "B@1h"])
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "WARN full-bars memo: touched 8,000 bars > ceiling 7,000"], out


def test_no_warn_at_or_below_the_ceiling(tmp_path, monkeypatch, capsys):
    _rows_file(tmp_path, "A_1h.csv", 4000)
    _rows_file(tmp_path, "B_1h.csv", 4000)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 2500)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", 8000)  # == total
    quarantine_mod.size_full_bars_memo(tmp_path, ["A@1h", "B@1h"])
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", 9000)
    quarantine_mod.size_full_bars_memo(tmp_path, ["A@1h", "B@1h"])
    assert "WARN" not in capsys.readouterr().out


def test_the_ceiling_alarm_reaches_the_quarantine_ledger(tmp_path, monkeypatch, capsys):
    """The Sentinel reads degraded_quarantine.json: a run whose touched total
    passes the ceiling adds ONE memo_ceiling item, and a run under it none."""
    reg, data = _two_cell_chain(tmp_path)
    date = _twin_bars()[0][151]["date"]
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_MAX_BARS", 10)
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", 100)
    assert quarantine_run(argv_for(reg, data, "--date", date)) == 0
    assert "WARN full-bars memo: touched " in capsys.readouterr().out
    items = [i for i in ledger(reg)["items"] if i["source"] == "memo_ceiling"]
    assert len(items) == 1 and items[0]["key"] == "full_bars_memo"
    assert "> ceiling 100" in items[0]["reason"]

    reg2, data2 = _two_cell_chain(tmp_path / "under")
    monkeypatch.setattr(quarantine_mod, "FULL_BARS_MEMO_CEILING_BARS", 10 ** 9)
    quarantine_mod._reset_full_bars_memo()
    assert quarantine_run(argv_for(reg2, data2, "--date", date)) == 0
    assert "WARN full-bars memo" not in capsys.readouterr().out
    assert not [i for i in ledger(reg2)["items"] if i["source"] == "memo_ceiling"]
