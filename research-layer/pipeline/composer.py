"""Composer agent: compose candidate strategy specs from accepted research
cards and pre-register them into the chained registry, before any results.

Usage:
    python -m pipeline.composer --max-families 8 --sibling-cap 25 \
        [--dry-run] [--model claude-opus-5] [--registry registry_log.jsonl] \
        [--run-id 2026-08-06-manual]

Two-stage: one structured-output model call proposes idea families (blocks,
cited cards, sweep axes); deterministic code validates each family against
the block grammar, expands sweeps into sibling specs sharing a
sibling_group_id, and chains block_type_registered + strategy_registered
entries. Invalid families are dropped loudly, never silently.
"""
from __future__ import annotations

import re
import sys
import copy
import json
import hashlib
import argparse
import itertools
from math import prod
from pathlib import Path
from datetime import datetime, timezone

from .common import content_id
from .registry import Registry
from .blocks import BLOCK_TYPES, validate_block, block_type_payload

PIPELINE_VERSION = "g1.0.0"
DEFAULT_MODEL = "claude-opus-5"
SIBLING_CAP_DEFAULT = 25
ALLOWED_ASSETS = ("BTCUSD", "ETHUSD")
UNIVERSE_BASE = {"asset_class": "crypto", "timeframe": "1d", "session": "24x7"}
COST_MODEL = {"commission_per_side": 0.001, "slippage_ticks": 0.0005}
# ^ both are FRACTIONS of notional (10 bps + 5 bps); the field name
#   slippage_ticks is inherited from spec schema v1 — see design doc.


def composition_fingerprint(spec: dict) -> str:
    """Identity of a STRATEGY, not of a registration: universe and blocks
    only. Excludes strategy_id, created_utc, run_id, name, family and
    provenance, so two registrations of the same strategy collide.

    Params are snapped to their grid values HERE rather than trusting the
    caller to have done it: 2 and 2.0 must never fingerprint differently.
    This function is the only thing standing between a buried strategy and
    a fresh id, so it does not rely on caller discipline."""
    blocks = []
    for b in spec["blocks"]:
        schema = BLOCK_TYPES.get((b["role"], b["type"]), {})
        params = {}
        for p, v in b["params"].items():
            grid = schema.get(p, {}).get("grid")
            params[p] = next((g for g in grid if g == v), v) if grid else v
        blocks.append({"role": b["role"], "type": b["type"],
                       "params": dict(sorted(params.items()))})
    blocks.sort(key=lambda b: (b["role"], b["type"],
                               json.dumps(b["params"], sort_keys=True)))
    u = spec["universe"]
    core = {"assets": sorted(u["assets"]),
            "timeframe": u["timeframe"],
            "asset_class": u.get("asset_class"),
            "session": u.get("session"),
            "blocks": blocks}
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()


def registered_fingerprints(registry: Registry) -> dict[str, str]:
    """{composition_fingerprint: strategy_id} over every registered strategy,
    in ANY lifecycle state — a graveyarded composition must never return."""
    out = {}
    for e in registry.entries():
        if e["entry_type"] == "strategy_registered":
            out.setdefault(composition_fingerprint(e["payload"]),
                           e["payload"]["strategy_id"])
    return out


