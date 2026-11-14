from __future__ import annotations

import hashlib
import json

import pytest

from pipeline.quarantine import run as quarantine_run
from pipeline.registry import Registry
from pipeline.test_gen3b import argv_for, flat_dated_bars
from pipeline.test_quarantine_isolation import btc_and_eth_solo, restate
from tools import quarantine_parity as qp

DATES = ("2023-01-22", "2023-01-23")


def chained_two_days(tmp_path):
    """Two strategies (BTCUSD-only, ETHUSD-only), both recorded on two dates
    by the real recorder, so every digest and row in a dump has a chained
    twin to be checked against."""
    reg, a, b, data = btc_and_eth_solo(tmp_path, eth_bars=flat_dated_bars())
    for d in DATES:
        assert quarantine_run(argv_for(reg, data, "--date", d)) == 0
    return reg, a, b, data


def dump(reg, data, out, *extra):
    rc = qp.main(["dump", "--registry", str(reg.log_path), "--data-dir", str(data),
                  "--out", str(out), *extra])
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


def chained_rows(reg, date):
    rows = [e["payload"] for e in reg.entries()
            if e["entry_type"] == "quarantine_decision" and e["payload"]["date"] == date]
    return sorted(rows, key=lambda r: (r["strategy_id"], r["asset"]))


def chained_snapshot(reg, date):
    return next(e["payload"] for e in reg.entries()
                if e["entry_type"] == "quarantine_data_snapshot"
                and e["payload"]["date"] == date)


# ------------------------------- dump ---------------------------------

def test_dump_reproduces_the_chained_rows_and_digests(tmp_path):
    reg, a, b, data = chained_two_days(tmp_path)
    d = dump(reg, data, tmp_path / "d.json")
    assert d["errors"] == {}
    assert sorted(d["results"]) == list(DATES)
    for date in DATES:
        r = d["results"][date]
        assert len(r["rows"]) == 2
        assert r["rows"] == chained_rows(reg, date)
        assert r["chain_rows_equal"] is True
        snap = chained_snapshot(reg, date)
        assert r["bars_sha256"] == snap["bars_sha256"]
        assert r["data_sha256"] == snap["data_sha256"]
        assert set(r["bars_sha256"]) == {"BTCUSD", "ETHUSD"}


def test_dump_meta_identifies_the_code_and_the_chain(tmp_path):
    reg, a, b, data = chained_two_days(tmp_path)
    d = dump(reg, data, tmp_path / "d.json", "--sample", "7", "--seed", "3")
    m = d["meta"]
    assert m["seed"] == 3 and m["sample_n"] == 7
    assert m["registry_sha256"] == hashlib.sha256(reg.log_path.read_bytes()).hexdigest()
    assert m["registry_entries"] == len(list(Registry(reg.log_path).entries()))
    assert m["qcells_module"] is True           # this tree has pipeline.qcells
    assert m["pipeline_file"].endswith("__init__.py")


def test_dump_samples_only_snapshot_dates_seeded(tmp_path):
    reg, a, b, data = chained_two_days(tmp_path)
    # bars exist for 2023-01-01..23 but only two dates carry a snapshot
    full = dump(reg, data, tmp_path / "full.json", "--sample", "60")
    assert sorted(full["results"]) == list(DATES)
    picks = set()
    for seed in range(12):
        one = dump(reg, data, tmp_path / f"s{seed}.json", "--sample", "1", "--seed", str(seed))
        assert len(one["results"]) == 1
        again = dump(reg, data, tmp_path / "again.json", "--sample", "1", "--seed", str(seed))
        assert list(again["results"]) == list(one["results"])     # deterministic
        picks |= set(one["results"])
    assert picks == set(DATES)                                    # and the seed matters


def test_dump_flags_but_keeps_a_date_whose_rows_no_longer_reproduce(tmp_path):
    """chain_rows_equal is information (an older engine may have written the
    rows), not an error: a restated close changes the recomputed row."""
    reg, a, b, data = chained_two_days(tmp_path)
    restate(data, "BTCUSD", "2023-01-23", "2023-01-23,50.0,50.0,50.0,50.0,1.0")
    d = dump(reg, data, tmp_path / "d.json")
    assert d["errors"] == {}
    assert d["results"]["2023-01-23"]["chain_rows_equal"] is False
    assert d["results"]["2023-01-22"]["chain_rows_equal"] is True


