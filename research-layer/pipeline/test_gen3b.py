"""Offline tests for Composer gen-3 + gauntlet protocol-v3, rev 2.

No network, no API, no writes outside tmp_path.

Run: python -m pytest pipeline/test_gen3b.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .gauntlet import (PROTOCOL, FAIL_ORDER, DSR_MIN, evaluate_spec,
                       check_aligned, run as gauntlet_run)
from .stats import expected_max_sharpe, moments, psr, sharpe

LAYER = Path(__file__).resolve().parent.parent


def gt(entry_date, ret, frac=0.2):
    return {"entry_date": entry_date, "return_net": ret, "notional_frac": frac}


G_IS = [gt("2022-01-01", 0.05)] * 30 + [gt("2022-06-01", -0.02)] * 20
G_OOS = [gt("2024-02-01", 0.05)] * 12 + [gt("2024-06-01", -0.02)] * 8
STEADY = [0.001, 0.002, -0.001, 0.0015, 0.001] * 200


def geval(trials_n=3, trials_var=0.0001, returns=None, group_n=4,
          registered_n=None):
    """Every robustness gate passes on these fixtures; only the DSR inputs
    vary, so any failure isolates to the retired gate."""
    stress = [gt(t["entry_date"], t["return_net"] - 0.001) for t in G_OOS]
    return evaluate_spec(G_IS, G_OOS, stress,
                         STEADY if returns is None else returns,
                         1.0, 1.0, trials_n, trials_var, seed=12345,
                         group_n=group_n, registered_n=registered_n)


# ---------------- protocol-v3: the DSR gate is gone ----------------

def test_protocol_is_v3():
    assert PROTOCOL == "gauntlet-protocol-v3"


def test_fail_order_excludes_dsr():
    assert FAIL_ORDER == ("oos_negative", "edge_decay", "mc_p05", "p_ruin",
                          "cost_stress")
    assert "dsr" not in FAIL_ORDER


def test_low_dsr_passes_all_five_gates():
    """The whole point of rev 2: a strategy the retired gate would have
    killed outright now passes, because the five robustness gates are what
    this stage is for. trials_n=500 with variance 4.0 drives the hurdle far
    above anything this system produces, so DSR collapses toward zero."""
    passed, reason, metrics, _ = geval(trials_n=500, trials_var=4.0)
    assert passed is True
    assert reason is None
    assert metrics["deflated_sharpe"] < DSR_MIN


def test_metrics_carry_registered_n():
    _, _, metrics, _ = geval(trials_n=3, registered_n=56)
    assert metrics["trials_n"] == 3
    assert metrics["registered_n"] == 56
    assert metrics["sibling_group_n"] == 4


def test_registered_n_absent_records_none():
    """No fallback to trials_n: in an append-only integrity log a visibly
    absent count beats a quietly wrong one."""
    _, _, metrics, _ = geval(trials_n=7, registered_n=None)
    assert metrics["registered_n"] is None


def test_metrics_make_the_recorded_dsr_reproducible():
    """v3's trials variance comes from cluster representatives, so without
    these an auditor cannot recompute a recorded deflated_sharpe from the
    entry alone."""
    _, _, metrics, _ = geval(trials_n=56, trials_var=0.25)
    assert metrics["trials_sr_var"] == 0.25
    assert metrics["expected_max_sharpe"] == pytest.approx(
        expected_max_sharpe(56, 0.25))
    assert metrics["deflated_sharpe"] == pytest.approx(
        psr(sharpe(STEADY), metrics["expected_max_sharpe"], len(STEADY),
            *moments(STEADY)[2:]))


def test_metrics_carry_the_protocol_discriminator():
    """trials_n means 'registered strategies' under v2 and 'clusters' under
    v3, under the same key, so the entry must say which produced it."""
    _, _, metrics, _ = geval()
    assert metrics["protocol"] == PROTOCOL == "gauntlet-protocol-v3"


def test_mc_summary_carries_the_full_cone():
    """Graduation review needs P25-P75; v2 stored only p05/p25/p50."""
    _, _, _, mc = geval()
    assert set(mc) == {"seed", "paths", "p05", "p25", "p50", "p75",
                       "p_ruin", "ruin_level"}
    assert mc["p05"] <= mc["p25"] <= mc["p50"] <= mc["p75"]


def test_dsr_still_ranks_even_though_it_no_longer_gates():
    """DSR remains the sibling-selection statistic, so it must still respond
    to the trial count.

    The comparison uses a SHORT return series on purpose. DSR is a normal CDF
    of z = (sr_hat - sr_star) * sqrt(T - 1) / sqrt(under). Over the full
    T=1000 STEADY series that z is ~+18 at both trial counts, and normal_cdf
    saturates to exactly 1.0 in float64 well before then, so both DSRs come
    back 1.0 and the ordering is unobservable. At T=25 the z values land in
    the CDF's responsive band and the ranking is visible."""
    short = STEADY[:25]
    _, _, few, _ = geval(trials_n=3, returns=short)
    _, _, many, _ = geval(trials_n=56, returns=short)
    assert 0.0 < many["deflated_sharpe"] < few["deflated_sharpe"] < 1.0


