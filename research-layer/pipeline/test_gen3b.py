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

def test_protocol_is_current():
    # this suite pins v3's BEHAVIOUR (the retired DSR gate), not v3's version
    # string; protocol-v4 kept every one of those behaviours and added three
    # gates on top and v5 amended only the PBO gate, so the anchor tracks
    # whatever protocol is current.
    assert PROTOCOL == "gauntlet-protocol-v6"


def test_fail_order_excludes_dsr():
    # protocol-v6 removed pbo, pbo_underpowered and plateau from the battery;
    # this suite pins v3's BEHAVIOUR (the retired DSR gate), which is unchanged.
    assert FAIL_ORDER == ("sharpe_floor", "oos_negative", "edge_decay",
                          "mc_p05", "p_ruin", "cost_stress")
    assert "dsr" not in FAIL_ORDER


def test_low_dsr_passes_every_gate():
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
    assert metrics["protocol"] == PROTOCOL == "gauntlet-protocol-v6"


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
    reg.append("note", {"text": f"{PROTOCOL}: test anchor"})
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
        assert m["protocol"] == PROTOCOL

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


def z_family(family="zfam", z_values=(60, 75, 90)):
    """A single-axis sweep over max_lookback (trend_scan_dense): len(z_values)
    siblings, one family. max_lookback's grid has 5 points ([60, 75, 90, 105,
    120]), unlike t_min's 3 ([2.0, 2.5, 3.0]) -- protocol-v4's minimum-3-values
    rule means a 3-point grid has no room for two DIFFERENT 3+-value subsets
    to partially overlap, which callers below need."""
    return good_family(family=family, card_ids=[CARD_ID],
                       sweep=[{"block": 0, "param": "max_lookback",
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
    keeps its remaining siblings instead of dying."""
    reg = seeded(tmp_path)
    fam_a = z_family(family="fam_a", z_values=(60, 75, 90))
    fam_b = z_family(family="fam_b", z_values=(90, 105, 120))
    rc = composer_run(["--registry", str(reg.log_path), "--run-id", "gen3",
                       "--dry-run"],
                      propose_fn=lambda cards: [fam_a, fam_b])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fam_a: 3 expanded, 0 already registered, 3 new" in out
    assert ("fam_b: 3 expanded, 0 already registered, "
            "1 duplicated in this run, 2 new") in out
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
    a partially accumulated kept_specs (several siblings survive before the
    first mirrored pair is reached), so only the caller's early exit stops
    them reaching the chain. Assert the chain, not just the message."""
    reg = seeded(tmp_path)
    fam = good_family(family="mirrored", card_ids=[CARD_ID])
    fam["blocks"].append({"role": "stop", "type": "atr_stop_dense",
                          "params": {"atr_len": 14, "mult": 2.0}})
    # protocol-v4 requires >= 3 values per swept axis, contiguous on the
    # declared grid [1.5, 2.0, 2.5, 3.0, 3.5], so the mirrored pair sweeps
    # the same contiguous 3-value set on both blocks
    fam["sweep"] = [{"block": 1, "param": "mult", "values": [2.0, 2.5, 3.0]},
                    {"block": 4, "param": "mult", "values": [3.0, 2.5, 2.0]}]
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

from datetime import datetime, timedelta

from . import quarantine as quarantine_mod
from .quarantine import (run as quarantine_run, MIN_TRADING_DAYS,
                         BACKFILL_LAG_DAYS, CONE_CAVEAT, MAX_LISTED_GAPS)
from .registry import DuplicateQuarantineDecision
from .common import content_id
from .test_screen import dated_target_hit_bars, write_data_dir
from .test_gauntlet import gauntlet_registry

ENTERED = "2023-01-21"
CAVEAT = CONE_CAVEAT.format(n=MIN_TRADING_DAYS)


def flat_dated_bars(n=23, px=100.0, start="2023-01-01"):
    """A never-signalling series on dated_target_hit_bars' calendar."""
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [{"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
             "open": px, "high": px, "low": px, "close": px, "volume": 1.0}
            for i in range(n)]


def quarantined(tmp_path, bars=None):
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
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()
                                     if bars is None else bars})
    return reg, spec, data


def quarantined_two_asset(tmp_path):
    """Same lifecycle, but a BTCUSD+ETHUSD universe, so the per-asset
    invariants have something to bite on. ETHUSD never signals."""
    reg, spec = gauntlet_registry(tmp_path)
    reg.record_state_change(spec["strategy_id"], "graveyard",
                            "replaced by the two-asset fixture")
    two = json.loads(json.dumps(spec))
    two["universe"] = dict(two["universe"], assets=["BTCUSD", "ETHUSD"])
    two["strategy_id"] = None
    two["strategy_id"] = content_id(two, "strategy_id")
    reg.register_strategy(two)
    reg.record_state_change(two["strategy_id"], "screened", "test")
    reg.record_state_change(two["strategy_id"], "gauntlet", None)
    reg.record_state_change(two["strategy_id"], "quarantine", "test",
                            ts_utc=f"{ENTERED}T00:00:00Z")
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars(),
                                     "ETHUSD": flat_dated_bars()})
    return reg, two, data


def argv_for(reg, data, *rest):
    return ["--registry", str(reg.log_path), "--data-dir", str(data), *rest]


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
    # stderr, like PARTIAL WRITE: a scheduled job's streams are often captured
    # separately, and a refusal must not be buried in the report stream
    assert "REFUSED" in capsys.readouterr().err
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
        quarantine_run(argv_for(reg, data, "--date", d))
    n_before = sum(1 for _ in reg.entries())
    capsys.readouterr()
    rc = quarantine_run(argv_for(reg, data, "--review"))
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_before
    out = capsys.readouterr().out
    assert f"days 2/{MIN_TRADING_DAYS}" in out
    assert "NOT YET ELIGIBLE" in out
    # no cone was stored, so the caveat about the cone must not appear
    assert "(no mc_summary.json found)" in out
    assert CAVEAT not in out


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
    rc = quarantine_run(argv_for(reg, data, "--review",
                                 "--artifacts-dir", str(tmp_path / "art")))
    assert rc == 0
    assert sum(1 for _ in reg.entries()) == n_before
    out = capsys.readouterr().out
    assert "p25=1.1000 p50=1.4000 p75=1.9000" in out
    assert CAVEAT in out


# ---- the record must be refusable, auditable and concurrency-safe ----

def test_missing_registry_is_refused_in_both_modes(tmp_path, capsys):
    """Registry.entries() returns silently on a missing file, so a typo'd
    path used to report success and leave an invisible hole."""
    reg, spec, data = quarantined(tmp_path)
    absent = str(tmp_path / "nope.jsonl")
    for extra in (["--date", "2023-01-22"], ["--review"]):
        rc = quarantine_run(["--registry", absent, "--data-dir", str(data),
                             *extra])
        assert rc == 1
        assert "REFUSED" in capsys.readouterr().err
    assert decisions(reg) == []


