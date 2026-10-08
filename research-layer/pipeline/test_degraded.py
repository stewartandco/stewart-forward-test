"""Degraded ledgers (2026-10-08 per-asset isolation design s3.1)."""
import json

from pipeline import degraded as dg

T0, T1 = "2026-10-08T12:00:00+00:00", "2026-10-09T12:00:00+00:00"


def _seen(*keys, source="restated"):
    return [{"source": source, "key": k, "reason": f"{k} why"} for k in keys]


def test_a_new_item_starts_its_clock_now():
    items = dg.merge([], _seen("EFA"), T0, remove_unseen=True)
    assert items == [{"source": "restated", "key": "EFA", "reason": "EFA why",
                      "since_utc": T0, "last_seen_utc": T0}]


def test_an_item_seen_again_keeps_its_original_since():
    first = dg.merge([], _seen("EFA"), T0, remove_unseen=True)
    again = dg.merge(first, _seen("EFA"), T1, remove_unseen=True)
    assert again[0]["since_utc"] == T0 and again[0]["last_seen_utc"] == T1


def test_an_unseen_item_is_removed_only_when_asked():
    first = dg.merge([], _seen("EFA", "SPY"), T0, remove_unseen=True)
    assert [i["key"] for i in dg.merge(first, _seen("EFA"), T1, remove_unseen=True)] == ["EFA"]
    kept = dg.merge(first, _seen("EFA"), T1, remove_unseen=False)
    assert [i["key"] for i in kept] == ["EFA", "SPY"]
    assert next(i for i in kept if i["key"] == "SPY")["last_seen_utc"] == T0


def test_keep_sources_keeps_an_unchecked_source_unchanged_and_drops_the_rest():
    prev = (dg.merge([], _seen("EFA", source="stage0"), T0, remove_unseen=True)
            + dg.merge([], _seen("AUD_1d", source="freshness"), T0, remove_unseen=True))
    out = dg.merge(prev, [], T1, remove_unseen=True, keep_sources=("stage0",))
    assert out == [{"source": "stage0", "key": "EFA", "reason": "EFA why",
                    "since_utc": T0, "last_seen_utc": T0}]


def test_same_key_in_two_sources_are_two_items():
    items = dg.merge([], _seen("EFA") + _seen("EFA", source="price_file_missing"),
                     T0, remove_unseen=True)
    assert [(i["source"], i["key"]) for i in items] == [
        ("price_file_missing", "EFA"), ("restated", "EFA")]


def test_record_writes_an_empty_ledger_too(tmp_path):
    p = tmp_path / "logs" / dg.QUARANTINE_LEDGER
    dg.record(p, "quarantine", [], remove_unseen=True)
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["writer"] == "quarantine" and body["items"] == [] and body["ts_utc"]
    assert "note" not in body


def test_record_round_trips_and_keeps_the_clock(tmp_path):
    p = tmp_path / dg.LOOP_LEDGER
    dg.record(p, "loop", _seen("EFA", source="stage0"), remove_unseen=True)
    since = json.loads(p.read_text(encoding="utf-8"))["items"][0]["since_utc"]
    dg.record(p, "loop", _seen("EFA", source="stage0"), remove_unseen=True)
    assert json.loads(p.read_text(encoding="utf-8"))["items"][0]["since_utc"] == since


def test_an_unreadable_previous_ledger_restarts_the_clock_and_says_so(tmp_path):
    p = tmp_path / dg.LOOP_LEDGER
    p.write_text("{not json", encoding="utf-8")
    dg.record(p, "loop", _seen("EFA", source="stage0"), remove_unseen=True)
    body = json.loads(p.read_text(encoding="utf-8"))
    assert "restarts" in body["note"] and len(body["items"]) == 1


def test_a_malformed_items_list_is_unreadable_too(tmp_path):
    p = tmp_path / dg.LOOP_LEDGER
    p.write_text(json.dumps({"items": [{"key": "EFA"}]}), encoding="utf-8")
    items, note = dg.read_items(p)
    assert items == [] and note


def test_a_missing_previous_ledger_is_a_first_run_not_a_note(tmp_path):
    assert dg.read_items(tmp_path / "absent.json") == ([], None)


def test_now_utc_carries_an_explicit_offset():
    assert dg.now_utc().endswith("+00:00")


def _record_at(monkeypatch, p, when, seen, *, ledger_text=None):
    monkeypatch.setattr(dg, "now_utc", lambda: when)
    if ledger_text is not None:
        p.write_text(ledger_text, encoding="utf-8")
    dg.record(p, "quarantine", seen, remove_unseen=True)
    return json.loads(p.read_text(encoding="utf-8"))


def test_a_clock_restart_note_survives_the_next_clean_write(tmp_path, monkeypatch):
    p = tmp_path / dg.QUARANTINE_LEDGER
    first = _record_at(monkeypatch, p, "2026-10-08T09:00:00+00:00",
                       _seen("EFA"), ledger_text="{not json")
    assert "restarts" in first["note"]
    assert first["note_utc"] == "2026-10-08T09:00:00+00:00"
    second = _record_at(monkeypatch, p, "2026-10-08T09:10:00+00:00", _seen("EFA"))
    assert second["note"] == first["note"]
    assert second["note_utc"] == "2026-10-08T09:00:00+00:00"
    assert second["ts_utc"] == "2026-10-08T09:10:00+00:00"


def test_a_note_older_than_36_hours_is_dropped(tmp_path, monkeypatch):
    p = tmp_path / dg.QUARANTINE_LEDGER
    _record_at(monkeypatch, p, "2026-10-08T09:00:00+00:00", _seen("EFA"),
               ledger_text="{not json")
    inside = _record_at(monkeypatch, p, "2026-10-09T20:59:00+00:00", _seen("EFA"))
    assert "note" in inside
    gone = _record_at(monkeypatch, p, "2026-10-09T21:01:00+00:00", _seen("EFA"))
    assert "note" not in gone and "note_utc" not in gone


def test_a_new_note_replaces_the_carried_one(tmp_path, monkeypatch):
    p = tmp_path / dg.QUARANTINE_LEDGER
    _record_at(monkeypatch, p, "2026-10-08T09:00:00+00:00", _seen("EFA"),
               ledger_text="{not json")
    again = _record_at(monkeypatch, p, "2026-10-08T10:00:00+00:00", _seen("EFA"),
                       ledger_text="{still not json")
    assert again["note_utc"] == "2026-10-08T10:00:00+00:00"


def test_two_consecutive_writes_leave_no_temp_file(tmp_path):
    p = tmp_path / "logs" / dg.LOOP_LEDGER
    dg.write(p, "loop", [], T0)
    dg.write(p, "loop", [], T1)
    assert sorted(f.name for f in p.parent.iterdir()) == [dg.LOOP_LEDGER]
    assert not list(p.parent.glob("*.tmp"))
