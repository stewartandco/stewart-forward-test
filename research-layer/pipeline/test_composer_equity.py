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
# CORRECTED 2026-08-25 (track 2a review): the live registry does NOT have
# zero accepted futures cards. The first pass (2026-08-24) measured review
# status by reading the embedded review.status on card_registered payloads,
# which is always "pending" at registration time and never folds in the
# later card_reviewed entries the way Registry.cards(status=...) does (the
# same join composer.run() itself uses to build `accepted`) -- so it wrongly
# reported 342/342 pending. Measured correctly THROUGH Registry.cards(): of
# 342 futures-tagged cards, 216 are accepted, 42 rejected, 84 pending.
# Intersecting the 216 accepted cards' topics against
# composer.INDEX_FUTURES_PROXY_TOPICS finds exactly ONE real match:
# f3c7efcd1bb41166 (topic "S&P 500") -- so a real equity_etf composer run
# today proxy-routes that one card. Both facts are pinned below: the real
# match against the live registry (read-only), and the routing MECHANISM
# itself against fixture-injected cards, so the mechanism stays covered even
# if that one card is later re-triaged.

def test_index_futures_proxy_topics_measured_and_bounded():
    """Sanity on the declared constant itself: 3-6 topics (build brief
    2026-08-24), all strings, frozen so it cannot be mutated at runtime."""
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


def test_live_registry_has_exactly_one_accepted_proxy_match():
    """Pinned against the LIVE registry (read-only -- no writes, no compose/
    screen/gauntlet run against it, matching this package's convention of
    treating the live chain as read-only from tests). If this ever fails
    because the corpus was re-triaged, re-measure through Registry.cards()
    and update this assertion together with composer.py's comment -- never
    one without the other (that mismatch is exactly what this fix corrects)."""
    live = Registry(LAYER / "registry_log.jsonl")
    accepted = live.cards(status="accepted")
    futures_accepted = {
        cid: c for cid, c in accepted.items()
        if "futures" in (c.get("tags") or {}).get("asset_classes", [])
    }
    matches = {
        cid for cid, c in futures_accepted.items()
        if set((c.get("tags") or {}).get("topics") or []) & composer.INDEX_FUTURES_PROXY_TOPICS
    }
    assert matches == {"f3c7efcd1bb41166"}


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

    # Coen, 2026-08-25: proxy routing is ALSO recorded on the registration
    # (the drift record above stays the run-level view; this is the
    # per-strategy, chain-queryable marker). The family here cites BOTH a
    # native card (equity_cid) and a proxy-routed one (proxy_cid): every
    # spec it expands to carries the family's proxy SUBSET, never the whole
    # run's proxy set and never equity_cid.
    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        assert spec["provenance"]["routed_via"] == "proxy"
        assert spec["provenance"]["proxy_card_ids"] == [proxy_cid]
        assert equity_cid not in spec["provenance"]["proxy_card_ids"]


# ---------------- Coen 2026-08-25: proxy provenance on the registration ----
#
# Registrations carry the routed_via/proxy_card_ids marker; the drift record
# (routing/routed_card_ids/proxy_routed_card_ids, above) stays the run-level
# view. This makes the Norgate re-test set (spec D2/D4) chain-queryable per
# strategy. composition_fingerprint hashes universe+blocks only, so this
# marker never affects a spec's identity, N accounting, or the resurrection
# guard -- pinned directly below.

def test_proxy_family_specs_carry_routed_via_and_proxy_card_ids():
    """A family whose card_ids intersect the run's proxy set gets that
    intersection -- not the whole run's proxy set -- stamped on every spec
    it expands to."""
    fam = equity_family(card_ids=["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"], sweep=[])
    specs = composer.expand_family_for_class(
        fam, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "equity_etf",
        proxy_card_ids=frozenset({"bbbbbbbbbbbbbbbb", "zzzzzzzzzzzzzzzz"}))
    assert len(specs) == 16
    for spec in specs:
        assert spec["provenance"]["routed_via"] == "proxy"
        # the family's OWN proxy subset (bbbb...), not the run's whole proxy
        # set (which also names zzzz..., a card this family never cited)
        assert spec["provenance"]["proxy_card_ids"] == ["bbbbbbbbbbbbbbbb"]
        assert spec["provenance"]["card_ids"] == ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]