def test_missing_data_dir_is_refused_in_both_modes(tmp_path, capsys):
    reg, spec, data = quarantined(tmp_path)
    absent = str(tmp_path / "no-such-data")
    for extra in (["--date", "2023-01-22"], ["--review"]):
        rc = quarantine_run(["--registry", str(reg.log_path), "--data-dir",
                             absent, *extra])
        assert rc == 1
        assert "REFUSED" in capsys.readouterr().err
    assert decisions(reg) == []


@pytest.mark.parametrize("bad", ["not-a-date", "2023-1-22", "2023-02-30",
                                 "2023-01", ""])
def test_malformed_date_is_refused(tmp_path, capsys, bad):
    reg, spec, data = quarantined(tmp_path)
    rc = quarantine_run(argv_for(reg, data, "--date", bad))
    assert rc == 1
    assert decisions(reg) == []


def test_date_and_review_are_mutually_exclusive(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    assert quarantine_run(argv_for(reg, data)) == 1
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22",
                                   "--review")) == 1
    assert decisions(reg) == []


def test_review_with_nothing_in_quarantine(tmp_path, capsys):
    reg, spec = gauntlet_registry(tmp_path)
    data = write_data_dir(tmp_path, {"BTCUSD": dated_target_hit_bars()})
    rc = quarantine_run(argv_for(reg, data, "--review"))
    assert rc == 0
    assert "No strategies in 'quarantine' state." in capsys.readouterr().out


def test_review_names_the_bar_dates_that_were_never_recorded(tmp_path, capsys):
    """A nine-day outage and sixty consecutive days must not look the same.
    Here 2023-01-22 is a real bar that never got a decision."""
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(argv_for(reg, data, "--date", "2023-01-23"))
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    assert "unrecorded bar dates in window: 1" in out
    assert "2023-01-22" in out


def test_review_reports_backfill_lag(tmp_path, capsys):
    """The fixture's bars are dated 2023 but the rows are chained now, so
    every row is visibly a backfill rather than a forward observation --
    which is exactly the thing the audit has to be able to say."""
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    assert f"1 row(s) chained more than {BACKFILL_LAG_DAYS}d after their bar" in out


def test_a_complete_record_reports_no_gaps(tmp_path, capsys):
    reg, spec, data = quarantined(tmp_path)
    for d in ("2023-01-22", "2023-01-23"):
        quarantine_run(argv_for(reg, data, "--date", d))
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    assert "unrecorded bar dates in window: 0" in out
    assert "partially recorded dates: 0" in out


