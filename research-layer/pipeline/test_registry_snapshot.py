"""Registry.snapshot / advance / record_gauntlet_outcome (Build 2a, task 5
fix round 1, Ruling 8): the gauntlet worker's chain.lock hold must cost
O(tail), not O(chain). One full read outside the lock; under it, parse only
what was appended since, and append the verdict + state change in one shot."""
import pytest

from . import registry as registry_mod
from .common import entry_hash
from .registry import ChainMoved, Registry
from .test_gauntlet import gauntlet_registry, run_verifier

FIXED_TS = "2026-10-01T00:00:00Z"
METRICS = {"protocol": "gauntlet-protocol-v6.1", "fail_reason": "oos_negative"}


def _twin_registries(tmp_path):
    """Two registries holding byte-identical chains (the second a copy of
    the first), so the same writes through two paths can be compared."""
    ra, spec = gauntlet_registry(tmp_path)
    rb = Registry(tmp_path / "twin.jsonl")
    rb.log_path.write_bytes(ra.log_path.read_bytes())
    return ra, rb, spec


def _same(a, b):
    assert dict(a.states) == dict(b.states)
    assert set(a.gauntlet_judged) == set(b.gauntlet_judged)
    assert a.head_hash == b.head_hash
    assert a.byte_len == b.byte_len


def test_snapshot_matches_the_existing_readers(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "fail", METRICS, "0" * 64)
    snap = reg.snapshot()
    assert dict(snap.states) == reg.strategy_states()
    assert snap.gauntlet_judged == {spec["strategy_id"]}
    assert snap.head_hash == reg._head_hash()
    assert snap.byte_len == reg.log_path.stat().st_size


def test_snapshot_of_a_missing_log_is_genesis(tmp_path):
    snap = Registry(tmp_path / "none.jsonl").snapshot()
    assert snap.byte_len == 0 and snap.head_hash == "0" * 64 and not snap.states


def test_snapshot_ignores_a_torn_trailing_line(tmp_path):
    reg, _ = gauntlet_registry(tmp_path)
    whole = reg.snapshot()
    with reg.log_path.open("ab") as f:
        f.write(b'{"version":1,"entry_t')            # a writer mid-append
    _same(reg.snapshot(), whole)


