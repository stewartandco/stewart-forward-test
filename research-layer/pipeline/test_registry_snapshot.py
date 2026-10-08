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


# ---------------- Task 7 (Ruling 10): batched gauntlet_stats ----------------

V61_NOTE = "gauntlet-protocol-v6.1: test anchor"


def _statted_registry(tmp_path, name="reg.jsonl"):
    """Five siblings, each with a v6.1 gauntlet verdict and its state change.
    Returns (registry, [(sid, verdict_entry_hash), ...])."""
    from .test_gauntlet import v4_sweep_registry
    d = tmp_path / name.replace(".jsonl", "")
    d.mkdir()
    reg, by_lb = v4_sweep_registry(d)
    reg.append("note", {"text": V61_NOTE})
    out = []
    for lb, sid in sorted(by_lb.items()):
        v = reg.record_verdict(sid, "gauntlet", "fail",
                               {"protocol": "gauntlet-protocol-v6.1",
                                "fail_reason": "oos_negative"}, "0" * 64)
        reg.record_state_change(sid, "graveyard", "oos_negative")
        out.append((sid, entry_hash(v)))
    return reg, out


def _stats(i):
    return {"trials_n": 3 + i, "deflated_sharpe": 0.25 * i, "pbo": None,
            "cluster_method": "effective_trials/v3 correlation-distance",
            "data_vintage": "2026-09-27"}


def test_stats_snapshot_tracks_v61_verdicts_and_stats(tmp_path):
    reg, vs = _statted_registry(tmp_path)
    reg.record_gauntlet_stats(vs[0][0], vs[0][1], _stats(0))
    snap = reg.snapshot(track_stats=True)
    assert dict(snap.v61_verdicts) == {vh: sid for sid, vh in vs}
    assert snap.statted == {vs[0][1]}
    # the default snapshot does not pay for hashing every verdict
    plain = reg.snapshot()
    assert not plain.tracks_stats and not plain.v61_verdicts


def test_stats_batch_is_byte_identical_to_sequential_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_mod, "_now_utc", lambda: FIXED_TS)
    ra, vs = _statted_registry(tmp_path)
    rb = Registry(tmp_path / "twin.jsonl")
    rb.log_path.write_bytes(ra.log_path.read_bytes())
    for i, (sid, vh) in enumerate(vs):
        ra.record_gauntlet_stats(sid, vh, _stats(i))
    new = rb.record_gauntlet_stats_batch(
        rb.snapshot(track_stats=True),
        [(sid, vh, _stats(i)) for i, (sid, vh) in enumerate(vs)])
    assert rb.log_path.read_bytes() == ra.log_path.read_bytes()
    fresh = rb.snapshot(track_stats=True)
    _same(new, fresh)
    assert new.statted == fresh.statted == {vh for _, vh in vs}
    r = run_verifier(rb.log_path)
    assert r.returncode == 0, r.stdout


def test_stats_batch_drops_a_verdict_that_already_has_stats(tmp_path):
    reg, vs = _statted_registry(tmp_path)
    snap = reg.snapshot(track_stats=True)
    # chained by someone else AFTER the snapshot was taken: the batch must
    # see it on the advanced tail and drop the item, not write a second one
    reg.record_gauntlet_stats(vs[0][0], vs[0][1], _stats(0))
    before = reg.log_path.read_bytes()
    new = reg.record_gauntlet_stats_batch(snap, [(vs[0][0], vs[0][1], _stats(9))])
    assert reg.log_path.read_bytes() == before          # nothing written
    new = reg.record_gauntlet_stats_batch(
        new, [(sid, vh, _stats(1)) for sid, vh in vs[:2]])
    stats = [e for e in reg.entries() if e["entry_type"] == "gauntlet_stats"]
    assert [e["payload"]["verdict_entry_hash"] for e in stats] == [vs[0][1], vs[1][1]]
    assert stats[0]["payload"]["trials_n"] == 3       # the first one stands
    assert run_verifier(reg.log_path).returncode == 0


def test_stats_batch_refuses_a_duplicate_within_the_batch(tmp_path):
    reg, vs = _statted_registry(tmp_path)
    before = reg.log_path.read_bytes()
    sid, vh = vs[0]
    with pytest.raises(ValueError):
        reg.record_gauntlet_stats_batch(reg.snapshot(track_stats=True),
                                        [(sid, vh, _stats(0)), (sid, vh, _stats(1))])
    assert reg.log_path.read_bytes() == before


@pytest.mark.parametrize("case", ["unknown_hash", "wrong_sid", "v6_verdict"])
def test_stats_batch_refuses_what_the_verifier_would(tmp_path, case):
    reg, vs = _statted_registry(tmp_path)
    if case == "unknown_hash":
        item = (vs[0][0], "f" * 64, _stats(0))
    elif case == "wrong_sid":
        item = (vs[1][0], vs[0][1], _stats(0))
    else:
        # a verdict that is NOT v6.1 cannot carry a gauntlet_stats entry
        from .test_gauntlet import gauntlet_registry
        r2, spec = gauntlet_registry(tmp_path / "v6")
        v = r2.record_verdict(spec["strategy_id"], "gauntlet", "fail",
                              {"protocol": "gauntlet-protocol-v6"}, "0" * 64)
        reg, item = r2, (spec["strategy_id"], entry_hash(v), _stats(0))
    before = reg.log_path.read_bytes()
    with pytest.raises(ValueError):
        reg.record_gauntlet_stats_batch(reg.snapshot(track_stats=True),
                                        [vs[2] + (_stats(2),), item]
                                        if case != "v6_verdict" else [item])
    assert reg.log_path.read_bytes() == before


