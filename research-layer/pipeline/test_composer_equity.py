"""Offline tests for the equity_etf composer path, SP4 Track 2a (addendum
docs/2026-08-24-sp4-track2a-addendum.md, on top of the Track 1 plan's
conventions). No API calls: every test below injects propose_fn or a stub
client, exactly like pipeline/test_composer_fx.py.

Run: python -m pytest pipeline/test_composer_equity.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from . import composer
from . import cells as cells_mod
from .registry import Registry
from .test_pipeline import make_card

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent


def _register_accepted(reg: Registry, **card_overrides) -> str:
    card = make_card(**card_overrides)
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    return card["card_id"]


def equity_family(**overrides):
    """A minimal equity_etf family: ma_cross_dense (sweepable) entry,
    pct_stop, r_multiple target, fixed_fraction risk -- the same shape as
    test_composer_fx.py's fx_family(), reused deliberately so the two
    fixtures read as siblings rather than independently invented shapes."""
    fam = {
        "family": "equity_trend_family",
        "rationale": "Equity-index ETF trend continuation on daily bars.",
        "card_ids": ["aaaaaaaaaaaaaaaa"],
        "assets": ["SPY"],
        "blocks": [
            {"role": "entry", "type": "ma_cross_dense",
             "params": {"fast": 13, "slow": 50, "direction": "long"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "sweep": [
            {"block": 0, "param": "fast", "values": [8, 13, 20]},
        ],
    }
    fam.update(overrides)
    return fam


ACCEPTED_EQUITY = {"aaaaaaaaaaaaaaaa"}


def _drift_record(registry_path: Path) -> dict:
    log_path = registry_path.resolve().parent / "logs" / "batch_drift.jsonl"
    return json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])


def _registered_specs(registry_path: Path) -> list[dict]:
    return [e["payload"] for e in Registry(registry_path).entries()
            if e["entry_type"] == "strategy_registered"]


# ---------------- equity_etf universe, costs, cell expansion ----------------

def test_equity_universe_and_costs(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["equities"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "eqrun", "--asset-class", "equity_etf"],
        propose_fn=lambda cards: [equity_family(card_ids=[cid])])
    assert rc == 0

    specs = _registered_specs(reg_path)
    assert len(specs) == 3 * 16   # 3 swept `fast` values x 16 equity_etf cells

    fps_by_fast: dict[float, set[str]] = {}
    for spec in specs:
        u = spec["universe"]
        assert len(u["assets"]) == 1
        asset = u["assets"][0]
        assert asset in cells_mod.EQUITY_ETF_ASSETS
        assert u == {"assets": [asset], "asset_class": "equity_etf",
                    "timeframe": "1d", "session": "us_equity_5d"}
        assert spec["cost_model"] == cells_mod.CLASSES["equity_etf"]["cost_model"]
        assert spec["provenance"]["sibling_group_id"].endswith(f":{asset}_1d")
        fast = spec["blocks"][0]["params"]["fast"]
        fps_by_fast.setdefault(fast, set()).add(composer.composition_fingerprint(spec))

    # composition_fingerprint differs across the 16 cells of each sweep combo
    assert set(fps_by_fast) == {8, 13, 20}
    for fast, fps in fps_by_fast.items():
        assert len(fps) == 16

    # ...but is stable for the same family + run_id + timestamp + cell.
    a = composer.expand_family_for_class(
        equity_family(card_ids=[cid]), "eqrun", composer.DEFAULT_MODEL,
        "2026-08-25T00:00:00Z", "equity_etf")
    b = composer.expand_family_for_class(
        equity_family(card_ids=[cid]), "eqrun", composer.DEFAULT_MODEL,
        "2026-08-25T00:00:00Z", "equity_etf")
    assert [s["strategy_id"] for s in a] == [s["strategy_id"] for s in b]
    assert [composer.composition_fingerprint(s) for s in a] == \
          [composer.composition_fingerprint(s) for s in b]


# ---------------- equity_etf card routing ----------------

def test_equity_card_routing(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    crypto_cid = _register_accepted(reg, asset_classes=["crypto"])
    equity_cid = _register_accepted(reg, asset_classes=["equities"])
    cross_cid = _register_accepted(reg, asset_classes=["cross"])
    # reader.py:162 defaults an untagged card's asset_classes to ["cross"];
    # an empty list here reproduces that same default at build_card time.
    untagged_cid = _register_accepted(reg, asset_classes=[])

    captured = {}

    def spy(cards):
        captured["cards"] = set(cards)
        return [equity_family(card_ids=[equity_cid])]

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "eqrun", "--asset-class", "equity_etf"],
        propose_fn=spy)
    assert rc == 0
    assert captured["cards"] == {equity_cid, cross_cid, untagged_cid}
    assert crypto_cid not in captured["cards"]

    drift = _drift_record(reg_path)
    assert drift["routing"] == {"asset_class": "equity_etf", "eligible_tags": ["cross", "equities"]}
    assert set(drift["routed_card_ids"]) == {equity_cid, cross_cid, untagged_cid}
    # No futures-tagged card was seeded in this test, so the proxy lane ran
    # and found nothing -- present as [], not absent, per drift_record's
    # docstring (an equity_etf run always sets the key).
    assert drift["proxy_routed_card_ids"] == []


# ---------------- futures->equity_etf proxy lane (spec s10.8) ----------------
#
# The live registry has ZERO accepted futures cards as of the 2026-08-24
# measurement recorded on composer.INDEX_FUTURES_PROXY_TOPICS (342/342
# futures-tagged cards pending), so the positive-routing case here is
# fixture-injected rather than drawn from real registry state, per the
# addendum's instruction for exactly this situation.

def test_index_futures_proxy_topics_measured_and_bounded():
    """Sanity on the declared constant itself: 3-6 topics (addendum's
    instruction), all strings, frozen so it cannot be mutated at runtime."""
    topics = composer.INDEX_FUTURES_PROXY_TOPICS
    assert isinstance(topics, frozenset)
    assert 3 <= len(topics) <= 6
    assert all(isinstance(t, str) and t for t in topics)
    # The measured set, pinned so a future edit is a deliberate re-measurement
    # rather than an accidental drift.
    assert topics == frozenset({
        "S&P 500", "ES futures", "VIX futures",
        "VIX futures term structure", "TVIX", "contango",
    })


def test_futures_card_with_matching_topic_proxy_routes(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    # A futures card is never tagged "equities" or "cross", so it is invisible
    # to the native ROUTING["equity_etf"] filter -- only the topic-matched
    # proxy lane can surface it.
    proxy_cid = _register_accepted(
        reg, asset_classes=["futures"],
        claim="VIX futures term structure sits in contango most of the time.",
        quote="the VIX futures term structure sits in contango most of the time",
        topics=["VIX futures term structure", "contango"])
    off_topic_cid = _register_accepted(
        reg, asset_classes=["futures"],
        claim="Bond futures roll yield differs by contract month.",
        quote="roll yield on bond futures differs by contract month",
        topics=["roll yield", "bond futures"])
    equity_cid = _register_accepted(reg, asset_classes=["equities"])

    captured = {}

    def spy(cards):
        captured["cards"] = set(cards)
        return [equity_family(card_ids=[equity_cid, proxy_cid])]

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "eqrun", "--asset-class", "equity_etf"],
        propose_fn=spy)
    assert rc == 0

    assert proxy_cid in captured["cards"]
    assert off_topic_cid not in captured["cards"]
    assert equity_cid in captured["cards"]

    drift = _drift_record(reg_path)
    assert drift["proxy_routed_card_ids"] == [proxy_cid]
    assert proxy_cid in drift["routed_card_ids"]
    assert off_topic_cid not in drift["routed_card_ids"]


# ---------------- equity_etf block exclusions: NONE (real OHLC bars) --------

def test_equity_range_blocks_are_not_excluded():
    # Unlike fx (single-fix bars), equity_etf has real OHLC bars: every block
    # type is eligible, including the ones RANGE_REQUIRING excludes for fx.
    assert cells_mod.CLASSES["equity_etf"]["excluded_block_types"] == frozenset()

    for excluded_type, role, params in (
        ("atr_stop", "stop", {"atr_len": 14, "mult": 2.0}),
        ("channel_breakout", "entry", {"lookback": 55, "direction": "long"}),
    ):
        fam = equity_family(sweep=[])   # the default sweep axis targets the
                                        # entry block's `fast` param, which
                                        # channel_breakout does not have
        idx = 1 if role == "stop" else 0
        fam["blocks"][idx] = {"role": role, "type": excluded_type, "params": params}
        errs = composer.validate_family(
            fam, ACCEPTED_EQUITY, 25,
            excluded_types=cells_mod.CLASSES["equity_etf"]["excluded_block_types"],
            asset_class="equity_etf")
        assert errs == [], errs


def test_run_end_to_end_accepts_range_requiring_blocks(tmp_path):
    """The exclusion-coupling switch (T4-rider-3) end to end: a family using
    atr_stop, which composer.run() would reject for --asset-class fx, is
    accepted and registers cleanly for --asset-class equity_etf, because
    run() now reads excluded_types from cells.CLASSES[cls]["excluded_block_types"]
    instead of a hardcoded "any non-crypto class" rule."""
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["equities"])
    fam = equity_family(card_ids=[cid], sweep=[])
    fam["blocks"][1] = {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}}

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "eqrun", "--asset-class", "equity_etf"],
        propose_fn=lambda cards: [fam])
    assert rc == 0
    specs = _registered_specs(reg_path)
    assert len(specs) == 16   # no sweep: one spec per equity_etf cell
    assert all(b["type"] == "atr_stop" for s in specs for b in s["blocks"] if b["role"] == "stop")


# ---------------- equity_etf specs are schema-valid --------------------------
#
# This is the regression that would have caught the schema gap this task
# found and fixed: universe.asset_class="equity_etf" was not a member of
# schemas/strategy_spec.schema.json's enum (it only had the 8 card-taxonomy
# values, "equities" among them, never "equity_etf") until this task added
# it -- additively, alongside the existing 8, never removing or renaming any.

def test_equity_specs_validate_against_schema(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["equities"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "eqrun", "--asset-class", "equity_etf"],
        propose_fn=lambda cards: [equity_family(card_ids=[cid])])
    assert rc == 0

    schema = json.loads(
        (LAYER / "schemas" / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        validator.validate(spec)


def test_strategy_schema_enum_is_additive_not_a_replacement():
    """The 8 pre-existing card-taxonomy values are all still present; the
    schema change for this task only ADDED "equity_etf", never removed or
    renamed anything a crypto/fx spec already relies on."""
    schema = json.loads(
        (LAYER / "schemas" / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["universe"]["properties"]["asset_class"]["enum"]
    assert set(enum) == {"futures", "equities", "crypto", "fx", "options",
                         "rates", "commodities", "cross", "equity_etf"}


# ---------------- Track 2a: per-class proposer brief (equity_etf) -----------

def test_equity_prompt_names_dividends_and_survivorship():
    """spec "Class declaration" / addendum build delta #2: the equity_etf
    prompt must name dividends-excluded price returns AND survivorship,
    because a family reasoning about either would be reasoning about data
    this universe does not carry."""
    system = composer.system_prompt_for("equity_etf")
    assert system is not composer.SYSTEM_PROMPT
    assert system == composer._equity_etf_system_prompt()

    assert "dividend" in system.lower()
    assert "survivorship" in system.lower()

    # The real equity_etf universe, pulled from cells.CLASSES, is named.
    for asset in cells_mod.CLASSES["equity_etf"]["assets"]:
        assert asset in system
    assert "16" in system

    # The crypto mission statement must not leak (a contrastive mention of
    # fx's single-fix bars is fine -- it explains why range blocks ARE
    # eligible here, unlike fx).
    assert "crypto daily bars" not in system
    assert "BTCUSD" not in system


def test_equity_schema_assets_enum_is_the_equity_universe():
    schema = composer.proposal_schema_for("equity_etf")
    assert schema is not composer.PROPOSAL_SCHEMA
    enum = schema["properties"]["families"]["items"]["properties"]["assets"]["items"]["enum"]
    assert enum == list(cells_mod.CLASSES["equity_etf"]["assets"])


def test_unknown_asset_class_still_raises():
    import pytest
    with pytest.raises(ValueError, match="no proposer brief"):
        composer.system_prompt_for("bond_etf")
