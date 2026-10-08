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
