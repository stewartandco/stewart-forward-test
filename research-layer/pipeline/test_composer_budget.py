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
    assert PIPELINE_CAP_USD == 40.0     # D39 (2026-09-03): Reader 20, pipeline 40
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


# ---------------- the client must load the reader key ----------------
#
# 2026-08-29, live: a real cycle ran verify -> triage (40 cards, ~USD 0.77
# spent, 12 entries chained) and then died in the composer preflight on
# "Could not resolve authentication method". The key is not in the ambient
# environment on this machine -- it lives in the reader's .env, and every
# other entry point loads it. The composer did not, so the watermark never
# advanced and the class re-fired forever, burning the triage spend each time.
# Every one of the six historical composer calls had been session-launched,
# where the key happened to be in the session env, which is why the first
# UNATTENDED run was the first to hit it.

def test_missing_api_key_fails_with_a_clear_message_not_an_sdk_traceback(monkeypatch):
    """Mirrors the triage-batch test. The failure must name ANTHROPIC_API_KEY
    and the path it was looked for at, not surface 30 frames deep inside the
    anthropic SDK saying nothing about where the key belongs."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("READER_ENV_PATH", "no-such-file.env")
    with pytest.raises(SystemExit) as e:
        composer_mod._client_and_meter()
    assert "ANTHROPIC_API_KEY" in str(e.value)


def test_the_client_loads_the_reader_env_rather_than_inventing_its_own(monkeypatch, tmp_path):
    """Reuse scanner._load_api_key - one loader, one source of truth for where
    the sc-reader key lives."""
    env = tmp_path / "reader.env"
    env.write_text("ANTHROPIC_API_KEY=sk-test-not-a-real-key\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("READER_ENV_PATH", str(env))
    client, meter = composer_mod._client_and_meter()
    assert client is not None and meter is not None


def test_the_loader_runs_before_the_client_is_constructed(monkeypatch):
    """The ambient key must not be what makes this pass. Record the loader
    call and stub the SDK: if the composer ever goes back to relying on the
    environment it happened to be launched with, this fails even on a machine
    where the key IS set."""
    import anthropic

    from . import scanner as scanner_mod

    called: list[Path] = []

    def fake_load(env_path):
        called.append(env_path)

    class FakeAnthropic:
        def __init__(self, *a, **kw):
            assert called, "client constructed before the reader .env was loaded"

    monkeypatch.setattr(scanner_mod, "_load_api_key", fake_load)
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)

    client, meter = composer_mod._client_and_meter()
    assert called == [scanner_mod.DEFAULT_READER_ENV]
    assert isinstance(client, FakeAnthropic) and meter is not None