def screen_siblings(specs: list[dict], known_fps: dict[str, str],
                    run_fps: dict[str, str]) -> tuple[list[dict], list[str], bool]:
    """Rule 7 at SIBLING level -> (kept_specs, drop_notes, malformed).

    A composition already registered in ANY lifecycle state, graveyard
    included, can never be re-registered — but its siblings can. A real idea
    comes back at neighbouring parameters; one that worked only at the exact
    buried point was overfit and deserved burying. So a collision drops that
    sibling alone and the family survives on the rest.

    Intra-family duplicates are different in kind: two siblings that are the
    same composition mean duplicate blocks or mirrored sweep axes, which is a
    malformed proposal rather than a collision. malformed=True means the
    caller drops the whole family and ignores kept_specs.

    The invariant the public chain rests on: no returned spec's fingerprint
    is in known_fps or in run_fps."""
    fam_fps: dict[str, str] = {}
    kept_specs: list[dict] = []
    drop_notes: list[str] = []
    for spec in specs:
        fp = composition_fingerprint(spec)
        if fp in fam_fps:
            # Return on the FIRST duplicate rather than listing every one:
            # the family is already dead, and one named pair diagnoses the
            # mirrored axis better than its downstream consequences do.
            drop_notes.append(
                f"siblings {fam_fps[fp]} and {spec['strategy_id']} are the "
                f"same composition — duplicate blocks or mirrored sweep axes")
            return kept_specs, drop_notes, True
        fam_fps[fp] = spec["strategy_id"]
        if fp in known_fps:
            drop_notes.append(f"sibling {spec['strategy_id']} dropped: "
                              f"composition already registered as {known_fps[fp]}")
        elif fp in run_fps:
            drop_notes.append(f"sibling {spec['strategy_id']} dropped: "
                              f"composition duplicates family {run_fps[fp]} "
                              f"in this run")
        else:
            kept_specs.append(spec)
    return kept_specs, drop_notes, False


def validate_family(fam: dict, accepted_ids: set[str], sibling_cap: int) -> list[str]:
    """Return error strings; empty = family is expandable."""
    errors = []
    if not re.fullmatch(r"[a-z0-9_]+", fam.get("family", "")):
        errors.append(f"family name {fam.get('family')!r} must match [a-z0-9_]+")
    if not fam.get("card_ids"):
        errors.append("no cards cited")
    for cid in fam.get("card_ids", []):
        if cid not in accepted_ids:
            errors.append(f"card {cid} not accepted (or unknown)")
    if not fam.get("assets") or not set(fam["assets"]) <= set(ALLOWED_ASSETS):
        errors.append(f"assets {fam.get('assets')} must be a non-empty subset of {list(ALLOWED_ASSETS)}")

    blocks = fam.get("blocks", [])
    roles = [b.get("role") for b in blocks]
    if roles.count("entry") != 1:
        errors.append("exactly one entry block required")
    if "stop" not in roles:
        errors.append("at least one stop block required")
    if "risk" not in roles:
        errors.append("at least one risk block required")
    types = {b.get("type") for b in blocks}
    if {"regime_ma", "regime_ma_short"} <= types:
        errors.append("cannot combine regime_ma and regime_ma_short — the "
                      "gate AND is empty, so the spec would never trade")
    for b in blocks:
        errors.extend(validate_block(b.get("role"), b.get("type"), b.get("params", {})))

    seen_axes = set()
    for ax in fam.get("sweep", []):
        i = ax.get("block")
        if not isinstance(i, int) or not 0 <= i < len(blocks):
            errors.append(f"sweep axis references bad block index {i!r}")
            continue
        key = (blocks[i].get("role"), blocks[i].get("type"))
        schema = BLOCK_TYPES.get(key, {})
        p = ax.get("param")
        if p not in schema:
            errors.append(f"sweep axis param {p!r} not in {key[0]}/{key[1]}")
            continue
        if (i, p) in seen_axes:
            errors.append(f"duplicate sweep axis {p!r} on block {i}")
        seen_axes.add((i, p))
        values = ax.get("values", [])
        if not values or not set(values) <= set(schema[p]["grid"]):
            errors.append(f"sweep values for {p!r} not a subset of grid {schema[p]['grid']}")
        if len(set(values)) != len(values):
            errors.append(f"duplicate values in sweep axis {p!r}")

    if not errors:
        n = prod(len(ax["values"]) for ax in fam.get("sweep", [])) if fam.get("sweep") else 1
        if n > sibling_cap:
            errors.append(f"{n} siblings exceeds cap {sibling_cap} — rejected, not clipped")
    return errors


def _build_name(assets: list[str], family: str, blocks: list[dict]) -> str:
    bits = ["+".join(a.replace("USD", "") for a in assets),
            UNIVERSE_BASE["timeframe"], family]
    for b in blocks:
        for p, v in sorted(b["params"].items()):
            short = "".join(w[0] for w in p.split("_"))
            bits.append(f"{short}{v}")
    full = " ".join(bits)
    if len(full) <= 120:
        return full
    # keep names unique after truncation: suffix = hash of the full name
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    return full[:111] + "~" + digest


