"""SP5 Phase 2 Task 4: sweep rotation (D6), sweep queues (D10), re-trials (D9).

The chained pre-declaration for D9/D10 is docs/notes/family-openness-v1.md;
D6 is declared in docs/2026-08-28-market-data-universe-design.md s5. Where
this module and that note disagree, THE NOTE WINS -- it is on the chain and
this file is not.

Three mechanisms, one theme: the gauntlet is the only place an edge can die.
Rotation is a cost SCHEDULE (every active cell is still swept, just not all
in one generation); the sibling queue replaces a refusal that discarded work
(so nothing proposed is dropped without either a verdict or a queue entry);
the re-trial window replaces a permanent exclusion with an expiry measured in
DATA, not wall-clock.

Run: python -m pytest pipeline/test_sp5_p2t4.py -q
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from . import cells, composer, loop, loop_state


# ============================ D6: sweep rotation ============================

def test_rotation_size_is_twelve():
    """Spec s5's declared window, as a module constant on the loop."""
    assert loop.ROTATION_SIZE == 12


def test_window_is_the_whole_set_when_it_fits_and_the_cursor_never_moves():
    """THE SMALL-SET RULE (pinned).

    An active set no larger than the window is returned WHOLE, in declared
    order, and the cursor is not created or advanced. This is what keeps every
    class whose active set already fits byte-identical to pre-rotation
    behaviour: the loop compares the window against the full active list and
    passes no --assets subset at all when they are equal.
    """
    state = {"classes": {}}
    assets = ["A", "B", "C"]
    assert loop_state.rotation_window(state, "fx", assets, 12) == assets
    loop_state.advance_rotation(state, "fx", len(assets), 12)
    assert loop_state.rotation_window(state, "fx", assets, 12) == assets
    assert "rotation_cursor" not in state["classes"].get("fx", {})


def test_window_is_empty_for_an_empty_active_set():
    """Rotating over zero active cells rotates nothing (crypto, this phase)."""
    state = {"classes": {}}
    assert loop_state.rotation_window(state, "crypto", [], 12) == []
    loop_state.advance_rotation(state, "crypto", 0, 12)
    assert state["classes"].get("crypto", {}).get("rotation_cursor") is None


def test_window_advances_and_wraps():
    state = {"classes": {}}
    assets = [f"A{i}" for i in range(10)]
    assert loop_state.rotation_window(state, "crypto", assets, 4) == assets[:4]
    loop_state.advance_rotation(state, "crypto", len(assets), 4)
    assert loop_state.rotation_window(state, "crypto", assets, 4) == assets[4:8]
    loop_state.advance_rotation(state, "crypto", len(assets), 4)
    # wraps at the end of the universe
    assert loop_state.rotation_window(state, "crypto", assets, 4) == [
        "A8", "A9", "A0", "A1"]


def test_rotation_sweeps_every_asset_with_equal_frequency():
    """Rotation is a SCHEDULE, never a selection mechanism (D6/D10).

    100 assets, window 12: after 25 generations the cursor has walked
    25 x 12 = 300 slots = exactly 3 full passes. Unequal frequency would make
    rotation a filter -- some cells tested more than others -- which is the
    one thing it must not be, because N accounting assumes every active cell
    is reachable.

    ASSERTED AS THE EXACT SEQUENCE, not as a count histogram (P2-T4 review,
    mutation note). A per-asset count of 3 is satisfied by a 2x or 3x cursor
    stride too -- any stride whose gcd with 100 is 4 still lands 3 starts per
    12 positions -- so the histogram alone does not pin the schedule, only its
    fairness. The concatenated sequence pins BOTH: coverage, frequency, and
    that consecutive windows are contiguous rather than merely balanced.
    """
    state = {"classes": {}}
    assets = [f"A{i:03d}" for i in range(100)]
    swept: list[str] = []
    for _ in range(25):
        swept.extend(loop_state.rotation_window(state, "crypto", assets, 12))
        loop_state.advance_rotation(state, "crypto", len(assets), 12)
    assert swept == [assets[i % 100] for i in range(300)]
    counts = {a: swept.count(a) for a in assets}
    assert set(counts.values()) == {3}


def test_cursor_round_trips_through_the_state_file(tmp_path):
    """The cursor is PERSISTED per class, not per process."""
    path = tmp_path / "loop_state.json"
    state = loop_state.load(path)
    assets = [f"A{i}" for i in range(10)]
    loop_state.rotation_window(state, "crypto", assets, 4)
    loop_state.advance_rotation(state, "crypto", len(assets), 4)
    loop_state.save(path, state)
    reloaded = loop_state.load(path)
    assert reloaded["classes"]["crypto"]["rotation_cursor"] == 4
    assert loop_state.rotation_window(reloaded, "crypto", assets, 4) == assets[4:8]


def test_rotation_classes_is_a_declaration_not_an_inference():
    """Which classes a generation ROTATES is declared, never derived.

    Design s5 scopes D6 to crypto ("a crypto generation sweeps a rotating
    window of 12 assets ... full coverage in 9 generations (100/12)"). It is
    declared as a tuple rather than inferred from "active set larger than the
    window" because equity_etf's active set is 16 assets -- above the window --
    so an inferred rule would silently window equity_etf 12-of-16 and break
    the Phase 2 sweep freeze. Same convention as LIVE_CLASSES/ACTIVE_CELLS:
    a denominator-affecting schedule change is a declaration, not a side
    effect of an asset count.
    """
    assert loop.ROTATION_CLASSES == ("crypto",)
    assert len(cells.CLASSES["equity_etf"]["assets"]) > loop.ROTATION_SIZE


# ============================ D10: caps become queues ============================