def test_native_family_specs_carry_neither_proxy_key():
    """A family that cites no proxy-routed card gets NEITHER key -- absence
    IS native routing; no null or empty-list placeholder is ever written."""
    fam = equity_family(sweep=[])
    specs = composer.expand_family_for_class(
        fam, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "equity_etf")
    assert len(specs) == 16
    for spec in specs:
        assert "routed_via" not in spec["provenance"]
        assert "proxy_card_ids" not in spec["provenance"]
    # Also true with an explicit-but-non-matching proxy set: the family's
    # OWN card_ids simply don't intersect it.
    specs2 = composer.expand_family_for_class(
        fam, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "equity_etf",
        proxy_card_ids=frozenset({"zzzzzzzzzzzzzzzz"}))
    for spec in specs2:
        assert "routed_via" not in spec["provenance"]
        assert "proxy_card_ids" not in spec["provenance"]


_FX_CHECK_FAMILY = {
    "family": "fx_check_family",
    "card_ids": ["cccccccccccccccc"],
    "assets": ["EUR"],
    "blocks": [
        {"role": "entry", "type": "ma_cross_dense",
         "params": {"fast": 13, "slow": 50, "direction": "long"}},
        {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
        {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
        {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
    ],
    "sweep": [],
}


def test_fx_specs_never_carry_proxy_provenance_keys():
    """Regression: fx (Track 1) has no proxy lane. expand_family_for_class's
    proxy_card_ids parameter defaults to frozenset(), so composer.run()'s
    fx call site (which never passes it) keeps emitting neither key,
    byte-identical to before Coen's 2026-08-25 decision -- even when an fx
    family's card_ids happen to collide with a caller-supplied proxy set."""
    specs = composer.expand_family_for_class(
        _FX_CHECK_FAMILY, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "fx")
    assert len(specs) == 12
    for spec in specs:
        assert "routed_via" not in spec["provenance"]
        assert "proxy_card_ids" not in spec["provenance"]
    # Even if a caller passed a proxy set that happens to name this fx
    # family's own card: fx composer.run() never does this (proxy routing
    # is equity_etf-only), but the function itself does not special-case fx
    # by name -- proving the guarantee holds structurally, not by omission.
    specs2 = composer.expand_family_for_class(
        _FX_CHECK_FAMILY, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "fx",
        proxy_card_ids=frozenset(_FX_CHECK_FAMILY["card_ids"]))
    for spec in specs2:
        assert spec["provenance"]["routed_via"] == "proxy"
        assert spec["provenance"]["proxy_card_ids"] == _FX_CHECK_FAMILY["card_ids"]
    # composer.run() itself never constructs that proxy set for fx: it stays
    # None throughout an fx run (only equity_etf runs populate it), so the
    # frozenset(proxy_routed_card_ids or ()) call site always resolves to
    # frozenset() for fx -- the first assertion block above is what a real
    # fx run actually produces.


def test_crypto_path_structurally_cannot_carry_proxy_keys():
    """crypto composition never goes through expand_family_for_class at
    all -- composer.run() branches to expand_family for asset_class ==
    "crypto", a function this task does not touch and which builds its
    provenance dict with no routed_via/proxy_card_ids branch at all. There
    is therefore no code path by which a crypto spec's provenance could
    carry either key; asserted directly against expand_family's output."""
    fam = dict(_FX_CHECK_FAMILY, family="crypto_check_family", assets=["BTCUSD"],
              card_ids=["dddddddddddddddd"])
    specs = composer.expand_family(fam, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z")
    assert len(specs) == 1
    assert "routed_via" not in specs[0]["provenance"]
    assert "proxy_card_ids" not in specs[0]["provenance"]


def test_proxy_provenance_never_affects_composition_fingerprint():
    """composition_fingerprint hashes universe+blocks only (never
    provenance), so proxy routing must never change a spec's identity, its
    N accounting, or the resurrection guard's de-duplication. strategy_id
    DOES differ (content-addressed on the whole spec, provenance included)
    -- chain-address identity is not the same guarantee as trial identity."""
    fam = equity_family(sweep=[])
    native = composer.expand_family_for_class(
        fam, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "equity_etf")
    proxy = composer.expand_family_for_class(
        fam, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "equity_etf",
        proxy_card_ids=frozenset(fam["card_ids"]))
    assert len(native) == len(proxy) == 16
    for n, p in zip(native, proxy):
        assert n["universe"] == p["universe"]
        assert n["blocks"] == p["blocks"]
        assert composer.composition_fingerprint(n) == composer.composition_fingerprint(p)
        assert n["strategy_id"] != p["strategy_id"]


def test_proxy_provenance_validates_against_schema():
    """Both provenance shapes -- native (neither key) and proxy (both keys)
    -- validate against the additively-extended schema."""
    schema = json.loads(
        (LAYER / "schemas" / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    fam = equity_family(sweep=[])
    for proxy_ids in (frozenset(), frozenset(fam["card_ids"])):
        specs = composer.expand_family_for_class(
            fam, "run1", composer.DEFAULT_MODEL, "2026-08-25T00:00:00Z", "equity_etf",
            proxy_card_ids=proxy_ids)
        assert specs
        for spec in specs:
            validator.validate(spec)


def test_schema_rejects_proxy_keys_without_the_additive_change():
    """Probe (git show) that the PRE-EDIT schema rejects these two keys, so
    this task's schema change is a real widening, not a no-op. Kept as a
    live test (not just a commit-message claim) so a future accidental
    revert of the schema edit is caught here first."""
    import subprocess
    pre_edit = subprocess.run(
        ["git", "show", "ec910bf:research-layer/schemas/strategy_spec.schema.json"],
        cwd=LAYER.parent, capture_output=True, text=True, encoding="utf-8")
    if pre_edit.returncode != 0:
        import pytest
        pytest.skip("pre-edit commit ec910bf not reachable in this checkout")
    schema = json.loads(pre_edit.stdout)
    validator = jsonschema.Draft202012Validator(schema)
    provenance = {"card_ids": ["a" * 16], "sibling_group_id": "x", "generation": 0,
                 "routed_via": "proxy", "proxy_card_ids": ["a" * 16]}
    errs = [e for e in validator.iter_errors({"provenance": provenance})
           if list(e.path) and list(e.path)[0] == "provenance"]
    assert errs, "pre-edit schema should have rejected routed_via/proxy_card_ids"


# ---------------- SP5 P2-T2: expansion sweeps the ACTIVE set ----------------
# equity_etf is the per-cell class this module already fixtures (equity_family
# above), so the gate is pinned here rather than in a fixture invented for it.

def _active_specs():
    """equity_family() expanded for equity_etf: 3 swept `fast` values x
    however many cells the ACTIVE set admits."""
    return composer.expand_family_for_class(
        equity_family(), "run1", composer.DEFAULT_MODEL,
        "2026-08-25T00:00:00Z", "equity_etf")


def test_expansion_sweeps_the_active_set_not_the_declared_grid(monkeypatch):
    # SP5 s3: the declared grid admits data work; the ACTIVE set admits
    # sweeping. A class whose active set is a strict subset expands to
    # that subset only.
    monkeypatch.setitem(cells_mod.ACTIVE_CELLS, "equity_etf",
                        {"assets": ("SPY",), "timeframes": "all"})
    specs = _active_specs()
    assert {s["universe"]["assets"][0] for s in specs} == {"SPY"}
    assert len(specs) == 3   # 3 sweep combos x 1 active cell


def test_full_active_set_expansion_is_unchanged_for_tradfi():
    # "all"/"all" -> byte-identical to the pre-gate behavior
    specs = _active_specs()
    assert len(specs) == 3 * len(cells_mod.class_cells("equity_etf"))
    assert {(s["universe"]["assets"][0], s["universe"]["timeframe"]) for s in specs} \
        == set(cells_mod.class_cells("equity_etf"))


def test_an_empty_active_set_expands_to_nothing(monkeypatch):
    # This is the state crypto will be in after P2-T3: declared, not swept.
    monkeypatch.setitem(cells_mod.ACTIVE_CELLS, "equity_etf",
                        {"assets": (), "timeframes": ()})
    specs = _active_specs()
    assert specs == []


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
    schema change for THIS task only ADDED "equity_etf", never removed or
    renamed anything a crypto/fx spec already relies on. Track 2b (later)
    additively adds "bond_etf"/"metal_etf" on top -- pinned as a superset
    check here, and exactly by test_composer_2b.py's own additive test, so
    a Track 2b regression is caught in two places rather than only replacing
    this assertion's exact set."""
    schema = json.loads(
        (LAYER / "schemas" / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["universe"]["properties"]["asset_class"]["enum"]
    assert {"futures", "equities", "crypto", "fx", "options",
            "rates", "commodities", "cross", "equity_etf"} <= set(enum)


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
    # bond_etf/metal_etf are now declared (Track 2b) and have their own
    # proposer briefs, so they no longer serve as the "unknown class" probe
    # here -- "commodity_future" is not declared by any class.
    import pytest
    with pytest.raises(ValueError, match="no proposer brief"):
        composer.system_prompt_for("commodity_future")