# ---------------- alignment guard ----------------

def test_ragged_return_series_fail_closed():
    """cluster.correlation compares BY INDEX, so misaligned series would give
    a wrong k and a wrong recorded DSR silently. Refuse instead."""
    with pytest.raises(ValueError, match="ragged return series"):
        check_aligned({"a" * 16: [0.1, 0.2, 0.3], "b" * 16: [0.1, 0.2]})


def test_ragged_error_names_the_offenders():
    with pytest.raises(ValueError) as e:
        check_aligned({"a" * 16: [0.1, 0.2, 0.3], "b" * 16: [0.1, 0.2]})
    assert f"{'a' * 16}=3" in str(e.value)
    assert f"{'b' * 16}=2" in str(e.value)


def test_aligned_and_degenerate_inputs_are_accepted():
    check_aligned({"a" * 16: [0.1, 0.2], "b" * 16: [0.3, 0.4]})
    check_aligned({"a" * 16: [0.1, 0.2]})
    check_aligned({})


# ---------------- end-to-end: the run() wiring ----------------

def multi_strategy_registry(tmp_path, n=3):
    """Registry with n registered strategies, all advanced to gauntlet state.

    THREE, not two: cluster.effective_trials short-circuits at n==2 and
    returns k=2, which would make trials_n == registered_n and the
    cluster-count-vs-registration-count assertion below vacuous. At n=3 the
    only admissible k is 2, so the two numbers genuinely differ."""
    from .common import content_id
    from .test_screen import screening_registry

    reg, spec = screening_registry(tmp_path)
    specs = [spec]
    for i in range(1, n):
        # vary a field outside the blocks so the id changes but the spec stays
        # valid against the already-registered grammar
        clone = json.loads(json.dumps(spec))
        clone["name"] = f"{spec['name']} variant {i}"
        clone["provenance"]["sibling_group_id"] = f"g-test-{i}"
        clone["strategy_id"] = None
        clone["strategy_id"] = content_id(clone, "strategy_id")
        reg.register_strategy(clone)
        specs.append(clone)

    reg.append("note", {"text": "screen-protocol-v1: test anchor"})
    for s in specs:
        sid = s["strategy_id"]
        reg.record_state_change(sid, "screened", "test")
        reg.record_verdict(sid, "screened", "pass",
                           {"trades": 50, "net_pnl": 0.5, "win_rate": 0.5,
                            "max_dd": -0.1}, "0" * 64)
        reg.record_state_change(sid, "gauntlet", None)
    reg.append("note", {"text": "gauntlet-protocol-v3: test anchor"})
    return reg, specs