def test_validate_family_no_longer_refuses_an_over_cap_sweep():
    """D10: the "exceeds cap - rejected, not clipped" refusal is GONE.

    family-openness-v1: "validate_family's 'exceeds cap, rejected, not
    clipped' refusal is replaced by split-and-carry". A capacity limit
    presented as a judgment is exactly the mistake protocol-v6 retired.
    """
    from .test_composer import good_family, ACCEPTED
    fam = good_family(sweep=[
        {"block": 0, "param": "t_min", "values": [2.0, 2.5, 3.0]},
        {"block": 0, "param": "max_lookback", "values": [60, 75, 90, 105, 120]},
        {"block": 1, "param": "mult", "values": [1.5, 2.0, 2.5, 3.0, 3.5]},
    ])  # 3 x 5 x 5 = 75 siblings, against a cap of 25
    errs = composer.validate_family(fam, ACCEPTED, 25)
    assert not [e for e in errs if "cap" in e], errs


def test_split_for_cycle_splits_rather_than_drops():
    specs = [{"strategy_id": f"s{i}"} for i in range(75)]
    this_cycle, queued = composer.split_for_cycle(specs, 60)
    assert len(this_cycle) == 60
    assert len(queued) == 15
    # THE INVARIANT: nothing is lost across the split.
    assert [s["strategy_id"] for s in this_cycle + queued] == \
        [s["strategy_id"] for s in specs]


def test_split_for_cycle_is_a_no_op_under_the_cap():
    specs = [{"strategy_id": f"s{i}"} for i in range(10)]
    this_cycle, queued = composer.split_for_cycle(specs, 60)
    assert this_cycle == specs and queued == []


def test_split_for_cycle_with_no_cap_keeps_everything():
    """cap None/0 means "no per-cycle bound" -- a manual run with nowhere to
    persist a queue must register everything rather than drop the remainder."""
    specs = [{"strategy_id": f"s{i}"} for i in range(75)]
    assert composer.split_for_cycle(specs, None) == (specs, [])
    assert composer.split_for_cycle(specs, 0) == (specs, [])


def test_queue_round_trips_per_class(tmp_path):
    path = tmp_path / "loop_state.json"
    state = loop_state.load(path)
    assert loop_state.queue_depth(state, "fx") == 0
    loop_state.enqueue_specs(state, "fx", [{"strategy_id": "a"}, {"strategy_id": "b"}])
    loop_state.save(path, state)

    state = loop_state.load(path)
    assert loop_state.queue_depth(state, "fx") == 2
    taken = loop_state.dequeue_specs(state, "fx", 1)
    assert [s["strategy_id"] for s in taken] == ["a"]
    assert loop_state.queue_depth(state, "fx") == 1
    # a drained-empty queue leaves no dangling key
    loop_state.dequeue_specs(state, "fx", 5)
    assert loop_state.queue_depth(state, "fx") == 0
    assert "sibling_queue" not in state["classes"]["fx"]


def test_queues_are_class_scoped(tmp_path):
    state = {"classes": {}}
    loop_state.enqueue_specs(state, "fx", [{"strategy_id": "a"}])
    assert loop_state.queue_depth(state, "fx") == 1
    assert loop_state.queue_depth(state, "crypto") == 0


def test_refresh_queues_recovers_a_composer_write(tmp_path):
    """The composer runs as a SUBPROCESS and writes the queue into the same
    loop_state.json the loop holds in memory. Without this the loop's own
    end-of-cycle save() would clobber the queue the composer just wrote.
    """
    path = tmp_path / "loop_state.json"
    on_disk = {"classes": {"fx": {"threshold": 25, "watermark": 4,
                                  "sibling_queue": [{"strategy_id": "q1"}]}}}
    loop_state.save(path, on_disk)
    in_memory = {"classes": {"fx": {"threshold": 25, "watermark": 4}}}
    loop_state.refresh_queues(in_memory, path)
    assert loop_state.queue_depth(in_memory, "fx") == 1
    # ... and a queue that DRAINED to empty on disk must not be resurrected
    loop_state.save(path, {"classes": {"fx": {"threshold": 25}}})
    loop_state.refresh_queues(in_memory, path)
    assert loop_state.queue_depth(in_memory, "fx") == 0


# ============================ D9: the re-trial window ============================

def _registered_strategy(tmp_path, name="s"):
    """A registry holding exactly one registered strategy, plus its spec."""
    from .registry import Registry
    from .test_pipeline import make_strategy, register_example_blocks
    reg = Registry(tmp_path / "registry_log.jsonl")
    reg.register_card({"card_id": f"card-{name}", "claim": "c", "quote": "q",
                       "topics": [], "tags": {"asset_classes": ["crypto"]},
                       "review": {"status": "pending", "reject_reason": None},
                       "source": {}, "links": [], "credibility_tier": "practitioner"})
    reg.review_card(f"card-{name}", "accepted", "coen")
    register_example_blocks(reg)
    spec = make_strategy([f"card-{name}"])
    reg.register_strategy(spec)
    return reg, spec


def _later_run(spec, run_id="a-later-run"):
    """The same COMPOSITION proposed by a LATER run -- which is what a
    re-trial candidate actually is.

    Handing the oracle back the very spec object it already has on the chain
    makes the candidate its own prior, in its own run, and the rule refuses
    that outright: family-openness-v1, "a composition duplicating one already
    registered earlier in the same run is still dropped ... those ARE
    same-data by construction". The fingerprint covers universe and blocks
    only, so changing run_id keeps the collision these tests are about.
    """
    out = json.loads(json.dumps(spec))
    out["generator"] = dict(out["generator"], run_id=run_id)
    assert (composer.composition_fingerprint(out)
            == composer.composition_fingerprint(spec))
    return out


def test_retrial_window_days_is_183():
    assert composer.RETRIAL_WINDOW_DAYS == 183