def test_dump_records_an_unreadable_date_as_an_error_and_omits_it(tmp_path):
    reg, a, b, data = chained_two_days(tmp_path)
    (data / "ETHUSD_1d.csv").unlink()
    d = dump(reg, data, tmp_path / "d.json")
    assert d["results"] == {}
    assert sorted(d["errors"]) == list(DATES)
    assert all("ETHUSD" in e for e in d["errors"].values())


def test_dump_never_writes_the_chain(tmp_path):
    reg, a, b, data = chained_two_days(tmp_path)
    before = reg.log_path.read_bytes()
    files = {p.name: p.read_bytes() for p in data.iterdir()}
    dump(reg, data, tmp_path / "d.json")
    assert reg.log_path.read_bytes() == before
    assert {p.name: p.read_bytes() for p in data.iterdir()} == files


# ------------------------------ compare -------------------------------

def good():
    return {"meta": {"pipeline_file": "/p/__init__.py", "qcells_module": True, "seed": 1,
                     "sample_n": 60, "registry_sha256": "abc", "registry_entries": 100},
            "results": {"2023-01-22": {"data_sha256": {"BTCUSD": "h1"},
                                       "bars_sha256": {"BTCUSD": "b1"},
                                       "rows": [{"strategy_id": "s", "asset": "BTCUSD",
                                                 "equity": 1.0}],
                                       "chain_rows_equal": True},
                        "2023-01-23": {"data_sha256": {"BTCUSD": "h2"},
                                       "bars_sha256": {"BTCUSD": "b2"},
                                       "rows": [], "chain_rows_equal": True}},
            "errors": {}}


def cmp(tmp_path, a, b):
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    return qp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")])


def test_compare_of_a_dump_with_itself_passes_and_names_both_trees(tmp_path, capsys):
    reg, a, b, data = chained_two_days(tmp_path)
    d = dump(reg, data, tmp_path / "d.json")
    capsys.readouterr()
    assert qp.main(["compare", str(tmp_path / "d.json"), str(tmp_path / "d.json")]) == 0
    out = capsys.readouterr().out
    assert "compared 2; mismatches 0" in out
    assert out.count(d["meta"]["pipeline_file"]) == 2
    assert out.count("qcells_module: True") == 2


def test_compare_prints_a_missing_qcells_module_as_false(tmp_path, capsys):
    x, y = good(), good()
    y["meta"]["qcells_module"] = False
    y["meta"]["pipeline_file"] = "/old/__init__.py"
    assert cmp(tmp_path, x, y) == 0
    out = capsys.readouterr().out
    assert "/old/__init__.py" in out and "qcells_module: False" in out


def test_compare_fails_when_one_row_value_differs(tmp_path, capsys):
    x, y = good(), good()
    y["results"]["2023-01-22"]["rows"][0]["equity"] = 1.0000001
    assert cmp(tmp_path, x, y) == 1
    out = capsys.readouterr().out
    assert "2023-01-22" in out and "mismatches 0" not in out


@pytest.mark.parametrize("path,value", [
    (("data_sha256", "BTCUSD"), "other"),
    (("bars_sha256", "BTCUSD"), "other"),
    (("chain_rows_equal",), False),
])
def test_compare_fails_when_a_digest_or_flag_differs(tmp_path, path, value):
    x, y = good(), good()
    node = y["results"]["2023-01-22"]
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    assert cmp(tmp_path, x, y) == 1


@pytest.mark.parametrize("key,value", [
    ("registry_sha256", "xyz"), ("seed", 2), ("sample_n", 61), ("registry_entries", 101)])
def test_compare_fails_when_the_sampling_or_chain_identity_differs(tmp_path, capsys, key, value):
    x, y = good(), good()
    y["meta"][key] = value
    assert cmp(tmp_path, x, y) == 1
    assert f"{key} differs" in capsys.readouterr().out


def test_compare_fails_when_either_side_has_no_meta(tmp_path):
    x, y = good(), good()
    del y["meta"]
    assert cmp(tmp_path, x, y) == 1
    assert cmp(tmp_path, y, x) == 1


