"""The pipeline's budget line: a standing monthly cap with a hard stop, plus
an 80% stop BETWEEN batches (the D21 Composer pattern).

The 2026-08-15 scanner runaway is why the hard stop exists: billing errors were
retried forever and logged 105,565 decisions in two hours. An alert alone does
not stop a machine."""
import pytest

from pipeline import pipeline_budget as pb
from pipeline.budget import PIPELINE_CAP_USD


def test_the_standing_line_is_declared_not_guessed():
    assert pb.MONTHLY_USD == PIPELINE_CAP_USD          # D39: one constant, 40 today
    assert pb.BATCH_STOP_FRACTION == 0.80


def test_under_the_line_a_batch_may_start():
    assert pb.may_start_batch(spent=5.0) is True


def test_at_eighty_percent_the_next_batch_is_refused():
    """Stopping BETWEEN batches, not mid-batch, so a batch is never half-done
    and half-chained."""
    assert pb.may_start_batch(spent=pb.MONTHLY_USD * pb.BATCH_STOP_FRACTION) is False
    assert pb.may_start_batch(spent=pb.MONTHLY_USD * pb.BATCH_STOP_FRACTION - 0.01) is True


def test_at_the_cap_nothing_may_spend():
    assert pb.may_spend(spent=pb.MONTHLY_USD) is False
    assert pb.may_spend(spent=pb.MONTHLY_USD - 0.01) is True


def test_a_batch_already_running_is_not_killed_mid_flight():
    """may_spend stays true above the batch line: the stop is a gate on
    STARTING work, not a guillotine on work in progress."""
    assert pb.may_start_batch(spent=pb.MONTHLY_USD * pb.BATCH_STOP_FRACTION + 1.0) is False
    assert pb.may_spend(spent=17.0) is True


def test_state_names_which_limit_was_hit():
    assert pb.state(spent=5.0) == "OK"
    assert pb.state(spent=pb.MONTHLY_USD * pb.BATCH_STOP_FRACTION) == "BATCH_STOP"
    assert pb.state(spent=pb.MONTHLY_USD) == "CAP"


# ---------------- 5c / D36: per-agent attribution on one ledger ----------------