def test_retrial_window_opens_exactly_at_183_days():
    """Both sides of the boundary, on the PURE function.

    The clock runs on the DATA, not the wall (family-openness-v1): the
    gauntlet is deterministic, so a same-data re-test is a known answer bought
    at the price of a higher bar for every live survivor.
    """
    cutoff = date(2023, 12, 31)
    just_short = (cutoff + timedelta(days=182)).isoformat()
    exactly = (cutoff + timedelta(days=183)).isoformat()
    assert composer.retrial_window_open(cutoff.isoformat(), just_short) is False
    assert composer.retrial_window_open(cutoff.isoformat(), exactly) is True
    assert composer.retrial_window_open(
        cutoff.isoformat(), (cutoff + timedelta(days=900)).isoformat()) is True


def test_retrial_window_tolerates_a_timestamped_data_end():
    """data_end can carry a time component ("2026-08-27 00:00:00") -- the
    comparison is on DATES, exactly like screen.load_bars' fence."""
    assert composer.retrial_window_open("2023-12-31", "2026-08-27 00:00:00") is True


def test_retrial_window_is_closed_on_unusable_dates():
    """Anything unparseable is CLOSED, never open: an expiry that cannot be
    established is not an expiry."""
    assert composer.retrial_window_open(None, "2026-08-27") is False
    assert composer.retrial_window_open("2023-12-31", "") is False
    assert composer.retrial_window_open("not-a-date", "2026-08-27") is False


def test_screen_siblings_still_drops_a_collision_by_default():
    """No oracle passed = today's behaviour, exactly. Every existing caller
    (and every existing test) keeps the permanent exclusion."""
    specs = [{"strategy_id": "new", "universe": {"assets": ["BTCUSD"],
                                                 "timeframe": "1d",
                                                 "asset_class": "crypto",
                                                 "session": "24x7"},
              "blocks": []}]
    fp = composer.composition_fingerprint(specs[0])
    kept, notes, malformed = composer.screen_siblings(specs, {fp: "old"}, {})
    assert kept == [] and not malformed
    assert any("already registered as old" in n for n in notes)


def test_screen_siblings_admits_a_retrial_when_the_oracle_says_so():
    specs = [{"strategy_id": "new", "universe": {"assets": ["BTCUSD"],
                                                 "timeframe": "1d",
                                                 "asset_class": "crypto",
                                                 "session": "24x7"},
              "blocks": []}]
    fp = composer.composition_fingerprint(specs[0])
    kept, notes, malformed = composer.screen_siblings(
        specs, {fp: "old"}, {}, retrial_ok=lambda sid, spec: sid == "old")
    assert [s["strategy_id"] for s in kept] == ["new"]
    assert not malformed
    assert any("RE-TRIAL" in n for n in notes)


def test_in_run_duplicates_are_never_re_trials():
    """family-openness-v1: in-run and in-cycle duplicates remain
    malformed/dropped -- those ARE same-data by construction, so the oracle is
    never consulted for them."""
    spec = {"strategy_id": "new", "universe": {"assets": ["BTCUSD"],
                                               "timeframe": "1d",
                                               "asset_class": "crypto",
                                               "session": "24x7"},
            "blocks": []}
    fp = composer.composition_fingerprint(spec)
    # duplicates a DIFFERENT family in the same run -> dropped, not re-tried
    kept, notes, malformed = composer.screen_siblings(
        [spec], {}, {fp: "otherfam"}, retrial_ok=lambda sid, s: True)
    assert kept == [] and not malformed
    assert any("duplicates family" in n for n in notes)

    # THE BLOCKER CASE (F1): buried on the chain AND already re-tried by an
    # earlier family in THIS run. The re-trial branch must not out-rank the
    # in-run duplicate drop, or one composition chains twice on identical
    # data -- the first duplicate composition fingerprint in the chain's
    # history. The earlier test above only exercised known_fps={}, which is
    # exactly why this slipped through.
    kept, notes, malformed = composer.screen_siblings(
        [spec], {fp: "old"}, {fp: "famA"}, retrial_ok=lambda sid, s: True)
    assert kept == [], (
        "a composition already re-tried by an earlier family in this run was "
        "admitted a SECOND time as a re-trial -- same run, same data")
    assert not malformed
    # It falls through to the known_fps drop, whose message is UNCHANGED from
    # the pre-D9 wording: the reviewer's fix is deliberately message-
    # preserving so the no-oracle path stays byte-identical. What matters is
    # that no note calls it a re-trial.
    assert not any("RE-TRIAL" in n for n in notes), notes
    assert any("already registered as old" in n for n in notes)
    # two identical siblings in ONE family -> malformed, whole family dies
    kept, notes, malformed = composer.screen_siblings(
        [spec, dict(spec, strategy_id="new2")], {}, {},
        retrial_ok=lambda sid, s: True)
    assert malformed


def test_one_run_never_chains_a_composition_twice_even_with_the_window_open(
        tmp_path, monkeypatch):
    """F1 end to end, through composer.run(): the chain-wide invariant.

    Two families in ONE run, proposing the SAME compositions, against a chain
    where those compositions are already registered and the re-trial window is
    forced open. Family A's siblings are legitimate re-trials; family B's are
    in-run duplicates of A and must be dropped.

    NOTE THE INVARIANT IS PER RUN, not chain-wide. Chain-wide fingerprint
    uniqueness held for all 2,775 pre-D9 registrations and D9 deliberately
    ends it: a re-trial IS a second registration of a buried composition, on
    new data, under a new id. What may never happen is two registrations of
    one composition in the SAME run -- that is same-data by construction, and
    it is what the run_fps guard exists to stop.
    """
    from .test_composer import seeded_registry, good_family
    from .registry import Registry
    reg_path, cid = seeded_registry(tmp_path)
    state_path = tmp_path / "loop_state.json"

    def _run(run_id, families):
        return composer.run(["--registry", str(reg_path), "--run-id", run_id,
                             "--loop-state", str(state_path)],
                            propose_fn=lambda cards: families)

    assert _run("first", [good_family(card_ids=[cid])]) == 0
    monkeypatch.setattr(composer, "retrial_oracle",
                        lambda *a, **k: (lambda sid, spec: True))
    assert _run("second", [good_family(card_ids=[cid], family="fam_a"),
                           good_family(card_ids=[cid], family="fam_b")]) == 0

    specs = [e["payload"] for e in Registry(reg_path).entries()
             if e["entry_type"] == "strategy_registered"]
    per_run: dict[str, list[str]] = {}
    for s in specs:
        per_run.setdefault(s["generator"]["run_id"], []).append(
            composer.composition_fingerprint(s))
    for run_id, fps in per_run.items():
        assert len(fps) == len(set(fps)), (
            f"run {run_id} registered {len(fps) - len(set(fps))} composition(s) "
            f"twice -- a same-data re-test the window exists to forbid")
    assert len(specs) == 18      # 9 originals + 9 re-trials, never 27