def test_advance_over_an_appended_tail_equals_a_fresh_snapshot(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    reg.append("note", {"text": "foreign"})
    reg.record_verdict(spec["strategy_id"], "gauntlet", "fail", METRICS, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "graveyard", "oos_negative")
    _same(reg.advance(snap), reg.snapshot())


def test_advance_with_nothing_new_is_a_no_op(tmp_path):
    reg, _ = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    _same(reg.advance(snap), snap)


def test_advance_refuses_a_tail_that_does_not_link(tmp_path):
    reg, _ = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    bad = {"version": 1, "ts_utc": FIXED_TS, "entry_type": "note",
           "prev_entry_hash": "f" * 64, "payload": {"text": "forked"}}
    with reg.log_path.open("a", encoding="utf-8") as f:
        f.write(registry_mod.canonical_json(bad) + "\n")
    with pytest.raises(ChainMoved):
        reg.advance(snap)


def test_advance_refuses_a_truncated_log(tmp_path):
    reg, _ = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    data = reg.log_path.read_bytes()
    reg.log_path.write_bytes(data[:len(data) // 2])
    with pytest.raises(ChainMoved):
        reg.advance(snap)


def test_advance_refuses_a_rewritten_head(tmp_path):
    """Same length, different last entry: no new tail to fail the link
    check, so the head itself is re-hashed."""
    reg, _ = gauntlet_registry(tmp_path)
    reg.append("note", {"text": "aaaa"})
    snap = reg.snapshot()
    data = reg.log_path.read_bytes()
    reg.log_path.write_bytes(data.replace(b'"text":"aaaa"', b'"text":"bbbb"'))
    with pytest.raises(ChainMoved):
        reg.advance(snap)


def test_outcome_refuses_a_strategy_not_in_gauntlet(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_state_change(spec["strategy_id"], "graveyard", "test")
    snap = reg.snapshot()
    with pytest.raises(ValueError):
        reg.record_gauntlet_outcome(snap, spec["strategy_id"], "fail", METRICS,
                                    "0" * 64, "graveyard", "oos_negative")


def test_outcome_refuses_an_already_judged_strategy(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "fail", METRICS, "0" * 64)
    snap = reg.snapshot()
    with pytest.raises(ValueError):
        reg.record_gauntlet_outcome(snap, spec["strategy_id"], "fail", METRICS,
                                    "0" * 64, "graveyard", "oos_negative")


def test_outcome_refuses_an_illegal_target_state(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    with pytest.raises(ValueError):
        reg.record_gauntlet_outcome(snap, spec["strategy_id"], "pass", METRICS,
                                    "0" * 64, "live", "gauntlet pass")


def test_outcome_refuses_a_log_that_grew_since_the_snapshot(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    reg.append("note", {"text": "appended without chain.lock"})
    size = reg.log_path.stat().st_size
    with pytest.raises(ChainMoved):
        reg.record_gauntlet_outcome(snap, spec["strategy_id"], "fail", METRICS,
                                    "0" * 64, "graveyard", "oos_negative")
    assert reg.log_path.stat().st_size == size          # wrote nothing


@pytest.mark.parametrize("verdict,to,reason", [
    ("fail", "graveyard", "oos_negative"), ("pass", "quarantine", "gauntlet pass")])
def test_outcome_is_byte_identical_to_the_two_record_calls(tmp_path, monkeypatch,
                                                           verdict, to, reason):
    monkeypatch.setattr(registry_mod, "_now_utc", lambda: FIXED_TS)
    ra, rb, spec = _twin_registries(tmp_path)
    sid = spec["strategy_id"]
    ra.record_verdict(sid, "gauntlet", verdict, METRICS, "ab" * 32)
    ra.record_state_change(sid, to, reason)
    new = rb.record_gauntlet_outcome(rb.snapshot(), sid, verdict, METRICS,
                                     "ab" * 32, to, reason)
    assert rb.log_path.read_bytes() == ra.log_path.read_bytes()
    _same(new, rb.snapshot())
    assert run_verifier(rb.log_path).returncode == 0


def test_state_change_at_matches_record_state_change(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_mod, "_now_utc", lambda: FIXED_TS)
    ra, rb, spec = _twin_registries(tmp_path)
    sid = spec["strategy_id"]
    ra.record_state_change(sid, "graveyard", "gauntlet fail")
    new = rb.record_state_change_at(rb.snapshot(), sid, "graveyard", "gauntlet fail")
    assert rb.log_path.read_bytes() == ra.log_path.read_bytes()
    _same(new, rb.snapshot())
    with pytest.raises(ValueError):            # out of a terminal state
        rb.record_state_change_at(new, sid, "quarantine", "x")


def test_outcome_chains_onto_the_snapshot_head(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    reg.record_gauntlet_outcome(snap, spec["strategy_id"], "fail", METRICS,
                                "0" * 64, "graveyard", "oos_negative")
    tail = list(reg.entries())[-2:]
    assert tail[0]["prev_entry_hash"] == snap.head_hash
    assert tail[1]["prev_entry_hash"] == entry_hash(tail[0])
    assert tail[1]["payload"]["buried_at"] == "gauntlet"


# ---------------- fix round 2 (Ruling 9): hash the serialized line ----------------

def test_outcome_refuses_metrics_that_do_not_round_trip(tmp_path):
    """canonical_json sorts keys BEFORE stringifying them, so a dict with
    int keys {9, 10} serializes 9-then-10 but parses back as "10"-then-"9":
    the line on disk would not hash to what the in-memory entry hashes to,
    and the state change chained after it would be a permanent broken link.
    Refuse before writing anything."""
    from .registry import UnstableEntry
    reg, spec = gauntlet_registry(tmp_path)
    snap = reg.snapshot()
    before = reg.log_path.read_bytes()
    with pytest.raises(UnstableEntry):
        reg.record_gauntlet_outcome(snap, spec["strategy_id"], "fail",
                                    {"protocol": "gauntlet-protocol-v6.1",
                                     "m": {9: 1.0, 10: 2.0}},
                                    "0" * 64, "graveyard", "oos_negative")
    assert reg.log_path.read_bytes() == before
    assert not (tmp_path / "reg.jsonl.lock").exists()
    assert run_verifier(reg.log_path).returncode == 0


def test_outcome_hashes_the_line_not_the_dict(tmp_path):
    """Regression pin: a value canonical_json stringifies (default=str) is
    stable and still verifies, and the returned snapshot's head matches a
    fresh read. (Not discriminating on its own -- default=str hashes the same
    in memory and parsed; the non-string-key test above is the one that
    separates line-hash from dict-hash.)"""
    import datetime as dt
    reg, spec = gauntlet_registry(tmp_path)
    new = reg.record_gauntlet_outcome(
        reg.snapshot(), spec["strategy_id"], "fail",
        {"protocol": "gauntlet-protocol-v6.1", "when": dt.date(2026, 10, 1)},
        "0" * 64, "graveyard", "oos_negative")
    _same(new, reg.snapshot())
    assert run_verifier(reg.log_path).returncode == 0