def test_stats_batch_refuses_an_untracked_snapshot_or_an_oversized_batch(tmp_path):
    reg, vs = _statted_registry(tmp_path)
    before = reg.log_path.read_bytes()
    with pytest.raises(ValueError):
        reg.record_gauntlet_stats_batch(reg.snapshot(), [vs[0] + (_stats(0),)])
    too_many = [vs[0] + (_stats(0),)] * (registry_mod.STATS_BATCH_MAX + 1)
    with pytest.raises(ValueError):
        reg.record_gauntlet_stats_batch(reg.snapshot(track_stats=True), too_many)
    assert reg.log_path.read_bytes() == before


def test_stats_batch_refuses_a_log_that_grew_without_linking(tmp_path):
    reg, vs = _statted_registry(tmp_path)
    snap = reg.snapshot(track_stats=True)
    bad = {"version": 1, "ts_utc": FIXED_TS, "entry_type": "note",
           "prev_entry_hash": "f" * 64, "payload": {"text": "forked"}}
    with reg.log_path.open("a", encoding="utf-8") as f:
        f.write(registry_mod.canonical_json(bad) + "\n")
    before = reg.log_path.read_bytes()
    with pytest.raises(ChainMoved):
        reg.record_gauntlet_stats_batch(snap, [vs[0] + (_stats(0),)])
    assert reg.log_path.read_bytes() == before


def test_advance_tracks_stats_over_the_tail(tmp_path):
    reg, vs = _statted_registry(tmp_path)
    snap = reg.snapshot(track_stats=True)
    reg.record_gauntlet_stats(vs[3][0], vs[3][1], _stats(3))
    adv = reg.advance(snap)
    fresh = reg.snapshot(track_stats=True)
    _same(adv, fresh)
    assert adv.statted == fresh.statted == {vs[3][1]}
    assert dict(adv.v61_verdicts) == dict(fresh.v61_verdicts)


# Screen batch tests

from .registry import SCREEN_BATCH_MAX


def _proposed_registry(tmp_path, n=2):
    """A registry with n proposed strategies, built through the screen test fixture."""
    from .test_screen import screening_registry
    reg, spec = screening_registry(tmp_path)
    sids = [spec["strategy_id"]]
    for i in range(1, n):
        s = dict(spec, name=f"test breakout {i}", strategy_id=None)
        s["blocks"] = [dict(b) for b in spec["blocks"]]
        s["blocks"][0] = {"role": "entry", "type": "channel_breakout",
                          "params": {"lookback": 20 + i, "direction": "long"}}
        from .common import content_id
        s["strategy_id"] = content_id(s, "strategy_id")
        reg.register_strategy(s)
        sids.append(s["strategy_id"])
    return reg, sids


def test_screen_batch_writes_the_same_entries_as_the_three_record_calls(tmp_path):
    a, sids_a = _proposed_registry(tmp_path / "a")
    b, sids_b = _proposed_registry(tmp_path / "b")
    assert sids_a == sids_b
    metrics = {"trades": 1, "net_pnl": 0.5}
    # old path
    for sid, (v, to, why) in zip(sids_a, [("fail", "graveyard", "trade_count"),
                                          ("pass", "gauntlet", None)]):
        a.record_state_change(sid, "screened", "screen run, cutoff 2023-12-31")
        a.record_verdict(sid, "screened", v, metrics, "h" * 64)
        a.record_state_change(sid, to, why)
    # new path
    snap = b.snapshot()
    b.record_screen_outcomes_batch(snap, [
        (sids_b[0], "fail", metrics, "h" * 64, "graveyard", "trade_count",
         "screen run, cutoff 2023-12-31"),
        (sids_b[1], "pass", metrics, "h" * 64, "gauntlet", None,
         "screen run, cutoff 2023-12-31")])
    strip = lambda es: [(e["entry_type"], e["payload"]) for e in es]
    assert strip(a.entries()) == strip(b.entries())
    assert b.strategy_states()[sids_b[0]] == "graveyard"
    assert b.strategy_states()[sids_b[1]] == "gauntlet"


def test_screen_batch_refuses_a_spec_not_in_proposed_and_writes_nothing(tmp_path):
    reg, sids = _proposed_registry(tmp_path)
    reg.record_state_change(sids[1], "graveyard", "hand")
    before = reg.log_path.read_bytes()
    snap = reg.snapshot()
    with pytest.raises(ValueError, match="not 'proposed'"):
        reg.record_screen_outcomes_batch(snap, [
            (sids[0], "fail", {}, "h" * 64, "graveyard", "trade_count", "r"),
            (sids[1], "fail", {}, "h" * 64, "graveyard", "trade_count", "r")])
    assert reg.log_path.read_bytes() == before


def test_screen_batch_refuses_a_duplicate_and_an_oversized_batch(tmp_path):
    reg, sids = _proposed_registry(tmp_path)
    snap = reg.snapshot()
    item = (sids[0], "fail", {}, "h" * 64, "graveyard", "trade_count", "r")
    with pytest.raises(ValueError, match="twice"):
        reg.record_screen_outcomes_batch(snap, [item, item])
    with pytest.raises(ValueError, match="at most"):
        reg.record_screen_outcomes_batch(snap, [item] * (SCREEN_BATCH_MAX + 1))
