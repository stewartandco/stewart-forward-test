"""Rebuild registry_log.example.jsonl — a small, coherent worked example.

Run from research-layer/:  python examples/regenerate_example.py
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.registry import Registry
from pipeline.reader import build_card

SOURCE = {
    "type": "book", "title": "Advances in Financial Machine Learning",
    "authors": ["M. López de Prado"], "year": 2018, "url": None, "doi": None,
    "isbn": "978-1119482086", "credibility_tier": "practitioner",
}

CARD_A = {
    "claim": "Narrow opening ranges on index futures show positive follow-through intraday.",
    "quote": "breakouts from compressed opening ranges exhibit significant follow-through",
    "locator": "ch. 3, sec 4.2",
    "asset_classes": ["futures"], "topics": ["orb", "momentum"], "horizon": "intraday",
    "testability_score": 0.9, "data_required": ["MNQ 15m bars"], "notes": None,
}

CARD_B = {
    "claim": "Commercial real-estate cap-rate spreads predict regional REIT returns.",
    "quote": "cap-rate spreads over treasuries compress before regional price appreciation",
    "locator": "appendix B",
    "asset_classes": ["equities"], "topics": ["real_estate", "macro"], "horizon": "multi_month",
    "testability_score": 0.3, "data_required": ["regional cap-rate series"], "notes": "off-topic for the futures program",
}

BLOCK_TYPES = [
    {"role": "entry", "type": "orb_breakout",
     "params_schema": {"window_min": {"type": "int", "grid": [15]}}},
    {"role": "stop", "type": "structure", "params_schema": {}},
    {"role": "risk", "type": "fixed_contracts",
     "params_schema": {"n": {"type": "int", "grid": [1]}}},
]


def make_spec(card_ids):
    from pipeline.common import content_id
    spec = {
        "strategy_id": None, "version": 1, "created_utc": "2026-08-06T04:00:00Z",
        "name": "MNQ ORB 15m structure-stop 1R", "family": "orb_breakout",
        "universe": {"assets": ["MNQ"], "asset_class": "futures",
                     "timeframe": "15m", "session": "RTH"},
        "blocks": [
            {"role": "entry", "type": "orb_breakout", "params": {"window_min": 15}},
            {"role": "stop", "type": "structure", "params": {}},
            {"role": "risk", "type": "fixed_contracts", "params": {"n": 1}},
        ],
        "provenance": {"card_ids": card_ids, "parent_strategy_id": None,
                       "sibling_group_id": "orb-mnq-example", "generation": 0},
        "generator": {"agent": "composer", "model": "claude-opus-5",
                      "pipeline_version": "g1.0.0", "run_id": "example"},
        "cost_model": {"commission_per_side": 0.62, "slippage_ticks": 1},
    }
    spec["strategy_id"] = content_id(spec, "strategy_id")
    return spec


def main():
    out = Path(__file__).resolve().parent / "registry_log.example.jsonl"
    out.unlink(missing_ok=True)
    reg = Registry(out)
    for bt in BLOCK_TYPES:
        reg.register_block_type(bt)
    card_a = build_card(CARD_A, SOURCE, "claude-opus-5", "example")
    card_b = build_card(CARD_B, SOURCE, "claude-opus-5", "example")
    reg.register_card(card_a)
    reg.register_card(card_b)
    reg.review_card(card_a["card_id"], "accepted", "coen")
    reg.review_card(card_b["card_id"], "rejected", "coen", reject_reason="off_topic")
    spec = make_spec([card_a["card_id"]])
    reg.register_strategy(spec)
    reg.record_state_change(spec["strategy_id"], "screened", "compute scheduled")
    reg.record_verdict(spec["strategy_id"], "screened", "pass",
                       {"trades": 180, "net_pnl": 2400.0, "win_rate": 0.52, "max_dd": -900.0},
                       "0" * 64)
    reg.record_state_change(spec["strategy_id"], "gauntlet", "screen passed")
    print(f"rebuilt {out} ({sum(1 for _ in reg.entries())} entries)")


if __name__ == "__main__":
    main()
