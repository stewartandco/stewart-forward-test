"""Tests for tools.survivorship_templates (fixtures only; never the real registry)."""
import copy as _copy
import json
from pathlib import Path

import pytest

from pipeline.common import content_id
from tools import survivorship_templates as st


def _spec(name, asset, blocks, tf="1d", sid=None):
    s = {"blocks": blocks, "cost_model": {"fee": 0.001}, "created_utc": "2026-01-01T00:00:00Z",
         "family": "fam", "generator": "g", "name": name, "provenance": {},
         "universe": {"asset_class": "crypto", "assets": [asset], "session": "24x7",
                      "timeframe": tf}, "version": 1}
    s["strategy_id"] = sid or content_id(s, "strategy_id")
    return s


def _entry(etype, payload):
    return {"entry_type": etype, "payload": payload, "prev_entry_hash": "x",
            "ts_utc": "2026-01-01T00:00:00Z", "version": 1}


def _write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


B1 = [{"id": "a", "params": {"x": 1, "y": 2}}]
B1_REORDERED = [{"params": {"y": 2, "x": 1}, "id": "a"}]


def test_refuses_dir_with_registry_log_d(tmp_path):
    (tmp_path / "registry_log.d").mkdir()
    f = _write(tmp_path / "copy.jsonl", [])
    with pytest.raises(st.LiveChainRefused):
        st.refuse_live(f)


def test_refuses_layer_chain():
    layer = Path(st.__file__).resolve().parent.parent
    with pytest.raises(st.LiveChainRefused):
        st.refuse_live(layer / "registry_log.jsonl")


def test_accepts_scratch_copy(tmp_path):
    f = _write(tmp_path / "copy.jsonl", [])
    st.refuse_live(f)


def test_freeze_ignores_later_registrations(tmp_path):
    specs = [_spec(f"s{i}", "AAAUSDT", B1) for i in range(3)]
    for i, s in enumerate(specs):
        s["universe"]["assets"] = [f"A{i}USDT"]
        s["strategy_id"] = content_id(s, "strategy_id")
    f = _write(tmp_path / "c.jsonl", [_entry("strategy_registered", s) for s in specs])
    c = st.read_copy(f, upto=2)
    assert set(c.specs) == {specs[0]["strategy_id"], specs[1]["strategy_id"]}
    assert c.n_entries == 3
    assert len(st.read_copy(f, None).specs) == 3


def test_screen_notes_and_filters(tmp_path):
    s = _spec("s", "AAAUSDT", B1)
    multi = _spec("m", "AAAUSDT", B1)
    multi["universe"]["assets"] = ["A", "B"]
    multi["strategy_id"] = content_id(multi, "strategy_id")
    eq = _spec("e", "AAAUSDT", B1)
    eq["universe"]["asset_class"] = "equity"
    eq["strategy_id"] = content_id(eq, "strategy_id")
    v = lambda stage: {"strategy_id": s["strategy_id"], "stage": stage, "verdict": "pass",
                       "metrics": {}, "artifacts_hash": "h"}
    f = _write(tmp_path / "c.jsonl", [
        _entry("strategy_registered", s), _entry("strategy_registered", multi),
        _entry("strategy_registered", eq), _entry("verdict", v("screened")),
        _entry("verdict", v("other")), _entry("note", {"text": "hi"}),
        _entry("verdict", v("screened"))])
    c = st.read_copy(f, upto=6)
    assert set(c.specs) == {s["strategy_id"]}
    assert s["strategy_id"] in c.screen
    assert c.notes == [(5, "hi")]
    assert st.read_copy(f, upto=0).screen == {}
    assert st.read_copy(f, upto=0).notes == [(5, "hi")]


def test_templates_group_by_blocks_not_params_order(tmp_path):
    a = _spec("a", "AUSDT", B1)
    b = _spec("b", "BUSDT", B1_REORDERED)
    d = _spec("d", "DUSDT", B1, tf="4h")
    f = _write(tmp_path / "c.jsonl", [_entry("strategy_registered", x) for x in (a, b, d)])
    c = st.read_copy(f, None)
    t = st.templates(c, "1d")
    assert len(t) == 1
    assert list(t.values())[0] == sorted([a["strategy_id"], b["strategy_id"]])
    assert list(st.templates(c, "4h").values())[0] == [d["strategy_id"]]


def _copy_of(tmp_path, specs):
    f = _write(tmp_path / "c.jsonl", [_entry("strategy_registered", x) for x in specs])
    return st.read_copy(f, None)


def test_cell_spec_verbatim_on_own_cell(tmp_path):
    a = _spec("a", "AUSDT", B1)
    c = _copy_of(tmp_path, [a])
    tkey, sids = next(iter(st.templates(c, "1d").items()))
    out = st.cell_spec(c, tkey, sids, "AUSDT", "1d")
    assert out == a and out["strategy_id"] == a["strategy_id"]


def test_cell_spec_transplant_id_is_content_id(tmp_path):
    a = _spec("a", "AUSDT", B1)
    c = _copy_of(tmp_path, [a])
    tkey, sids = next(iter(st.templates(c, "1d").items()))
    before = _copy.deepcopy(c.specs)
    out = st.cell_spec(c, tkey, sids, "ZUSDT", "1d")
    assert c.specs == before
    assert out["universe"]["assets"] == ["ZUSDT"]
    assert out["strategy_id"] == content_id(out, "strategy_id")
    assert out["strategy_id"] != a["strategy_id"]
    assert "ZUSDT".replace("USDT", "") in out["name"]
    strip = lambda s: {k: v for k, v in s.items() if k not in ("name", "strategy_id")} | {
        "universe": {k: v for k, v in s["universe"].items() if k != "assets"}}
    assert strip(out) == strip(a)



def test_cell_spec_verbatim_is_a_deep_copy(tmp_path):
    # final review M-f: a caller mutating the spec must not change the frozen copy
    a = _spec("a", "AUSDT", B1)
    c = _copy_of(tmp_path, [a])
    tkey, sids = next(iter(st.templates(c, "1d").items()))
    out = st.cell_spec(c, tkey, sids, "AUSDT", "1d")
    before = _copy.deepcopy(c.specs)
    out["universe"]["assets"].append("ZUSDT")
    out["blocks"][0]["params"]["x"] = 1
    assert c.specs == before
