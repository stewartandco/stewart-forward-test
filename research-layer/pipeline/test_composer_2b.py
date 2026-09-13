"""Offline tests for the bond_etf + metal_etf composer paths, SP4 Track 2b
(addendum docs/2026-08-27-sp4-track2b-addendum.md, on top of Track 2a's
conventions). No API calls: every test below injects propose_fn or a stub
client, exactly like pipeline/test_composer_fx.py and
pipeline/test_composer_equity.py.

Run: python -m pytest pipeline/test_composer_2b.py -q
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


def bond_family(**overrides):
    """A minimal bond_etf family: ma_cross_dense (sweepable) entry, ma_stop
    (pct_stop until D15 exit rules v7 retired it for version-2 specs),
    r_multiple target, fixed_fraction risk -- the same shape as
    test_composer_equity.py's equity_family(), reused deliberately."""
    fam = {
        "family": "bond_trend_family",
        "rationale": "Bond ETF trend continuation on daily bars.",
        "card_ids": ["aaaaaaaaaaaaaaaa"],
        "assets": ["SHY"],
        "blocks": [
            {"role": "entry", "type": "ma_cross_dense",
             "params": {"fast": 13, "slow": 50, "direction": "long"}},
            {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "sweep": [
            {"block": 0, "param": "fast", "values": [8, 13, 20]},
        ],
    }
    fam.update(overrides)
    return fam


def metal_family(**overrides):
    """A minimal metal_etf family, same shape again."""
    fam = {
        "family": "metal_trend_family",
        "rationale": "Gold/silver ETF trend continuation on daily bars.",
        "card_ids": ["aaaaaaaaaaaaaaaa"],
        "assets": ["GLD"],
        "blocks": [
            {"role": "entry", "type": "ma_cross_dense",
             "params": {"fast": 13, "slow": 50, "direction": "long"}},
            {"role": "stop", "type": "ma_stop", "params": {"ma_len": 50}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        "sweep": [
            {"block": 0, "param": "fast", "values": [8, 13, 20]},
        ],
    }
    fam.update(overrides)
    return fam


ACCEPTED_BOND = {"aaaaaaaaaaaaaaaa"}
ACCEPTED_METAL = {"aaaaaaaaaaaaaaaa"}


def _drift_record(registry_path: Path) -> dict:
    log_path = registry_path.resolve().parent / "logs" / "batch_drift.jsonl"
    return json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])


def _registered_specs(registry_path: Path) -> list[dict]:
    return [e["payload"] for e in Registry(registry_path).entries()
            if e["entry_type"] == "strategy_registered"]


# ==================== bond_etf ====================

# ---------------- bond_etf universe, costs, cell expansion ----------------

def test_bond_universe_and_costs(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["rates"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "bondrun", "--asset-class", "bond_etf"],
        propose_fn=lambda cards: [bond_family(card_ids=[cid])])
    assert rc == 0

    specs = _registered_specs(reg_path)
    assert len(specs) == 3 * 8   # 3 swept `fast` values x 8 bond_etf cells

    fps_by_fast: dict[float, set[str]] = {}
    for spec in specs:
        u = spec["universe"]
        assert len(u["assets"]) == 1
        asset = u["assets"][0]
        assert asset in cells_mod.BOND_ETF_ASSETS
        assert u == {"assets": [asset], "asset_class": "bond_etf",
                    "timeframe": "1d", "session": "us_equity_5d"}
        assert spec["cost_model"] == cells_mod.CLASSES["bond_etf"]["cost_model"]
        assert spec["provenance"]["sibling_group_id"].endswith(f":{asset}_1d")
        fast = spec["blocks"][0]["params"]["fast"]
        fps_by_fast.setdefault(fast, set()).add(composer.composition_fingerprint(spec))

    assert set(fps_by_fast) == {8, 13, 20}
    for fast, fps in fps_by_fast.items():
        assert len(fps) == 8

    a = composer.expand_family_for_class(
        bond_family(card_ids=[cid]), "bondrun", composer.DEFAULT_MODEL,
        "2026-08-27T00:00:00Z", "bond_etf")
    b = composer.expand_family_for_class(
        bond_family(card_ids=[cid]), "bondrun", composer.DEFAULT_MODEL,
        "2026-08-27T00:00:00Z", "bond_etf")
    assert [s["strategy_id"] for s in a] == [s["strategy_id"] for s in b]


# ---------------- bond_etf card routing: the WHOLE rates lane is proxy ------
#
# Track 2b addendum ("Routing") deviation from the parent spec's DEFAULT
# proxy shape (a topic-matched futures subset, as built for equity_etf and
# metal_etf below): bond_etf declares the entire rates-tagged lane a proxy,
# because rates cards are largely about rate futures/derivatives, not the
# cash ETFs. A card that reaches propose_input only via "cross" (asset-class-
# agnostic by definition) stays native.

def test_bond_card_routing_rates_is_proxy_cross_is_native(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    rates_cid = _register_accepted(reg, asset_classes=["rates"])
    cross_cid = _register_accepted(reg, asset_classes=["cross"])
    crypto_cid = _register_accepted(reg, asset_classes=["crypto"])
    # reader.py:162 defaults an untagged card's asset_classes to ["cross"]
    untagged_cid = _register_accepted(reg, asset_classes=[])

    captured = {}

    def spy(cards):
        captured["cards"] = set(cards)
        return [bond_family(card_ids=[rates_cid, cross_cid])]

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "bondrun", "--asset-class", "bond_etf"],
        propose_fn=spy)
    assert rc == 0
    assert captured["cards"] == {rates_cid, cross_cid, untagged_cid}
    assert crypto_cid not in captured["cards"]

    drift = _drift_record(reg_path)
    assert drift["routing"] == {"asset_class": "bond_etf", "eligible_tags": ["cross", "rates"]}
    assert set(drift["routed_card_ids"]) == {rates_cid, cross_cid, untagged_cid}
    # The WHOLE rates tag is proxy: only rates_cid, never cross_cid/untagged_cid.
    assert drift["proxy_routed_card_ids"] == [rates_cid]

    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        assert spec["provenance"]["routed_via"] == "proxy"
        assert spec["provenance"]["proxy_card_ids"] == [rates_cid]
        assert cross_cid not in spec["provenance"]["proxy_card_ids"]


def test_bond_family_citing_only_cross_card_is_natively_routed(tmp_path):
    """A family that cites no rates-tagged card gets NEITHER proxy key --
    absence IS native routing, exactly like equity_etf's futures-topic lane."""
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cross_cid = _register_accepted(reg, asset_classes=["cross"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "bondrun", "--asset-class", "bond_etf"],
        propose_fn=lambda cards: [bond_family(card_ids=[cross_cid])])
    assert rc == 0

    drift = _drift_record(reg_path)
    assert drift["proxy_routed_card_ids"] == []

    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        assert "routed_via" not in spec["provenance"]
        assert "proxy_card_ids" not in spec["provenance"]


def test_bond_etf_proxy_tags_declared():
    assert composer.BOND_ETF_PROXY_TAGS == frozenset({"rates"})
    assert composer.ROUTING["bond_etf"] == ("rates", "cross")


# ==================== metal_etf ====================

# ---------------- metal_etf universe, costs, cell expansion ----------------

def test_metal_universe_and_costs(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["commodities"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "metalrun", "--asset-class", "metal_etf"],
        propose_fn=lambda cards: [metal_family(card_ids=[cid])])
    assert rc == 0

    specs = _registered_specs(reg_path)
    assert len(specs) == 3 * 2   # 3 swept `fast` values x 2 metal_etf cells

    fps_by_fast: dict[float, set[str]] = {}
    for spec in specs:
        u = spec["universe"]
        asset = u["assets"][0]
        assert asset in cells_mod.METAL_ETF_ASSETS
        assert u == {"assets": [asset], "asset_class": "metal_etf",
                    "timeframe": "1d", "session": "us_equity_5d"}
        assert spec["cost_model"] == cells_mod.CLASSES["metal_etf"]["cost_model"]
        assert spec["cost_model"]["short_financing_per_year"] == -0.0075
        fast = spec["blocks"][0]["params"]["fast"]
        fps_by_fast.setdefault(fast, set()).add(composer.composition_fingerprint(spec))

    assert set(fps_by_fast) == {8, 13, 20}
    for fast, fps in fps_by_fast.items():
        assert len(fps) == 2


# ---------------- metal_etf card routing: commodities+cross NATIVE ---------
#
# DEVIATION from the parent spec's routing table (declared in the addendum):
# the parent table omitted "commodities" entirely; metal_etf routes on it
# natively because commodities cards about gold/silver are the closest
# native population for a metals-only class.

def test_metal_card_routing_commodities_is_native(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    crypto_cid = _register_accepted(reg, asset_classes=["crypto"])
    commodities_cid = _register_accepted(reg, asset_classes=["commodities"])
    cross_cid = _register_accepted(reg, asset_classes=["cross"])
    untagged_cid = _register_accepted(reg, asset_classes=[])

    captured = {}

    def spy(cards):
        captured["cards"] = set(cards)
        return [metal_family(card_ids=[commodities_cid])]

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "metalrun", "--asset-class", "metal_etf"],
        propose_fn=spy)
    assert rc == 0
    assert captured["cards"] == {commodities_cid, cross_cid, untagged_cid}
    assert crypto_cid not in captured["cards"]

    drift = _drift_record(reg_path)
    assert drift["routing"] == {"asset_class": "metal_etf", "eligible_tags": ["commodities", "cross"]}
    assert set(drift["routed_card_ids"]) == {commodities_cid, cross_cid, untagged_cid}
    # No futures-tagged card was seeded: the proxy lane ran and found none --
    # present as [], not absent (an equity_etf/metal_etf run always sets it).
    assert drift["proxy_routed_card_ids"] == []

    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        assert "routed_via" not in spec["provenance"]
        assert "proxy_card_ids" not in spec["provenance"]


