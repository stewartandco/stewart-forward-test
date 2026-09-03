"""Offline tests for the class-aware composer (--asset-class), Tasks 4 and 6b
of the SP4 Track 1 plan (docs/plans/2026-08-24-sp4-track1-fx.md). No API
calls: the first five tests below (Task 4) run() injecting propose_fn,
exactly like test_composer.py; the Task 6b tests below them inject a stub
client + a real BudgetMeter pointed at a tmp ledger, so propose_families
itself runs and the exact request it would send a live model can be
captured and pinned.

Run: python -m pytest pipeline/test_composer_fx.py -q
"""
from __future__ import annotations

import hashlib
import json
import types
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from . import composer
from . import cells as cells_mod
from . import reader
from .budget import BudgetMeter
from .registry import Registry
from .test_pipeline import make_card
from .test_composer import good_family, seeded_registry

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent


class _FixedDatetime(datetime):
    """Freezes composer.py's two datetime.now() call sites (the --run-id
    default and created_utc) so a captured drift record can be pinned
    byte-for-byte instead of only after stripping run-dependent fields."""
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 24, 0, 0, 0, tzinfo=tz)


def _register_accepted(reg: Registry, **card_overrides) -> str:
    card = make_card(**card_overrides)
    reg.register_card(card)
    reg.review_card(card["card_id"], "accepted", "tester")
    return card["card_id"]


def fx_family(**overrides):
    """A minimal fx family: ma_cross_dense (sweepable) entry, pct_stop (the
    one stop RANGE_REQUIRING does not exclude), r_multiple target,
    fixed_fraction risk.

    "assets" is real now (real-fx-generation finding, task 6b follow-up):
    validate_family checks it against cells.CLASSES[asset_class]["assets"]
    when asset_class != "crypto" (the first real fx generation dropped all
    5 model-proposed families on this exact check still enforcing BTCUSD/
    ETHUSD), so this fixture carries an actual fx pair rather than the
    crypto ticker it used to need only to satisfy a crypto-only check. It
    is still NOT a cell-selecting field: the real per-cell assets come from
    cells.active_cells("fx") inside expand_family_for_class, which ignores
    this field entirely when building specs. Declared vs ACTIVE (SP5 D4):
    class_cells is fx's whole declared grid, active_cells is the subset the
    ACTIVE_CELLS gate lets a generation sweep. fx gates "all"/"all", so the
    two are the same list today -- they stop being the same the moment a
    gate narrows, and expansion follows the gate.
    """
    fam = {
        "family": "fx_trend_family",
        "rationale": "FX trend continuation on daily fixes.",
        "card_ids": ["aaaaaaaaaaaaaaaa"],
        "assets": ["EUR"],
        "blocks": [
            {"role": "entry", "type": "ma_cross_dense",
             "params": {"fast": 13, "slow": 50, "direction": "long"}},
            {"role": "stop", "type": "pct_stop", "params": {"pct": 0.05}},
            {"role": "target", "type": "r_multiple", "params": {"r": 1.5}},
            {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.01}},
        ],
        # contiguous on ma_cross_dense.fast's grid [5, 8, 13, 20, 34] (indices
        # 1-3); three values so plateau selection has a neighbour either side.
        "sweep": [
            {"block": 0, "param": "fast", "values": [8, 13, 20]},
        ],
    }
    fam.update(overrides)
    return fam


ACCEPTED_FX = {"aaaaaaaaaaaaaaaa"}


def _drift_record(registry_path: Path) -> dict:
    log_path = registry_path.resolve().parent / "logs" / "batch_drift.jsonl"
    return json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])


def _registered_specs(registry_path: Path) -> list[dict]:
    return [e["payload"] for e in Registry(registry_path).entries()
            if e["entry_type"] == "strategy_registered"]


# ---------------- crypto default path: byte-identity regression ----------------

def test_default_flag_is_byte_identical(tmp_path, monkeypatch):
    """Capture-first regression (same technique as Task 3's engine pin).

    This EXACT drift record was captured from the pre-Task-4 composer.py by
    running the fixture below through composer.run(["--dry-run", ...],
    propose_fn=...) with datetime frozen to 2026-08-24T00:00:00Z and
    --run-id "fixed-run" -- no --asset-class flag, so this is the default
    path. Freezing time (rather than only stripping run_id/created_utc)
    means strategy_ids -- which hash created_utc -- are reproducible too, so
    the whole record can be compared byte-for-byte instead of field-by-field.
    The accepted card is also content-addressed on its own created_utc
    (reader.build_card), so reader.datetime is frozen too -- otherwise the
    card id, and everything hashed from it, would drift every real second.
    Every branch this task adds is guarded on asset_class != "crypto"; this
    pins that guard.
    """
    monkeypatch.setattr(composer, "datetime", _FixedDatetime)
    monkeypatch.setattr(reader, "datetime", _FixedDatetime)
    path, cid = seeded_registry(tmp_path)
    rc = composer.run(
        ["--registry", str(path), "--run-id", "fixed-run", "--dry-run"],
        propose_fn=lambda cards: [good_family(card_ids=[cid])])
    assert rc == 0
    assert _drift_record(path) == {
        "families": ["trend_scan_family"],
        "mode": "dry",
        "n_specs": 9,
        "run_id": "fixed-run",
        "strategy_ids": [
            "c91e0aba31a02fc3", "4884f3367a5ea660", "9f9baba815e2c85c",
            "173c9d1780c69994", "eb6cb5d485c2e3ea", "553a02e81372fd19",
            "673190978df1d299", "04b7a1df5069427a", "f02ab8fd383c1a4c",
        ],
    }


