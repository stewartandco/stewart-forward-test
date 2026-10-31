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
