"""2b-3 C3: registration refuses a spec the engine cannot run honestly.

Intraday is supported for session '24x7' only (bars.bars_per_day). A tradfi
or session-less intraday spec used to chain fine and fail later, inside the
gauntlet/live path; the append-only chain cannot be cleaned afterwards, so
the refusal sits BEFORE any write. The rule itself lives in bars.bars_per_day
and is called, never copied.
"""
from __future__ import annotations

import pytest

from . import composer, loop_state
from .common import content_id
from .registry import Registry
from .test_pipeline import make_card, make_strategy, register_example_blocks
from .testing_crypto import active_crypto


def _setup(tmp_path):
    reg = Registry(tmp_path / "log.jsonl")
    register_example_blocks(reg)
    card = make_card()
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    return reg, card["card_id"]


def _spec(cid, session, tf, asset_class="crypto", omit_session=False):
    spec = make_strategy([cid])
    u = {"assets": ["BTCUSDT"], "asset_class": asset_class, "timeframe": tf}
    if not omit_session:
        u["session"] = session
    spec["universe"] = u
    spec["strategy_id"] = None
    spec["strategy_id"] = content_id(spec, "strategy_id")
    return spec


def test_a_tradfi_1h_spec_is_refused_and_the_chain_is_untouched(tmp_path):
    reg, cid = _setup(tmp_path)
    before = (tmp_path / "log.jsonl").read_bytes()
    spec = _spec(cid, "us_equity_5d", "1h", asset_class="equity_etf")
    with pytest.raises(ValueError, match=r"strategy .*2b-3 C3: intraday needs a 24x7 session"):
        reg.register_strategy(spec)
    assert (tmp_path / "log.jsonl").read_bytes() == before


def test_an_unknown_timeframe_is_refused(tmp_path):
    reg, cid = _setup(tmp_path)
    before = (tmp_path / "log.jsonl").read_bytes()
    with pytest.raises(ValueError, match="unknown timeframe"):
        reg.register_strategy(_spec(cid, "24x7", "2h"))
    assert (tmp_path / "log.jsonl").read_bytes() == before


def test_a_missing_session_with_intraday_is_refused(tmp_path):
    reg, cid = _setup(tmp_path)
    before = (tmp_path / "log.jsonl").read_bytes()
    with pytest.raises(ValueError, match="2b-3 C3"):
        reg.register_strategy(_spec(cid, None, "1h", omit_session=True))
    with pytest.raises(ValueError, match="2b-3 C3"):
        reg.register_strategy(_spec(cid, None, "1h"))
    assert (tmp_path / "log.jsonl").read_bytes() == before


@pytest.mark.parametrize("session,tf,asset_class", [
    ("us_equity_5d", "1d", "equity_etf"),
    ("fx_5d", "1d", "fx"),
    ("24x7", "1d", "crypto"),
    ("24x7", "1h", "crypto"),
    (None, "1d", "crypto"),
])
def test_every_1d_spec_and_24x7_intraday_still_registers(tmp_path, session, tf, asset_class):
    reg, cid = _setup(tmp_path)
    entry = reg.register_strategy(_spec(cid, session, tf, asset_class=asset_class))
    assert entry["entry_type"] == "strategy_registered"


def test_a_legacy_spec_without_a_session_key_at_1d_still_registers(tmp_path):
    reg, cid = _setup(tmp_path)
    reg.register_strategy(_spec(cid, None, "1d", omit_session=True))


def test_the_composer_drain_parks_the_refused_spec_dead(tmp_path, capsys):
    """The drain catches ValueError per spec and parks it with the registry's
    own reason (test_sp5_p2t4 F3); the new refusal rides that path."""
    from .test_composer import seeded_registry
    from .test_sp5_p2t4 import _over_cap_family
    with active_crypto(("BTCUSDT",)):
        reg_path, cid = seeded_registry(tmp_path)
        state_path = tmp_path / "loop_state.json"
        composer.run(["--registry", str(reg_path), "--run-id", "r1",
                      "--sibling-cap", "25", "--loop-state", str(state_path)],
                     propose_fn=lambda cards: [_over_cap_family(cid)])
        state = loop_state.load(state_path)
        queued = state["classes"]["crypto"]["sibling_queue"]
        assert queued, "nothing was queued to drain"
        for spec in queued:
            spec["universe"]["timeframe"] = "1h"
            spec["universe"]["session"] = "us_equity_5d"
        loop_state.save(state_path, state)
        n_before = sum(1 for _ in Registry(reg_path).entries())

        rc = composer.run(["--registry", str(reg_path), "--run-id", "r2",
                           "--sibling-cap", "25", "--loop-state", str(state_path)],
                          propose_fn=lambda cards: [])
        assert rc == 0
        state = loop_state.load(state_path)
        dead = state["classes"]["crypto"]["sibling_queue_dead"]
        assert len(dead) == 25
        assert all("2b-3 C3: intraday needs a 24x7 session" in d["reason"]
                   for d in dead), dead
        assert sum(1 for _ in Registry(reg_path).entries()) == n_before
        assert "DEAD queued" in capsys.readouterr().out