# ---------------- fx universe, costs, cell expansion ----------------

def test_fx_universe_and_costs(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["fx"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "fxrun", "--asset-class", "fx"],
        propose_fn=lambda cards: [fx_family(card_ids=[cid])])
    assert rc == 0

    specs = _registered_specs(reg_path)
    assert len(specs) == 3 * 12   # 3 swept `fast` values x 12 fx cells

    fps_by_fast: dict[float, set[str]] = {}
    for spec in specs:
        u = spec["universe"]
        assert len(u["assets"]) == 1
        asset = u["assets"][0]
        assert asset in cells_mod.FX_ASSETS
        assert u == {"assets": [asset], "asset_class": "fx",
                    "timeframe": "1d", "session": "fx_5d"}
        assert spec["cost_model"] == cells_mod.CLASSES["fx"]["cost_model"]
        assert spec["provenance"]["sibling_group_id"].endswith(f":{asset}_1d")
        fast = spec["blocks"][0]["params"]["fast"]
        fps_by_fast.setdefault(fast, set()).add(composer.composition_fingerprint(spec))

    # composition_fingerprint differs across the 12 cells of each sweep combo
    assert set(fps_by_fast) == {8, 13, 20}
    for fast, fps in fps_by_fast.items():
        assert len(fps) == 12

    # ...but is stable for the same family + run_id + timestamp + cell.
    a = composer.expand_family_for_class(
        fx_family(card_ids=[cid]), "fxrun", composer.DEFAULT_MODEL,
        "2026-08-24T00:00:00Z", "fx")
    b = composer.expand_family_for_class(
        fx_family(card_ids=[cid]), "fxrun", composer.DEFAULT_MODEL,
        "2026-08-24T00:00:00Z", "fx")
    assert [s["strategy_id"] for s in a] == [s["strategy_id"] for s in b]
    assert [composer.composition_fingerprint(s) for s in a] == \
          [composer.composition_fingerprint(s) for s in b]


# ---------------- fx card routing ----------------

def test_fx_card_routing(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    crypto_cid = _register_accepted(reg, asset_classes=["crypto"])
    fx_cid = _register_accepted(reg, asset_classes=["fx"])
    # reader.py:162 defaults an untagged card's asset_classes to ["cross"];
    # an empty list here reproduces that same default at build_card time.
    untagged_cid = _register_accepted(reg, asset_classes=[])

    captured = {}

    def spy(cards):
        captured["cards"] = set(cards)
        return [fx_family(card_ids=[fx_cid])]

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "fxrun", "--asset-class", "fx"],
        propose_fn=spy)
    assert rc == 0
    assert captured["cards"] == {fx_cid, untagged_cid}
    assert crypto_cid not in captured["cards"]

    drift = _drift_record(reg_path)
    assert drift["routing"] == {"asset_class": "fx", "eligible_tags": ["cross", "fx"]}
    assert set(drift["routed_card_ids"]) == {fx_cid, untagged_cid}
    # Track 2a review (missing regression, closed here): the futures->
    # equity_etf proxy lane is equity_etf-only. drift_record's
    # proxy_routed_card_ids param stays None for every fx run, so the key
    # must be ABSENT from the drift dict entirely, not present-and-empty
    # (that shape is reserved for equity_etf runs, where it always appears
    # because the proxy lane always ran).
    assert "proxy_routed_card_ids" not in drift


# ---------------- fx block exclusions ----------------

