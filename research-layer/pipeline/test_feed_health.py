"""2b-3 C1/C2: hole and stale-feed detection, plus the acknowledgement file."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .feed_health import (ACK_FILE, HOLE_SOURCE, STALE_SOURCE,
                          STALE_TOLERANCE_S, find_holes, is_stale,
                          load_acks, scan)

HEADER = "date,open,high,low,close,volume\n"


def _ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def _write(path: Path, dates):
    path.write_text(HEADER + "".join(f"{d},1,2,0.5,1.5,10\n" for d in dates),
                    encoding="utf-8")


def _hourly(start: datetime, end: datetime, skip_day=None):
    out, t = [], start
    while t <= end:
        if skip_day is None or t.strftime("%Y-%m-%d") != skip_day:
            out.append(t.strftime("%Y-%m-%d %H:%M:%S"))
        t += timedelta(hours=1)
    return out


def test_stale_constants():
    assert (HOLE_SOURCE, STALE_SOURCE) == ("feed_hole", "feed_stale")
    assert STALE_TOLERANCE_S == 600
    assert ACK_FILE == "intraday_holes_ack.json"


def test_find_holes_middle_gap():
    assert find_holes(["2024-01-01 00:00:00", "2024-01-02 00:00:00",
                       "2024-01-05 00:00:00"]) == ["2024-01-03", "2024-01-04"]


def test_partial_first_and_last_days_are_not_holes():
    start = datetime(2024, 1, 1, 13)
    end = datetime(2024, 1, 3, 5)
    assert find_holes(_hourly(start, end)) == []
    assert find_holes(_hourly(start, end, skip_day="2024-01-02")) == ["2024-01-02"]


def test_bare_daily_dates_work():
    assert find_holes(["2024-01-01", "2024-01-03"]) == ["2024-01-02"]


def test_fresh_at_the_refresh_and_fetch_times():
    now_daily = _ms(2026, 10, 19, 0, 2)
    assert not is_stale("2026-10-18 00:00:00", "1d", now_daily)
    # BRIEF CONFLICT (reported): the brief expects open 2026-10-17 to be stale at
    # 00:02, but the specified rule (close < now - 1 bar - 10 min) cannot flag it
    # until 00:10 UTC, because the missing 10-18 bar closed only 2 min before. The
    # rule is implemented as written; this pins what it actually does.
    assert not is_stale("2026-10-17 00:00:00", "1d", now_daily)
    assert is_stale("2026-10-17 00:00:00", "1d", _ms(2026, 10, 19, 0, 12))
    now_hourly = _ms(2026, 10, 19, 0, 20)
    assert not is_stale("2026-10-18 23:00:00", "1h", now_hourly)
    assert is_stale("2026-10-18 22:00:00", "1h", now_hourly)


def test_is_stale_accepts_a_bare_date_as_midnight_utc():
    now = _ms(2026, 10, 19, 0, 2)
    assert not is_stale("2026-10-18", "1d", now)
    assert is_stale("2026-10-17", "1d", _ms(2026, 10, 19, 0, 12))


def test_an_acknowledged_hole_is_printed_not_itemised(tmp_path, capsys):
    _write(tmp_path / "BTCUSDT_1d.csv", ["2024-01-01 00:00:00", "2024-01-03 00:00:00"])
    items = scan(tmp_path, ["BTCUSDT"], now_ms=_ms(2024, 1, 4),
                 stale_keys=set(), acks={"BTCUSDT": {"2024-01-02"}})
    assert [i for i in items if i["source"] == HOLE_SOURCE] == []
    assert "acknowledged hole(s): 2024-01-02" in capsys.readouterr().out

    _write(tmp_path / "BTCUSDT_1d.csv",
           ["2024-01-01 00:00:00", "2024-01-03 00:00:00", "2024-01-05 00:00:00"])
    items = scan(tmp_path, ["BTCUSDT"], now_ms=_ms(2024, 1, 6),
                 stale_keys=set(), acks={"BTCUSDT": {"2024-01-02"}})
    holes = [i for i in items if i["source"] == HOLE_SOURCE]
    assert len(holes) == 1 and holes[0]["key"] == "BTCUSDT"
    assert "2024-01-04" in holes[0]["reason"]
    assert "2024-01-02" not in holes[0]["reason"]
    assert holes[0]["reason"].startswith("1 day(s) with no bars:")


def test_unreadable_or_missing_file_is_one_item_not_a_crash(tmp_path):
    (tmp_path / "HEADER_1d.csv").write_text(HEADER, encoding="utf-8")
    (tmp_path / "GARBAGE_1d.csv").write_bytes(b"\xff\xfe\x00\x81garbage\x00\xff\n\xfe")
    for key in ("MISSING", "HEADER", "GARBAGE"):
        items = scan(tmp_path, [key], now_ms=_ms(2024, 1, 4),
                     stale_keys={key}, acks={})
        assert len(items) == 1, (key, items)
        assert items[0]["source"] == HOLE_SOURCE
        assert items[0]["key"] == key
        assert items[0]["reason"].startswith("cannot scan:")


def test_nonsense_date_column_is_one_item(tmp_path):
    _write(tmp_path / "BAD_1d.csv", ["not-a-date", "also-bad"])
    items = scan(tmp_path, ["BAD"], now_ms=_ms(2024, 1, 4), stale_keys=set(), acks={})
    assert len(items) == 1 and items[0]["reason"].startswith("cannot scan:")


def test_malformed_ack_file_is_an_item_and_honours_nothing(tmp_path):
    (tmp_path / ACK_FILE).write_text("{not json", encoding="utf-8")
    acks, note = load_acks(tmp_path)
    assert acks == {} and note
    item = {"source": HOLE_SOURCE, "key": ACK_FILE,
            "reason": f"ack file unreadable: {note}"}
    assert item["reason"].startswith("ack file unreadable:")
    for bad in ('[]', '{"BTCUSDT": ["2024-01-02"]}',
                '{"BTCUSDT": {"dates": ["2024-13-99"], "reason": "x"}}',
                '{"BTCUSDT": {"dates": "2024-01-02", "reason": "x"}}'):
        (tmp_path / ACK_FILE).write_text(bad, encoding="utf-8")
        acks, note = load_acks(tmp_path)
        assert acks == {} and note, bad


def test_ack_file_missing_empty_and_valid(tmp_path):
    assert load_acks(tmp_path) == ({}, None)
    (tmp_path / ACK_FILE).write_text("{}", encoding="utf-8")
    assert load_acks(tmp_path) == ({}, None)
    (tmp_path / ACK_FILE).write_text(json.dumps(
        {"BTCUSDT@1h": {"dates": ["2024-01-02"], "reason": "exchange outage"}}),
        encoding="utf-8")
    assert load_acks(tmp_path) == ({"BTCUSDT@1h": {"2024-01-02"}}, None)


def test_stale_only_for_stale_keys(tmp_path):
    _write(tmp_path / "BTCUSDT_1d.csv", ["2024-01-01 00:00:00", "2024-01-02 00:00:00"])
    now = _ms(2024, 2, 1)
    assert scan(tmp_path, ["BTCUSDT"], now_ms=now, stale_keys=set(), acks={}) == []
    items = scan(tmp_path, ["BTCUSDT"], now_ms=now, stale_keys={"BTCUSDT"}, acks={})
    assert len(items) == 1 and items[0]["source"] == STALE_SOURCE
    assert items[0]["key"] == "BTCUSDT"
    assert items[0]["reason"] == \
        "last closed bar 2024-01-02 00:00:00 is older than one 1d bar + 10 min"


def test_intraday_cell_path_and_fresh_feed(tmp_path):
    _write(tmp_path / "BTCUSDT_1h.csv", ["2026-10-18 22:00:00", "2026-10-18 23:00:00"])
    items = scan(tmp_path, ["BTCUSDT@1h"], now_ms=_ms(2026, 10, 19, 0, 20),
                 stale_keys={"BTCUSDT@1h"}, acks={})
    assert items == []