@pytest.mark.parametrize("key", ["registry_sha256", "seed", "sample_n", "registry_entries"])
def test_compare_fails_when_a_required_meta_key_is_missing_on_both_sides(tmp_path, key):
    """two absent values must not read as 'equal'."""
    x, y = good(), good()
    del x["meta"][key]
    del y["meta"][key]
    assert cmp(tmp_path, x, y) == 1


def test_compare_fails_when_either_side_has_an_error_even_if_identical(tmp_path, capsys):
    x, y = good(), good()
    x["errors"] = {"2023-01-24": "ValueError('no BTCUSD bar')"}
    y["errors"] = dict(x["errors"])
    assert cmp(tmp_path, x, y) == 1
    out = capsys.readouterr().out
    assert "ValueError('no BTCUSD bar')" in out
    y2 = good()
    assert cmp(tmp_path, good(), dict(y2, errors={"d": "e"})) == 1
    assert cmp(tmp_path, dict(good(), errors={"d": "e"}), good()) == 1


def test_compare_fails_when_either_side_has_zero_results(tmp_path):
    x, y = good(), good()
    x["results"], y["results"] = {}, {}
    assert cmp(tmp_path, x, y) == 1            # identical emptiness is not parity
    assert cmp(tmp_path, good(), dict(good(), results={})) == 1
    assert cmp(tmp_path, dict(good(), results={}), good()) == 1


def test_compare_fails_when_the_date_sets_differ(tmp_path, capsys):
    x, y = good(), good()
    del y["results"]["2023-01-23"]
    assert cmp(tmp_path, x, y) == 1
    assert "2023-01-23" in capsys.readouterr().out


def test_compare_fails_on_a_malformed_or_unreadable_dump(tmp_path):
    x = good()
    (tmp_path / "a.json").write_text(json.dumps(x))
    (tmp_path / "b.json").write_text("{not json")
    assert qp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
    assert qp.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "nope.json")]) == 1
    y = good()
    del y["errors"]                              # a dump that never recorded its errors
    assert cmp(tmp_path, x, y) == 1


def test_compare_prints_only_the_first_five_differing_dates(tmp_path, capsys):
    x, y = good(), good()
    days = [f"2023-02-{i:02d}" for i in range(1, 9)]
    for d in days:
        x["results"][d] = {"rows": [], "v": 1}
        y["results"][d] = {"rows": [], "v": 2}
    assert cmp(tmp_path, x, y) == 1
    out = capsys.readouterr().out
    for d in days[:5]:
        assert d in out
    for d in days[5:]:
        assert d not in out
    assert "8 date(s) differ" in out


# --------------------------- end to end -------------------------------

def test_a_restated_bar_changes_the_digest_and_fails_the_compare(tmp_path, capsys):
    """Sensitivity: only the volume of one BTCUSD bar changes, so every row
    still reproduces; the digests are the only witnesses and the compare must
    still fail."""
    reg, a, b, data = chained_two_days(tmp_path)
    first = dump(reg, data, tmp_path / "first.json")
    restate(data, "BTCUSD", "2023-01-23", "2023-01-23,112,120,112,118,999.0")
    lines = (data / "BTCUSD_1d.csv").read_text().splitlines()
    assert any(l.startswith("2023-01-23,") and l.endswith("999.0") for l in lines)
    second = dump(reg, data, tmp_path / "second.json")
    r1, r2 = first["results"], second["results"]
    assert r1["2023-01-23"]["bars_sha256"]["BTCUSD"] != r2["2023-01-23"]["bars_sha256"]["BTCUSD"]
    assert r1["2023-01-22"]["bars_sha256"] == r2["2023-01-22"]["bars_sha256"]
    assert r1["2023-01-23"]["rows"] == r2["2023-01-23"]["rows"]
    capsys.readouterr()
    assert qp.main(["compare", str(tmp_path / "first.json"), str(tmp_path / "second.json")]) == 1
    out = capsys.readouterr().out
    # the whole-file digest moves for both dates; only 01-23's BARS digest moves
    assert "2 date(s) differ" in out and "2023-01-23" in out
    # and the same data twice is parity
    dump(reg, data, tmp_path / "third.json")
    assert qp.main(["compare", str(tmp_path / "second.json"), str(tmp_path / "third.json")]) == 0
