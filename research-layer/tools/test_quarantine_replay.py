from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pipeline.quarantine import run as quarantine_run
from pipeline.registry import Registry
from pipeline.test_gen3b import argv_for, flat_dated_bars, quarantined

LAYER = Path(__file__).resolve().parent.parent
TOOL = LAYER / "tools" / "quarantine_replay.py"


def _tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True)


def _chain_with_two_days(tmp_path):
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars(n=25))
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    n_before = len(reg.log_path.read_bytes().splitlines())
    # stamp the next day's entries late enough to cut on
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-23")) == 0
    return reg, data, n_before


def test_prefix_cuts_before_the_first_quarantine_entry_at_or_after_the_stamp(tmp_path):
    reg, data, n_before = _chain_with_two_days(tmp_path)
    lines = reg.log_path.read_bytes().splitlines(keepends=True)
    cut_ts = json.loads(lines[n_before])["ts_utc"]
    out = tmp_path / "prefix.jsonl"
    r = _tool("prefix", "--registry", str(reg.log_path), "--cut-utc", cut_ts,
              "--out", str(out))
    assert r.returncode == 0, r.stderr
    kept = out.read_bytes()
    # the cut is the FIRST quarantine entry at/after the stamp, which may be
    # earlier than n_before when both days share a second; it is a prefix
    assert reg.log_path.read_bytes().startswith(kept)
    assert f"kept {len(kept.splitlines())} lines" in r.stdout
    assert all(json.loads(l)["ts_utc"] < cut_ts
               or not json.loads(l)["entry_type"].startswith("quarantine_")
               for l in kept.splitlines())


def test_prefix_with_nothing_to_cut_exits_2(tmp_path):
    reg, data, _ = _chain_with_two_days(tmp_path)
    r = _tool("prefix", "--registry", str(reg.log_path), "--cut-utc",
              "2999-01-01T00:00:00Z", "--out", str(tmp_path / "x.jsonl"))
    assert r.returncode == 2
    assert "no quarantine entry" in r.stderr


def _copy(src, dst):
    dst.write_bytes(src.read_bytes())
    return dst


