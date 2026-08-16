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
                       composition_fingerprint, screen_siblings, SYSTEM_PROMPT)
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


# ---------------- composer prompt: generation-2 evidence ----------------

EVIDENCE_HEAD = "What happened so far, as measured, not as opinion:"
EVIDENCE_TAIL = "Draw your own conclusions from those facts."

# Bare imperatives. The evidence block states what was measured and lets the
# model conclude; the Rules: block below it is where instructions belong.
IMPERATIVES = ("Treat ", "Do ", "Don't ", "Avoid ", "Prefer ", "Use ",
               "Consider ", "Ensure ", "Make sure ", "Note that ")


def evidence_block() -> str:
    """The measured-outcomes paragraph only, excluding the Rules: block."""
    start = SYSTEM_PROMPT.index(EVIDENCE_HEAD)
    end = SYSTEM_PROMPT.index(EVIDENCE_TAIL)
    return SYSTEM_PROMPT[start + len(EVIDENCE_HEAD):end]


def test_prompt_frames_evidence_not_instruction():
    """The point of this paragraph is that the model draws its OWN conclusions
    from measurements. An imperative smuggled in here pre-empts that, and does
    it on whichever topic the author felt strongest about - which is exactly
    where the evidence is likely to be weakest.

    This is not hypothetical. The bullet on sizing once ended 'Treat sizing as
    untested, not as settled.' - grammatically the same form as the
    'Do not simply reproduce that shape.' directive this whole task existed to
    delete, sitting two lines above the invitation to conclude, on the one
    topic where the sample has a single arm. It was replaced with the fact
    that licenses the inference instead: fixed_fraction exists in the grammar,
    is registered on 4 specs, and has never reached the gauntlet."""
    assert EVIDENCE_TAIL in SYSTEM_PROMPT
    assert "volatility-normalized" in SYSTEM_PROMPT
    body = evidence_block()
    for line in body.splitlines():
        stripped = line.lstrip("- ").strip()
        for imp in IMPERATIVES:
            assert not stripped.startswith(imp), (
                f"bare imperative in the evidence block: {line.strip()!r}")
    # the directives the v3 rewrite removed must not creep back
    assert "Do not simply reproduce" not in SYSTEM_PROMPT
    assert "Treat sizing as untested" not in SYSTEM_PROMPT


def test_prompt_drops_the_generation_1_only_framing():
    # the old paragraph told the model what NOT to do; the new one states
    # measured outcomes and lets it conclude
    assert "Do not simply reproduce that shape." not in SYSTEM_PROMPT
    assert "What happened in generation 1:" not in SYSTEM_PROMPT


def test_prompt_still_has_grammar_guidance():
    assert "trend_scan_ds" in SYSTEM_PROMPT
    assert "regime_ma_short" in SYSTEM_PROMPT
    assert "regime_hypothesis" in SYSTEM_PROMPT