# ---------------- futures->metal_etf proxy lane (METALS_PROXY_TOPICS) ------
#
# MEASURED against the live registry (research-layer/registry_log.jsonl,
# read-only scan, 2026-08-27, same method as INDEX_FUTURES_PROXY_TOPICS): of
# the 342 futures-tagged cards in any review state, ZERO genuinely name a
# specific gold/silver/precious-metals futures instrument (one keyword hit,
# "the gold standard for weights", is an idiom about portfolio-weight
# estimation, not a metals claim), and ZERO topic tags across the whole
# futures corpus mention gold/silver/metal/precious/comex/xau/xag at all.
# METALS_PROXY_TOPICS is therefore declared EMPTY -- an honest record of
# what was measured. Both facts are pinned below: the real (empty) measured
# constant, and the routing MECHANISM itself proven with a monkeypatched,
# non-empty topic set (fixture-injected, per the build brief's
# "fixture-injected routing tests either way" -- the live constant being
# empty means no real card could ever demonstrate a positive match today).

def test_metals_proxy_topics_measured_empty():
    topics = composer.METALS_PROXY_TOPICS
    assert isinstance(topics, frozenset)
    assert topics == frozenset()


def test_live_registry_has_zero_accepted_metals_proxy_matches():
    """Pinned against the LIVE registry (read-only -- no writes, matching
    this package's convention of treating the live chain as read-only from
    tests). If this ever fails because the corpus grew a real metals-named
    futures card, re-measure through Registry.cards(), update
    METALS_PROXY_TOPICS's provenance comment in composer.py, and update this
    assertion together -- never one without the other."""
    live = Registry(LAYER / "registry_log.jsonl")
    accepted = live.cards(status="accepted")
    futures_accepted = {
        cid: c for cid, c in accepted.items()
        if "futures" in (c.get("tags") or {}).get("asset_classes", [])
    }
    # Not pinning an exact futures-accepted count here (unlike the build
    # report's one-time measurement): the live chain grows between sessions,
    # and the invariant this test protects is "no metals match today", not
    # "the corpus is frozen at 216".
    matches = {
        cid for cid, c in futures_accepted.items()
        if set((c.get("tags") or {}).get("topics") or []) & composer.METALS_PROXY_TOPICS
    }
    assert matches == set()