def _snap_to_grid(blocks: list[dict]) -> None:
    """Replace each param value with the equal grid element (in place), so
    2 and 2.0 hash identically. Membership was already validated."""
    for b in blocks:
        schema = BLOCK_TYPES.get((b["role"], b["type"]), {})
        for p, v in b["params"].items():
            grid = schema.get(p, {}).get("grid")
            if grid:
                b["params"][p] = next(g for g in grid if g == v)


def expand_family(fam: dict, run_id: str, model: str, created_utc: str) -> list[dict]:
    """Cartesian expansion of sweep axes, in declaration order. Deterministic:
    same family + run_id + timestamp -> same strategy_ids."""
    assets = sorted(fam["assets"])
    axes = fam.get("sweep", [])
    combos = itertools.product(*[ax["values"] for ax in axes]) if axes else [()]
    specs = []
    for combo in combos:
        blocks = copy.deepcopy(fam["blocks"])
        for ax, val in zip(axes, combo):
            blocks[ax["block"]]["params"][ax["param"]] = val
        _snap_to_grid(blocks)
        spec = {
            "strategy_id": None,
            "version": 1,
            "created_utc": created_utc,
            "name": _build_name(assets, fam["family"], blocks),
            "family": fam["family"],
            "universe": {"assets": assets, **UNIVERSE_BASE},
            "blocks": blocks,
            "provenance": {
                "card_ids": sorted(fam["card_ids"]),
                "parent_strategy_id": None,
                "sibling_group_id": f"{fam['family']}-{run_id}",
                "generation": 0,
            },
            "generator": {
                "agent": "composer",
                "model": model,
                "pipeline_version": PIPELINE_VERSION,
                "run_id": run_id,
            },
            "cost_model": dict(COST_MODEL),
        }
        spec["strategy_id"] = content_id(spec, "strategy_id")
        specs.append(spec)
    return specs


PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "family": {"type": "string"},
                    "rationale": {"type": "string"},
                    "regime_hypothesis": {"type": "string"},
                    "card_ids": {"type": "array", "items": {"type": "string"}},
                    "assets": {"type": "array",
                               "items": {"enum": list(ALLOWED_ASSETS)}},
                    "blocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"enum": ["entry", "filter", "exit",
                                                   "stop", "target", "risk", "regime"]},
                                "type": {"type": "string"},
                                # structured outputs cannot express free-form
                                # maps (every object needs additionalProperties
                                # false), so params travel as name/value pairs
                                # and normalize_proposal() converts to dicts
                                "params": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "value": {"type": ["number", "string"]},
                                        },
                                        "required": ["name", "value"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["role", "type", "params"],
                            "additionalProperties": False,
                        },
                    },
                    "sweep": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "block": {"type": "integer"},
                                "param": {"type": "string"},
                                "values": {"type": "array",
                                           "items": {"type": ["number", "string"]}},
                            },
                            "required": ["block", "param", "values"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["family", "rationale", "regime_hypothesis",
                              "card_ids", "assets", "blocks", "sweep"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["families"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are the Composer agent in Stewart & Co.'s research pipeline. You design
candidate trading strategies for crypto daily bars (BTCUSD, ETHUSD) as
compositions of typed blocks, grounded in accepted research cards.

What happened so far, as measured, not as opinion:

Generation 1 registered three families, all long-biased. Every one passed the
screen and failed out of sample, because their per-trade edge scaled with a
price drift and a volatility level that did not persist. Passive buy-and-hold
decayed just as hard over the same window.

Generation 2 corrected the long-only bias: all four families were
short-capable or symmetric. Results:
- Symmetric z-score reversion lost 46% to 66% in training and failed the
  screen outright.
- A short-only trend family produced only 10-19 trades in seven years and
  failed the screen's trade-count floor, not its P&L gate.
- Of the 18 that reached the gauntlet, 12 showed POSITIVE
  volatility-normalized edge decay out of sample, from +1.8% to +54.3%: the
  two-sided trend and breakout designs held their edge per unit of available
  opportunity.
- Every one of the four worst ruin and Monte Carlo outcomes came from
  vol_target sizing.

Draw your own conclusions from those facts.

Rules:
- Use ONLY the block types and parameter grid values given in the grammar.
- Every family must cite the card_ids that motivate it. Cite only cards that
  genuinely inform the composition; do not decorate with irrelevant citations.
- Exactly one entry block; at least one stop and one risk block per family.
- Every family must state a regime_hypothesis: which market conditions it
  expects to work in, and why it is not merely levered exposure to an upward
  drift. A family whose edge disappears when drift and volatility fall should
  say so plainly.
- Short-capable types exist: trend_scan_ds and ma_cross_ds take a direction
  parameter (long, short, both), and regime_ma_short permits entries below a
  moving average. channel_breakout and zscore_reversion already accept
  direction: both.
- regime_ma and regime_ma_short cannot appear in the same family — their
  filters are mutually exclusive and the spec would never trade. Express
  "long in one regime, short in the other" as two separate families.
- Choose sweep axes ONLY where the cited research motivates exploring the
  parameter; sweep values must come from the declared grids. Small, motivated
  sweeps beat exhaustive ones.
- Strategies must be implementable from daily OHLCV alone.
- Propose fewer, better-grounded families over many weak ones. If the cards
  support only two good families, propose two."""


def grammar_summary() -> str:
    lines = []
    for (role, btype), schema in BLOCK_TYPES.items():
        params = ", ".join(f"{p} in {s['grid']}" for p, s in schema.items())
        lines.append(f"- {role}/{btype}: {params or '(no params)'}")
    return "\n".join(lines)


def cards_summary(accepted: dict[str, dict]) -> str:
    lines = []
    for cid, c in accepted.items():
        lines.append(f"- {cid} [{c['testability']['score']:.2f}] {c['claim']}")
    return "\n".join(lines)


def preflight_block_types(registry: Registry) -> list[str]:
    """Conflicts between the in-code grammar and already-chained block types.
    Non-empty means blocks.py mutated a chained schema — abort before any write."""
    chained = {}
    for e in registry.entries():
        if e["entry_type"] == "block_type_registered":
            chained[(e["payload"]["role"], e["payload"]["type"])] = e["payload"]["params_schema"]
    return [
        f"{role}/{btype} already chained with a different params_schema"
        for (role, btype), schema in BLOCK_TYPES.items()
        if (role, btype) in chained and chained[(role, btype)] != schema
    ]


def propose_families(model: str, accepted: dict[str, dict],
                     max_families: int) -> list[dict]:
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=model,
        max_tokens=32_000,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                f"Block grammar:\n{grammar_summary()}\n\n"
                f"Accepted research cards:\n{cards_summary(accepted)}\n\n"
                f"Propose up to {max_families} strategy families per your rules."
            ),
        }],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        print("  model refusal", file=sys.stderr)
        return []
    text = next(b.text for b in message.content if b.type == "text")
    return normalize_proposal(json.loads(text)["families"])


def normalize_proposal(families: list[dict]) -> list[dict]:
    """Convert model-emitted [{name, value}] param lists back into the
    {name: value} dicts the rest of the pipeline uses (see PROPOSAL_SCHEMA)."""
    for fam in families:
        for b in fam.get("blocks", []):
            if isinstance(b.get("params"), list):
                b["params"] = {p["name"]: p["value"] for p in b["params"]}
    return families


def run(argv: list[str] | None = None, propose_fn=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-families", type=int, default=8)
    ap.add_argument("--sibling-cap", type=int, default=SIBLING_CAP_DEFAULT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent.parent / "registry_log.jsonl")
    ap.add_argument("--run-id",
                    default=datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-manual")
    ap.add_argument("--dry-run", action="store_true",
                    help="print families and specs, do not write to the registry")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    accepted = registry.cards(status="accepted")
    if not accepted:
        print("No accepted cards in the registry — run the Reader and triage first.")
        return 1

    conflicts = preflight_block_types(registry)
    if conflicts:
        for c in conflicts:
            print(f"  GRAMMAR CONFLICT: {c}")
        print("Aborting — grammar changes must be additive (new types); "
              "never mutate a chained params_schema.")
        return 1

    if propose_fn is None:
        proposals = propose_families(args.model, accepted, args.max_families)
    else:
        proposals = propose_fn(accepted)
    if len(proposals) > args.max_families:
        print(f"  NOTE: {len(proposals) - args.max_families} families beyond "
              f"--max-families {args.max_families} discarded unvalidated.")
        proposals = proposals[:args.max_families]

    import jsonschema
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas"
         / "strategy_spec.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    accepted_ids = set(accepted)
    known_fps = registered_fingerprints(registry)
    run_fps: dict[str, str] = {}
    kept, dropped, seen_names = [], 0, set()
    for fam in proposals:
        name = fam.get("family", "?")
        errors = validate_family(fam, accepted_ids, args.sibling_cap)
        if name in seen_names:
            errors.append("duplicate family name in this run")
        if errors:
            dropped += 1
            print(f"  DROPPED family {name}:")
            for e in errors:
                print(f"    - {e}")
            continue
        seen_names.add(name)
        specs = expand_family(fam, args.run_id, args.model, created_utc)
        kept_specs, drop_notes, malformed = screen_siblings(
            specs, known_fps, run_fps)
        if malformed:
            dropped += 1
            print(f"  DROPPED family {name}:")
            for note in drop_notes:
                print(f"    - {note}")
            continue

        # Split the two drop causes: a chain collision is expected saturation
        # as the grammar space fills, while a family duplicated under two
        # names in one run is a proposal-quality defect. Reporting both as
        # "already registered" would send the operator after the wrong thing.
        # Counting known_fps over `specs` cannot double-count a survivor —
        # screen_siblings guarantees no kept fingerprint is in known_fps, and
        # a repeated fingerprint would have returned malformed above.
        n_buried = sum(1 for s in specs
                       if composition_fingerprint(s) in known_fps)
        n_dupe = len(specs) - len(kept_specs) - n_buried
        dupe_txt = f", {n_dupe} duplicated in this run" if n_dupe else ""
        print(f"  family {name}: {len(specs)} expanded, {n_buried} already "
              f"registered{dupe_txt}, {len(kept_specs)} new")
        for note in drop_notes:
            print(f"    - {note}")
        if not kept_specs:
            # expand_family always yields at least one combo, so an empty
            # kept_specs here is always a genuine all-collide, never a
            # vacuous family reported as one.
            dropped += 1
            print(f"  DROPPED family {name}: every sibling already registered"
                  + (" or duplicated in this run" if n_dupe else ""))
            continue
        for spec in kept_specs:
            validator.validate(spec)   # composer bug if this raises: abort pre-write
            run_fps[composition_fingerprint(spec)] = name
        kept.append((fam, kept_specs))

    total = sum(len(s) for _, s in kept)
    for fam, specs in kept:
        print(f"family {fam['family']}: {len(specs)} sibling(s), "
              f"cites {len(fam['card_ids'])} card(s) — {fam['rationale']}")
        print(f"  regime hypothesis: {fam.get('regime_hypothesis', '(none)')}")
        if args.dry_run:
            for spec in specs:
                print(json.dumps(spec, indent=2, ensure_ascii=False))

    if args.dry_run:
        print(f"\nDRY RUN — {len(kept)} families kept, {dropped} dropped, "
              f"{total} sibling spec(s); nothing written.")
        return 0

    existing = registry.block_types()
    n_blocks = 0
    for key in BLOCK_TYPES:
        if key not in existing:
            registry.register_block_type(block_type_payload(*key))
            n_blocks += 1
    n_written = 0
    try:
        for fam, specs in kept:
            for spec in specs:
                registry.register_strategy(spec)
                n_written += 1
                print(f"  registered {spec['strategy_id']}  {spec['name']}")
    except BaseException:
        print(f"\nPARTIAL WRITE: {n_written}/{total} spec(s) chained before failure — "
              f"the last sibling group is incomplete; review the registry tail "
              f"before re-running.", file=sys.stderr)
        raise

    print(f"\n{len(kept)} families kept, {dropped} dropped, {total} spec(s) "
          f"registered in {len(kept)} sibling group(s), "
          f"{n_blocks} block type(s) newly registered.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
