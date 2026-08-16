"""Audit protocol-v2's gates against the generation-1 graveyard.

Usage:
    python diagnose_protocol_v2.py [--registry registry_log.jsonl]
                                   [--data-dir data] [--cutoff 2023-12-31]

Reports what the already-buried gen-1 strategies WOULD have scored under
gauntlet-protocol-v2. This audits the gate, not the strategies: those
verdicts are final and `graveyard` is terminal.

This script has NO registry write path by construction — it cannot chain
anything, and there is deliberately no flag that would let it try. Run it
only AFTER the v2 note is chained; computing these numbers while the rule is
still being chosen would make it impossible to prove the rule was not tuned
until something survived.

Per the ratchet in the v2 note: this diagnostic may only TIGHTEN protocol-v2,
never loosen it, and any change requires a fresh pre-declared note.

This script is PINNED to protocol-v2 and does not track the live gauntlet.
protocol-v3 retired the deflated-Sharpe gate from `gauntlet.evaluate_spec`, so
the sixth gate is re-applied locally here, in v2's position, against a local
copy of v2's threshold. A frozen audit must keep reporting what v2 actually
said even after the live protocol moves on.
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path

from pipeline.registry import Registry
from pipeline.engine import run_spec
from pipeline.stats import sharpe
from pipeline.screen import load_bars
from pipeline.gauntlet import (split_trades, evaluate_spec,
                               window_vol, daily_returns_from_curve, stressed)

# Pinned deliberately: NOT imported from pipeline.gauntlet. This audit is
# frozen at the protocol it names, so it must not drift when the live protocol
# moves. DSR_MIN_V2 is likewise a local literal, so retuning the live DSR_MIN
# cannot retroactively change what this historical audit reports.
PROTOCOL = "gauntlet-protocol-v2"
DSR_MIN_V2 = 0.95


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", type=Path,
                    default=Path(__file__).resolve().parent / "registry_log.jsonl")
    ap.add_argument("--data-dir", type=Path,
                    default=Path(__file__).resolve().parent / "data")
    ap.add_argument("--cutoff", default="2023-12-31")
    args = ap.parse_args(argv)

    registry = Registry(args.registry)
    states = registry.strategy_states()
    all_specs = [e["payload"] for e in registry.entries()
                 if e["entry_type"] == "strategy_registered"]
    buried = [s for s in all_specs
              if states.get(s["strategy_id"]) == "graveyard"]

    print(f"PROTOCOL-V2 DIAGNOSTIC — {PROTOCOL}")
    print(f"registry: {args.registry}   cutoff: {args.cutoff}")
    print(f"{len(buried)} graveyarded strateg(ies) of {len(all_specs)} registered")
    print("These verdicts are FINAL. This audits the gate, not the specs.\n")
    if not buried:
        print("nothing buried; nothing to audit.")
        return 0

    assets = sorted({a for s in all_specs for a in s["universe"]["assets"]})
    bars = {a: load_bars(args.data_dir, a, "9999-12-31") for a in assets}

    results, srs = {}, []
    for s in all_specs:
        res = run_spec(s, {a: bars[a] for a in s["universe"]["assets"]})
        results[s["strategy_id"]] = res
        srs.append(sharpe(daily_returns_from_curve(res["equity"])))
    trials_n = len(srs)
    mean_sr = sum(srs) / len(srs)
    trials_var = (sum((x - mean_sr) ** 2 for x in srs) / (len(srs) - 1)
                  if len(srs) > 1 else 0.0)

    n_pass = 0
    for s in buried:
        sid = s["strategy_id"]
        res = results[sid]
        stress_res = run_spec(stressed(s),
                              {a: bars[a] for a in s["universe"]["assets"]})
        is_t, oos_t = split_trades(res["trades"], args.cutoff)
        _, stress_oos = split_trades(stress_res["trades"], args.cutoff)
        univ = s["universe"]["assets"]
        passed, reason, m, _ = evaluate_spec(
            is_t, oos_t, stress_oos, daily_returns_from_curve(res["equity"]),
            window_vol(bars, univ, "", args.cutoff),
            window_vol(bars, univ, args.cutoff, "9999-12-31"),
            trials_n, trials_var, seed=int(sid, 16) % (2 ** 31))
        # protocol-v2 had a sixth gate that gauntlet.evaluate_spec no longer
        # applies. Re-apply it here, in v2's position (fifth, before
        # cost_stress), so this script audits the gates it names. Reaching v2's
        # dsr check means passing the four gates ahead of it, i.e. the current
        # reason is None or the later cost_stress.
        if not m["deflated_sharpe"] >= DSR_MIN_V2 and reason in (None,
                                                                 "cost_stress"):
            passed, reason = False, "dsr"
        n_pass += bool(passed)
        d = m["edge_decay_pct"]
        print(f"{sid}  {'WOULD PASS' if passed else 'still fails':<11} "
              f"decay_norm={'n/a' if d is None else f'{d:+.1f}%'}  "
              f"dsr={m['deflated_sharpe']:.3f}  trials_n={m['trials_n']}"
              + (f"  [{reason}]" if reason else ""))

    print(f"\n{n_pass}/{len(buried)} would pass under {PROTOCOL}.")
    print("Ratchet: this result may TIGHTEN the protocol, never loosen it.")
    if n_pass > len(buried) // 2:
        print("WARNING: a majority would now pass — the gate may have lost "
              "its teeth. Tighten before running generation 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
