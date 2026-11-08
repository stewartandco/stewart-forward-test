"""The triage eval gate (harness audit 2026-11-08, Tier 2 #9; Coen's bar: zero false accepts).

Fails unless docs/evals/triage/ holds a complete, passing eval for the CURRENT triage prompt
(triage_batch.REVIEW_PROMPT_SHA, itself pinned in test_prompt_stamps) and the model the loop
runs (triage_batch.DEFAULT_MODEL). Editing REVIEW_PROMPT, VOTE_SCHEMA or the model therefore
cannot land until `python -m tools.eval_triage --layer <live research-layer>` has run on Coen's
labelled cards and its result is committed. Never relax this test to make a change land: run the eval.
"""
from tools.eval_triage import EVAL_DIR, gate_status

from .triage_batch import DEFAULT_MODEL, REVIEW_PROMPT_SHA


def test_the_current_triage_prompt_and_model_have_a_passing_eval():
    ok, why = gate_status(EVAL_DIR, REVIEW_PROMPT_SHA, DEFAULT_MODEL)
    assert ok, why