def test_fx_block_exclusions():
    # asset_class="fx" on every call below: fx_family()'s "assets" is now a
    # real fx pair (real-fx-generation finding, task 6b follow-up), checked
    # against cells.CLASSES["fx"]["assets"] rather than the crypto-only
    # ALLOWED_ASSETS these calls used to rely on by omission.
    assert composer.RANGE_REQUIRING == {"channel_breakout", "channel_breakout_dense",
                                        "atr_stop", "atr_stop_dense",
                                        # D15 exit rules v7: read highs/lows
                                        "swing_stop", "channel_stop", "channel_exit"}

    good = fx_family()
    assert composer.validate_family(
        good, ACCEPTED_FX, 25, excluded_types=composer.RANGE_REQUIRING,
        asset_class="fx") == []

    for excluded_type, role in (("atr_stop", "stop"), ("channel_breakout", "entry")):
        bad = fx_family()
        idx = 1 if role == "stop" else 0
        bad["blocks"][idx] = {"role": role, "type": excluded_type,
                              "params": {"atr_len": 14, "mult": 2.0}
                              if excluded_type == "atr_stop"
                              else {"lookback": 55, "direction": "long"}}
        errs = composer.validate_family(
            bad, ACCEPTED_FX, 25, excluded_types=composer.RANGE_REQUIRING,
            asset_class="fx")
        assert any(excluded_type in e and "single_fix" in e for e in errs), errs

    # Without excluded_types, the same family is fine even though it still
    # targets fx: block exclusion and the asset_class check are independent.
    bad = fx_family()
    bad["blocks"][1] = {"role": "stop", "type": "atr_stop",
                        "params": {"atr_len": 14, "mult": 2.0}}
    assert composer.validate_family(bad, ACCEPTED_FX, 25, asset_class="fx") == []


# ---------------- Task 6b follow-up: class-aware assets check ----------------
#
# The first real fx generation dropped all 5 model-proposed families:
# validate_family's assets check still enforced ALLOWED_ASSETS (BTCUSD/
# ETHUSD) against every asset_class, including fx, because the dry-run
# fixture above carried a vestigial ["BTCUSD"] and no test ever exercised a
# real fx-shaped "assets" field through validate_family with asset_class=
# "fx". These three tests close that gap directly.

def test_fx_family_with_fx_assets_passes_validate_family():
    assert composer.validate_family(
        fx_family(), ACCEPTED_FX, 25, asset_class="fx") == []


def test_fx_family_with_crypto_asset_rejected_naming_fx():
    bad = fx_family(assets=["BTCUSD"])
    errs = composer.validate_family(bad, ACCEPTED_FX, 25, asset_class="fx")
    assert any("BTCUSD" in e and "fx" in e for e in errs), errs


def test_fx_family_with_unknown_asset_rejected_naming_fx():
    bad = fx_family(assets=["XXXYYY"])
    errs = composer.validate_family(bad, ACCEPTED_FX, 25, asset_class="fx")
    assert any("XXXYYY" in e and "fx" in e for e in errs), errs


# ---------------- fx specs are schema-valid ----------------