def test_a_composition_with_no_burying_verdict_never_expires(tmp_path):
    """T5's declared resolution, and it is a TIGHTENING.

    family-openness-v1: "Where the matching registration is NOT buried, in
    quarantine or live, there is no burying verdict, no expiry, and the
    composition stays permanently excluded: the strategy is currently under
    test and a second copy of it is a duplicate, not a re-trial."
    """
    reg, spec = _registered_strategy(tmp_path, "live")
    sid = spec["strategy_id"]
    reg.record_state_change(sid, "screened")
    reg.record_state_change(sid, "gauntlet")
    reg.record_state_change(sid, "quarantine")

    art = tmp_path / "artifacts"
    (art / sid / "gauntlet").mkdir(parents=True)
    (art / sid / "gauntlet" / "config.json").write_text(
        json.dumps({"cutoff": "2019-01-01"}), encoding="utf-8")

    ok = composer.retrial_oracle(reg, art, lambda cell: "2026-08-27")
    assert ok(sid, _later_run(spec)) is False, (
        "a quarantine registration has no burying verdict and therefore no "
        "expiry -- it must stay permanently excluded")


def test_a_buried_composition_expires_once_its_cell_data_moves_on(tmp_path):
    reg, spec = _registered_strategy(tmp_path, "buried")
    sid = spec["strategy_id"]
    reg.record_state_change(sid, "screened")
    reg.record_state_change(sid, "gauntlet")
    reg.record_state_change(sid, "graveyard", "failed the gauntlet")

    art = tmp_path / "artifacts"
    (art / sid / "gauntlet").mkdir(parents=True)
    (art / sid / "gauntlet" / "config.json").write_text(
        json.dumps({"cutoff": "2023-12-31"}), encoding="utf-8")

    candidate = _later_run(spec)
    open_now = composer.retrial_oracle(reg, art, lambda cell: "2026-08-27")
    assert open_now(sid, candidate) is True
    still_shut = composer.retrial_oracle(reg, art, lambda cell: "2024-03-01")
    assert still_shut(sid, candidate) is False
    # ... and the SAME composition proposed again inside the burial's own run
    # is never a re-trial, however open the window
    assert open_now(sid, spec) is False


def test_a_later_copy_under_test_shuts_the_window_the_old_burial_opened(tmp_path):
    """The edge case D9 itself creates, and the only one that can un-tighten
    the tightening.

    Once a composition can be registered twice, the OLDEST registration may be
    buried while a LATER re-trial of it sits in quarantine. known_fps names
    only the first id, so an oracle that trusted it would readmit a
    composition that is currently under test -- exactly what
    family-openness-v1 forbids ("a second copy of it is a duplicate, not a
    re-trial"). Every registration of the fingerprint has to be buried.
    """
    reg, spec = _registered_strategy(tmp_path, "twice")
    old = spec["strategy_id"]
    reg.record_state_change(old, "screened")
    reg.record_state_change(old, "graveyard", "failed")
    # the re-trial: same composition, a new id, now in quarantine
    retried = dict(spec, created_utc="2026-08-31T00:00:00Z")
    from .common import content_id
    retried["strategy_id"] = content_id(retried, "strategy_id")
    reg.register_strategy(retried)
    new = retried["strategy_id"]
    assert new != old
    assert composer.composition_fingerprint(retried) == \
        composer.composition_fingerprint(spec)
    reg.record_state_change(new, "screened")
    reg.record_state_change(new, "gauntlet")
    reg.record_state_change(new, "quarantine")

    art = tmp_path / "artifacts"
    (art / old).mkdir(parents=True)
    (art / old / "config.json").write_text(json.dumps({"cutoff": "2019-01-01"}),
                                           encoding="utf-8")
    ok = composer.retrial_oracle(reg, art, lambda cell: "2026-08-27")
    assert ok(old, _later_run(spec)) is False, (
        "the window opened off a stale burial while a live copy of the same "
        "composition is under test")


def test_a_buried_composition_with_no_readable_cutoff_stays_excluded(tmp_path):
    """No cutoff on disk = no establishable expiry = permanent exclusion.

    This is also why every tmp-registry test in the suite is unaffected by
    D9: nothing writes an artifact bundle, so no oracle ever opens."""
    reg, spec = _registered_strategy(tmp_path, "nobundle")
    sid = spec["strategy_id"]
    reg.record_state_change(sid, "screened")
    reg.record_state_change(sid, "graveyard", "screen fail")
    ok = composer.retrial_oracle(reg, tmp_path / "artifacts",
                                 lambda cell: "2026-08-27")
    assert ok(sid, _later_run(spec)) is False


def test_a_buried_composition_uses_the_screen_cutoff_when_it_never_reached_gauntlet(tmp_path):
    reg, spec = _registered_strategy(tmp_path, "screenedout")
    sid = spec["strategy_id"]
    reg.record_state_change(sid, "graveyard", "screen fail")
    art = tmp_path / "artifacts"
    (art / sid).mkdir(parents=True)
    (art / sid / "config.json").write_text(
        json.dumps({"cutoff": "2023-12-31"}), encoding="utf-8")
    ok = composer.retrial_oracle(reg, art, lambda cell: "2026-08-27")
    assert ok(sid, _later_run(spec)) is True