def test_prompt_figures_match_what_was_measured():
    """The prompt presents these to the model as measurement, not opinion, so
    a silent edit to any figure is a correctness bug, not a wording change.

    This is a WORDING GUARD, not a recomputation: it pins the prompt's text
    against figures recorded here, deliberately rather than reading
    registry_log.jsonl, which a concurrent scanner session is appending to and
    which would make the suite non-hermetic. Provenance lives in this
    docstring instead. Every figure was recomputed from the chain 2026-08-16:

      gen 1: 22 specs, 13 passed the screen, all 13 failed the gauntlet.
      gen 2: 34 specs, 18 passed the screen, all 18 failed the gauntlet.
      forced_flow_overshoot_reversion: net_pnl -0.455..-0.658, 8/8 buried
        `net_negative` - the same reason gen 1's reversion family died.
      downtrend_short_only: 10-19 trades, 8/8 buried `trade_count`, and 4 of
        the 8 also had negative net_pnl (-0.070..-0.148).
      decay: 12 of 18 positive, +1.75%..+54.27%; the other 6 down to -69.79%;
        both surviving families appear in BOTH groups.
      sizing: ALL 18 gauntlet entrants were vol_target, so the worst-4 p_ruin
        and worst-4 mc_p05 being vol_target carries no comparison at all.
      screen window: 2017-08-17 to the 2023-12-31 fence = 6.37 years.

    Two errors reached the prompt and were caught by review, not by this test:
    an earlier draft said 11 of 18 (a source note quoted the right range but
    miscounted the set), and an inherited sentence claimed every gen-1 spec
    passed the screen when 9 of 22 did not. Both had propagated under the
    'as measured, not as opinion' banner, which is exactly the claim that
    makes a wrong figure a defect rather than a wording choice."""
    # gen-1 figures - the inherited sentence that was wrong went unpinned
    assert "13 of its 22 specs" in SYSTEM_PROMPT
    assert "all 13 then failed out of sample" in SYSTEM_PROMPT
    # gen-2 headline verdict, which an earlier draft omitted entirely while
    # keeping a flattering sub-metric
    assert "It still passed nothing." in SYSTEM_PROMPT
    assert "18 of its 34 specs" in SYSTEM_PROMPT
    assert "all 18 then failed the gauntlet" in SYSTEM_PROMPT
    # measured detail
    assert "Of the 18 that reached the gauntlet, 12 showed POSITIVE" in SYSTEM_PROMPT
    assert "from +1.8% to +54.3%" in SYSTEM_PROMPT
    assert "The other 6" in SYSTEM_PROMPT and "-69.8%" in SYSTEM_PROMPT
    assert "lost 46% to 66% over the training window" in SYSTEM_PROMPT
    assert "only 10-19 trades in six and a half years" in SYSTEM_PROMPT
    assert "Four of its eight specs were also" in SYSTEM_PROMPT
    # the sizing bullet must keep its no-contrast-group caveat, or it reads as
    # a finding when all 18 entrants shared the one sizing rule
    assert "All 18 used vol_target sizing" in SYSTEM_PROMPT
    assert "nothing here compares sizing rules" in SYSTEM_PROMPT
    assert "registered on 4 specs and has never reached the" in SYSTEM_PROMPT


# ---------------- quarantine: the daily forward runner ----------------

from .quarantine import run as quarantine_run, MIN_TRADING_DAYS
from .test_screen import dated_target_hit_bars, write_data_dir
from .test_gauntlet import gauntlet_registry

ENTERED = "2023-01-21"


def quarantined(tmp_path):
    """One strategy in quarantine state, entered 2023-01-21, over
    dated_target_hit_bars (2023-01-01 .. 2023-01-23). Its book enters long on
    2023-01-22 at 111 and exits at the 116.55 target on 2023-01-23."""
    reg, spec = gauntlet_registry(tmp_path)
    reg.append("note", {"text": "gauntlet-protocol-v3: test anchor"})
    reg.record_verdict(spec["strategy_id"], "gauntlet", "pass",
                       {"deflated_sharpe": 0.5}, "0" * 64)
    reg.record_state_change(spec["strategy_id"], "quarantine",
                            "gauntlet pass, group-selected",
                            ts_utc=f"{ENTERED}T00:00:00Z")
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    return reg, spec, data


def decisions(reg):
    return [e["payload"] for e in reg.entries()
            if e["entry_type"] == "quarantine_decision"]


def test_decision_row_shape_and_values_on_entry_day(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    rc = quarantine_run(["--registry", str(reg.log_path), "--data-dir",
                         str(data), "--date", "2023-01-22"])
    assert rc == 0
    rows = decisions(reg)
    assert len(rows) == 1
    r = rows[0]
    assert set(r) == {"strategy_id", "date", "asset", "action", "price",
                      "position_frac", "equity"}
    assert r["strategy_id"] == spec["strategy_id"]
    assert r["date"] == "2023-01-22"
    assert r["asset"] == "BTCUSD"
    assert r["action"] == "enter_long"
    assert r["price"] == pytest.approx(111.0)
    # f=0.01, stop distance 5% of 111 -> notional_frac 0.2
    assert r["position_frac"] == pytest.approx(0.2)
    # entered at the open and marked at the same close -> unrealised 0
    assert r["equity"] == pytest.approx(1.0)


def test_decision_on_exit_day(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                    "--date", "2023-01-23"])
    r = decisions(reg)[-1]
    assert r["action"] == "exit"
    assert r["price"] == pytest.approx(118.0)
    assert r["position_frac"] == pytest.approx(0.0)
    # target 116.55 from entry 111 -> gross 0.05, net 0.05 - 2*0.0015 = 0.047,
    # sized 0.2 -> equity 1 + 0.2*0.047
    assert r["equity"] == pytest.approx(1.0094)


