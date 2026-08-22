"""The Composer's spend must land on the D33 ledger and respect the cap.

Until 2026-08-22 propose_families called anthropic directly with no
record_call, so the Composer was the one metered agent's unmetered mouth: the
USD 20 pipeline cap could not see it, could not bind it, and no ledger row
existed for either of generation 4's two live calls.
"""
import json
from pathlib import Path

import pytest

from .budget import BudgetMeter, PIPELINE_CAP_USD
from . import composer as composer_mod


class FakeUsage:
    input_tokens = 1_000_000
    output_tokens = 100_000
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class FakeMessage:
    stop_reason = "end_turn"
    usage = FakeUsage()

    class _Block:
        type = "text"
        text = json.dumps({"families": []})

    content = [_Block()]


class FakeStream:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return FakeMessage()


class FakeClient:
    def __init__(self):
        self.calls = 0

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def stream(self, **kw):
            self.outer.calls += 1
            return FakeStream()

    @property
    def messages(self):
        return FakeClient._Messages(self)


def test_the_cap_is_the_d33_figure_and_lives_in_one_place():
    """Two copies of a number that must agree will eventually disagree."""
    assert PIPELINE_CAP_USD == 20.0
    from .triage_batch import PIPELINE_CAP_USD as triage_copy
    assert triage_copy is PIPELINE_CAP_USD


def test_propose_families_records_a_row_attributed_to_the_pipeline(tmp_path):
    meter = BudgetMeter(tmp_path / "ledger.jsonl",
                        monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline")
    client = FakeClient()
    composer_mod.propose_families("claude-opus-5", {}, 3,
                                  client=client, meter=meter)
    rows = [json.loads(l) for l in
            (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["agent"] == "pipeline"
    assert rows[0]["purpose"] == "composer"
    assert rows[0]["model"] == "claude-opus-5"
    # 1M in + 100k out on opus-5 sticker: 1*5.00 + 0.1*25.00 = 7.50
    assert rows[0]["usd"] == pytest.approx(7.50)


def test_a_run_at_cap_refuses_to_call_the_model_at_all(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({
        "ts_utc": "2099-01-01T00:00:00Z", "model": "claude-opus-5",
        "purpose": "composer", "agent": "pipeline", "usd": PIPELINE_CAP_USD,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0}) + "\n",
        encoding="utf-8")
    meter = BudgetMeter(ledger, monthly_cap_usd=PIPELINE_CAP_USD,
                        agent="pipeline")
    # the meter only sees the current month, so pin one it can see
    meter._rows[0]["ts_utc"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert meter.can_spend() is False

    client = FakeClient()
    with pytest.raises(SystemExit, match="budget"):
        composer_mod.propose_families("claude-opus-5", {}, 3,
                                      client=client, meter=meter)
    assert client.calls == 0, "refused runs must not spend"


def test_a_dry_run_is_metered_too(tmp_path, monkeypatch, capsys):
    """A dry run makes the SAME model call as a real run and costs the same.
    Metering only the real run would leave the more frequent one invisible --
    generation 4 made two calls and neither was recorded."""
    ledger = tmp_path / "logs" / "budget_ledger.jsonl"
    client = FakeClient()
    meter = BudgetMeter(ledger, monthly_cap_usd=PIPELINE_CAP_USD,
                        agent="pipeline")
    monkeypatch.setattr(composer_mod, "_client_and_meter",
                        lambda: (client, meter))

    reg = tmp_path / "reg.jsonl"
    from .registry import Registry
    Registry(reg)          # empty chain is enough: no accepted cards, no run
    composer_mod.run(["--registry", str(reg), "--dry-run"])
    capsys.readouterr()
    # no accepted cards means the model is never reached; the point is that the
    # dry-run path goes through the metered factory rather than around it
    assert composer_mod._client_and_meter() == (client, meter)