def test_full_run_records_cluster_count_and_group_context(tmp_path):
    """The meaning change this protocol makes: run-level trials_n is the
    CLUSTER count, not the registration count, and the clustering that
    produced it is carried into the artifact bundle."""
    from .test_screen import write_data_dir, dated_target_hit_bars

    reg, specs = multi_strategy_registry(tmp_path, n=3)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    art = tmp_path / "art"
    rc = gauntlet_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                       "--artifacts-dir", str(art)])
    assert rc == 0

    verdicts = [e["payload"] for e in reg.entries()
                if e["entry_type"] == "verdict"
                and e["payload"].get("stage") == "gauntlet"]
    assert len(verdicts) == 3
    for v in verdicts:
        m = v["metrics"]
        assert m["registered_n"] == 3        # honest raw registration count
        assert m["trials_n"] == 2            # effectively independent trials
        assert m["trials_n"] < m["registered_n"]
        assert m["protocol"] == "gauntlet-protocol-v3"

    cfg = json.loads((art / specs[0]["strategy_id"] / "gauntlet" /
                      "config.json").read_text(encoding="utf-8"))
    gc = cfg["group_context"]
    assert gc["effective_trials"] == 2
    assert gc["registered_n"] == 3
    assert set(gc["cluster_labels"]) == {s["strategy_id"] for s in specs}
    assert len(set(gc["cluster_labels"].values())) == 2

    mc = json.loads((art / specs[0]["strategy_id"] / "gauntlet" /
                     "mc_summary.json").read_text(encoding="utf-8"))
    assert mc["p05"] <= mc["p25"] <= mc["p50"] <= mc["p75"]


# ---------------- rule 7: sibling-level fingerprint guard ----------------

from .registry import Registry
from .composer import (run as composer_run, expand_family,
                       composition_fingerprint, screen_siblings)
from .test_composer import good_family, register_grammar
from .test_pipeline import make_card

GEN3_TS = "2026-08-16T00:00:00Z"
# ONE card object shared by CARD_ID and seeded(): build_card() stamps
# created_utc at second resolution and the card_id is content-addressed over
# it, so two make_card() calls straddling a second boundary produce different
# ids — a family built from one would cite a card the registry never accepted.
CARD = make_card()
CARD_ID = CARD["card_id"]


def seeded(tmp_path, pre_register=()):
    """Registry with the grammar, one accepted card, and `pre_register`
    already chained as strategies."""
    reg = Registry(tmp_path / "reg.jsonl")
    register_grammar(reg)
    reg.register_card(CARD)
    reg.review_card(CARD_ID, "accepted", "tester")
    for spec in pre_register:
        reg.register_strategy(spec)
    return reg


def z_family(family="zfam", z_values=(1.5, 2.0, 2.5)):
    """A single-axis sweep over z_entry: len(z_values) siblings, one family."""
    return good_family(family=family, card_ids=[CARD_ID],
                       sweep=[{"block": 0, "param": "z_entry",
                               "values": list(z_values)}])


def pre_expand(fam):
    """The specs a family WOULD produce, for pre-registration. Their ids
    differ from a later composer run's (created_utc and model feed the id),
    but their composition FINGERPRINTS are identical - and the fingerprint is
    what rule 7 compares. expand_family deep-copies its input, so the same
    family object can be handed straight to the composer afterwards."""
    return expand_family(fam, "seed-run", "m", GEN3_TS)


def test_screen_siblings_never_returns_a_guarded_fingerprint():
    """The invariant the public chain rests on, asserted on the return value
    rather than on a log line: nothing rule 7 guards can survive screening."""
    specs = pre_expand(z_family())
    known = {composition_fingerprint(specs[0]): "b" * 16}
    run = {composition_fingerprint(specs[1]): "fam_earlier"}
    kept, drop_notes, malformed = screen_siblings(specs, known, run)
    assert malformed is False
    assert [s["strategy_id"] for s in kept] == [specs[2]["strategy_id"]]
    for s in kept:
        fp = composition_fingerprint(s)
        assert fp not in known and fp not in run
    assert len(drop_notes) == 2


def test_family_survives_partial_sibling_collision(tmp_path, capsys):
    fam = z_family()
    specs = pre_expand(fam)
    assert len(specs) == 3
    reg = seeded(tmp_path, pre_register=[specs[0]])
    rc = composer_run(["--registry", str(reg.log_path), "--run-id", "gen3",
                       "--dry-run"],
                      propose_fn=lambda cards: [fam])
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 expanded, 1 already registered, 2 new" in out
    assert "DROPPED family zfam" not in out
    assert "1 families kept" in out


def test_family_dropped_when_every_sibling_collides(tmp_path, capsys):
    reg = seeded(tmp_path, pre_register=pre_expand(z_family()))
    rc = composer_run(["--registry", str(reg.log_path), "--run-id", "gen3",
                       "--dry-run"],
                      propose_fn=lambda cards: [z_family()])
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 expanded, 3 already registered, 0 new" in out
    assert "every sibling already registered" in out
    assert "DROPPED family zfam" in out
    assert "0 families kept" in out