def test_metals_futures_proxy_lane_mechanism_fixture_injected(tmp_path, monkeypatch):
    """The routing MECHANISM (not the live, empty constant): with a
    monkeypatched non-empty METALS_PROXY_TOPICS, a futures-tagged card whose
    topics intersect it is proxy-routed exactly like the futures->equity_etf
    lane; an off-topic futures card and a native commodities card are not."""
    monkeypatch.setattr(composer, "METALS_PROXY_TOPICS", frozenset({"gold futures roll yield"}))

    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    proxy_cid = _register_accepted(
        reg, asset_classes=["futures"],
        claim="Gold futures exhibit a persistent positive roll yield in backwardation.",
        quote="gold futures show a positive roll yield when the curve is in backwardation",
        topics=["gold futures roll yield", "backwardation"])
    off_topic_cid = _register_accepted(
        reg, asset_classes=["futures"],
        claim="Bond futures roll yield differs by contract month.",
        quote="roll yield on bond futures differs by contract month",
        topics=["roll yield", "bond futures"])
    commodities_cid = _register_accepted(reg, asset_classes=["commodities"])

    captured = {}

    def spy(cards):
        captured["cards"] = set(cards)
        return [metal_family(card_ids=[commodities_cid, proxy_cid])]

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "metalrun", "--asset-class", "metal_etf"],
        propose_fn=spy)
    assert rc == 0

    assert proxy_cid in captured["cards"]
    assert off_topic_cid not in captured["cards"]
    assert commodities_cid in captured["cards"]

    drift = _drift_record(reg_path)
    assert drift["proxy_routed_card_ids"] == [proxy_cid]
    assert proxy_cid in drift["routed_card_ids"]
    assert off_topic_cid not in drift["routed_card_ids"]

    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        assert spec["provenance"]["routed_via"] == "proxy"
        assert spec["provenance"]["proxy_card_ids"] == [proxy_cid]
        assert commodities_cid not in spec["provenance"]["proxy_card_ids"]