def test_review_flags_a_partially_recorded_date(tmp_path, capsys, monkeypatch):
    """One asset recorded and the other not is incompleteness of the same
    kind as a wholly missing day, and it would sit there indefinitely if the
    re-run never happens. It gets its own line, naming the missing asset."""
    reg, spec, data = quarantined_two_asset(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    real = Registry.record_quarantine_decision
    calls = []

    def flaky(self, payload):
        calls.append(payload)
        if len(calls) == 2:                      # BTCUSD lands, ETHUSD does not
            raise RuntimeError("chain write blew up")
        return real(self, payload)

    monkeypatch.setattr(Registry, "record_quarantine_decision", flaky)
    with pytest.raises(RuntimeError):
        quarantine_run(argv)
    monkeypatch.undo()
    assert len(decisions(reg)) == 1

    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    assert "partially recorded dates: 1" in out
    assert "2023-01-22 missing ETHUSD" in out
    # 2023-01-23 has a bar and no decision at all, so it is a WHOLLY missing
    # date, reported separately from the partial one. The window runs to the
    # last bar that exists, not to the last row recorded.
    assert "unrecorded bar dates in window: 1  (2023-01-23)" in out


def test_a_truncated_gap_list_says_what_it_dropped(tmp_path, capsys):
    """A bounded list that stops silently reads as 'that was all of them'."""
    bars = flat_dated_bars(n=60)                 # 2023-01-01 .. 2023-03-01
    reg, spec, data = quarantined(tmp_path, bars=bars)
    last = bars[-1]["date"]
    quarantine_run(argv_for(reg, data, "--date", last))
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    # every bar after the 2023-01-21 entry except the one day recorded
    owed = [b["date"] for b in bars if b["date"] > ENTERED]
    n_missing = len(owed) - 1
    assert n_missing > MAX_LISTED_GAPS
    assert f"unrecorded bar dates in window: {n_missing}" in out
    assert f"... and {n_missing - MAX_LISTED_GAPS} more" in out
    # the listing itself is capped, not merely annotated
    line = next(l for l in out.splitlines() if "unrecorded bar dates" in l)
    assert line.count("2023-") == MAX_LISTED_GAPS


def test_a_concurrent_writer_is_absorbed_not_crashed(tmp_path, capsys,
                                                     monkeypatch):
    """A scheduler retry overlapping the daily job is routine. The registry
    still refuses the duplicate structurally; run() reports it as already
    present rather than paging someone with a traceback."""
    reg, spec, data = quarantined(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    quarantine_run(argv)
    assert len(decisions(reg)) == 1
    # the row lands after this run snapshots `seen`, so the pre-filter misses
    # it and the registry's under-lock guard is what fires
    monkeypatch.setattr(quarantine_mod, "existing_decisions", lambda r: set())
    capsys.readouterr()
    rc = quarantine_run(argv)
    assert rc == 0
    assert len(decisions(reg)) == 1
    assert "0 decision(s) chained, 1 already present" in capsys.readouterr().out


def test_only_the_duplicate_case_is_absorbed(tmp_path, capsys, monkeypatch):
    """A malformed payload must still be fatal: run() catches
    DuplicateQuarantineDecision, not ValueError."""
    reg, spec, data = quarantined(tmp_path)

    def bad(self, payload):
        raise ValueError("malformed payload")

    monkeypatch.setattr(Registry, "record_quarantine_decision", bad)
    with pytest.raises(ValueError, match="malformed payload"):
        quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    assert "PARTIAL WRITE" in capsys.readouterr().err
    assert decisions(reg) == []


def test_duplicate_error_is_a_valueerror_subclass():
    """Existing callers that catch ValueError must keep working."""
    assert issubclass(DuplicateQuarantineDecision, ValueError)


def test_registry_refuses_a_duplicate_even_without_the_seen_prefilter(tmp_path):
    """run()'s `seen` set is a per-process pre-filter and cannot see a
    concurrent writer. The de-duplication that matters lives under the lock."""
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    row = dict(decisions(reg)[0])
    with pytest.raises(ValueError, match="already chained"):
        reg.record_quarantine_decision(row)
    assert len(decisions(reg)) == 1


@pytest.mark.parametrize("bad", [
    {"action": "sell"}, {"action": None}, {"action": {}},
    {"date": "2023-1-22"}, {"date": 20230122}, {"price": "111"},
    {"price": True}, {"position_frac": None}, {"equity": float("nan")},
    {"equity": float("inf")}, {"asset": ""},
])
def test_registry_rejects_meaningless_decision_values(tmp_path, bad):
    reg, spec, data = quarantined(tmp_path)
    row = {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
           "asset": "BTCUSD", "action": "hold", "price": 100.0,
           "position_frac": 0.0, "equity": 1.0}
    row.update(bad)
    with pytest.raises(ValueError):
        reg.record_quarantine_decision(row)
    assert decisions(reg) == []


def test_append_public_behaviour_is_unchanged(tmp_path):
    """append() was split so record_quarantine_decision could hold the lock
    across check-then-append. Its public behaviour must not have moved."""
    reg = Registry(tmp_path / "r.jsonl")
    e = reg.append("note", {"text": "x"}, ts_utc="2023-01-01T00:00:00Z")
    assert e == {"version": 1, "ts_utc": "2023-01-01T00:00:00Z",
                 "entry_type": "note", "prev_entry_hash": "0" * 64,
                 "payload": {"text": "x"}}
    assert not (tmp_path / "r.jsonl.lock").exists()   # released
    assert [x["payload"] for x in reg.entries()] == [{"text": "x"}]


# ---- multi-asset and multi-day invariants ----

def test_two_asset_universe_writes_one_row_per_asset(tmp_path, capsys):
    reg, spec, data = quarantined_two_asset(tmp_path)
    rc = quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    assert rc == 0
    rows = decisions(reg)
    assert len(rows) == 2
    assert {r["asset"] for r in rows} == {"BTCUSD", "ETHUSD"}
    assert all(r["date"] == "2023-01-22" for r in rows)
    by_asset = {r["asset"]: r for r in rows}
    assert by_asset["BTCUSD"]["action"] == "enter_long"
    assert by_asset["ETHUSD"]["action"] == "hold"
    # a date counts once towards the minimum, not once per asset
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    assert f"days 1/{MIN_TRADING_DAYS}" in capsys.readouterr().out


def test_strategy_that_never_trades_records_flat_holds(tmp_path):
    reg, spec, data = quarantined(tmp_path, bars=flat_dated_bars())
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    r = decisions(reg)[-1]
    assert r["action"] == "hold"
    assert r["position_frac"] == pytest.approx(0.0)
    assert r["equity"] == pytest.approx(1.0)


def test_partial_write_is_announced_and_recovers_on_rerun(tmp_path, capsys,
                                                          monkeypatch):
    """The module's own crash promise: what landed stays, and re-running the
    date completes it rather than duplicating it."""
    reg, spec, data = quarantined_two_asset(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    real = Registry.record_quarantine_decision
    calls = []

    def flaky(self, payload):
        calls.append(payload)
        if len(calls) == 2:
            raise RuntimeError("chain write blew up")
        return real(self, payload)

    monkeypatch.setattr(Registry, "record_quarantine_decision", flaky)
    with pytest.raises(RuntimeError):
        quarantine_run(argv)
    assert "PARTIAL WRITE: 1 decision(s) chained" in capsys.readouterr().err
    assert len(decisions(reg)) == 1

    monkeypatch.undo()
    assert quarantine_run(argv) == 0
    rows = decisions(reg)
    assert len(rows) == 2
    assert {r["asset"] for r in rows} == {"BTCUSD", "ETHUSD"}
    assert "1 already present" in capsys.readouterr().out


def test_out_of_order_backfill_matches_forward_order(tmp_path):
    """Recording 01-23 then 01-22 must produce exactly the rows that
    recording them in order would, because each day recomputes from bar 0."""
    dates = ["2023-01-22", "2023-01-23"]
    produced = {}
    for name, order in (("fwd", dates), ("bwd", list(reversed(dates)))):
        root = tmp_path / name
        root.mkdir()
        reg, spec, data = quarantined(root)
        for d in order:
            assert quarantine_run(argv_for(reg, data, "--date", d)) == 0
        rows = decisions(reg)
        # strategy_id is content-addressed from a card that make_card() stamps
        # with the wall clock, so two fixtures built either side of a second
        # boundary carry different ids. That is a property of the fixture, not
        # of ordering: assert it per-registry, and leave it out of the
        # cross-order comparison so this cannot flake.
        assert {r["strategy_id"] for r in rows} == {spec["strategy_id"]}
        produced[name] = {
            (r["date"], r["asset"]): {k: v for k, v in r.items()
                                      if k != "strategy_id"}
            for r in rows}
    assert produced["fwd"] == produced["bwd"]
    assert len(produced["fwd"]) == 2


def test_a_gap_in_an_unrelated_asset_does_not_refuse_everyone(tmp_path, capsys):
    """The asset union is built from the strategies actually owed a decision,
    so a strategy still before its entry date cannot block the others."""
    reg, spec, data = quarantined(tmp_path)
    later = json.loads(json.dumps(spec))
    later["universe"] = dict(later["universe"], assets=["NOSUCHUSD"])
    later["strategy_id"] = None
    later["strategy_id"] = content_id(later, "strategy_id")
    reg.register_strategy(later)
    reg.record_state_change(later["strategy_id"], "screened", "test")
    reg.record_state_change(later["strategy_id"], "gauntlet", None)
    reg.record_state_change(later["strategy_id"], "quarantine", "test",
                            ts_utc="2023-06-01T00:00:00Z")
    rc = quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    assert rc == 0
    rows = decisions(reg)
    assert len(rows) == 1
    assert rows[0]["strategy_id"] == spec["strategy_id"]
    assert "not after its quarantine entry" in capsys.readouterr().out


def test_review_reports_a_record_that_has_stopped(tmp_path, capsys):
    """The audit window ends at the last bar that EXISTS, not the last one
    recorded. Ending it at the last recorded date made a job that died weeks
    ago report a clean bill of health -- and an ongoing outage is precisely
    the failure most likely to persist unattended."""
    bars = flat_dated_bars(n=40)                 # 2023-01-01 .. 2023-02-09
    reg, spec, data = quarantined(tmp_path, bars=bars)
    # record only the first two days after entry, then "stop"
    for d in ("2023-01-22", "2023-01-23"):
        quarantine_run(argv_for(reg, data, "--date", d))
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    # entry 2023-01-21, bars to 2023-02-09 -> 19 owed days, 2 recorded.
    # Under the old window (ending at the last RECORDED date) this read 0.
    assert "unrecorded bar dates in window: 17" in out
    # 17 > MAX_LISTED_GAPS, so the trailing dates are truncated -- and the
    # truncation says so rather than implying the list was complete
    assert f"... and {17 - MAX_LISTED_GAPS} more" in out


def test_review_audits_a_strategy_that_never_recorded_anything(tmp_path, capsys):
    """Zero rows must not read as 'nothing to see'. A strategy quarantined
    weeks ago with no decisions owes every bar since its entry."""
    bars = flat_dated_bars(n=40)
    reg, spec, data = quarantined(tmp_path, bars=bars)
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    # entry 2023-01-21, bars run to 2023-02-09 -> 19 owed days, none recorded
    assert "unrecorded bar dates in window: 19" in out


def test_review_warns_when_a_row_predates_its_own_bar(tmp_path, capsys):
    """A row chained BEFORE its bar date means a decision was recorded against
    a bar that had not happened -- the closest thing this system can produce
    to a fabricated forward observation. It must never land in the
    'every row chained within Nd' branch."""
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    row = decisions(reg)[0]
    # chain a second asset's row stamped a month BEFORE its bar date
    reg.append("quarantine_decision", dict(row, asset="ETHUSD"),
               ts_utc="2022-12-21T00:00:00Z")
    capsys.readouterr()
    quarantine_run(argv_for(reg, data, "--review"))
    out = capsys.readouterr().out
    assert "chained BEFORE their own bar date" in out
    assert "every row chained within" not in out


def test_rebase_refuses_when_no_bar_precedes_the_entry(tmp_path):
    """Silently rebasing on the first available bar would fold pre-quarantine
    performance into the forward record."""
    with pytest.raises(ValueError, match="no bar on or before"):
        quarantine_mod._rebase_index(
            [{"date": "2023-05-01"}], "2023-01-01", "BTCUSD")


def test_a_duplicate_with_different_content_is_fatal(tmp_path, capsys,
                                                     monkeypatch):
    """'Already present' must mean already present AND identical. Same key
    with different numbers means the data or the spec moved underneath us,
    and discarding the losing row in silence would hide exactly that.

    `existing_decisions` is stubbed empty so the cheap `seen` pre-filter
    misses and the writer's own guard -- the one that holds under a real
    race, inside the lock -- is what gets exercised."""
    reg, spec, data = quarantined(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    quarantine_run(argv)
    row = decisions(reg)[0]
    assert len(decisions(reg)) == 1
    # replace the chained row with the same key carrying different numbers
    lines = reg.log_path.read_text(encoding="utf-8").splitlines()
    reg.log_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    reg.append("quarantine_decision", dict(row, equity=9.9999))

    monkeypatch.setattr(quarantine_mod, "existing_decisions", lambda r: set())
    with pytest.raises(DuplicateQuarantineDecision):
        quarantine_run(argv)


def test_an_identical_duplicate_is_still_absorbed(tmp_path, capsys,
                                                  monkeypatch):
    """The other half of the same rule: a benign race -- a second process
    recomputing the SAME row -- resolves quietly, rc 0, counted honestly."""
    reg, spec, data = quarantined(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    quarantine_run(argv)
    assert len(decisions(reg)) == 1
    monkeypatch.setattr(quarantine_mod, "existing_decisions", lambda r: set())
    capsys.readouterr()
    rc = quarantine_run(argv)
    assert rc == 0
    assert len(decisions(reg)) == 1
    assert "0 decision(s) chained, 1 already present" in capsys.readouterr().out


# ---------------- quarantine_data_snapshot: the bars behind the record ----

import hashlib

from .registry import DuplicateQuarantineSnapshot


def sha_of(data_dir, asset):
    """The digest an auditor gets from a plain `sha256sum` of the price file
    -- the same construction screen.py and gauntlet.py already record."""
    return hashlib.sha256(
        (data_dir / f"{asset}_1d.csv").read_bytes()).hexdigest()


def bars_sha_of(data_dir, asset, date="2023-01-22"):
    return quarantine_mod.hash_bars_through(data_dir, asset, date)


def snap_payload(data_dir, assets, date="2023-01-22"):
    """A well-formed snapshot for `assets` as they are on disk right now."""
    return {"date": date,
            "data_sha256": {a: sha_of(data_dir, a) for a in assets},
            "bars_sha256": {a: bars_sha_of(data_dir, a, date) for a in assets}}


def entry_types(reg):
    return [e["entry_type"] for e in reg.entries()]


def snapshots(reg):
    return [e["payload"] for e in reg.entries()
            if e["entry_type"] == "quarantine_data_snapshot"]


def test_first_run_chains_one_snapshot_before_the_decision_rows(tmp_path):
    """Invariant 9 is 'an EARLIER snapshot', so ORDER is the assertion, not
    mere presence: a snapshot chained after the rows would prove nothing about
    what the rows were computed from."""
    reg, spec, data = quarantined(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    types = entry_types(reg)
    assert types.count("quarantine_data_snapshot") == 1
    assert (types.index("quarantine_data_snapshot")
            < types.index("quarantine_decision"))
    assert snapshots(reg)[0] == snap_payload(data, ["BTCUSD"])


def test_snapshot_covers_every_asset_the_day_loaded(tmp_path):
    reg, spec, data = quarantined_two_asset(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert snapshots(reg)[0] == snap_payload(data, ["BTCUSD", "ETHUSD"])


def test_rerunning_a_date_chains_no_second_snapshot(tmp_path, capsys):
    """One snapshot per date is a verifier invariant and the chain is
    append-only, so a second one could never be repaired."""
    reg, spec, data = quarantined(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    quarantine_run(argv)
    n_after_first = sum(1 for _ in reg.entries())
    capsys.readouterr()
    assert quarantine_run(argv) == 0
    assert sum(1 for _ in reg.entries()) == n_after_first
    assert len(snapshots(reg)) == 1
    assert "1 already present" in capsys.readouterr().out


def test_a_restated_price_file_refuses_the_whole_day(tmp_path, capsys):
    """The detector, not just the provenance. Re-running a date against bars
    that have been revised would not reproduce the rows already chained, so
    the day is refused rather than silently recomputed."""
    reg, spec, data = quarantined(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    quarantine_run(argv)
    before = sum(1 for _ in reg.entries())
    recorded = snapshots(reg)[0]["bars_sha256"]["BTCUSD"]

    csv_path = data / "BTCUSD_1d.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    # restate the 2023-01-22 bar ITSELF, not a later one: the failure this
    # guards is a revision to bars the chained rows were computed from
    i = next(i for i, l in enumerate(lines) if l.startswith("2023-01-22,"))
    lines[i] = "2023-01-22,111,111,111,112,1.0"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    capsys.readouterr()
    assert quarantine_run(argv) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "BTCUSD" in err
    assert recorded in err                      # names the chained hash
    assert bars_sha_of(data, "BTCUSD") in err   # and the recomputed one
    assert sum(1 for _ in reg.entries()) == before      # nothing written


def test_a_snapshot_that_misses_a_needed_asset_refuses(tmp_path, capsys):
    """The chain is append-only, so a snapshot cannot be amended to cover an
    asset it never named, and a second snapshot for the date would break
    uniqueness. Hand-chained here because a legitimate forward record cannot
    produce it: a strategy is only ever recorded for dates strictly after its
    own quarantine entry, so its assets first appear on a date with no
    snapshot yet."""
    reg, spec, data = quarantined_two_asset(tmp_path)
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD"]))
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "ETHUSD" in err
    assert decisions(reg) == []
    assert len(snapshots(reg)) == 1


def test_an_identical_concurrent_snapshot_is_reconciled_not_duplicated(
        tmp_path, capsys, monkeypatch):
    """Two processes that both read 'no snapshot for this date yet' must not
    chain two snapshots: verify_registry.py rejects that and the chain cannot
    be repaired. The writer's check therefore runs under the same lock as the
    append, and the loser reconciles against what landed."""
    reg, spec, data = quarantined(tmp_path)
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD"]))
    # the snapshot lands after this run reads the chain, so the pre-read
    # misses it and the writer's under-lock guard is what fires
    monkeypatch.setattr(quarantine_mod, "data_snapshots", lambda r: {})
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 0
    assert len(snapshots(reg)) == 1
    assert len(decisions(reg)) == 1


def test_a_conflicting_concurrent_snapshot_refuses(tmp_path, capsys,
                                                   monkeypatch):
    """The other half: what landed disagrees with the bars this run holds, so
    the day is refused rather than recorded against unknown data."""
    reg, spec, data = quarantined(tmp_path)
    reg.record_quarantine_snapshot(
        dict(snap_payload(data, ["BTCUSD"]), bars_sha256={"BTCUSD": "b" * 64}))
    monkeypatch.setattr(quarantine_mod, "data_snapshots", lambda r: {})
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 1
    assert "REFUSED" in capsys.readouterr().err
    assert decisions(reg) == []
    assert len(snapshots(reg)) == 1


def test_snapshot_conflicts_is_empty_when_the_bars_are_unchanged():
    """The comparison alone, without running the command -- the check_aligned
    precedent."""
    assert quarantine_mod.snapshot_conflicts({"BTCUSD": "a" * 64},
                                             {"BTCUSD": "a" * 64}) == []
    # a snapshot may cover MORE than a given day needs (a strategy buried
    # since), which is not a conflict
    assert quarantine_mod.snapshot_conflicts(
        {"BTCUSD": "a" * 64, "ETHUSD": "b" * 64}, {"BTCUSD": "a" * 64}) == []


def test_refused_day_chains_no_snapshot(tmp_path, capsys):
    """A day refused for a missing bar must not leave provenance for a record
    that does not exist."""
    reg, spec, data = quarantined(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-30")) == 1
    assert snapshots(reg) == []


def test_a_day_with_nobody_eligible_chains_no_snapshot(tmp_path, capsys):
    reg, spec, data = quarantined(tmp_path)
    assert quarantine_run(argv_for(reg, data, "--date", ENTERED)) == 0
    assert snapshots(reg) == []


OK_D = {"BTCUSD": "a" * 64}
OK_B = {"BTCUSD": "b" * 64}


@pytest.mark.parametrize("bad", [
    {},                                                     # every key missing
    {"date": "2023-01-22", "data_sha256": OK_D},            # no bars_sha256
    {"date": "2023-01-22", "bars_sha256": OK_B},            # no data_sha256
    {"data_sha256": OK_D, "bars_sha256": OK_B},             # no date
    {"date": "2023-01-22", "data_sha256": OK_D, "bars_sha256": OK_B,
     "note": "x"},                                          # unknown key
    {"date": "2023-1-22", "data_sha256": OK_D, "bars_sha256": OK_B},
    {"date": 20230122, "data_sha256": OK_D, "bars_sha256": OK_B},
    {"date": "2023-02-30", "data_sha256": OK_D, "bars_sha256": OK_B},
    {"date": "2023-01-22", "data_sha256": {}, "bars_sha256": OK_B},
    {"date": "2023-01-22", "data_sha256": OK_D, "bars_sha256": {}},
    {"date": "2023-01-22", "data_sha256": "a" * 64, "bars_sha256": OK_B},
    {"date": "2023-01-22", "data_sha256": OK_D, "bars_sha256": "b" * 64},
    {"date": "2023-01-22", "data_sha256": {"BTCUSD": "a" * 63},
     "bars_sha256": OK_B},
    {"date": "2023-01-22", "data_sha256": {"BTCUSD": "Z" * 64},
     "bars_sha256": OK_B},
    {"date": "2023-01-22", "data_sha256": {"BTCUSD": None},
     "bars_sha256": OK_B},
    {"date": "2023-01-22", "data_sha256": {"": "a" * 64}, "bars_sha256": OK_B},
    {"date": "2023-01-22", "data_sha256": OK_D,
     "bars_sha256": {"ETHUSD": "b" * 64}},          # maps disagree on assets
])
def test_registry_rejects_a_malformed_snapshot(tmp_path, bad):
    reg = Registry(tmp_path / "r.jsonl")
    with pytest.raises(ValueError):
        reg.record_quarantine_snapshot(bad)
    assert not reg.log_path.exists()


def test_registry_refuses_a_second_snapshot_for_the_same_date(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    payload = {"date": "2023-01-22", "data_sha256": {"BTCUSD": "a" * 64},
               "bars_sha256": {"BTCUSD": "b" * 64}}
    reg.record_quarantine_snapshot(dict(payload))
    with pytest.raises(DuplicateQuarantineSnapshot) as e:
        reg.record_quarantine_snapshot(dict(payload))
    assert e.value.chained == payload
    assert len(snapshots(reg)) == 1


def test_duplicate_snapshot_error_is_a_valueerror_subclass():
    assert issubclass(DuplicateQuarantineSnapshot, ValueError)


# ---------------- verifier invariants 7-9 + SCHEMA ----------------

from .test_screen import run_verifier


def test_verifier_accepts_a_valid_quarantine_decision(tmp_path):
    """End-to-end proof that the writer and the invariants agree: this only
    passes if quarantine_run chains the snapshot BEFORE the decision."""
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(["--registry", str(reg.log_path), "--data-dir", str(data),
                    "--date", "2023-01-22"])
    out = run_verifier(reg.log_path)
    assert out.returncode == 0, out.stdout
    assert "quarantine_decision=1" in out.stdout
    assert "quarantine_data_snapshot=1" in out.stdout


def test_verifier_rejects_decision_for_non_quarantined_strategy(tmp_path):
    """Invariant 7. The row also trips invariant 9 (no snapshot was chained),
    and the verifier reports every failure, so the named message is what pins
    this test to the state check rather than to the coverage check."""
    reg, spec = gauntlet_registry(tmp_path)          # still in 'gauntlet'
    # bypass the typed writer's guard to prove the CHAIN is checked too
    reg.append("quarantine_decision",
               {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
                "asset": "BTCUSD", "action": "hold", "price": 100.0,
                "position_frac": 0.0, "equity": 1.0})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "not 'quarantine'" in out.stdout


def test_verifier_rejects_duplicate_decision_key(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    row = {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
           "asset": "BTCUSD", "action": "hold", "price": 100.0,
           "position_frac": 0.0, "equity": 1.0}
    reg.append("quarantine_decision", dict(row))
    reg.append("quarantine_decision", dict(row))
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate quarantine_decision" in out.stdout


def test_verifier_rejects_a_decision_with_no_earlier_snapshot(tmp_path):
    """Invariant 9. The snapshot is on the chain but AFTER the row it would
    have to cover, so presence alone is not enough."""
    reg, spec, data = quarantined(tmp_path)
    reg.append("quarantine_decision",
               {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
                "asset": "BTCUSD", "action": "hold", "price": 100.0,
                "position_frac": 0.0, "equity": 1.0})
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD"]))
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "no earlier quarantine_data_snapshot" in out.stdout


def test_verifier_rejects_a_decision_the_snapshot_does_not_name(tmp_path):
    reg, spec, data = quarantined_two_asset(tmp_path)
    reg.record_quarantine_snapshot(snap_payload(data, ["BTCUSD"]))
    reg.append("quarantine_decision",
               {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
                "asset": "ETHUSD", "action": "hold", "price": 100.0,
                "position_frac": 0.0, "equity": 1.0})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "no earlier quarantine_data_snapshot" in out.stdout


def test_verifier_rejects_duplicate_snapshot_dates(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    reg.append("quarantine_data_snapshot", snap_payload(data, ["BTCUSD"]))
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate quarantine_data_snapshot" in out.stdout


def test_verifier_reports_a_malformed_snapshot_instead_of_crashing(tmp_path):
    """The chain is append-only: a single bad entry must not make the
    remaining thousands unverifiable. The walk continues and still counts
    every entry."""
    reg, spec, data = quarantined(tmp_path)
    reg.append("quarantine_data_snapshot",
               dict(snap_payload(data, ["BTCUSD"]), data_sha256=7))
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "data_sha256" in out.stdout
    n = sum(1 for _ in reg.entries())
    assert f"Entries           : {n}" in out.stdout


def test_verifier_rejects_a_duplicate_composition_fingerprint(tmp_path):
    """Invariant 8: rule 7 becomes chain-verifiable. The composer's in-process
    guard is bypassed here on purpose -- the point is that the CHAIN catches a
    re-registered composition even if the guard ever fails."""
    fam = good_family(family="dupfam", card_ids=[CARD_ID])
    spec = pre_expand(fam)[0]
    reg = seeded(tmp_path, pre_register=[spec])
    twin = json.loads(json.dumps(spec))
    twin["strategy_id"] = "f" * 16          # fresh id, identical composition
    twin["created_utc"] = "2026-08-17T00:00:00Z"
    assert composition_fingerprint(twin) == composition_fingerprint(spec)
    reg.append("strategy_registered", twin)
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "duplicate composition" in out.stdout
    assert spec["strategy_id"] in out.stdout   # names the original


def test_verifier_reports_an_unfingerprintable_spec_instead_of_crashing(tmp_path):
    reg = seeded(tmp_path)
    reg.append("strategy_registered",
               {"strategy_id": "e" * 16, "blocks": [],
                "provenance": {"card_ids": [CARD_ID]}})   # no universe
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "cannot fingerprint" in out.stdout
    n = sum(1 for _ in reg.entries())
    assert f"Entries           : {n}" in out.stdout


def test_schema_documents_quarantine_decision_and_the_v3_amendment():
    text = (LAYER / "SCHEMA.md").read_text(encoding="utf-8")
    assert "`quarantine_decision`" in text
    assert "position_frac" in text
    assert "protocol-v3" in text
    assert "quarantine → live" in text
    assert "composition_fingerprint" in text   # invariant 8 disclosed
    assert "`quarantine_data_snapshot`" in text


# ---------------- bars_sha256: the hash that keeps backfill working --------

import subprocess


def read_csv_lines(data_dir, asset="BTCUSD"):
    return (data_dir / f"{asset}_1d.csv").read_text(
        encoding="utf-8").splitlines()


def write_csv_lines(data_dir, lines, asset="BTCUSD", newline="\n"):
    (data_dir / f"{asset}_1d.csv").write_bytes(
        newline.join(lines).encode("utf-8") + newline.encode("utf-8"))


def test_bars_hash_matches_the_documented_shell_recipe(tmp_path):
    """The definition is only worth recording if a third party can reproduce
    it without our code. This IS the recipe from the docstring, done by hand:
    header + rows dated <= D, LF, one newline per line."""
    reg, spec, data = quarantined(tmp_path)
    lines = read_csv_lines(data)
    kept = [lines[0]] + [l for l in lines[1:] if l.split(",")[0] <= "2023-01-22"]
    by_hand = hashlib.sha256(
        ("\n".join(kept) + "\n").encode("utf-8")).hexdigest()
    assert quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22") \
        == by_hand
    # and it is genuinely a subset: the whole file hashes differently
    assert by_hand != sha_of(data, "BTCUSD")


def test_bars_hash_is_stable_across_a_crlf_round_trip(tmp_path):
    """This repo produces CRLF working copies, so a hash that moved with the
    line endings would refuse every day on a fresh clone."""
    reg, spec, data = quarantined(tmp_path)
    lines = read_csv_lines(data)
    write_csv_lines(data, lines, newline="\n")
    lf = quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22")
    lf_file = sha_of(data, "BTCUSD")
    write_csv_lines(data, lines, newline="\r\n")
    assert quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22") == lf
    # the whole-file hash DID move, which is what makes this assertion mean
    # something rather than restating that nothing changed
    assert sha_of(data, "BTCUSD") != lf_file


def test_bars_hash_ignores_bars_after_the_date(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    before = quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22")
    lines = read_csv_lines(data)
    write_csv_lines(data, lines + ["2023-02-01,200,200,200,200,1.0"])
    assert quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22") \
        == before
    # ... but the later date sees it
    assert quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-02-01") \
        != before


def test_appending_a_later_bar_leaves_an_earlier_rerun_alone(tmp_path, capsys):
    """THE regression this second hash exists for. Task 6 refreshes the data
    dir before running, so guarding on the whole-file hash would refuse every
    backfill -- the runner's primary recovery path -- even though load_bars
    truncates at the cutoff and returns byte-identical bars."""
    reg, spec, data = quarantined(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    quarantine_run(argv)
    chained = snapshots(reg)[0]

    write_csv_lines(data, read_csv_lines(data)
                    + ["2023-02-01,200,200,200,200,1.0"])
    # the refresh really did change the file, so this is not a no-op test
    assert sha_of(data, "BTCUSD") != chained["data_sha256"]["BTCUSD"]
    assert bars_sha_of(data, "BTCUSD") == chained["bars_sha256"]["BTCUSD"]

    capsys.readouterr()
    assert quarantine_run(argv) == 0
    assert "1 already present" in capsys.readouterr().out
    assert len(snapshots(reg)) == 1


def test_a_partial_day_still_backfills_after_a_refresh(tmp_path, capsys,
                                                       monkeypatch):
    """The same regression at the level it actually bites: a crash leaves one
    asset unrecorded, the data dir refreshes overnight, and the re-run must
    complete the day rather than refuse it."""
    reg, spec, data = quarantined_two_asset(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    real = Registry.record_quarantine_decision
    calls = []

    def flaky(self, payload):
        calls.append(payload)
        if len(calls) == 2:
            raise RuntimeError("chain write blew up")
        return real(self, payload)

    monkeypatch.setattr(Registry, "record_quarantine_decision", flaky)
    with pytest.raises(RuntimeError):
        quarantine_run(argv)
    monkeypatch.undo()
    assert len(decisions(reg)) == 1

    for asset in ("BTCUSD", "ETHUSD"):          # overnight refresh
        write_csv_lines(data, read_csv_lines(data, asset)
                        + ["2023-02-01,200,200,200,200,1.0"], asset=asset)
    capsys.readouterr()
    assert quarantine_run(argv) == 0
    assert {r["asset"] for r in decisions(reg)} == {"BTCUSD", "ETHUSD"}


def test_restating_a_bar_at_or_before_the_date_still_refuses(tmp_path, capsys):
    """The other half: the guard must stay a guard. A revision to a bar the
    chained rows were computed from is exactly what it exists to catch."""
    reg, spec, data = quarantined(tmp_path)
    argv = argv_for(reg, data, "--date", "2023-01-22")
    quarantine_run(argv)
    before = sum(1 for _ in reg.entries())

    lines = read_csv_lines(data)
    i = next(i for i, l in enumerate(lines) if l.startswith("2023-01-21,"))
    lines[i] = "2023-01-21,100,110,100,109,1.0"
    write_csv_lines(data, lines)

    capsys.readouterr()
    assert quarantine_run(argv) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "BTCUSD" in err
    assert "bars up to this date have changed" in err
    assert sum(1 for _ in reg.entries()) == before


def test_verifier_rejects_a_snapshot_whose_maps_disagree(tmp_path):
    """An asset hashed only one way is not fully provenanced, so it must not
    license a decision."""
    reg, spec, data = quarantined_two_asset(tmp_path)
    reg.append("quarantine_data_snapshot",
               dict(snap_payload(data, ["BTCUSD", "ETHUSD"]),
                    bars_sha256={"BTCUSD": "b" * 64}))
    reg.append("quarantine_decision",
               {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
                "asset": "ETHUSD", "action": "hold", "price": 100.0,
                "position_frac": 0.0, "equity": 1.0})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "names different assets" in out.stdout
    assert "no earlier quarantine_data_snapshot" in out.stdout


@pytest.mark.parametrize("field,value", [
    ("strategy_id", ["a"]), ("date", ["2023-01-22"]), ("asset", {"x": 1}),
    ("date", 20230122), ("asset", None),
])
def test_verifier_reports_an_unusable_decision_key_instead_of_crashing(
        tmp_path, field, value):
    """Invariants 7 and 9 put (strategy_id, date, asset) in a set and use the
    date as a dict key, so an unhashable field would raise out of the walk and
    leave every LATER entry unverified -- the worst failure mode an
    append-only public chain has."""
    reg, spec, data = quarantined(tmp_path)
    quarantine_run(argv_for(reg, data, "--date", "2023-01-22"))
    row = {"strategy_id": spec["strategy_id"], "date": "2023-01-23",
           "asset": "BTCUSD", "action": "hold", "price": 100.0,
           "position_frac": 0.0, "equity": 1.0}
    row[field] = value
    reg.append("quarantine_decision", row)
    reg.append("note", {"text": "an entry after the bad one"})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "must be strings" in out.stdout
    # the walk continued to the end rather than dying at the bad entry
    n = sum(1 for _ in reg.entries())
    assert f"Entries           : {n}" in out.stdout
    assert "note=" in out.stdout


def test_verifier_counts_failing_entries_not_failures(tmp_path):
    """One entry that trips several invariants is ONE failing entry. Counting
    problems instead made the summary claim more failures than the log has
    lines -- '2/27 entries fail' on a 26-entry chain -- which is exactly the
    kind of arithmetic that undermines 'verify it yourself'."""
    reg, spec, data = quarantined(tmp_path)
    # no snapshot on the chain and a duplicated key: two invariants, one row,
    # chained twice
    row = {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
           "asset": "BTCUSD", "action": "hold", "price": 100.0,
           "position_frac": 0.0, "equity": 1.0}
    reg.append("quarantine_decision", dict(row))
    reg.append("quarantine_decision", dict(row))
    out = run_verifier(reg.log_path)
    n = sum(1 for _ in reg.entries())
    assert f"Entries           : {n}" in out.stdout
    assert f"2/{n} entries fail." in out.stdout


# ---- the walk must survive any single malformed entry --------------------

def walk_reached_the_end(reg, out):
    """The property every guard below exists to preserve: the verifier
    reported the problem, kept walking, and still accounted for the entries
    AFTER the bad one."""
    n = sum(1 for _ in reg.entries())
    assert f"Entries           : {n}" in out.stdout, out.stdout
    assert "note=" in out.stdout, out.stdout


@pytest.mark.parametrize("etype", ["quarantine_data_snapshot",
                                   "quarantine_decision",
                                   "strategy_registered"])
@pytest.mark.parametrize("payload", ["oops", 7, ["a"], None])
def test_verifier_survives_a_non_dict_payload(tmp_path, etype, payload):
    """REGRESSION: before the global guard, a payload of "oops" raised
    AttributeError out of the walk. Every entry after it went unverified --
    and at ddc0838 the same chain verified clean, because these entry types
    matched no branch and the payload was never dereferenced."""
    reg, spec, data = quarantined(tmp_path)
    reg.append(etype, payload)
    reg.append("note", {"text": "an entry after the bad one"})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "payload is not a JSON object" in out.stdout
    walk_reached_the_end(reg, out)


def test_verifier_survives_an_entry_that_is_not_an_object(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    with reg.log_path.open("a", encoding="utf-8") as f:
        f.write('["not", "an", "object"]\n')
    reg.append("note", {"text": "an entry after the bad one"})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "entry is not a JSON object" in out.stdout
    # the head still advanced, so the FOLLOWING entry is not falsely accused
    assert "BROKEN CHAIN" not in out.stdout
    walk_reached_the_end(reg, out)


@pytest.mark.parametrize("mangle", [
    {"provenance": "nope"},
    {"provenance": {"card_ids": "not-a-list"}},
    {"provenance": {"card_ids": [["unhashable"]]}},
    {"provenance": {"card_ids": [1, 2]}},
    {"blocks": "nope"},
    {"blocks": ["not-an-object"]},
    {"blocks": [{"role": ["unhashable"], "type": "x"}]},
    {"blocks": [{"role": "entry"}]},
])
def test_verifier_survives_a_malformed_strategy_spec(tmp_path, mangle):
    """set() of a non-iterable, and a tuple containing a list, both raise --
    and both are reachable from a hand-appended spec."""
    reg = seeded(tmp_path)
    spec = pre_expand(good_family(family="mangled", card_ids=[CARD_ID]))[0]
    reg.append("strategy_registered", dict(json.loads(json.dumps(spec)), **mangle))
    reg.append("note", {"text": "an entry after the bad one"})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    walk_reached_the_end(reg, out)


def test_verifier_survives_a_non_string_snapshot_date(tmp_path):
    """A list date raises `unhashable type` on the snapshots dict lookup."""
    reg, spec, data = quarantined(tmp_path)
    reg.append("quarantine_data_snapshot",
               dict(snap_payload(data, ["BTCUSD"]), date=["2023-01-22"]))
    reg.append("note", {"text": "an entry after the bad one"})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "is not a string" in out.stdout
    walk_reached_the_end(reg, out)


def test_duplicate_composition_names_the_original_not_none(tmp_path):
    """An unnamed spec must not become the first holder of a fingerprint, or
    the duplicate that follows reports 'already registered as None' and the
    real culprit goes unnamed."""
    reg = seeded(tmp_path)
    spec = pre_expand(good_family(family="anon", card_ids=[CARD_ID]))[0]
    anon = json.loads(json.dumps(spec))
    del anon["strategy_id"]
    reg.append("strategy_registered", anon)
    reg.append("strategy_registered", spec)
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "already registered as None" not in out.stdout


# ---- the verifier must reject digests the writer rejects ------------------

def test_verifier_rejects_a_snapshot_digest_that_is_not_a_digest(tmp_path):
    """Invariant 8's own principle applied to invariant 9: the chain must not
    have to trust the in-process guard. A fabricated digest that the writer
    refuses must not verify clean, or an outsider cannot tell a real
    provenance record from an invented one."""
    reg, spec, data = quarantined(tmp_path)
    fake = dict(snap_payload(data, ["BTCUSD"]),
                data_sha256={"BTCUSD": "not-a-hash"},
                bars_sha256={"BTCUSD": "not-a-hash"})
    # the writer refuses this payload outright ...
    with pytest.raises(ValueError, match="64 lowercase hex"):
        reg.record_quarantine_snapshot(dict(fake))
    # ... so the verifier must too, when it is chained around the writer
    reg.append("quarantine_data_snapshot", fake)
    reg.append("quarantine_decision",
               {"strategy_id": spec["strategy_id"], "date": "2023-01-22",
                "asset": "BTCUSD", "action": "hold", "price": 100.0,
                "position_frac": 0.0, "equity": 1.0})
    out = run_verifier(reg.log_path)
    assert out.returncode == 1
    assert "not a sha256 digest" in out.stdout
    # and the day's decisions FAIL CLOSED: a fake digest licenses nothing
    assert "no earlier quarantine_data_snapshot" in out.stdout


@pytest.mark.parametrize("bad", ["not-a-hash", "A" * 64, "a" * 63, "a" * 65,
                                 None, 64, "", " " * 64])
def test_verifier_and_writer_agree_on_what_a_digest_is(bad):
    """One implementation, asserted directly: a second copy in the verifier is
    the drift hazard invariant 8 avoided for the fingerprint."""
    from .registry import is_sha256_hex
    assert is_sha256_hex(bad) is False
    assert is_sha256_hex("a" * 64) is True
    assert is_sha256_hex(hashlib.sha256(b"x").hexdigest()) is True


def test_a_snapshot_with_no_usable_bars_hash_refuses_rather_than_crashing(
        tmp_path, capsys):
    """`recorded.get("bars_sha256") or {}` in run(): a snapshot chained around
    the writer may carry no usable map, and snapshot_conflicts(None, ...)
    raises. Fail CLOSED instead -- every asset uncovered, day refused."""
    reg, spec, data = quarantined(tmp_path)
    reg.append("quarantine_data_snapshot",
               {"date": "2023-01-22",
                "data_sha256": {"BTCUSD": sha_of(data, "BTCUSD")}})
    capsys.readouterr()
    assert quarantine_run(argv_for(reg, data, "--date", "2023-01-22")) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "BTCUSD" in err
    assert decisions(reg) == []


def test_data_snapshots_keeps_the_first_payload_for_a_date(tmp_path):
    """Last-wins would have made this the one place in the system where a
    duplicate snapshot silently took effect; the writer refuses the second and
    the verifier reports it, so the reader must keep the first too."""
    reg, spec, data = quarantined(tmp_path)
    first = snap_payload(data, ["BTCUSD"])
    reg.append("quarantine_data_snapshot", first)
    reg.append("quarantine_data_snapshot",
               dict(first, bars_sha256={"BTCUSD": "c" * 64}))
    assert quarantine_mod.data_snapshots(reg)["2023-01-22"] == first


# ---- hash_bars_through edge cases ----------------------------------------

def test_bars_hash_on_a_file_with_no_trailing_newline(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    lines = read_csv_lines(data)
    expected = quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22")
    (data / "BTCUSD_1d.csv").write_bytes("\n".join(lines).encode("utf-8"))
    assert quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22") \
        == expected


def test_bars_hash_on_a_header_only_file(tmp_path):
    """No bars at or before the date is not an error here -- run() refuses the
    day long before this -- but it must hash the header rather than blow up."""
    reg, spec, data = quarantined(tmp_path)
    header = read_csv_lines(data)[0]
    write_csv_lines(data, [header])
    assert quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22") \
        == hashlib.sha256((header + "\n").encode("utf-8")).hexdigest()
    # and a date before every bar behaves the same way on a full file
    reg2, spec2, data2 = quarantined(tmp_path / "b")
    assert quarantine_mod.hash_bars_through(data2, "BTCUSD", "1999-01-01") \
        == hashlib.sha256(
            (read_csv_lines(data2)[0] + "\n").encode("utf-8")).hexdigest()


def test_bars_hash_refuses_an_empty_price_file(tmp_path):
    reg, spec, data = quarantined(tmp_path)
    (data / "BTCUSD_1d.csv").write_bytes(b"")
    with pytest.raises(ValueError, match="price file is empty"):
        quarantine_mod.hash_bars_through(data, "BTCUSD", "2023-01-22")


@pytest.mark.parametrize("etype,payload", [
    ("card_registered", {"card_id": ["x"]}),
    ("card_reviewed", {"card_id": ["x"], "status": "accepted"}),
    ("block_type_registered", {"role": ["x"], "type": "y"}),
    ("block_type_registered", {"role": "entry", "type": {"a": 1}}),
    ("strategy_registered", {"strategy_id": ["x"]}),
    ("verdict", {"strategy_id": ["x"]}),
    ("state_change", {"strategy_id": ["x"], "from": "a", "to": "b"}),
])
def test_verifier_survives_an_unhashable_key_field(tmp_path, etype, payload):
    """The whole crash class, not one instance of it. Every one of these
    fields goes into a set or becomes a dict key, so a list raises TypeError
    out of the walk and leaves every LATER entry unverified. All six of these
    still crashed after the payload/provenance/blocks guards went in."""
    reg = Registry(tmp_path / "r.jsonl")
    reg.append(etype, payload)
    reg.append("note", {"text": "an entry after the bad one"})
    out = run_verifier(reg.log_path)
    assert "Traceback" not in out.stderr, out.stderr
    assert out.returncode == 1
    assert "must be a non-empty string" in out.stdout
    walk_reached_the_end(reg, out)
