from __future__ import annotations

import json
import os

from pipeline import loop_state
from pipeline.chainlock import ChainLock
from pipeline.registry import Registry
from tools import grid_switch as gs


def _spec(sid, assets, asset_class="crypto", *, omit_class=False):
    uni = {"assets": list(assets), "timeframe": "1d"}
    if not omit_class:
        uni["asset_class"] = asset_class
    return {"strategy_id": sid, "version": 2, "universe": uni}


def _registry(tmp_path):
    return Registry(tmp_path / "registry_log.jsonl")


def _register(reg, sid, assets, asset_class="crypto", state=None, **kw):
    reg.append("strategy_registered", _spec(sid, assets, asset_class, **kw))
    path = {None: [], "proposed": [], "screened": ["screened"],
            "gauntlet": ["screened", "gauntlet"],
            "graveyard": ["graveyard"]}[state]
    for to in path:
        reg.record_state_change(sid, to, "test")


def _loop_state(tmp_path, specs=None):
    path = tmp_path / "logs" / "loop_state.json"
    st = loop_state.load(path)
    if specs:
        loop_state.enqueue_specs(st, "crypto", specs)
    loop_state.save(path, st)
    return path


def test_preflight_names_a_proposed_pooled_spec(tmp_path, capsys):
    reg = _registry(tmp_path)
    _register(reg, "pooled-p", ["BTCUSD", "ETHUSD"])
    _register(reg, "pooled-g", ["BTCUSD"], state="gauntlet")
    _register(reg, "pooled-s", ["ETHUSD"], state="screened")
    ls = _loop_state(tmp_path)
    found = gs.pooled_in_flight(reg.log_path, ls)
    assert sorted((f["where"], f["strategy_id"]) for f in found) == [
        ("gauntlet", "pooled-g"), ("proposed", "pooled-p"),
        ("screened", "pooled-s")]
    assert [f["assets"] for f in found if f["strategy_id"] == "pooled-p"] == [
        ["BTCUSD", "ETHUSD"]]
    rc = gs.main(["preflight", "--registry", str(reg.log_path),
                  "--loop-state", str(ls)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "pooled-p" in out and "pooled-g" in out


def test_preflight_names_a_queued_pooled_spec(tmp_path):
    reg = _registry(tmp_path)
    ls = _loop_state(tmp_path, [_spec("q-pooled", ["BTCUSD", "ETHUSD"]),
                                _spec("q-ok", ["BTCUSDT"])])
    found = gs.pooled_in_flight(reg.log_path, ls)
    assert found == [{"where": "sibling_queue", "strategy_id": "q-pooled",
                      "assets": ["BTCUSD", "ETHUSD"]}]
    # the read never wrote the queue back
    assert loop_state.queue_depth(loop_state.load(ls), "crypto") == 2


def test_preflight_treats_a_missing_asset_class_as_crypto(tmp_path):
    reg = _registry(tmp_path)
    _register(reg, "no-class", ["ETHUSD"], omit_class=True)
    found = gs.pooled_in_flight(reg.log_path, _loop_state(tmp_path))
    assert [f["strategy_id"] for f in found] == ["no-class"]


def test_preflight_ignores_per_cell_usdt_and_tradfi_specs(tmp_path):
    reg = _registry(tmp_path)
    _register(reg, "usdt", ["BTCUSDT"])
    _register(reg, "usdt-g", ["SOLUSDT"], state="gauntlet")
    _register(reg, "fx", ["EURUSD", "GBPUSD"], asset_class="fx")
    _register(reg, "equity", ["BTCUSD"], asset_class="equity")
    # a legacy pooled spec that already left the pipeline is not in flight
    _register(reg, "old-dead", ["BTCUSD", "ETHUSD"], state="graveyard")
    ls = _loop_state(tmp_path, [_spec("q-fx", ["EURUSD", "GBPUSD"], "fx")])
    assert gs.pooled_in_flight(reg.log_path, ls) == []


def test_preflight_clear_exits_0(tmp_path, capsys):
    reg = _registry(tmp_path)
    _register(reg, "usdt", ["BTCUSDT"])
    rc = gs.main(["preflight", "--registry", str(reg.log_path),
                  "--loop-state", str(_loop_state(tmp_path))])
    assert rc == 0
    assert "preflight: no legacy pooled crypto spec in flight" in (
        capsys.readouterr().out)


# ---------------------------------------------------------------- chain-note

PREFIX = "grid-switch-1-record:"


def _note_file(tmp_path, text=None, name="note.md"):
    text = (PREFIX + " crypto per cell at 1d\nline two\n") if text is None else text
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def _chain_with_entry(tmp_path):
    reg = _registry(tmp_path)
    reg.append("note", {"text": "an older note\n"})
    return reg


def _notes(reg):
    return [e["payload"]["text"] for e in reg.entries()
            if e["entry_type"] == "note"]


def _n_lines(reg):
    return len(reg.log_path.read_bytes().splitlines())


def test_chain_note_refuses_crlf(tmp_path):
    reg = _chain_with_entry(tmp_path)
    note = tmp_path / "crlf.md"
    note.write_bytes((PREFIX + " x\r\nline two\r\n").encode())
    before = _n_lines(reg)
    assert gs.chain_note(reg.log_path, note, PREFIX, dry_run=False) == 2
    assert _n_lines(reg) == before


def test_chain_note_refuses_a_first_line_without_the_prefix(tmp_path):
    reg = _chain_with_entry(tmp_path)
    note = _note_file(tmp_path, "Some other heading\n" + PREFIX + " later\n")
    before = _n_lines(reg)
    assert gs.chain_note(reg.log_path, note, PREFIX, dry_run=False) == 2
    assert _n_lines(reg) == before


def test_chain_note_refuses_a_prefix_without_a_colon(tmp_path):
    reg = _chain_with_entry(tmp_path)
    note = _note_file(tmp_path, "grid-switch-1-record x\n")
    before = _n_lines(reg)
    assert gs.chain_note(reg.log_path, note, "grid-switch-1-record",
                         dry_run=False) == 2
    assert _n_lines(reg) == before


def test_chain_note_refuses_a_second_note_with_the_prefix(tmp_path):
    reg = _chain_with_entry(tmp_path)
    reg.append("note", {"text": PREFIX + " first one\n"})
    note = _note_file(tmp_path)
    before = _n_lines(reg)
    assert gs.chain_note(reg.log_path, note, PREFIX, dry_run=False) == 2
    assert _n_lines(reg) == before


def test_chain_note_refuses_a_held_chain_lock(tmp_path):
    reg = _chain_with_entry(tmp_path)
    note = _note_file(tmp_path)
    lock = ChainLock(tmp_path / "logs", "other", "writing")
    lock.acquire()
    try:
        before = _n_lines(reg)
        assert gs.chain_note(reg.log_path, note, PREFIX, dry_run=False) == 2
        assert _n_lines(reg) == before
        assert lock.path.exists()                      # never broken
    finally:
        lock.release()


def test_chain_note_refuses_a_stale_chain_lock_it_never_breaks(tmp_path):
    reg = _chain_with_entry(tmp_path)
    note = _note_file(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "chain.lock").write_text(json.dumps(
        {"holder": "dead", "pid": 2 ** 22 + 12345,
         "ts_utc": "2020-01-01T00:00:00+00:00", "purpose": "x"}),
        encoding="utf-8")
    old = 1_000_000_000
    os.utime(logs / "chain.lock", (old, old))
    before = _n_lines(reg)
    assert gs.chain_note(reg.log_path, note, PREFIX, dry_run=False) == 2
    assert _n_lines(reg) == before
    assert (logs / "chain.lock").exists()


def test_chain_note_appends_once_verbatim_and_verifies(tmp_path, capsys):
    reg = _chain_with_entry(tmp_path)
    note = _note_file(tmp_path)
    rc = gs.chain_note(reg.log_path, note, PREFIX, dry_run=False)
    out = capsys.readouterr().out
    assert rc == 0, out
    notes = _notes(reg)
    assert len(notes) == 2
    assert notes[-1] == note.read_bytes().decode("utf-8")
    last = list(reg.entries())[-1]
    assert last["entry_type"] == "note" and set(last["payload"]) == {"text"}
    assert "REGISTRY VALID" in out
    assert not (tmp_path / "logs" / "chain.lock").exists()    # released
    # running it again is now refused: the prefix is on the chain
    assert gs.chain_note(reg.log_path, note, PREFIX, dry_run=False) == 2
    assert len(_notes(reg)) == 2


def test_chain_note_dry_run_writes_nothing(tmp_path, capsys):
    reg = _chain_with_entry(tmp_path)
    note = _note_file(tmp_path)
    before = reg.log_path.read_bytes()
    rc = gs.chain_note(reg.log_path, note, PREFIX, dry_run=True)
    assert rc == 0
    assert reg.log_path.read_bytes() == before
    assert not (tmp_path / "logs").exists()                   # no lock taken
    assert "dry-run" in capsys.readouterr().out


def test_chain_note_exits_1_loudly_when_verify_is_invalid(tmp_path, capsys):
    reg = _chain_with_entry(tmp_path)
    reg.append("note", {"text": "another\n"})
    # break the link of the LAST entry so the chain verifies INVALID
    lines = reg.log_path.read_bytes().splitlines(keepends=True)
    e = json.loads(lines[-1])
    e["prev_entry_hash"] = "f" * 64
    lines[-1] = (json.dumps(e) + "\n").encode()
    reg.log_path.write_bytes(b"".join(lines))
    note = _note_file(tmp_path)
    rc = gs.chain_note(reg.log_path, note, PREFIX, dry_run=False)
    cap = capsys.readouterr()
    assert rc == 1
    assert "CHAINED" in cap.out + cap.err
