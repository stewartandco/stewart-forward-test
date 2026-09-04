"""D15 exit rules v7, composer side (plan Task 3): version-2 stamps, retired
types refused by validate_family, the new stop/exit types sweepable, fx-safe
stops, fingerprint carrying the version only when it is not 1,
v7_compliant_as_is, grammar_summary's retired line, prompt rules.

Kept in its own file (not test_exit_rules_v7.py, which Task 2 owns) so the
two tasks can be built concurrently and cherry-picked without a merge.
Run: python -m pytest pipeline/test_exit_rules_v7_composer.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

from .composer import (SWEEPABLE_TYPES, RANGE_REQUIRING, SPEC_VERSION, SYSTEM_PROMPT,
                       composition_fingerprint, validate_family, grammar_summary,
                       v7_compliant_as_is, expand_family, expand_family_for_class,
                       system_prompt_for)
from .blocks import RETIRED_TYPES
from . import cells

SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "strategy_spec.schema.json"

NEW_STOPS = {("stop", "swing_stop"), ("stop", "ma_stop"), ("stop", "channel_stop"), ("stop", "band_stop")}
NEW_EXITS = {("exit", "ma_crossunder"), ("exit", "channel_exit"), ("exit", "zscore_revert"),
             ("exit", "tstat_decay"), ("exit", "regime_flip")}


def test_sweepable_and_range_requiring_include_the_new_types():
    assert NEW_STOPS | NEW_EXITS <= SWEEPABLE_TYPES
    assert {"swing_stop", "channel_stop", "channel_exit"} <= RANGE_REQUIRING
    assert RANGE_REQUIRING == set(cells.FX_EXCLUDED_BLOCK_TYPES)         # still pinned equal
    # close-based stops and exits stay ALLOWED for single-fix (fx) bars: with
    # pct_stop retired they are the stops an fx family has.
    assert {"ma_stop", "band_stop", "ma_crossunder", "zscore_revert",
            "tstat_decay", "regime_flip"}.isdisjoint(RANGE_REQUIRING)


def test_retired_types_are_never_sweepable():
    assert set(RETIRED_TYPES).isdisjoint(SWEEPABLE_TYPES)


def fam(blocks, sweep=None):
    return {"family": "d15_fam", "assets": ["BTCUSD"], "card_ids": ["c1"], "regime_hypothesis": "x",
            "blocks": blocks, "sweep": sweep or []}


ENTRY = {"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}}
RISK = {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}


def test_validate_family_refuses_retired_types_and_allows_zero_or_many_exits():
    ok = fam([ENTRY,
              {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
              {"role": "exit", "type": "ma_crossunder", "params": {"fast": 8, "slow": 80}},
              {"role": "exit", "type": "regime_flip", "params": {"ma_len": 100}},
              RISK])
    assert validate_family(ok, {"c1"}, 60) == []
    no_exit = fam([ENTRY, {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}}, RISK])
    assert validate_family(no_exit, {"c1"}, 60) == []
    bad = fam([ENTRY,
               {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
               {"role": "exit", "type": "time_stop", "params": {"max_bars": 20}},
               RISK])
    errs = validate_family(bad, {"c1"}, 60)
    assert sum("retired" in e for e in errs) == 2, errs
    # a retired stop is the family's ONLY stop: still counts as a stop block
    # for the role check (one error per retired block, not two)
    assert not any("at least one stop" in e for e in errs), errs


def test_validate_family_refuses_retired_types_on_a_sweep_axis_too():
    bad = fam([ENTRY, {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}}, RISK],
              sweep=[{"block": 1, "param": "pct", "values": [0.05, 0.10, 0.15]}])
    errs = validate_family(bad, {"c1"}, 60)
    assert any("retired" in e for e in errs), errs
    assert any("not sweepable" in e for e in errs), errs


def test_new_types_are_sweepable_in_a_family():
    f = fam([ENTRY, {"role": "stop", "type": "band_stop", "params": {"lookback": 40, "mult": 2.0}},
             {"role": "exit", "type": "regime_flip", "params": {"ma_len": 100}}, RISK],
            sweep=[{"block": 1, "param": "mult", "values": [1.5, 2.0, 2.5]},
                   {"block": 2, "param": "ma_len", "values": [50, 100, 150]}])
    assert validate_family(f, {"c1"}, 60) == []


def test_fx_family_keeps_close_based_stops_and_loses_range_stops():
    fx = {"family": "fx_d15", "assets": ["EUR"], "card_ids": ["c1"], "regime_hypothesis": "x", "sweep": []}
    good = {**fx, "blocks": [ENTRY, {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
                             {"role": "exit", "type": "zscore_revert", "params": {"lookback": 40, "z_exit": 0.5}},
                             RISK]}
    assert validate_family(good, {"c1"}, 60, excluded_types=RANGE_REQUIRING, asset_class="fx") == []
    for stop in ({"role": "stop", "type": "swing_stop", "params": {"lookback": 20}},
                 {"role": "stop", "type": "channel_stop", "params": {"lookback": 55}}):
        bad = {**fx, "blocks": [ENTRY, stop, RISK]}
        errs = validate_family(bad, {"c1"}, 60, excluded_types=RANGE_REQUIRING, asset_class="fx")
        assert any("excluded for class 'fx'" in e for e in errs), errs
    bad_exit = {**fx, "blocks": [ENTRY, {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
                                 {"role": "exit", "type": "channel_exit", "params": {"lookback": 20}}, RISK]}
    errs = validate_family(bad_exit, {"c1"}, 60, excluded_types=RANGE_REQUIRING, asset_class="fx")
    assert any("channel_exit" in e and "excluded" in e for e in errs), errs


def test_expanded_specs_are_version_2():
    assert SPEC_VERSION == 2
    f = fam([ENTRY, {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}}, RISK],
            sweep=[{"block": 1, "param": "ma_len", "values": [20, 50, 100]}])
    specs = expand_family(f, "2026-09-03-test", "m", "2026-09-03T00:00:00Z")
    assert len(specs) == 3 and all(s["version"] == 2 for s in specs)
    fx = {**f, "assets": ["EUR"]}
    specs = expand_family_for_class(fx, "2026-09-03-test", "m", "2026-09-03T00:00:00Z", "fx")
    assert specs and all(s["version"] == 2 for s in specs)


def test_spec_schema_admits_both_versions():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["version"] == {"enum": [1, 2]}


def test_fingerprint_includes_version_but_is_unchanged_for_version_1():
    s1 = {"version": 1, "universe": {"assets": ["BTCUSD"], "timeframe": "1d", "asset_class": "crypto", "session": "24x7"},
          "blocks": [{"role": "entry", "type": "ma_cross", "params": {"fast": 10, "slow": 50}},
                     {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}},
                     {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}]}
    legacy_shape = {k: v for k, v in s1.items() if k != "version"}       # every chained spec has version 1
    assert composition_fingerprint(legacy_shape) == composition_fingerprint(s1)
    # literal pin: the version-1 fingerprint must be byte-for-byte what the chain already holds
    # (a version leaking into a v1 fingerprint would break every D9 re-trial lookup)
    assert composition_fingerprint(s1) == "fde922c180e46f62fa4d11af6c9dac832347f49167b5ed81369640296d601426"
    assert composition_fingerprint({**s1, "version": 2}) != composition_fingerprint(s1)
    # the v1 fingerprint is byte-for-byte the pre-D15 core (version never enters it)
    assert composition_fingerprint(s1) == "%s" % composition_fingerprint({**s1, "version": 1})


def test_v7_compliant_as_is_classifies_legacy_specs():
    base = {"version": 1, "universe": {"assets": ["BTCUSD"], "timeframe": "1d"},
            "blocks": [{"role": "entry", "type": "trend_scan_dense", "params": {"max_lookback": 60, "t_min": 2.0, "direction": "long"}},
                       {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 2.0}},
                       {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}]}
    assert v7_compliant_as_is(base) is True
    assert v7_compliant_as_is({**base, "blocks": base["blocks"] + [{"role": "exit", "type": "time_stop", "params": {"max_bars": 40}}]}) is False
    assert v7_compliant_as_is({**base, "blocks": [{"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}}] + base["blocks"]}) is False
    assert v7_compliant_as_is({**base, "blocks": [{"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}}] + base["blocks"][1:]}) is False
    assert v7_compliant_as_is({**base, "blocks": [{"role": "entry", "type": "ma_cross", "params": {"fast": 10, "slow": 50}}] + base["blocks"][1:]}) is False


def test_grammar_summary_omits_retired_types_and_names_them():
    text = grammar_summary()
    assert "- exit/time_stop:" not in text and "- stop/pct_stop:" not in text
    assert "retired" in text and "time_stop" in text and "pct_stop" in text
    assert "- exit/ma_crossunder:" in text and "- stop/ma_stop:" in text
    for role, btype in NEW_STOPS | NEW_EXITS:
        assert f"- {role}/{btype}:" in text


def test_every_prompt_carries_the_d15_exit_rule():
    prompts = {"crypto": SYSTEM_PROMPT}
    for cls in ("fx", "equity_etf", "bond_etf", "metal_etf"):
        prompts[cls] = system_prompt_for(cls)
    for cls, text in prompts.items():
        assert "EXITS (D15" in text, cls
        assert "NEVER a time stop" in text, cls
        assert "crossunder" in text, cls
        assert "time_stop" not in text.replace("NEVER a time stop", ""), cls
    # fx has no real high/low, so its prompt names the stops it actually has
    assert "ma_stop" in prompts["fx"] and "band_stop" in prompts["fx"]
    for excluded in ("swing_stop", "channel_stop", "channel_exit"):
        assert excluded in prompts["fx"]          # named as EXCLUDED, not offered


def test_registered_fingerprints_records_the_v2_form_of_a_compliant_as_is_legacy_spec(tmp_path):
    """D15(b), design s2: a version-2 proposal of the SAME blocks as a compliant-as-is
    legacy registration must collide (same trial); a non-compliant legacy spec
    (retired type / ma_cross entry) gets no v2 form (its re-trial is a new engine run)."""
    from .composer import registered_fingerprints, composition_fingerprint
    from .registry import Registry
    import json
    base_u = {"assets": ["BTCUSD"], "timeframe": "1d", "asset_class": "crypto", "session": "24x7"}
    ok = {"strategy_id": "1" * 16, "version": 1, "universe": base_u,
          "blocks": [{"role": "entry", "type": "trend_scan_dense", "params": {"max_lookback": 60, "t_min": 2.0, "direction": "long"}},
                     {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 2.0}},
                     {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}]}
    bad = {"strategy_id": "2" * 16, "version": 1, "universe": base_u,
           "blocks": [{"role": "entry", "type": "ma_cross_dense", "params": {"fast": 8, "slow": 80, "direction": "long"}},
                      {"role": "stop", "type": "atr_stop_dense", "params": {"atr_len": 14, "mult": 2.0}},
                      {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}}]}
    log = tmp_path / "registry_log.jsonl"
    log.write_text("".join(json.dumps({"entry_type": "strategy_registered", "payload": p}) + "\n" for p in (ok, bad)), encoding="utf-8")
    fps = registered_fingerprints(Registry(log))
    assert fps[composition_fingerprint(ok)] == ok["strategy_id"]
    assert fps[composition_fingerprint({**ok, "version": 2})] == ok["strategy_id"]        # v2 form recorded
    assert fps[composition_fingerprint(bad)] == bad["strategy_id"]
    assert composition_fingerprint({**bad, "version": 2}) not in fps                       # re-trial stays admissible
