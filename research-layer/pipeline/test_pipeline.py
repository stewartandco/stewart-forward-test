"""Offline tests for the research-layer pipeline (no API calls).

Run: python -m pytest research-layer/pipeline/test_pipeline.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .common import quote_in_source, content_id
from .registry import Registry
from .reader import build_card

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent

SOURCE_META = {
    "type": "paper", "title": "Test Paper", "authors": ["A. Author"],
    "year": 2021, "url": "https://example.org/x", "doi": None, "isbn": None,
    "credibility_tier": "practitioner",
}

RAW_CLAIM = {
    "claim": "Narrow opening ranges on index futures show positive follow-through intraday.",
    "quote": "breakouts from compressed opening ranges exhibit significant follow-through",
    "locator": "sec 4.2",
    "asset_classes": ["futures"],
    "topics": ["orb", "momentum"],
    "horizon": "intraday",
    "testability_score": 0.9,
    "data_required": ["MNQ 15m bars"],
    "notes": None,
}


def make_card(**overrides):
    raw = {**RAW_CLAIM, **overrides}
    return build_card(raw, SOURCE_META, "claude-opus-5", "test-run")


def make_strategy(card_ids):
    spec = {
        "strategy_id": None, "version": 1, "created_utc": "2026-08-05T00:00:00Z",
        "name": "test strat", "family": "orb_breakout",
        "universe": {"assets": ["MNQ"], "asset_class": "futures",
                     "timeframe": "15m", "session": "RTH"},
        "blocks": [
            {"role": "entry", "type": "orb_breakout", "params": {"window_min": 15}},
            {"role": "stop", "type": "structure", "params": {}},
            {"role": "risk", "type": "fixed_contracts", "params": {"n": 1}},
        ],
        "provenance": {"card_ids": card_ids, "parent_strategy_id": None,
                       "sibling_group_id": "g1", "generation": 0},
        "generator": {"agent": "composer", "model": "claude-opus-5",
                      "pipeline_version": "g1.0.0", "run_id": "test-run"},
        "cost_model": {"commission_per_side": 0.62, "slippage_ticks": 1},
    }
    spec["strategy_id"] = content_id(spec, "strategy_id")
    return spec


# ---------------- honesty guard ----------------

def test_quote_found_despite_whitespace():
    src = "Intro.\n\nbreakouts from   compressed\nopening ranges exhibit\nsignificant follow-through. More."
    assert quote_in_source(RAW_CLAIM["quote"], src)


def test_paraphrased_quote_rejected():
    src = "Breakouts from tight opening ranges show meaningful continuation."
    assert not quote_in_source(RAW_CLAIM["quote"], src)


# ---------------- card building ----------------

def test_card_is_content_addressed_and_schema_valid():
    card = make_card()
    assert card["card_id"] == content_id(card, "card_id")
    import jsonschema
    schema = json.loads((LAYER / "schemas" / "research_card.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(card)


def test_card_id_changes_with_content():
    assert make_card()["card_id"] != make_card(claim="A different testable claim about gaps.")["card_id"]


# ---------------- registry chain + invariants ----------------

def test_full_flow_produces_valid_chain(tmp_path):
    log = tmp_path / "registry_log.jsonl"
    reg = Registry(log)

    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")

    spec = make_strategy([card["card_id"]])
    reg.register_strategy(spec)
    reg.record_state_change(spec["strategy_id"], "screened", "compute scheduled")
    reg.record_verdict(spec["strategy_id"], "screened", "pass",
                       {"trades": 200, "net_pnl": 1000.0, "win_rate": 0.5, "max_dd": -300.0},
                       "0" * 64)
    reg.record_state_change(spec["strategy_id"], "graveyard", "test burial")

    out = subprocess.run(
        [sys.executable, str(LAYER / "verify_registry.py"), str(log)],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "REGISTRY VALID" in out.stdout


def test_strategy_requires_accepted_cards(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    card = make_card()
    reg.register_card(card)  # pending, never accepted
    with pytest.raises(ValueError, match="not registered\\+accepted"):
        reg.register_strategy(make_strategy([card["card_id"]]))


def test_illegal_transition_rejected(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    spec = make_strategy([card["card_id"]])
    reg.register_strategy(spec)
    with pytest.raises(ValueError, match="illegal transition"):
        reg.record_state_change(spec["strategy_id"], "live")  # proposed -> live skips


def test_terminal_state_is_final(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    spec = make_strategy([card["card_id"]])
    reg.register_strategy(spec)
    reg.record_state_change(spec["strategy_id"], "graveyard", "dead")
    with pytest.raises(ValueError, match="illegal transition"):
        reg.record_state_change(spec["strategy_id"], "screened")


def test_rejected_review_needs_reason(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    card = make_card()
    reg.register_card(card)
    with pytest.raises(ValueError, match="reject_reason"):
        reg.review_card(card["card_id"], "rejected", "tester")


def test_tampering_breaks_chain(tmp_path):
    log = tmp_path / "log.jsonl"
    reg = Registry(log)
    reg.register_card(make_card())
    reg.register_card(make_card(claim="Second distinct testable claim about volatility."))
    lines = log.read_text().splitlines()
    lines[0] = lines[0].replace("Narrow opening ranges", "Wide opening ranges")
    log.write_text("\n".join(lines) + "\n")
    out = subprocess.run(
        [sys.executable, str(LAYER / "verify_registry.py"), str(log)],
        capture_output=True, text=True)
    assert out.returncode != 0
    assert "BROKEN CHAIN" in out.stdout
