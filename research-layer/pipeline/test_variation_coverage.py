"""Tests for tools_variation_coverage_report.py (SP5 Task 6, D12).

Everything runs against a SYNTHETIC chain in tmp_path -- never the live
registry_log.jsonl. Fixture registrations are appended through the same
Registry class the tool reads with, mirroring test_crypto_backfill_report.py.

Grid-dependency note: tests 2-4 pin REAL grids from pipeline.blocks
BLOCK_TYPES (channel_breakout.lookback, zscore_reversion.z_entry,
r_multiple.r, time_stop.max_bars). If grid curation changes those grids the
pins break -- that is acceptable and honest: the fence moved, so the tests
that describe the fence must be re-read.
"""
import json

import pytest

from pipeline.registry import Registry

import tools_variation_coverage_report as tool


# -- fixture builders ------------------------------------------------------

def register(reg, sid, blocks, assets=("BTCUSD",), timeframe="1d"):
    """blocks: list of (role, type, params_dict)."""
    reg.append("strategy_registered", {
        "strategy_id": sid,
        "family": "fam",
        "universe": {"asset_class": "crypto", "assets": list(assets),
                     "session": "24x7", "timeframe": timeframe},
        "provenance": {"sibling_group_id": "fam-2026-08-28-test"},
        "blocks": [{"role": r, "type": t, "params": dict(p)}
                   for r, t, p in blocks],
    })


MA = ("entry", "ma_cross")            # fast [5, 10, 20], slow [50, 100, 200]
CB = ("entry", "channel_breakout")    # lookback [20, 55, 100]
ZS = ("entry", "zscore_reversion")    # z_entry [1.5, 2.0, 2.5]
RM = ("target", "r_multiple")         # r [1.0, 1.5, 2.0, 3.0]  (size 4)
TS = ("exit", "time_stop")            # max_bars [10, 20, 40]   (size 3)


def run_main(registry_path, out):
    return tool.main(["--registry", str(registry_path), "--out", str(out)])


# -- 1. structure identity -------------------------------------------------

def test_structure_identity(tmp_path):
    reg = Registry(tmp_path / "reg.jsonl")
    # Same blocks, different params -> ONE structure.
    register(reg, "s1", [(*MA, {"fast": 5, "slow": 50})])
    register(reg, "s2", [(*MA, {"fast": 10, "slow": 100})])
    # Different (role, type) set -> a SECOND structure.
    register(reg, "s3", [(*CB, {"lookback": 20, "direction": "long"})])

    cov = tool.collect(reg)
    assert len(cov) == 2
    assert (MA,) in cov and (CB,) in cov
    assert cov[(MA,)]["cells"]["BTCUSD_1d"]["registrations"] == 2
    assert cov[(CB,)]["cells"]["BTCUSD_1d"]["registrations"] == 1


def test_structure_key_sorts_blocks(tmp_path):
    p1 = {"universe": {"assets": ["BTCUSD"], "timeframe": "1d"},
          "blocks": [{"role": "target", "type": "r_multiple", "params": {}},
                     {"role": "entry", "type": "ma_cross", "params": {}}]}
    p2 = {"universe": {"assets": ["BTCUSD"], "timeframe": "1d"},
          "blocks": [{"role": "entry", "type": "ma_cross", "params": {}},
                     {"role": "target", "type": "r_multiple", "params": {}}]}
    assert tool.structure_key(p1) == tool.structure_key(p2) == (MA, RM)


# -- 2. per-param coverage: tested vs untested grid values -----------------

def test_per_param_tested_and_untested(tmp_path):
    # Depends on the REAL channel_breakout.lookback grid [20, 55, 100] in
    # pipeline/blocks.py (see module docstring's grid-dependency note).
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "s1", [(*CB, {"lookback": 20, "direction": "long"})])
    register(reg, "s2", [(*CB, {"lookback": 55, "direction": "long"})])

    cov = tool.collect(reg)
    cell = cov[(CB,)]["cells"]["BTCUSD_1d"]
    pkey = (*CB, "lookback")
    assert cell["tested_values"][pkey] == {20, 55}

    out = tmp_path / "report.md"
    assert run_main(tmp_path / "reg.jsonl", out) == 0
    report = out.read_text(encoding="utf-8")
    assert "entry/channel_breakout.lookback: tested = 20, 55; " \
           "untested = 100" in report


# -- 3. snap identity: 2 and 2.0 are ONE tested value ----------------------

def test_snap_identity(tmp_path):
    # Depends on the REAL zscore_reversion.z_entry grid [1.5, 2.0, 2.5].
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "s1", [(*ZS, {"lookback": 20, "z_entry": 2,
                                "direction": "long"})])
    register(reg, "s2", [(*ZS, {"lookback": 20, "z_entry": 2.0,
                                "direction": "long"})])

    cov = tool.collect(reg)
    cell = cov[(ZS,)]["cells"]["BTCUSD_1d"]
    assert len(cell["tested_values"][(*ZS, "z_entry")]) == 1
    # And the two registrations are ONE tested combo, not two.
    assert len(cell["tested_combos"]) == 1