def test_cross_family_run_collision_drops_the_sibling_only(tmp_path, capsys):
    """fam_b overlaps fam_a on one sibling. fam_a wins the overlap; fam_b
    keeps its remaining sibling instead of dying."""
    reg = seeded(tmp_path)
    fam_a = z_family(family="fam_a", z_values=(1.5, 2.0))
    fam_b = z_family(family="fam_b", z_values=(2.0, 2.5))
    rc = composer_run(["--registry", str(reg.log_path), "--run-id", "gen3",
                       "--dry-run"],
                      propose_fn=lambda cards: [fam_a, fam_b])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fam_a: 2 expanded, 0 already registered, 2 new" in out
    assert ("fam_b: 2 expanded, 0 already registered, "
            "1 duplicated in this run, 1 new") in out
    assert "DROPPED family fam_b" not in out
    assert "2 families kept" in out


def test_run_duplicates_are_never_reported_as_already_registered(tmp_path, capsys):
    """Nothing is registered in this run's chain, so neither the count nor
    the drop reason may claim a buried composition. The two diagnoses differ:
    a chain collision is expected saturation as the grammar space fills, a
    family proposed twice under two names is a proposal-quality defect."""
    reg = seeded(tmp_path)
    rc = composer_run(["--registry", str(reg.log_path), "--run-id", "gen3",
                       "--dry-run"],
                      propose_fn=lambda cards: [z_family(family="fam_a"),
                                                z_family(family="fam_b")])
    assert rc == 0
    out = capsys.readouterr().out
    assert ("fam_b: 3 expanded, 0 already registered, "
            "3 duplicated in this run, 0 new") in out
    assert ("DROPPED family fam_b: every sibling already registered "
            "or duplicated in this run") in out
    assert not [e for e in reg.entries()
                if e["entry_type"] == "strategy_registered"]


def test_intra_family_duplicate_siblings_still_kill_the_family(tmp_path, capsys):
    """Mirrored sweep axes are a malformed proposal, not a collision.

    NOT a dry run on purpose: this is the one path where screening abandons
    a partially accumulated kept_specs (three siblings survive before the
    mirrored pair is reached), so only the caller's early exit stops them
    reaching the chain. Assert the chain, not just the message."""
    reg = seeded(tmp_path)
    fam = good_family(family="mirrored", card_ids=[CARD_ID])
    fam["blocks"].append({"role": "stop", "type": "atr_stop",
                          "params": {"atr_len": 14, "mult": 2.0}})
    fam["sweep"] = [{"block": 1, "param": "mult", "values": [2.0, 3.0]},
                    {"block": 4, "param": "mult", "values": [3.0, 2.0]}]
    rc = composer_run(["--registry", str(reg.log_path), "--run-id", "gen3"],
                      propose_fn=lambda cards: [fam])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DROPPED family mirrored" in out
    assert "same composition" in out
    assert not [e for e in reg.entries()
                if e["entry_type"] == "strategy_registered"]


def test_partial_collision_registers_only_the_new_siblings(tmp_path, capsys):
    """Not a dry run. good_family's default sweep is 3 x 3 = 9 siblings;
    pre-register 3 of them and exactly the other 6 must reach the chain, and
    no buried composition may be re-registered."""
    fam = good_family(family="ninefam", card_ids=[CARD_ID])
    specs = pre_expand(fam)
    assert len(specs) == 9
    buried = specs[:3]
    reg = seeded(tmp_path, pre_register=buried)
    rc = composer_run(["--registry", str(reg.log_path), "--run-id", "gen3"],
                      propose_fn=lambda cards: [fam])
    assert rc == 0
    out = capsys.readouterr().out
    assert "9 expanded, 3 already registered, 6 new" in out
    chained = [e["payload"] for e in reg.entries()
               if e["entry_type"] == "strategy_registered"]
    assert len(chained) == 9                      # 3 pre-existing + 6 new
    fps = [composition_fingerprint(s) for s in chained]
    assert len(set(fps)) == 9                     # no composition twice
    assert set(fps) == {composition_fingerprint(s) for s in specs}
