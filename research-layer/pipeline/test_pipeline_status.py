"""The pipeline's status artifact and its escalation shortlist.

Escalation is a SHORT list on purpose: pushing on every state change trains
Coen to ignore the channel, so only things that cost something by morning push."""
import pytest

from pipeline import pipeline_status as ps


def test_conforms_to_the_agent_status_convention():
    doc = ps.build(stage_results={}, spent=0.0)
    for key in ("agent", "domain", "ts_utc", "overall", "summary", "items"):
        assert key in doc, key
    assert doc["agent"] == "pipeline"
    assert doc["domain"] == "intelligence"


def test_items_carries_one_row_per_stage():
    doc = ps.build(stage_results={"composer": "OK", "screen": "OK",
                                  "gauntlet": "WARN", "quarantine": "OK"},
                   spent=1.0)
    assert doc["items"] == {"composer": "OK", "screen": "OK",
                            "gauntlet": "WARN", "quarantine": "OK"}
    assert doc["overall"] == "WARN"


def test_an_unregistered_stage_status_ranks_as_warn_not_ok():
    """Fail-safe: a status string nobody added to the table must surface, not
    silently report the pipeline healthy."""
    assert ps.build(stage_results={"screen": "WEIRD"}, spent=0.0)["overall"] == "WEIRD"


@pytest.mark.parametrize("trigger", [
    "chain_invalid", "budget_cap", "quarantine_gap", "run_aborted"])
def test_the_push_shortlist_is_exactly_the_four_costly_failures(trigger):
    assert trigger in ps.PUSH_TRIGGERS


def test_routine_events_do_not_push():
    """A finished run, a passing gauntlet, a new registration - all digest-only."""
    for e in ("run_complete", "gauntlet_pass", "strategy_registered"):
        assert e not in ps.PUSH_TRIGGERS


def test_escalations_are_reported_with_their_trigger_named():
    doc = ps.build(stage_results={"screen": "FAIL"}, spent=0.0,
                   escalations=["chain_invalid"])
    assert doc["escalations"] == ["chain_invalid"]
    assert doc["push"] is True


def test_no_escalation_means_no_push():
    assert ps.build(stage_results={"screen": "OK"}, spent=0.0)["push"] is False
