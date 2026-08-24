"""Offline tests for the class-aware composer (--asset-class), Task 4 of the
SP4 Track 1 plan (docs/plans/2026-08-24-sp4-track1-fx.md). No API calls: every
run() invocation injects propose_fn, exactly like test_composer.py.

Run: python -m pytest pipeline/test_composer_fx.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from . import composer
from . import cells as cells_mod
from . import reader
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

    "assets" is vestigial here: validate_family's ALLOWED_ASSETS check is
    crypto-only (out of this task's scope, unchanged) and has nothing to do
    with fx cell selection -- the real per-cell assets come from
    cells.class_cells("fx") inside expand_family_for_class. The field only
    needs to satisfy that legacy check, so it stays a crypto ticker.
    """
    fam = {
        "family": "fx_trend_family",
        "rationale": "FX trend continuation on daily fixes.",
        "card_ids": ["aaaaaaaaaaaaaaaa"],
        "assets": ["BTCUSD"],
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


# ---------------- fx block exclusions ----------------

def test_fx_block_exclusions():
    assert composer.RANGE_REQUIRING == {"channel_breakout", "channel_breakout_dense",
                                        "atr_stop", "atr_stop_dense"}

    good = fx_family()
    assert composer.validate_family(
        good, ACCEPTED_FX, 25, excluded_types=composer.RANGE_REQUIRING) == []

    for excluded_type, role in (("atr_stop", "stop"), ("channel_breakout", "entry")):
        bad = fx_family()
        idx = 1 if role == "stop" else 0
        bad["blocks"][idx] = {"role": role, "type": excluded_type,
                              "params": {"atr_len": 14, "mult": 2.0}
                              if excluded_type == "atr_stop"
                              else {"lookback": 55, "direction": "long"}}
        errs = composer.validate_family(
            bad, ACCEPTED_FX, 25, excluded_types=composer.RANGE_REQUIRING)
        assert any(excluded_type in e and "single_fix" in e for e in errs), errs

    # Without excluded_types (the crypto default), the same family is fine.
    bad = fx_family()
    bad["blocks"][1] = {"role": "stop", "type": "atr_stop",
                        "params": {"atr_len": 14, "mult": 2.0}}
    assert composer.validate_family(bad, ACCEPTED_FX, 25) == []


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
