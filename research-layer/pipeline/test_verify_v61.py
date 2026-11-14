from .common import entry_hash
from .test_gauntlet import gauntlet_registry, run_verifier

V61 = "gauntlet-protocol-v6.1: test anchor"


def _v61_verdict(reg, sid, verdict="pass", **extra):
    reg.append("note", {"text": V61})
    return reg.record_verdict(sid, "gauntlet", verdict,
                              {"protocol": "gauntlet-protocol-v6.1", **extra}, "0" * 64)


def test_a_linked_stats_entry_verifies(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    v = _v61_verdict(reg, spec["strategy_id"])
    reg.record_state_change(spec["strategy_id"], "quarantine", "gauntlet pass")
    reg.record_gauntlet_stats(spec["strategy_id"], entry_hash(v),
                              {"trials_n": 3, "deflated_sharpe": 0.4})
    r = run_verifier(reg.log_path)
    assert r.returncode == 0, r.stdout


def test_stats_pointing_at_no_verdict_fails(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_gauntlet_stats(spec["strategy_id"], "f" * 64, {"trials_n": 3})
    assert run_verifier(reg.log_path).returncode != 0


def test_a_second_stats_entry_for_one_verdict_fails(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    v = _v61_verdict(reg, spec["strategy_id"])
    reg.record_state_change(spec["strategy_id"], "quarantine", "gauntlet pass")
    reg.record_gauntlet_stats(spec["strategy_id"], entry_hash(v), {"trials_n": 3})
    reg.record_gauntlet_stats(spec["strategy_id"], entry_hash(v), {"trials_n": 4})
    assert run_verifier(reg.log_path).returncode != 0


def test_a_family_kill_after_the_v61_note_fails(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)
    _v61_verdict(reg, spec["strategy_id"], "fail", pbo_family_kill=True)
    reg.record_state_change(spec["strategy_id"], "graveyard", "pbo_family_kill")
    r = run_verifier(reg.log_path)
    assert r.returncode != 0 and "pbo_family_kill" in r.stdout


def test_a_family_kill_before_the_v61_note_is_history(tmp_path):
    """The five 2026-08-25/09-03 burials stay valid chain history."""
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_verdict(spec["strategy_id"], "gauntlet", "fail",
                       {"protocol": "gauntlet-protocol-v6", "pbo_family_kill": True}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "graveyard", "pbo_family_kill")
    assert run_verifier(reg.log_path).returncode == 0


def test_invariant_12_verdict_leg_fires_on_the_flag_alone(tmp_path):
    """Leg 1 only: the verdict carries the flag; the state change cites the
    member's own reason, not the kill."""
    reg, spec = gauntlet_registry(tmp_path)
    _v61_verdict(reg, spec["strategy_id"], "fail", pbo_family_kill=True)
    reg.record_state_change(spec["strategy_id"], "graveyard", "oos_negative")
    r = run_verifier(reg.log_path)
    assert r.returncode != 0
    assert "gauntlet verdict applies pbo_family_kill" in r.stdout
    assert "state_change cites pbo_family_kill" not in r.stdout


def test_invariant_12_state_change_leg_fires_on_the_reason_alone(tmp_path):
    """Leg 2 only: the verdict's flag is false; the state change cites the
    retired reason."""
    reg, spec = gauntlet_registry(tmp_path)
    _v61_verdict(reg, spec["strategy_id"], "fail", pbo_family_kill=False)
    reg.record_state_change(spec["strategy_id"], "graveyard", "pbo_family_kill")
    r = run_verifier(reg.log_path)
    assert r.returncode != 0
    assert "state_change cites pbo_family_kill" in r.stdout
    assert "gauntlet verdict applies pbo_family_kill" not in r.stdout


# ---- step 7: the v6.1 amendment note must arm nothing keyed on the v6.1 note ----

def _amendment_text():
    """The draft amendment note, verbatim from docs/notes/ (what will be
    chained after Coen's approval)."""
    from pathlib import Path
    p = (Path(__file__).resolve().parent.parent / "docs" / "notes"
         / "gauntlet-protocol-v6.1-amendment-1.md")
    return p.read_text(encoding="utf-8")


def test_the_amendment_note_does_not_arm_invariant_12(tmp_path):
    """Only the amendment is on the chain (no v6.1 note): a family kill after
    it is still pre-v6.1 history to the verifier, so the chain stays VALID.
    The same chain with the v6.1 note instead is INVALID (the control)."""
    text = _amendment_text()
    assert text.startswith("gauntlet-protocol-v6.1-amendment-1: ")
    assert not text.startswith("gauntlet-protocol-v6.1:")
    for note, valid in ((text, True), (V61, False)):
        d = tmp_path / ("amend" if valid else "v61")
        d.mkdir()
        reg, spec = gauntlet_registry(d)
        reg.append("note", {"text": note})
        reg.record_verdict(spec["strategy_id"], "gauntlet", "fail",
                           {"protocol": "gauntlet-protocol-v6", "pbo_family_kill": True},
                           "0" * 64)
        reg.record_state_change(spec["strategy_id"], "graveyard", "pbo_family_kill")
        r = run_verifier(reg.log_path)
        assert (r.returncode == 0) is valid, r.stdout
