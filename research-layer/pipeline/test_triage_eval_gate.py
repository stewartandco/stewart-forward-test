"""The triage eval gate (harness audit 2026-11-08, Tier 2 #9). Coen's bar since
2026-11-08 is a RATCHET: the production rule passes; a candidate needs two runs each no worse than
production (false accepts strict, false rejects within 8 cards) and no worse run on file.

Fails unless docs/evals/triage/ holds MIN_RUNS complete, clean evals on the pinned label set
for the CURRENT rule key (tools.eval_triage.rule_sha: the review prompt plus panel_verdict,
review_card, PANEL_SIZE and MAX_VOTE_ATTEMPTS) and the model the loop runs
(triage_batch.DEFAULT_MODEL), with no run on file that saw a false accept. Editing the prompt,
the vote schema, the decision rule or the model therefore cannot land until
`python -m tools.eval_triage --layer <live research-layer>` has run twice on Coen's labelled
cards and the results are committed. Never relax this test to make a change land: run the eval.
"""
from tools.eval_triage import EVAL_DIR, gate_status, rule_sha

from .triage_batch import DEFAULT_MODEL


def test_the_current_triage_rule_and_model_are_production_or_beat_it_twice():
    ok, why = gate_status(EVAL_DIR, rule_sha(), DEFAULT_MODEL)
    assert ok, why