def test_compare_ignores_timestamps_and_hashes(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = len(reg.log_path.read_bytes().splitlines())
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    b = _copy(reg.log_path, tmp_path / "b.jsonl")
    for p in (a, b):
        assert quarantine_run(argv_for(Registry(p), data, "--date",
                                       "2023-01-22")) == 0
    r = _tool("compare", str(a), str(b), "--skip", str(base))
    assert r.returncode == 0 and "MATCH" in r.stdout


def test_compare_reports_a_payload_difference(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = len(reg.log_path.read_bytes().splitlines())
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    b = _copy(reg.log_path, tmp_path / "b.jsonl")
    Registry(a).append("note", {"text": "x"})
    Registry(b).append("note", {"text": "y"})
    r = _tool("compare", str(a), str(b), "--skip", str(base))
    assert r.returncode == 1 and "MISMATCH" in r.stdout and "index 0" in r.stdout


def test_compare_reports_a_length_difference(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = len(reg.log_path.read_bytes().splitlines())
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    b = _copy(reg.log_path, tmp_path / "b.jsonl")
    Registry(a).append("note", {"text": "x"})
    r = _tool("compare", str(a), str(b), "--skip", str(base))
    assert r.returncode == 1 and "MISMATCH" in r.stdout


def test_compare_checks_the_ledgers(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    la, lb = tmp_path / "la.json", tmp_path / "lb.json"
    la.write_text(json.dumps({"items": [{"source": "restated", "key": "X",
                                         "reason": "r", "since_utc": "1"}]}))
    lb.write_text(json.dumps({"items": [{"source": "restated", "key": "X",
                                         "reason": "r", "since_utc": "2"}]}))
    assert _tool("compare", str(a), str(a), "--skip", "0", "--ledger-a",
                 str(la), "--ledger-b", str(lb)).returncode == 0
    lb.write_text(json.dumps({"items": []}))
    assert _tool("compare", str(a), str(a), "--skip", "0", "--ledger-a",
                 str(la), "--ledger-b", str(lb)).returncode == 1


def test_run_times_the_lock_and_returns_the_code(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(LAYER), "--timing", str(timing), "--",
              *argv_for(reg, data, "--date", "2023-01-22"))
    assert r.returncode == 0, r.stderr
    t = json.loads(timing.read_text())
    assert t["rc"] == 0 and t["error"] is None
    assert len(t["holds_s"]) == 1 and t["max_hold_s"] == t["holds_s"][0]
    assert t["wall_s"] >= t["max_hold_s"] >= 0
    assert Path(t["pipeline_file"]).is_relative_to(LAYER.resolve())


def test_run_passes_a_refusal_through_as_its_rc(tmp_path):
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(LAYER), "--timing", str(timing), "--",
              "--date", "2023-01-22", "--registry", str(tmp_path / "missing.jsonl"),
              "--data-dir", str(tmp_path))
    t = json.loads(timing.read_text())
    assert r.returncode == t["rc"] == 1


def test_run_refuses_a_code_root_without_pipeline(tmp_path):
    root = tmp_path / "no_pipeline"
    root.mkdir()
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(root), "--timing", str(timing), "--",
              "--date", "2023-01-22")
    assert r.returncode == 2 and "refused" in r.stderr
    assert not timing.exists()


def test_run_reports_an_argparse_exit_as_its_code(tmp_path):
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(LAYER), "--timing", str(timing), "--",
              "--registry", str(tmp_path / "copy.jsonl"), "--bogus-flag")
    t = json.loads(timing.read_text())
    assert r.returncode == 2 and t["rc"] == 2
    assert t["error"] is not None and "SystemExit" in t["error"]


def test_prefix_refuses_out_equal_to_registry(tmp_path):
    reg, data, _ = _chain_with_two_days(tmp_path)
    before = reg.log_path.read_bytes()
    r = _tool("prefix", "--registry", str(reg.log_path), "--cut-utc",
              "2023-01-23T00:00:00Z", "--out", str(reg.log_path))
    assert r.returncode == 2 and "refused" in r.stderr
    assert reg.log_path.read_bytes() == before


def test_prefix_refuses_a_malformed_cut_utc(tmp_path):
    reg, data, _ = _chain_with_two_days(tmp_path)
    r = _tool("prefix", "--registry", str(reg.log_path), "--cut-utc",
              "2023-01-23", "--out", str(tmp_path / "x.jsonl"))
    assert r.returncode == 2 and "refused" in r.stderr


def _note_line(text: str) -> bytes:
    return (json.dumps({"entry_type": "note", "payload": {"text": text},
                        "ts_utc": "2023-01-23T00:00:00Z"}) + "\n").encode()


def _timing_file(path, rc):
    path.write_text(json.dumps({"rc": rc}), encoding="utf-8")
    return str(path)


def test_compare_with_nothing_appended_on_either_side_is_no_result(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = len(reg.log_path.read_bytes().splitlines())
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    b = _copy(reg.log_path, tmp_path / "b.jsonl")
    r = _tool("compare", str(a), str(b), "--skip", str(base))
    assert r.returncode == 3 and "NO RESULT" in r.stdout


def test_compare_keeps_every_entry_past_a_blank_line_in_the_prefix(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = reg.log_path.read_bytes()
    assert base.endswith(b"\n")
    skip = len(base.splitlines()) + 1          # the leading blank line counts
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_bytes(b"\n" + base + _note_line("x") + _note_line("y"))
    b.write_bytes(b"\n" + base + _note_line("x") + _note_line("y"))
    r = _tool("compare", str(a), str(b), "--skip", str(skip))
    assert r.returncode == 0, r.stdout
    assert "A appended 2, B appended 2" in r.stdout
    assert r.stdout.splitlines()[-1] == "MATCH"


def test_compare_different_prefixes_are_a_mismatch(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = reg.log_path.read_bytes()
    n = len(base.splitlines())
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_bytes(base + _note_line("x"))
    b.write_bytes(b"\n" + base + _note_line("x"))
    r = _tool("compare", str(a), str(b), "--skip", str(n))
    assert r.returncode == 1 and "do not share the first" in r.stdout


def test_compare_a_torn_tail_is_a_mismatch(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = reg.log_path.read_bytes()
    n = len(base.splitlines())
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_bytes(base + b'{"entry_type": "note", "pay')
    b.write_bytes(base + _note_line("x"))
    r = _tool("compare", str(a), str(b), "--skip", str(n))
    assert r.returncode == 1 and "unparseable line at index 0 in A" in r.stdout


def test_compare_refuses_a_missing_or_unpaired_ledger(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    a = _copy(reg.log_path, tmp_path / "a.jsonl")
    missing = tmp_path / "nope.json"
    r = _tool("compare", str(a), str(a), "--skip", "0", "--ledger-a",
              str(missing), "--ledger-b", str(missing))
    assert r.returncode == 2 and "refused" in r.stderr
    r = _tool("compare", str(a), str(a), "--skip", "0", "--ledger-a",
              str(missing))
    assert r.returncode == 2 and "must be given together" in r.stderr


def test_compare_checks_the_run_return_codes(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    base = reg.log_path.read_bytes()
    n = len(base.splitlines())
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_bytes(base + _note_line("x"))
    b.write_bytes(base + _note_line("x"))
    ta = _timing_file(tmp_path / "ta.json", 0)
    tb = _timing_file(tmp_path / "tb.json", 1)
    r = _tool("compare", str(a), str(b), "--skip", str(n),
              "--timing-a", ta, "--timing-b", tb)
    assert r.returncode == 1 and "MISMATCH" in r.stdout
    assert "A rc=0" in r.stdout and "B rc=1" in r.stdout
    same = _timing_file(tmp_path / "tb2.json", 0)
    r = _tool("compare", str(a), str(b), "--skip", str(n),
              "--timing-a", ta, "--timing-b", same)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "MATCH"
    r = _tool("compare", str(a), str(b), "--skip", str(n),
              "--timing-a", ta, "--timing-b", str(tmp_path / "gone.json"))
    assert r.returncode == 2 and "does not exist" in r.stderr


# ---- `run` is read-only against the live chain (final review item 2) ----

def _stub_code_root(tmp_path):
    """A code root the tool accepts (it only checks pipeline/quarantine.py
    before the argv refusal) holding a COPY of a chain and a data dir, so the
    refusals below can prove their targets are untouched."""
    root = tmp_path / "coderoot"
    (root / "pipeline").mkdir(parents=True)
    (root / "pipeline" / "quarantine.py").write_text("raise SystemExit(99)\n")
    (root / "data").mkdir()
    (root / "data" / "BTCUSD.csv").write_text("date,close\n")
    reg, spec, data = quarantined(tmp_path)
    (root / "registry_log.jsonl").write_bytes(reg.log_path.read_bytes())
    return root, reg, data


def _snapshot_dir(d):
    return {p.name: p.read_bytes() for p in sorted(Path(d).iterdir())}


def test_run_refuses_an_argv_with_no_registry(tmp_path):
    root, reg, data = _stub_code_root(tmp_path)
    before = (root / "registry_log.jsonl").read_bytes()
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(root), "--timing", str(timing), "--",
              "--date", "2023-01-22", "--data-dir", str(data))
    assert r.returncode == 2 and "refused: " in r.stderr
    assert "no --registry" in r.stderr
    assert not timing.exists()
    assert (root / "registry_log.jsonl").read_bytes() == before


def test_run_refuses_the_code_roots_live_chain_in_either_spelling(tmp_path):
    root, reg, data = _stub_code_root(tmp_path)
    live = root / "registry_log.jsonl"
    before = live.read_bytes()
    for spelling in (["--registry", str(live)], [f"--registry={live}"],
                     ["--registry", str(root / "data" / ".." / "registry_log.jsonl")]):
        timing = tmp_path / "t.json"
        r = _tool("run", "--code-root", str(root), "--timing", str(timing),
                  "--", "--date", "2023-01-22", *spelling,
                  "--data-dir", str(data))
        assert r.returncode == 2 and "refused: " in r.stderr, spelling
        assert "live chain" in r.stderr
        assert not timing.exists()
        assert live.read_bytes() == before


def test_run_refuses_this_tools_own_layer_chain(tmp_path):
    """--code-root is a scratch tree but --registry names the layer this tool
    lives in. That file is a live chain in the live tree and may not exist in
    a worktree; a refusal must not create or change it either way."""
    root, reg, data = _stub_code_root(tmp_path)
    layer_chain = LAYER / "registry_log.jsonl"
    before = layer_chain.read_bytes() if layer_chain.exists() else None
    timing = tmp_path / "t.json"
    r = _tool("run", "--code-root", str(root), "--timing", str(timing), "--",
              "--date", "2023-01-22", "--registry", str(layer_chain),
              "--data-dir", str(data))
    assert r.returncode == 2 and "refused: " in r.stderr
    assert not timing.exists()
    assert (layer_chain.read_bytes() if layer_chain.exists() else None) == before


def test_run_refuses_fetch_owed_without_a_scratch_data_dir(tmp_path):
    root, reg, data = _stub_code_root(tmp_path)
    scratch_reg = _copy(reg.log_path, tmp_path / "scratch.jsonl")
    reg_before = scratch_reg.read_bytes()
    root_data_before = _snapshot_dir(root / "data")
    layer_data_before = _snapshot_dir(LAYER / "data")
    cases = {
        "missing": [],
        "code root's data": ["--data-dir", str(root / "data")],
        "this layer's data": ["--data-dir", str(LAYER / "data")],
        "= form": [f"--data-dir={root / 'data'}"],
    }
    for name, data_args in cases.items():
        timing = tmp_path / "t.json"
        r = _tool("run", "--code-root", str(root), "--timing", str(timing),
                  "--", "--fetch-owed", "--registry", str(scratch_reg),
                  *data_args)
        assert r.returncode == 2 and "refused: " in r.stderr, name
        assert "--fetch-owed" in r.stderr, name
        assert not timing.exists(), name
        assert scratch_reg.read_bytes() == reg_before, name
        assert _snapshot_dir(root / "data") == root_data_before, name
        assert _snapshot_dir(LAYER / "data") == layer_data_before, name


def test_the_run_guard_lets_scratch_paths_through(tmp_path):
    """The positive side, checked on the guard itself: a fetch-owed run
    against copies is not refused (it is never executed here: it would fetch)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("quarantine_replay", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "coderoot"
    scratch = tmp_path / "scratch_data"
    scratch.mkdir()
    ok = ["--fetch-owed", "--registry", str(tmp_path / "copy.jsonl"),
          "--data-dir", str(scratch)]
    assert mod._run_argv_refusal(root, ok) is None
    assert mod._run_argv_refusal(root, ["--date", "2023-01-22", "--registry",
                                        str(tmp_path / "copy.jsonl")]) is None