# ---------------- metal_etf block exclusions: NONE (real OHLC bars) --------

def test_metal_range_blocks_are_not_excluded():
    assert cells_mod.CLASSES["metal_etf"]["excluded_block_types"] == frozenset()
    fam = metal_family(sweep=[])
    fam["blocks"][1] = {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}}
    errs = composer.validate_family(
        fam, ACCEPTED_METAL, 25,
        excluded_types=cells_mod.CLASSES["metal_etf"]["excluded_block_types"],
        asset_class="metal_etf")
    assert errs == [], errs


def test_bond_range_blocks_are_not_excluded():
    assert cells_mod.CLASSES["bond_etf"]["excluded_block_types"] == frozenset()
    fam = bond_family(sweep=[])
    fam["blocks"][1] = {"role": "stop", "type": "atr_stop", "params": {"atr_len": 14, "mult": 2.0}}
    errs = composer.validate_family(
        fam, ACCEPTED_BOND, 25,
        excluded_types=cells_mod.CLASSES["bond_etf"]["excluded_block_types"],
        asset_class="bond_etf")
    assert errs == [], errs


# ---------------- bond_etf / metal_etf specs validate against schema -------

def test_bond_and_metal_specs_validate_against_schema(tmp_path):
    schema = json.loads(
        (LAYER / "schemas" / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    bond_cid = _register_accepted(reg, asset_classes=["rates"])
    metal_cid = _register_accepted(reg, asset_classes=["commodities"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "bondrun", "--asset-class", "bond_etf"],
        propose_fn=lambda cards: [bond_family(card_ids=[bond_cid])])
    assert rc == 0
    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "metalrun", "--asset-class", "metal_etf"],
        propose_fn=lambda cards: [metal_family(card_ids=[metal_cid])])
    assert rc == 0

    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        validator.validate(spec)