# ================= D10 end to end, through composer.run() =================

def _over_cap_family(card_id):
    """A crypto family whose sweep is 3 x 5 x 5 = 75 siblings -- the exact
    shape validate_family used to refuse outright against a cap of 25."""
    from .test_composer import good_family
    return good_family(card_ids=[card_id], sweep=[
        {"block": 0, "param": "t_min", "values": [2.0, 2.5, 3.0]},
        {"block": 0, "param": "max_lookback", "values": [60, 75, 90, 105, 120]},
        {"block": 1, "param": "mult", "values": [1.5, 2.0, 2.5, 3.0, 3.5]},
    ])


def _registered_ids(reg_path):
    from .registry import Registry
    return [e["payload"]["strategy_id"] for e in Registry(reg_path).entries()
            if e["entry_type"] == "strategy_registered"]


def test_over_cap_family_registers_a_window_and_queues_the_rest(tmp_path):
    """THE INVARIANT, end to end: no proposed variation is dropped without
    either a gauntlet verdict or a queue entry.

    Before D10 this family produced ZERO registrations and zero queue
    entries -- 75 proposed variations discarded on a capacity bound. Now the
    first 25 are chained and the other 50 are on disk, waiting.
    """
    from .test_composer import seeded_registry
    reg_path, cid = seeded_registry(tmp_path)
    state_path = tmp_path / "loop_state.json"

    rc = composer.run(["--registry", str(reg_path), "--run-id", "r1",
                       "--sibling-cap", "25", "--loop-state", str(state_path)],
                      propose_fn=lambda cards: [_over_cap_family(cid)])
    assert rc == 0
    chained = _registered_ids(reg_path)
    assert len(chained) == 25
    state = loop_state.load(state_path)
    assert loop_state.queue_depth(state, "crypto") == 50
    # nothing lost: the 25 chained and the 50 queued are disjoint and total 75
    queued_ids = [s["strategy_id"] for s in state["classes"]["crypto"]["sibling_queue"]]
    assert len(set(chained) | set(queued_ids)) == 75


def test_the_next_cycle_drains_the_queue_before_proposing_anything(tmp_path):
    from .test_composer import seeded_registry
    reg_path, cid = seeded_registry(tmp_path)
    state_path = tmp_path / "loop_state.json"
    composer.run(["--registry", str(reg_path), "--run-id", "r1",
                  "--sibling-cap", "25", "--loop-state", str(state_path)],
                 propose_fn=lambda cards: [_over_cap_family(cid)])

    def _must_not_propose(cards):
        raise AssertionError("a draining cycle must propose nothing -- and so "
                             "must make no metered model call")

    rc = composer.run(["--registry", str(reg_path), "--run-id", "r2",
                       "--sibling-cap", "25", "--loop-state", str(state_path)],
                      propose_fn=_must_not_propose)
    assert rc == 0
    assert len(_registered_ids(reg_path)) == 50
    assert loop_state.queue_depth(loop_state.load(state_path), "crypto") == 25

    rc = composer.run(["--registry", str(reg_path), "--run-id", "r3",
                       "--sibling-cap", "25", "--loop-state", str(state_path)],
                      propose_fn=_must_not_propose)
    assert rc == 0
    # every one of the 75 proposed variations is now on the chain
    assert len(_registered_ids(reg_path)) == 75
    assert loop_state.queue_depth(loop_state.load(state_path), "crypto") == 0


def test_a_dry_run_never_consumes_the_queue(tmp_path):
    """The loop invokes the composer TWICE per cycle (a --dry-run preflight,
    then the real run). A dry run that drained the queue would leave the real
    run with nothing to register."""
    from .test_composer import seeded_registry
    reg_path, cid = seeded_registry(tmp_path)
    state_path = tmp_path / "loop_state.json"
    composer.run(["--registry", str(reg_path), "--run-id", "r1",
                  "--sibling-cap", "25", "--loop-state", str(state_path)],
                 propose_fn=lambda cards: [_over_cap_family(cid)])
    before = loop_state.load(state_path)

    rc = composer.run(["--registry", str(reg_path), "--run-id", "r2-dry",
                       "--sibling-cap", "25", "--loop-state", str(state_path),
                       "--dry-run"],
                      propose_fn=lambda cards: [_over_cap_family(cid)])
    assert rc == 0
    assert loop_state.load(state_path) == before
    assert len(_registered_ids(reg_path)) == 25


def test_a_family_that_fits_the_cap_queues_nothing(tmp_path):
    """The byte-identity half of D10: a family under the cap behaves exactly
    as it did before this change -- fully registered, no queue key written."""
    from .test_composer import seeded_registry, good_family
    reg_path, cid = seeded_registry(tmp_path)
    state_path = tmp_path / "loop_state.json"
    rc = composer.run(["--registry", str(reg_path), "--run-id", "r1",
                       "--sibling-cap", "60", "--loop-state", str(state_path)],
                      propose_fn=lambda cards: [good_family(card_ids=[cid])])
    assert rc == 0
    assert len(_registered_ids(reg_path)) == 9
    assert loop_state.queue_depth(loop_state.load(state_path), "crypto") == 0


# ================= D6 end to end, through composer.run() =================