def test_decision_uses_only_bars_up_to_the_date(tmp_path):
    """The data dir holds 2023-01-23 too. A 2023-01-22 decision that leaked
    it would report the exit instead of the entry."""
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                    "--date", "2023-01-22"])
    assert decisions(reg)[-1]["action"] == "enter_long"


def test_missing_bar_for_date_is_refused(tmp_path, capsys):
    reg, spec, data = quarantined(tmp_path)
    rc = quarantine_run(["--registry", str(reg.log_path), "--data-dir",
                         str(data), "--date", "2023-01-30"])
    assert rc == 1
    assert "REFUSED" in capsys.readouterr().out
    assert decisions(reg) == []


def test_rerunning_a_date_is_a_noop(tmp_path, capsys):
    reg, spec, data = quarantined(tmp_path)
    argv = ["--registry", str(reg.log_path), "--data-dir", str(data),
            "--date", "2023-01-22"]
    quarantine_run(argv)
    n_after_first = sum(1 for _ in reg.entries())
    capsys.readouterr()
    rc = quarantine_run(argv)
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_after_first
    assert "1 already present" in capsys.readouterr().out


def test_dates_at_or_before_entry_are_skipped(tmp_path, capsys):
    reg, spec, data = quarantined(tmp_path)
    rc = quarantine_run(["--registry", str(reg.log_path), "--data-dir",
                         str(data), "--date", ENTERED])
    assert rc == 0
    assert decisions(reg) == []
    assert "not after its quarantine entry" in capsys.readouterr().out


def test_registry_refuses_decision_for_non_quarantined_strategy(tmp_path):
    reg, spec = gauntlet_registry(tmp_path)          # still in 'gauntlet'
    with pytest.raises(ValueError, match="not in quarantine"):
        reg.record_quarantine_decision(
            {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
             "asset": "BTCUSD", "action": "hold", "price": 100.0,
             "position_frac": 0.0, "equity": 1.0})


def test_review_writes_nothing_and_reports_days(tmp_path, capsys):
    reg, spec, data = quarantined(tmp_path)
    for d in ("2023-01-22", "2023-01-23"):
        quarantine_run(["--registry", str(reg.log_path), "--data-dir",
                        str(data), "--date", d])
    n_before = sum(1 for _ in reg.entries())
    capsys.readouterr()
    rc = quarantine_run(["--registry", str(reg.log_path), "--review"])
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_before
    out = capsys.readouterr().out
    assert f"days 2/{MIN_TRADING_DAYS}" in out
    assert "NOT YET ELIGIBLE" in out
    assert "NOT directly comparable" in out


def test_review_reads_the_stored_mc_cone(tmp_path, capsys):
    """The cone is reported, never applied. Hand-checked against a fixture
    mc_summary.json so the reader knows exactly what it is looking at."""
    reg, spec, data = quarantined(tmp_path)
    bundle = tmp_path / "art" / spec["strategy_id"] / "gauntlet"
    bundle.mkdir(parents=True)
    (bundle / "mc_summary.json").write_text(json.dumps(
        {"seed": 1, "paths": 2000, "p05": 0.8123, "p25": 1.1000,
         "p50": 1.4000, "p75": 1.9000, "p_ruin": 0.01, "ruin_level": 0.5}),
        encoding="utf-8")
    n_before = sum(1 for _ in reg.entries())
    capsys.readouterr()
    rc = quarantine_run(["--registry", str(reg.log_path), "--review",
                         "--artifacts-dir", str(tmp_path / "art")])
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_before
    out = capsys.readouterr().out
    assert "p25=1.1000 p50=1.4000 p75=1.9000" in out
    assert "NOT directly comparable" in out