def _this_month_ts() -> str:
    """A timestamp inside the CURRENT month. BudgetMeter.state() judges the
    current calendar month, so a row pinned to the month this file was
    written in goes invisible the moment the month turns -- which is exactly
    what happened on 2026-09-01."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(day=2, hour=0, minute=0, second=0,
                                              microsecond=0).isoformat()


def _row(meter, purpose, usd, agent=None, ts="2026-08-01T00:00:00Z"):
    """Append a raw ledger row, bypassing the API-usage shape. The default
    month is FIXED because the attribution tests query "2026-08" explicitly;
    a test that judges state() -- which reads the CURRENT month -- must pass
    ts=_this_month_ts() or it goes silent when the month turns."""
    import json
    r = {"ts_utc": ts, "model": "m", "purpose": purpose, "usd": usd,
         "input_tokens": 0, "output_tokens": 0,
         "cache_read_tokens": 0, "cache_write_tokens": 0}
    if agent is not None:
        r["agent"] = agent
    with meter.ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")


def test_month_spend_can_be_scoped_to_one_agent(tmp_path):
    """D33 gave the Reader 35 and the pipeline 20, on ONE key and ONE ledger,
    so the split could not be expressed, enforced or even measured. Attribution
    is what makes those two numbers real."""
    from .budget import BudgetMeter
    m = BudgetMeter(tmp_path / "l.jsonl")
    _row(m, "screen", 1.0, agent="reader")
    _row(m, "triage", 2.0, agent="pipeline")
    m2 = BudgetMeter(tmp_path / "l.jsonl")

    assert m2.month_spend("2026-08") == pytest.approx(3.0)
    assert m2.month_spend("2026-08", agent="reader") == pytest.approx(1.0)
    assert m2.month_spend("2026-08", agent="pipeline") == pytest.approx(2.0)


def test_historical_rows_are_attributed_from_purpose(tmp_path):
    """Coen, 2026-08-18: DERIVE from purpose rather than splitting the ledger.
    Every existing row predates the agent field, and the mapping is read out of
    data already present rather than invented."""
    from .budget import BudgetMeter
    m = BudgetMeter(tmp_path / "l.jsonl")
    for purpose, usd in (("screen", 8.96), ("extract", 11.79), ("scout", 2.34),
                         ("triage", 1.50)):
        _row(m, purpose, usd)
    m2 = BudgetMeter(tmp_path / "l.jsonl")

    assert m2.month_spend("2026-08", agent="reader") == pytest.approx(23.09)
    assert m2.month_spend("2026-08", agent="pipeline") == pytest.approx(1.50)


def test_unmappable_historical_spend_is_visible_not_hidden(tmp_path):
    """The investigation rows (reextract_test, hg_diag) are neither agent's
    work. They must not be silently dropped from the record, nor charged to an
    agent that did not spend them: they surface as `unattributed`."""
    from .budget import BudgetMeter
    m = BudgetMeter(tmp_path / "l.jsonl")
    _row(m, "screen", 1.0)
    _row(m, "hg_diag", 0.19)
    _row(m, "reextract_test", 1.25)
    m2 = BudgetMeter(tmp_path / "l.jsonl")

    assert m2.month_spend("2026-08", agent="reader") == pytest.approx(1.0)
    assert m2.month_spend("2026-08", agent="unattributed") == pytest.approx(1.44)
    # the total still reconciles: nothing invented, nothing lost
    assert m2.month_spend("2026-08") == pytest.approx(2.44)


def test_a_new_call_must_name_its_agent(tmp_path):
    """Only HISTORY may be unattributed. A new row that omitted the agent would
    escape every cap, so record_call refuses rather than defaulting."""
    from .budget import BudgetMeter

    class _U:
        input_tokens = output_tokens = 0
        cache_read_input_tokens = cache_creation_input_tokens = 0

    m = BudgetMeter(tmp_path / "l.jsonl")
    with pytest.raises(TypeError):
        m.record_call("claude-sonnet-5", _U(), "screen")


def test_state_is_judged_against_the_meters_own_agent(tmp_path):
    """A Reader meter must not go to CAP because the pipeline spent. This is
    the whole point of the split, and the failure it prevents is a scanner
    stopping on someone else's bill."""
    from .budget import BudgetMeter
    m = BudgetMeter(tmp_path / "l.jsonl")
    # state() judges the CURRENT month: stamp this month, or the rows are
    # invisible from the next month on (this test broke on 2026-09-01 exactly so).
    _row(m, "triage", 30.0, agent="pipeline", ts=_this_month_ts())
    _row(m, "screen", 1.0, agent="reader", ts=_this_month_ts())

    reader = BudgetMeter(tmp_path / "l.jsonl", monthly_cap_usd=35.0,
                         agent="reader")
    assert reader.state() == "OK"
    unscoped = BudgetMeter(tmp_path / "l.jsonl", monthly_cap_usd=35.0)
    assert unscoped.state() == "WARN"


def test_running_agents_scope_their_own_meters():
    """5c is only real if the RUNNING agents use it. A meter built unscoped
    totals every row, so the Reader would still stop on the pipeline's bill --
    the failure D33's split exists to prevent, and the reason this assertion
    reads the source rather than trusting the constructor's default.
    """
    import inspect
    from . import scanner, scout, triage_batch
    for mod, expected in ((scanner, '"reader"'), (scout, '"reader"'),
                          (triage_batch, '"pipeline"')):
        src = inspect.getsource(mod).replace("'", '"')
        for line in src.splitlines():
            if "BudgetMeter(" in line and "import" not in line:
                break
        else:                                   # pragma: no cover
            raise AssertionError(f"{mod.__name__}: no BudgetMeter construction")
        window = src[src.index("BudgetMeter("):]
        assert f"agent={expected}" in window[:220], \
            f"{mod.__name__} builds an unscoped meter; it must pass agent={expected}"