def test_assets_subset_narrows_the_sweep_without_touching_the_active_set(tmp_path):
    from .test_composer_fx import fx_family, _register_accepted
    from .registry import Registry
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["fx"])
    rc = composer.run(["--registry", str(reg_path), "--run-id", "fxwin",
                       "--asset-class", "fx", "--assets", "EUR,GBP"],
                      propose_fn=lambda cards: [fx_family(card_ids=[cid])])
    assert rc == 0
    specs = [e["payload"] for e in Registry(reg_path).entries()
             if e["entry_type"] == "strategy_registered"]
    assert {s["universe"]["assets"][0] for s in specs} == {"EUR", "GBP"}
    assert len(specs) == 3 * 2          # 3 swept `fast` values x 2 cells
    # the ACTIVE SET is untouched -- a window is a view onto it, not an edit
    assert len(cells.active_cells("fx")) == 12


def test_assets_subset_refuses_an_inactive_asset(tmp_path):
    with pytest.raises(ValueError, match="not ACTIVE cells"):
        composer.sweep_cells("fx", ["EUR", "NOTAPAIR"])


def test_assets_is_refused_on_the_legacy_pooled_path(tmp_path, capsys):
    """The refusal is keyed on the ROUTING DISPATCH, not on the class name
    (F2), so it lifts by itself on the commit that makes a window legal."""
    from .test_composer import seeded_registry
    reg_path, cid = seeded_registry(tmp_path)
    rc = composer.run(["--registry", str(reg_path), "--assets", "BTCUSDT"],
                      propose_fn=lambda cards: [])
    assert rc == 1
    out = capsys.readouterr().out
    assert "legacy pooled path" in out and "expander_for" in out


def test_the_assets_refusal_lifts_when_the_class_leaves_the_pooled_path(
        tmp_path, monkeypatch):
    """F2's coupling, from the composer's side: nothing about `--assets`
    depends on the string "crypto". Switch the dispatch (what Phase 3 does)
    and the same invocation is accepted."""
    from .test_composer import seeded_registry, good_family
    monkeypatch.setattr(composer, "expander_for",
                        lambda cls: composer.expand_family_for_class)
    gate = dict(cells.ACTIVE_CELLS["crypto"])
    cells.ACTIVE_CELLS["crypto"] = {"assets": cells.ASSETS[:3],
                                    "timeframes": ("1d",)}
    try:
        reg_path, cid = seeded_registry(tmp_path)
        rc = composer.run(["--registry", str(reg_path), "--run-id", "p3",
                           "--assets", cells.ASSETS[0], "--dry-run"],
                          propose_fn=lambda cards: [good_family(card_ids=[cid])])
        assert rc == 0, "the refusal did not lift with the routing dispatch"
    finally:
        cells.ACTIVE_CELLS["crypto"] = gate


# ================= the loop's half of D6 and D10 =================

def test_loop_passes_no_assets_flag_while_nothing_rotates(tmp_path, monkeypatch):
    """The Phase 2 sweep freeze, asserted on the ACTUAL composer argv.

    Not one live class rotates today, so both composer invocations must carry
    exactly the argv they carried before D6 -- no --assets anywhere."""
    from .test_loop import FakeRunner, _mk_layer, _seed_crypto_caught_up, _add_cards
    monkeypatch.setattr(loop, "_live_task_window_s", lambda *a, **k: None)
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _add_cards(reg, 4, status="pending", asset_classes=["crypto"], prefix="pend")

    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    composer_calls = [c for c in fr.calls if "pipeline.composer" in c]
    assert composer_calls, "no composer stage ran -- vacuous freeze proof"
    for call in composer_calls:
        assert "--assets" not in call, (
            f"the loop passed a rotation subset to the composer: {call}")


def test_the_cursor_never_advances_when_no_window_was_emitted(tmp_path, monkeypatch):
    """Observation B: the advance is gated on `rotates`, not on membership.

    Those are different questions. ROTATION_CLASSES membership says the class
    SHOULD rotate; `rotates` says a window was actually emitted THIS cycle.
    In a half-landed Phase 3 -- ACTIVE_CELLS["crypto"] populated but
    expander_for still pooled -- _sweep_window correctly returns rotates=False
    and the loop passes no --assets, so nothing is swept from a window. A
    membership-gated advance would nonetheless walk the cursor 12 positions on
    every completed generation, silently skipping the assets it stepped over:
    exactly what D6's own comment says must never happen.

    Simulated at the level the defect lives at -- a non-empty active set with
    the pooled expander still in place -- rather than by stubbing the loop.
    """
    from .test_loop import (FakeRunner, _mk_layer, _add_cards,
                            _seed_all_classes_caught_up)
    monkeypatch.setattr(loop, "_live_task_window_s", lambda *a, **k: None)
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    # A card population exists only so registry_log.jsonl exists on disk (the
    # cycle's commit step reads it); every class is then seeded caught-up, so
    # NO class is over threshold on cards.
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="p")
    _seed_all_classes_caught_up(layer, layer / "registry_log.jsonl")
    state_path = layer / "logs" / "loop_state.json"
    # CRYPTO must be the class that fires, or this test proves nothing: an
    # fx cycle never touches the crypto cursor under EITHER the correct gate
    # or the membership-only mutant. A queued sibling is the cheapest way to
    # make crypto the picked class (F4) without inventing a card population.
    st = loop_state.load(state_path)
    loop_state.enqueue_specs(st, "crypto", [{"strategy_id": "q1"}])
    loop_state.save(state_path, st)

    gate = dict(cells.ACTIVE_CELLS["crypto"])
    cells.ACTIVE_CELLS["crypto"] = {"assets": cells.ASSETS[:20],
                                    "timeframes": ("1d",)}
    try:
        assert composer.expander_for("crypto") is composer.expand_family, (
            "premise: this test simulates the HALF-landed Phase 3")
        assert len(cells.active_cells("crypto")) == 20 > loop.ROTATION_SIZE, (
            "premise: the active set must exceed the window, or "
            "advance_rotation returns early under both gates and the mutant "
            "survives for the wrong reason")
        fr = FakeRunner()
        assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
        items = json.loads((layer / "logs" / "pipeline_status.json")
                           .read_text(encoding="utf-8"))["items"]
        assert items["outcome"] == "cycle_complete", items
        assert items["asset_class"] == "crypto", items
        assert not any("--assets" in c for c in fr.calls), (
            "premise: no window was emitted this cycle")
        entry = loop_state.load(state_path)["classes"]["crypto"]
        assert "rotation_cursor" not in entry, (
            f"the cursor advanced to {entry.get('rotation_cursor')} on a cycle "
            f"that emitted no window -- every asset it stepped over is skipped "
            f"silently, which is the one thing a schedule must never do")
    finally:
        cells.ACTIVE_CELLS["crypto"] = gate