def test_schema_enum_includes_bond_and_metal_etf_additively():
    """The 9 pre-existing values (8 card-taxonomy values + equity_etf) are
    all still present; this task's schema change only ADDS bond_etf and
    metal_etf, never removes or renames anything a crypto/fx/equity_etf spec
    already relies on."""
    schema = json.loads(
        (LAYER / "schemas" / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["universe"]["properties"]["asset_class"]["enum"]
    assert set(enum) == {"futures", "equities", "crypto", "fx", "options",
                         "rates", "commodities", "cross", "equity_etf",
                         "bond_etf", "metal_etf"}


def test_schema_rejects_bond_and_metal_before_the_additive_change():
    """Probe (git show) that the PRE-EDIT schema (2309b4e, the addendum
    commit, before this task's code) rejects universe.asset_class values
    "bond_etf"/"metal_etf", so this task's schema change is a real widening,
    not a no-op -- same technique as test_composer_equity.py's
    test_schema_rejects_proxy_keys_without_the_additive_change."""
    import subprocess
    pre_edit = subprocess.run(
        ["git", "show", "2309b4e:research-layer/schemas/strategy_spec.schema.json"],
        cwd=LAYER.parent, capture_output=True, text=True, encoding="utf-8")
    if pre_edit.returncode != 0:
        import pytest
        pytest.skip("pre-edit commit 2309b4e not reachable in this checkout")
    schema = json.loads(pre_edit.stdout)
    enum = schema["properties"]["universe"]["properties"]["asset_class"]["enum"]
    assert "bond_etf" not in enum
    assert "metal_etf" not in enum
    validator = jsonschema.Draft202012Validator(schema)
    for cls in ("bond_etf", "metal_etf"):
        errs = [e for e in validator.iter_errors({"universe": {"asset_class": cls}})
               if list(e.path) == ["universe", "asset_class"]]
        assert errs, f"pre-edit schema should have rejected asset_class={cls!r}"


# ---------------- Track 2b: per-class proposer briefs -----------------------

def test_bond_prompt_names_coupon_understatement():
    system = composer.system_prompt_for("bond_etf")
    assert system is not composer.SYSTEM_PROMPT
    assert system == composer._bond_etf_system_prompt()

    assert "coupon" in system.lower()
    assert "materially understated" in system.lower() or "understated" in system.lower()

    for asset in cells_mod.CLASSES["bond_etf"]["assets"]:
        assert asset in system
    assert "8" in system

    assert "crypto daily bars" not in system
    assert "BTCUSD" not in system


def test_metal_prompt_names_gld_slv_trust_structure():
    system = composer.system_prompt_for("metal_etf")
    assert system is not composer.SYSTEM_PROMPT
    assert system == composer._metal_etf_system_prompt()

    assert "trust" in system.lower()
    assert "GLD" in system and "SLV" in system
    assert "spot" in system.lower()

    for asset in cells_mod.CLASSES["metal_etf"]["assets"]:
        assert asset in system
    assert "2" in system

    assert "crypto daily bars" not in system
    assert "BTCUSD" not in system


def test_bond_and_metal_schema_assets_enum_is_their_own_universe():
    bond_schema = composer.proposal_schema_for("bond_etf")
    metal_schema = composer.proposal_schema_for("metal_etf")
    assert bond_schema is not composer.PROPOSAL_SCHEMA
    assert metal_schema is not composer.PROPOSAL_SCHEMA
    bond_enum = bond_schema["properties"]["families"]["items"]["properties"]["assets"]["items"]["enum"]
    metal_enum = metal_schema["properties"]["families"]["items"]["properties"]["assets"]["items"]["enum"]
    assert bond_enum == list(cells_mod.CLASSES["bond_etf"]["assets"])
    assert metal_enum == list(cells_mod.CLASSES["metal_etf"]["assets"])