def test_fx_specs_validate_against_schema(tmp_path):
    reg_path = tmp_path / "reg.jsonl"
    reg = Registry(reg_path)
    cid = _register_accepted(reg, asset_classes=["fx"])

    rc = composer.run(
        ["--registry", str(reg_path), "--run-id", "fxrun", "--asset-class", "fx"],
        propose_fn=lambda cards: [fx_family(card_ids=[cid])])
    assert rc == 0

    schema = json.loads(
        (LAYER / "schemas" / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    specs = _registered_specs(reg_path)
    assert specs
    for spec in specs:
        validator.validate(spec)


# ---------------- Task 6b: per-class proposer brief (stub client) ----------------
#
# The T4 review (docs/plans/2026-08-24-sp4-track1-fx.md, "Task 6b") found that
# on --asset-class fx runs the model still received the crypto mission
# statement (SYSTEM_PROMPT's "crypto daily bars (BTCUSD, ETHUSD)" + the
# gen-1/2 crypto history) and a schema whose assets enum forces BTCUSD/
# ETHUSD. Every test above bypasses propose_families entirely via propose_fn,
# so none of them could ever have caught that: this section injects a stub
# client instead, so the exact request composer.propose_families would send
# a live model can be captured and asserted on.

# Captured from composer.py at commit 854e929 (pre-task-6b, the exact HEAD
# this task started from): sha256 of SYSTEM_PROMPT (utf-8) and of
# PROPOSAL_SCHEMA (json.dumps sort_keys). Task 6b's system_prompt_for/
# proposal_schema_for must return these two objects BY IDENTITY on
# asset_class="crypto" (identity, not reconstruction), so this pin is the
# byte-for-byte guarantee that a real crypto model call never changed.
CRYPTO_SYSTEM_PROMPT_SHA256 = "1235a1bf292d3434fbeb38cb3e24713e3a80d9b4f8ebb384776af2e1e0c2c1d7"
CRYPTO_PROPOSAL_SCHEMA_SHA256 = "8345ffec2e7f2c20b80695acd50bad53df1b9fa32e6a07c5707f5b38750c48ce"


class _FakeStream:
    """The context-manager shape client.messages.stream(...) returns."""

    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        return self._message


class _FakeClient:
    """Captures the exact kwargs passed to messages.stream and returns a
    canned structured-output message, so propose_families runs to
    completion with no network call. usage is an empty SimpleNamespace:
    BudgetMeter.record_call reads every field via getattr(..., 0), so an
    empty usage records a zero-cost row rather than raising."""

    def __init__(self, families=()):
        self.captured: dict = {}
        families = list(families)

        class _Messages:
            @staticmethod
            def stream(**kwargs):
                self.captured.update(kwargs)
                message = types.SimpleNamespace(
                    usage=types.SimpleNamespace(),
                    stop_reason="end_turn",
                    content=[types.SimpleNamespace(
                        type="text", text=json.dumps({"families": families}))],
                )
                return _FakeStream(message)

        self.messages = _Messages()


def _meter(tmp_path) -> BudgetMeter:
    return BudgetMeter(tmp_path / "ledger.jsonl", monthly_cap_usd=20.0, agent="pipeline")


def test_crypto_prompt_and_schema_are_byte_identical_to_pre_edit(tmp_path):
    """(a) Crypto system prompt + schema pinned against the pre-edit values,
    both as module constants and as what propose_families actually sends."""
    assert hashlib.sha256(composer.SYSTEM_PROMPT.encode("utf-8")).hexdigest() \
        == CRYPTO_SYSTEM_PROMPT_SHA256
    assert hashlib.sha256(
        json.dumps(composer.PROPOSAL_SCHEMA, sort_keys=True).encode("utf-8")
    ).hexdigest() == CRYPTO_PROPOSAL_SCHEMA_SHA256

    # Identity, not reconstruction: task 6b's dispatchers must hand back the
    # exact pre-existing objects on the default class.
    assert composer.system_prompt_for("crypto") is composer.SYSTEM_PROMPT
    assert composer.proposal_schema_for("crypto") is composer.PROPOSAL_SCHEMA

    client = _FakeClient(families=[])
    composer.propose_families(composer.DEFAULT_MODEL, {}, 8,
                              client=client, meter=_meter(tmp_path))
    assert client.captured["system"] is composer.SYSTEM_PROMPT
    assert client.captured["output_config"]["format"]["schema"] is composer.PROPOSAL_SCHEMA


def test_fx_prompt_names_the_real_universe_and_drops_crypto_history(tmp_path):
    """(b) On asset_class="fx", the system prompt names the 12-pair fx
    universe, single-fix bars, the weekday calendar and no-carry returns,
    and never mentions crypto's mission statement or its gen-1/2 history.
    The user message still carries the existing excluded-block-types line
    (spec s10.7, unchanged by this task)."""
    client = _FakeClient(families=[])
    composer.propose_families(composer.DEFAULT_MODEL, {}, 8,
                              client=client, meter=_meter(tmp_path),
                              asset_class="fx")

    system = client.captured["system"]
    assert system is not composer.SYSTEM_PROMPT
    assert system == composer.system_prompt_for("fx")

    # The crypto mission statement must not leak into an fx run.
    assert "crypto daily bars" not in system
    assert "BTCUSD" not in system
    assert "ETHUSD" not in system
    assert "Generation 1" not in system
    assert "Generation 2" not in system

    # The real fx universe, pulled from cells.CLASSES["fx"], is named.
    for asset in cells_mod.CLASSES["fx"]["assets"]:
        assert asset in system
    assert "12" in system
    assert "single-fix" in system
    assert "weekday" in system.lower()
    assert "carry" in system.lower()

    # The existing exclusion line (added by Task 4) still travels in the
    # user message; this task does not move or duplicate it.
    user_content = client.captured["messages"][0]["content"]
    for excluded_type in sorted(composer.RANGE_REQUIRING):
        assert excluded_type in user_content


def test_fx_schema_assets_enum_is_the_fx_universe(tmp_path):
    """(c) The fx schema's assets enum is cells.CLASSES["fx"]["assets"], not
    ALLOWED_ASSETS -- the model may propose any subset; per-cell expansion
    (expand_family_for_class) overrides it regardless (see
    proposal_schema_for's docstring)."""
    client = _FakeClient(families=[])
    composer.propose_families(composer.DEFAULT_MODEL, {}, 8,
                              client=client, meter=_meter(tmp_path),
                              asset_class="fx")

    schema = client.captured["output_config"]["format"]["schema"]
    assert schema is not composer.PROPOSAL_SCHEMA
    enum = schema["properties"]["families"]["items"]["properties"]["assets"]["items"]["enum"]
    assert enum == list(cells_mod.CLASSES["fx"]["assets"])

    # Nothing else in the schema moved.
    stripped_fx = json.loads(json.dumps(schema))
    stripped_fx["properties"]["families"]["items"]["properties"]["assets"]["items"]["enum"] = \
        list(composer.ALLOWED_ASSETS)
    assert stripped_fx == composer.PROPOSAL_SCHEMA