def test_loop_reports_queue_depth_and_does_not_clobber_it(tmp_path, monkeypatch):
    """D10's visibility half, plus the cross-process hazard it creates.

    The composer writes BOTH queue keys into logs/loop_state.json as a
    SUBPROCESS. Here a fake composer stage does exactly that mid-cycle; the
    loop must fold them back in (refresh_queues) rather than overwrite them
    with its own older in-memory copy at the end-of-cycle save, and must
    report the depths in pipeline_status.json so a parked queue is visible
    without opening the state file.

    `sibling_queue_dead` is asserted alongside `sibling_queue` on purpose
    (P2-T4 re-review, M12). refresh_queues carries both keys, but only the
    live one was pinned across the loop's save: narrowing its `queue_keys`
    tuple to ("sibling_queue",) left the whole suite green while silently
    destroying the casualty list -- composer writes queue 25 / dead 25, loop
    saves, dead becomes 0. Those specs would vanish from the live queue AND
    from the record of why they were parked, which is the silent drop D10
    exists to remove. test_a_poison_queued_spec_does_not_wedge_the_class
    drives the composer directly and never crosses the loop's save, so it
    cannot see this.
    """
    from .test_loop import FakeRunner, _mk_layer, _seed_crypto_caught_up, _add_cards
    monkeypatch.setattr(loop, "_live_task_window_s", lambda *a, **k: None)
    layer, reg = _mk_layer(tmp_path, accepted_fx=30)
    _seed_crypto_caught_up(layer, 30)
    _add_cards(reg, 4, status="pending", asset_classes=["crypto"], prefix="pend")
    state_path = layer / "logs" / "loop_state.json"

    class QueueWritingRunner(FakeRunner):
        def __call__(self, argv, **kw):
            if "pipeline.composer" in argv and "--dry-run" not in argv:
                st = loop_state.load(state_path)
                loop_state.enqueue_specs(st, "crypto",
                                         [{"strategy_id": "q1"}, {"strategy_id": "q2"}])
                loop_state.record_dead_specs(st, "crypto", [
                    {"spec": {"strategy_id": "d1"}, "reason": "card revoked"}])
                loop_state.save(state_path, st)
            return super().__call__(argv, **kw)

    assert loop.run(["--once", "--layer", str(layer)], runner=QueueWritingRunner()) == 0
    saved = loop_state.load(state_path)
    assert loop_state.queue_depth(saved, "crypto") == 2, (
        "the loop's end-of-cycle save clobbered the queue the composer wrote")
    assert loop_state.dead_queue_depths(saved).get("crypto") == 1, (
        "the loop's end-of-cycle save destroyed the casualty list the composer "
        "wrote -- those specs are gone from the queue AND from the record of "
        "why, which is the silent drop D10 exists to remove")
    items = json.loads((layer / "logs" / "pipeline_status.json")
                       .read_text(encoding="utf-8"))["items"]
    assert items["outcome"] == "cycle_complete"
    assert items["queue_crypto"] == "2"
    assert items["queue_dead_crypto"] == "1"


def test_a_non_empty_queue_is_itself_a_trigger(tmp_path):
    """F4: the exemption has to sit at pick_class too, not only at the
    no_new_accepted_cards guard.

    A class whose card flow goes quiet never crosses its watermark threshold,
    so it is never picked, so its queue never drains -- the silent drop D10
    removes, moved one gate earlier. A queue is a second, independent reason
    to be picked; the threshold arithmetic itself is untouched.
    """
    counts = {c: 0 for c in cells.LIVE_CLASSES}
    state = {"classes": {c: {"threshold": 25, "watermark": 0}
                         for c in cells.LIVE_CLASSES}}
    assert loop_state.pick_class(state, counts) is None
    loop_state.enqueue_specs(state, "bond_etf", [{"strategy_id": "q1"}])
    assert loop_state.pick_class(state, counts) == "bond_etf"
    # ...and draining it removes the reason again
    loop_state.dequeue_specs(state, "bond_etf", 5)
    assert loop_state.pick_class(state, counts) is None


def test_a_dead_queue_entry_is_not_a_trigger(tmp_path):
    """A parked-forever spec must NOT keep re-firing its class: it needs a
    human, not another cycle. Only the live queue triggers."""
    counts = {c: 0 for c in cells.LIVE_CLASSES}
    state = {"classes": {c: {"threshold": 25, "watermark": 0}
                         for c in cells.LIVE_CLASSES}}
    loop_state.record_dead_specs(state, "bond_etf",
                                 [{"spec": {"strategy_id": "d1"}, "reason": "x"}])
    assert loop_state.pick_class(state, counts) is None


