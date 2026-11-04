"""cells.RETIRED_CELLS (2b-3 plan 3, Task 6): a delisted or dead cell stops
blocking registry-wide comparability.

The declaration is code, in a reviewed commit next to the Coen-gated
retirement note. These tests pin the mechanism; RETIRED_CELLS itself is empty
as committed, and with it empty nothing here may change any behaviour.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from . import (cells, crypto_refresh, feed_health, gauntlet_stats as gs, loop,
               qcells, quarantine, screen)
from .test_loop import (FakeRunner, _layer_with_two_registered_cells, _modules,
                        _read_status, _FULL_SEQUENCE)
# the autouse fixture that stubs the live-task window reader and points the
# producer root nowhere: imported so it applies to this module's loop tests
from .test_loop import _no_real_schtasks  # noqa: F401
# the autouse fixture that stubs the live-task window reader and points the
# producer root nowhere: imported so it applies to this module's loop tests
from .test_loop import _no_real_schtasks  # noqa: F401

_ENTRY = {"retired_utc": "2026-10-20", "note_prefix": "cell-retirement-v1"}


def _retire(monkeypatch, *keys):
    monkeypatch.setattr(cells, "RETIRED_CELLS", {k: dict(_ENTRY) for k in keys})


# ---------------- the declaration and its import-time check ----------------

def test_retired_cells_is_empty_as_committed():
    assert cells.RETIRED_CELLS == {}
    assert cells.retired_cell_ids() == frozenset()
    assert cells.is_retired("BTCUSDT") is False


def test_a_retired_cell_cannot_be_active():
    # BTCUSDT 1d is in ACTIVE_CELLS["crypto"]: retiring its bare key must raise
    with pytest.raises(AssertionError, match="BTCUSDT"):
        cells.assert_retired_cells_valid({"BTCUSDT": dict(_ENTRY)})
    # the fx class is "all": any of its cells is active too
    fx_asset = cells.CLASSES["fx"]["assets"][0]
    with pytest.raises(AssertionError, match=fx_asset):
        cells.assert_retired_cells_valid({fx_asset: dict(_ENTRY)})


def test_a_retired_cell_that_is_declared_but_not_active_is_accepted():
    # ENAUSDT is declared but outside the 76 Switch 1 admission (first bar
    # 2024); a BTCUSDT 15m cell is declared but not active either (1h/4h/12h
    # became active at Switch 2; 15m and 30m are phase 3)
    assert "ENAUSDT" in cells.CLASSES["crypto"]["assets"]
    assert ("ENAUSDT", "1d") not in cells.active_cells("crypto")
    assert ("BTCUSDT", "15m") not in cells.active_cells("crypto")
    cells.assert_retired_cells_valid({"ENAUSDT": dict(_ENTRY)})
    cells.assert_retired_cells_valid({"BTCUSDT@15m": dict(_ENTRY)})
    cells.assert_retired_cells_valid({})


@pytest.mark.parametrize("key,entry,needle", [
    ("NOTACELLUSDT", dict(_ENTRY), "declared"),
    ("BTCUSDT@7m", dict(_ENTRY), "timeframe"),
    ("BTCUSDT@1d", dict(_ENTRY), "bad cell key"),
    ("@1h", dict(_ENTRY), "bad cell key"),
    ("BTCUSDT@1h", {"retired_utc": "2026-13-40", "note_prefix": "x"}, "retired_utc"),
    ("BTCUSDT@1h", {"retired_utc": "2026-10-20"}, "note_prefix"),
    ("BTCUSDT@1h", {"retired_utc": "2026-10-20", "note_prefix": ""}, "note_prefix"),
    ("BTCUSDT@1h", "retired", "must be a dict"),
])
def test_a_malformed_retirement_is_refused(key, entry, needle):
    with pytest.raises(AssertionError, match=needle):
        cells.assert_retired_cells_valid({key: entry})


def test_the_check_runs_at_import_against_the_committed_declaration():
    # the module-level call is the import-time gate; calling it again on the
    # live (empty) declaration is a no-op that must not raise
    cells.assert_retired_cells_valid()


def test_the_key_parse_matches_qcells():
    for asset in cells.CLASSES["crypto"]["assets"][:5]:
        for tf in cells.CLASSES["crypto"]["timeframes"]:
            key = qcells.cell_key(asset, tf)
            assert cells._parse_retired_key(key) == qcells.split_cell_key(key) == (asset, tf)
    assert qcells.SEP == cells._KEY_SEP


def test_retired_cell_ids_are_the_cell_id_format_screen_uses(monkeypatch):
    _retire(monkeypatch, "ETHUSDT", "BTCUSDT@1h")
    assert cells.retired_cell_ids() == {cells.cell_id("ETHUSDT", "1d"),
                                        cells.cell_id("BTCUSDT", "1h")}
    assert cells.is_retired("ETHUSDT") and cells.is_retired("BTCUSDT@1h")
    assert not cells.is_retired("BTCUSDT")


# ---------------- screen.assert_cells_comparable ----------------

_DEAD = {"BTCUSDT_1d": "2026-10-20", "ETHUSDT_1d": "2026-10-20",
         "LTCUSDT_1d": "2026-09-20"}                  # LTC: dead 30 days
_CLS = {c: "crypto" for c in _DEAD}


def test_a_non_retired_dead_cell_still_refuses():
    with pytest.raises(ValueError, match="LTCUSDT_1d"):
        screen.assert_cells_comparable(dict(_DEAD), class_of=_CLS)
    with pytest.raises(ValueError, match="LTCUSDT_1d"):
        screen.assert_cells_comparable(dict(_DEAD))


def test_a_retired_dead_cell_does_not_make_cells_incomparable(monkeypatch):
    with pytest.raises(ValueError):                            # without
        screen.assert_cells_comparable(dict(_DEAD), class_of=_CLS)
    _retire(monkeypatch, "LTCUSDT")                            # with
    screen.assert_cells_comparable(dict(_DEAD), class_of=_CLS)
    screen.assert_cells_comparable(dict(_DEAD))


def test_retiring_one_cell_does_not_excuse_another(monkeypatch):
    _retire(monkeypatch, "ETHUSDT")
    with pytest.raises(ValueError, match="LTCUSDT_1d"):
        screen.assert_cells_comparable(dict(_DEAD), class_of=_CLS)


def test_a_retired_cell_is_dropped_by_cell_id_not_by_asset(monkeypatch):
    # retiring BTCUSDT@1h must leave BTCUSDT_1d (a different cell) subject to
    # the rule
    _retire(monkeypatch, "BTCUSDT@1h")
    with pytest.raises(ValueError, match="BTCUSDT_1d"):
        screen.assert_cells_comparable({"BTCUSDT_1d": "2026-09-01",
                                        "ETHUSDT_1d": "2026-10-20"})


def test_the_input_dicts_are_not_mutated(monkeypatch):
    _retire(monkeypatch, "LTCUSDT")
    data_end, class_of = dict(_DEAD), dict(_CLS)
    screen.assert_cells_comparable(data_end, class_of=class_of)
    assert data_end == _DEAD and class_of == _CLS


def test_with_the_declaration_empty_the_verdicts_and_messages_are_unchanged():
    """Pin: the empty declaration changes no verdict and no message. The
    expected outcomes are the pre-Task-6 behaviour, written out."""
    assert cells.RETIRED_CELLS == {}

    def outcome(data_end, class_of=None):
        try:
            screen.assert_cells_comparable(dict(data_end), class_of=class_of)
        except ValueError as exc:
            return str(exc)
        return None

    assert outcome({"BTCUSDT_1h": "2026-08-15 23:00:00",
                    "BTCUSDT_4h": "2026-08-15 20:00:00"}) is None
    assert outcome({}) is None
    assert outcome({"BTCUSD_1d": "2026-08-18"}) is None
    assert outcome({"BTCUSD_1d": "2026-08-18", "XRPUSDT_15m": ""}) == (
        "cell(s) loaded with no bars, so their data end is unknown: "
        "XRPUSDT_15m. An empty cell must not compare equal to every other cell.")
    assert outcome({"BTCUSDT_1h": "2026-08-15 23:00:00",
                    "SOLUSDT_1d": "2026-08-01 00:00:00"}) == (
        "cells stop on different days, so they cannot be compared: "
        "2026-08-01: SOLUSDT_1d; 2026-08-15: BTCUSDT_1h. Re-fetch to a common "
        "end date; excluding the short cells would quietly shrink the reported "
        "search space.")
    msg = outcome(_DEAD, _CLS)
    assert msg is not None and "within class 'crypto'" in msg
    assert "2026-09-20: LTCUSDT_1d" in msg
    assert outcome({"AUD_1d": "2026-09-01", "BTCUSD_1d": "2026-09-30"},
                   {"AUD_1d": "fx", "BTCUSD_1d": "crypto"}) is not None


def test_comparable_cells_still_lists_a_retired_cell(monkeypatch):
    spec = {"universe": {"assets": ["LTCUSDT", "ETHUSDT"], "asset_class": "crypto",
                         "timeframe": "1d"}}
    before = screen.comparable_cells([spec])
    _retire(monkeypatch, "LTCUSDT")
    assert screen.comparable_cells([spec]) == before
    assert ("LTCUSDT", "1d") in before[0]
    assert "LTCUSDT_1d" in before[1]


# ---------------- the loop preflight ----------------

def test_the_preflight_does_not_park_on_a_retired_dead_cell(tmp_path, monkeypatch):
    """test_loop.py's stale_data fixture: fired class fx, AUD ends 08-21 while
    sibling CAD ends 09-10 -- parks without a retirement, runs with AUD retired."""
    _retire(monkeypatch, "AUD")
    layer = _layer_with_two_registered_cells(tmp_path, "2026-08-21 00:00:00",
                                             "2026-09-10 00:00:00",
                                             fx2_end="2026-09-10 00:00:00")
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    assert rc == 0
    assert _modules(fr) == _FULL_SEQUENCE
    assert _read_status(layer)["items"]["outcome"] == "cycle_complete"


def test_the_preflight_still_parks_a_non_retired_dead_cell(tmp_path):
    layer = _layer_with_two_registered_cells(tmp_path, "2026-08-21 00:00:00",
                                             "2026-09-10 00:00:00",
                                             fx2_end="2026-09-10 00:00:00")
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 1
    assert _modules(fr) == []
    assert _read_status(layer)["items"]["outcome"] == "stale_data"


def test_a_retired_laggard_is_not_named_when_another_cell_is_the_breach(tmp_path, monkeypatch):
    """The retired AUD (08-21) must not be reported as a stale FIRED cell just
    because a genuine breach elsewhere (crypto BTCUSD ends 08-01, a cross-class
    gap) sends the preflight to _lagging_cells."""
    _retire(monkeypatch, "AUD")
    layer = _layer_with_two_registered_cells(tmp_path, "2026-08-21 00:00:00",
                                             "2026-08-01 00:00:00",
                                             fx2_end="2026-09-10 00:00:00")
    fr = FakeRunner()
    rc = loop.run(["--once", "--layer", str(layer)], runner=fr)
    status = _read_status(layer)
    assert "AUD_1d" not in status["items"].get("stale_fired_cells", "")
    assert "AUD_1d" not in status["items"].get("stale_other_cells", "")
    assert rc == 0 and status["items"]["outcome"] == "cycle_complete"
    assert status["items"]["stale_other_cells"] == "BTCUSD_1d"


def test_lagging_cells_ignores_a_retired_cell(monkeypatch):
    ends = {"AUD_1d": "2026-08-21", "CAD_1d": "2026-09-10"}
    cls = {"AUD_1d": "fx", "CAD_1d": "fx"}
    assert loop._lagging_cells(ends, cls) == {"AUD_1d"}
    _retire(monkeypatch, "AUD")
    assert loop._lagging_cells(ends, cls) == set()


# ---------------- gauntlet_stats ----------------

def _dead_fx_cell_registry(tmp_path):
    """The mixed registry (crypto BTCUSD, fx GBP + EUR) judged by the worker on
    healthy bars, then EUR's file cut back to 2020-05-29 -- a dead fx cell next
    to a live sibling, which stats must refuse, or, once EUR is retired,
    simulate."""
    from .test_gauntlet_classes import mixed_class_gauntlet_registry, _daily_bars, _weekday_bars
    from .test_gauntlet_stats import _amend, _walk_weekday_bars
    from .test_gauntlet_worker import V61
    from .test_screen import write_data_dir
    from . import gauntlet_worker as gw
    d = tmp_path / "dead"
    d.mkdir()
    reg, _, _, _ = mixed_class_gauntlet_registry(d)
    reg.append("note", {"text": V61})
    _amend(reg)
    data = write_data_dir(d, {
        "BTCUSD": _daily_bars(dt.date(2020, 1, 1), dt.date(2020, 12, 31)),
        "GBP": _walk_weekday_bars(dt.date(2015, 1, 1), dt.date(2020, 12, 31)),
        "EUR": _weekday_bars(dt.date(2020, 1, 1), dt.date(2020, 12, 31))})
    cutoff = "2020-06-30"
    assert gw.run(["--registry", str(reg.log_path), "--data-dir", str(data),
                   "--artifacts-dir", str(d / "art"), "--logs-dir", str(d / "logs"),
                   "--cutoff", cutoff, "--no-perturb", "--max-workers", "1"]) == 0
    eur = Path(data) / "EUR_1d.csv"
    lines = eur.read_text(encoding="utf-8").splitlines()
    keep = [ln for i, ln in enumerate(lines)
            if i == 0 or ln.split(",", 1)[0][:10] <= "2020-05-29"]
    assert 1 < len(keep) < len(lines)
    eur.write_text("\n".join(keep) + "\n", encoding="utf-8")
    args = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--logs-dir", str(d / "logs"), "--cutoff", cutoff]
    return d, args


def _stats_status(d):
    return json.loads((d / "logs" / "gauntlet_stats_status.json").read_text(encoding="utf-8"))


def test_gauntlet_stats_refuses_on_a_non_retired_dead_cell(tmp_path, capsys):
    d, args = _dead_fx_cell_registry(tmp_path)
    assert gs.run(args) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert _stats_status(d)["exit_reason"] == "cells_not_comparable"


def test_gauntlet_stats_does_not_refuse_on_a_retired_dead_cell(tmp_path, monkeypatch, capsys):
    d, args = _dead_fx_cell_registry(tmp_path)
    _retire(monkeypatch, "EUR")
    assert gs.run(args) == 0
    assert "REFUSED" not in capsys.readouterr().out
    assert _stats_status(d)["exit_reason"] == "done"


# ---------------- the detectors (Tasks 2 and 3) ----------------

def test_retired_cells_are_skipped_by_the_detectors(tmp_path, monkeypatch):
    calls = []

    def spy(data_dir, keys, **kw):
        calls.append((list(keys), set(kw.get("stale_keys") or ())))
        return []

    monkeypatch.setattr(feed_health, "scan", spy)
    _retire(monkeypatch, "ETHUSDT", "BTCUSDT@1h")

    # crypto_refresh: a 1d key is the bare asset
    crypto_refresh._feed_health_items(
        tmp_path, [("BTCUSDT", "1d"), ("ETHUSDT", "1d")],
        {"fetched": {"BTCUSDT_1d": 1, "ETHUSDT_1d": 1}}, 1_800_000_000_000)
    assert calls[-1] == (["BTCUSDT"], {"BTCUSDT"})

    # quarantine fetch-owed: keys are ASSET@TF
    quarantine._feed_health_items(
        tmp_path, ["BTCUSDT@1h", "ETHUSDT@1h", "SOLUSDT@1h"],
        {"BTCUSDT@1h", "SOLUSDT@1h"}, 1_800_000_000_000)
    assert calls[-1] == (["ETHUSDT@1h", "SOLUSDT@1h"], {"SOLUSDT@1h"})


def test_the_detectors_pass_every_key_with_nothing_retired(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(feed_health, "scan",
                        lambda d, keys, **kw: seen.append(list(keys)) or [])
    crypto_refresh._feed_health_items(
        tmp_path, [("BTCUSDT", "1d"), ("ETHUSDT", "1d")], {"fetched": {}}, 1_800_000_000_000)
    quarantine._feed_health_items(tmp_path, ["BTCUSDT@1h", "ETHUSDT@1h"], set(), 1_800_000_000_000)
    assert seen == [["BTCUSDT", "ETHUSDT"], ["BTCUSDT@1h", "ETHUSDT@1h"]]


# ---------------- quarantine --fetch-owed (fix round 1) ----------------

_NOW_MS = 1_791_201_600_000          # 2026-10-05 12:00:00 UTC


def _owed_pair():
    """Two quarantined 1h strategies: BTCUSD@1h and ETHUSD@1h."""
    import json as _json
    from .test_intraday_statistics import _twin_spec
    a = _twin_spec("1h")
    b = _json.loads(_json.dumps(a))
    b["strategy_id"] = "b" * 16
    b["universe"]["assets"] = ["ETHUSD"]
    return a, b, {a["strategy_id"]: a, b["strategy_id"]: b}


def test_a_retired_owed_cell_is_never_fetched_but_its_sibling_is(tmp_path, monkeypatch, capsys):
    a, b, specs = _owed_pair()
    _retire(monkeypatch, "BTCUSD@1h")
    calls, sleeps = [], []

    def fetch(sym, tf, d, **kw):
        if sym == "BTCUSD":
            raise RuntimeError("delisted")      # would cost two 120 s retry passes
        calls.append((sym, tf))
        return {}

    items = quarantine.fetch_owed([a["strategy_id"], b["strategy_id"]], specs,
                                  tmp_path, fetch=fetch, sleep=sleeps.append,
                                  now_ms=_NOW_MS)
    assert calls == [("ETHUSD", "1h")]
    assert sleeps == []                          # no pacing (one cell), no retry wait
    assert not [i for i in items if i["source"] == quarantine.FETCH_SOURCE]
    out = capsys.readouterr().out
    assert "fetch-owed: BTCUSD@1h retired (2026-10-20); not fetched" in out
    assert "fetch-owed: ETHUSD@1h fetched" in out


def test_when_every_owed_cell_is_retired_nothing_is_fetched_and_nothing_sleeps(
        tmp_path, monkeypatch, capsys):
    a, b, specs = _owed_pair()
    _retire(monkeypatch, "BTCUSD@1h", "ETHUSD@1h")
    calls, sleeps = [], []
    items = quarantine.fetch_owed([a["strategy_id"], b["strategy_id"]], specs,
                                  tmp_path,
                                  fetch=lambda *x, **k: calls.append(x) or {},
                                  sleep=sleeps.append, now_ms=_NOW_MS)
    assert calls == [] and sleeps == [] and items == []
    out = capsys.readouterr().out
    assert out.count("retired (2026-10-20); not fetched") == 2
    assert "fetch-owed: no intraday cell owed" in out


def test_with_nothing_retired_every_owed_cell_is_fetched(tmp_path):
    a, b, specs = _owed_pair()
    calls = []
    quarantine.fetch_owed([a["strategy_id"], b["strategy_id"]], specs, tmp_path,
                          fetch=lambda sym, tf, d, **kw: calls.append(sym) or {},
                          sleep=lambda s: None, now_ms=_NOW_MS)
    assert sorted(calls) == ["BTCUSD", "ETHUSD"]