# -- 4. combo counts: declared = product of grid sizes, tested = distinct --

def test_combo_counts(tmp_path):
    # Depends on the REAL grids r_multiple.r (size 4) and time_stop.max_bars
    # (size 3): declared combos = 4 * 3 = 12.
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "s1", [(*RM, {"r": 1.0}), (*TS, {"max_bars": 10})])
    register(reg, "s2", [(*RM, {"r": 1.5}), (*TS, {"max_bars": 10})])
    register(reg, "s3", [(*RM, {"r": 1.5}), (*TS, {"max_bars": 20})])

    cov = tool.collect(reg)
    skey = tool.structure_key({
        "universe": {"assets": ["BTCUSD"], "timeframe": "1d"},
        "blocks": [{"role": "target", "type": "r_multiple", "params": {}},
                   {"role": "exit", "type": "time_stop", "params": {}}]})
    assert tool.declared_combo_count(skey) == 12
    assert len(cov[skey]["cells"]["BTCUSD_1d"]["tested_combos"]) == 3


# -- 5. per-cell split: single-asset vs pooled cells are separate rows -----

def test_per_cell_split(tmp_path):
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "s1", [(*MA, {"fast": 5, "slow": 50})],
             assets=("BTCUSD",))
    register(reg, "s2", [(*MA, {"fast": 5, "slow": 50})],
             assets=("BTCUSD", "ETHUSD"))

    cov = tool.collect(reg)
    cells = cov[(MA,)]["cells"]
    assert sorted(cells) == ["BTCUSD+ETHUSD_1d", "BTCUSD_1d"]
    assert cells["BTCUSD_1d"]["registrations"] == 1
    assert cells["BTCUSD+ETHUSD_1d"]["registrations"] == 1

    out = tmp_path / "report.md"
    assert run_main(tmp_path / "reg.jsonl", out) == 0
    report = out.read_text(encoding="utf-8")
    assert "| BTCUSD_1d |" in report
    assert "| BTCUSD+ETHUSD_1d |" in report


# -- 6. read-only: registry untouched, exactly one new file ----------------

def test_read_only(tmp_path):
    registry_path = tmp_path / "reg.jsonl"
    reg = Registry(registry_path)
    register(reg, "s1", [(*MA, {"fast": 5, "slow": 50})])
    before_bytes = registry_path.read_bytes()
    before_files = {p for p in tmp_path.rglob("*") if p.is_file()}

    out = tmp_path / "out" / "report.md"
    assert run_main(registry_path, out) == 0

    assert registry_path.read_bytes() == before_bytes
    after_files = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after_files - before_files == {out}


# -- 7. report content: untested listing + truncation past 20 --------------

def test_untested_truncation(tmp_path, monkeypatch):
    # A monkeypatched grid is fine HERE because this tests RENDERING (the
    # truncation rule), not the fence: no real grid is wide enough.
    monkeypatch.setitem(
        tool.BLOCK_TYPES, ("entry", "synthetic_wide"),
        {"wide": {"type": "int", "grid": list(range(30))}})
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "s1", [("entry", "synthetic_wide", {"wide": 0})])

    out = tmp_path / "report.md"
    assert run_main(tmp_path / "reg.jsonl", out) == 0
    report = out.read_text(encoding="utf-8")
    # 29 untested values: first 20 rendered, then "... (+9 more)".
    assert "... (+9 more)" in report
    assert "untested = 1, 2, 3" in report
    # The 21st untested value (21) must NOT be rendered as a list element.
    assert ", 21," not in report.split("... (+9 more)")[0].rsplit(
        "untested = ", 1)[-1]


def test_refuses_empty_registry(tmp_path, capsys):
    registry_path = tmp_path / "reg.jsonl"
    registry_path.write_text("", encoding="utf-8")
    out = tmp_path / "report.md"
    with pytest.raises(SystemExit) as exc:
        run_main(registry_path, out)
    assert exc.value.code == 2
    assert not out.exists()
    assert "REFUSED" in capsys.readouterr().err


# -- report framing pins ---------------------------------------------------

def test_report_framing(tmp_path):
    reg = Registry(tmp_path / "reg.jsonl")
    register(reg, "s1", [(*MA, {"fast": 5, "slow": 50})])
    out = tmp_path / "report.md"
    assert run_main(tmp_path / "reg.jsonl", out) == 0
    report = out.read_text(encoding="utf-8")
    assert "READ ONLY" in report
    # D12 framing: fence, declared edit, steering OUT of scope.
    assert "reasonable" in report and "fence" in report
    assert "reviewed, declared edit" in report
    assert "OUT" in report
    # Params without grids are excluded from the declared denominator.
    assert "excluded from the declared-combo denominator" in report
    # Global summary present.
    assert "## Global summary" in report