def test_a_poison_queued_spec_does_not_wedge_the_class(tmp_path, capsys):
    """F3: a queued spec can outlive the card that justified it.

    50 queued, then the cited card's acceptance is revoked. Before the fix
    every subsequent drain raised on the first spec, chained nothing, and left
    the queue at 50 forever -- 50 proposed variations with neither a verdict
    nor any prospect of one, which inverts D10's invariant instead of serving
    it. Now the offenders are parked visibly and the drain keeps going.
    """
    from .test_composer import seeded_registry
    from .registry import Registry
    reg_path, cid = seeded_registry(tmp_path)
    state_path = tmp_path / "loop_state.json"
    composer.run(["--registry", str(reg_path), "--run-id", "r1",
                  "--sibling-cap", "25", "--loop-state", str(state_path)],
                 propose_fn=lambda cards: [_over_cap_family(cid)])
    assert loop_state.queue_depth(loop_state.load(state_path), "crypto") == 50

    Registry(reg_path).review_card(cid, "rejected", "coen", "off_topic")

    rc = composer.run(["--registry", str(reg_path), "--run-id", "r2",
                       "--sibling-cap", "25", "--loop-state", str(state_path)],
                      propose_fn=lambda cards: [])
    assert rc == 0, "the drain raised instead of parking the casualties"
    state = loop_state.load(state_path)
    assert loop_state.queue_depth(state, "crypto") == 25, "the queue did not move"
    dead = state["classes"]["crypto"]["sibling_queue_dead"]
    assert len(dead) == 25
    assert all("not registered+accepted" in d["reason"] for d in dead), dead
    assert "DEAD queued" in capsys.readouterr().out
    # The drain also had to get PAST the "no accepted cards" refusal, which is
    # a proposal precondition, not a drain one -- checked first it wedges the
    # queue one gate earlier, for the same reason.

    # ...and the class is not wedged: the next cycle drains the rest too
    assert composer.run(["--registry", str(reg_path), "--run-id", "r3",
                         "--sibling-cap", "25", "--loop-state", str(state_path)],
                        propose_fn=lambda cards: []) == 0
    state = loop_state.load(state_path)
    assert loop_state.queue_depth(state, "crypto") == 0
    assert len(state["classes"]["crypto"]["sibling_queue_dead"]) == 50


def test_dead_queue_depth_is_reported_only_when_non_zero(tmp_path):
    state = {"classes": {"crypto": {"threshold": 25}}}
    assert "queue_dead_crypto" not in loop._queue_items(state)
    assert loop._queue_items(state)["queue_crypto"] == "0"
    loop_state.record_dead_specs(state, "crypto",
                                 [{"spec": {"strategy_id": "d"}, "reason": "r"}])
    assert loop._queue_items(state)["queue_dead_crypto"] == "1"


def test_refresh_queues_never_stubs_a_class_it_was_not_carrying(tmp_path):
    """F7: loop_state.json has writers other than the loop.

    An earlier version minted a bare {"threshold": 25} for every class it saw
    on disk, and the loop's end-of-cycle save then wrote that stub over a real
    entry -- a bond_etf watermark of 77 came back as a stub. A disk-only class
    is now copied WHOLE (when it carries queue state worth rescuing) or left
    alone entirely.
    """
    path = tmp_path / "loop_state.json"
    loop_state.save(path, {"classes": {
        "bond_etf": {"threshold": 25, "watermark": 77},
        "fx": {"threshold": 25, "watermark": 4,
               "sibling_queue": [{"strategy_id": "q1"}]}}})
    in_memory = {"classes": {"crypto": {"threshold": 25, "watermark": 9}}}
    loop_state.refresh_queues(in_memory, path)

    assert "bond_etf" not in in_memory["classes"], (
        "a disk-only class with no queue was stubbed into the loop's state; "
        "the end-of-cycle save would overwrite its real watermark")
    # ...but a disk-only class the COMPOSER parked work in is adopted whole
    assert in_memory["classes"]["fx"] == {
        "threshold": 25, "watermark": 4,
        "sibling_queue": [{"strategy_id": "q1"}]}
    assert in_memory["classes"]["crypto"]["watermark"] == 9


def test_a_queued_class_is_not_stopped_by_no_new_accepted_cards(tmp_path, monkeypatch):
    """D10 vs "no new information, no new trials" (spec Decision 2).

    That guard exists to stop the composer PROPOSING against an unchanged
    corpus. A queued sibling was already proposed and already counted, and
    draining it needs no new card -- so a class with a non-empty queue must
    still reach the composer, or the queue parks forever behind a quiet
    corpus, which is the silent drop D10 removes, just slower.
    """
    from .test_loop import (FakeRunner, _mk_layer, _add_cards,
                            _seed_all_classes_caught_up)
    monkeypatch.setattr(loop, "_live_task_window_s", lambda *a, **k: None)
    layer, reg = _mk_layer(tmp_path, accepted_fx=0)
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="p")
    _seed_all_classes_caught_up(layer, layer / "registry_log.jsonl")
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="q")
    state_path = layer / "logs" / "loop_state.json"

    # Baseline: triage accepts nothing, so the guard stops the cycle.
    assert loop.run(["--once", "--layer", str(layer)], runner=FakeRunner()) == 0
    items = json.loads((layer / "logs" / "pipeline_status.json")
                       .read_text(encoding="utf-8"))["items"]
    assert items["outcome"] == "no_new_accepted_cards"

    # Same corpus, but crypto now has queued siblings: the cycle must run.
    _add_cards(reg, 30, status="pending", asset_classes=["crypto"], prefix="r")
    st = loop_state.load(state_path)
    loop_state.enqueue_specs(st, "crypto", [{"strategy_id": "q1"}])
    loop_state.save(state_path, st)
    fr = FakeRunner()
    assert loop.run(["--once", "--layer", str(layer)], runner=fr) == 0
    items = json.loads((layer / "logs" / "pipeline_status.json")
                       .read_text(encoding="utf-8"))["items"]
    assert items["outcome"] == "cycle_complete", items
    assert any("pipeline.composer" in c for c in fr.calls)


def test_cell_data_end_reads_the_last_bar_on_disk(tmp_path):
    (tmp_path / "FOO_1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-08-25,1,1,1,1,1\n2026-08-26,1,1,1,1,1\n", encoding="utf-8")
    assert composer.cell_data_end(tmp_path, ("FOO", "1d")) == "2026-08-26"
    assert composer.cell_data_end(tmp_path, ("MISSING", "1d")) == ""
