"""D15 exit-rules-v7 re-trial classification (DRY RUN ONLY).

Classifies every registered family on the chain into the compositions that
are v7-compliant AS REGISTERED (no retired block type and an entry that never
had the engine's implicit crossunder exit) and the compositions that need a
version-2 re-trial in the unified re-run (design
docs/2026-09-03-exit-rules-v7-design.md s6, decision D15(b)).

Usage:
    python tools_retrial_families_v7.py --dry-run \
        [--registry registry_log.jsonl] \
        [--out docs/runs/2026-09-03-exit-rules-v7-retrial-plan.md]

Reads the chain and writes ONE markdown report; nothing else is written and
the chain is never touched. `--fire` is refused: firing the re-trial is a
chain write inside a pipeline cycle and is Coen-gated (design s6). No model
call happens anywhere in this tool.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections import Counter
from pathlib import Path

from pipeline.blocks import RETIRED_TYPES
from pipeline.composer import v7_compliant_as_is
from pipeline.registry import Registry

IMPLICIT_EXIT_REASON = "implicit ma_cross exit"
FIRE_REFUSAL = "firing is Coen-gated; see docs/2026-09-03-exit-rules-v7-design.md §6"


def retrial_reasons(spec: dict) -> list[str]:
    """Why a legacy spec needs a v2 re-trial: each retired type it carries,
    plus the implicit crossunder exit for ma_cross* entries. Empty list means
    compliant as registered (the same test v7_compliant_as_is applies)."""
    reasons = []
    for b in spec.get("blocks", []):
        key = (b.get("role"), b.get("type"))
        if key in RETIRED_TYPES:
            reasons.append(f"{key[0]}/{key[1]}")
    entry = next((b for b in spec.get("blocks", []) if b.get("role") == "entry"), None)
    if entry is not None and str(entry.get("type", "")).startswith("ma_cross"):
        reasons.append(IMPLICIT_EXIT_REASON)
    return reasons


def classify(entries) -> dict[str, dict]:
    """{family: {"compliant": [sid...], "retrial": [sid...],
                 "reasons": {sid: [reason...]}, "states": {sid: state}}}
    over every strategy_registered entry, in chain order. Pure: reads, never
    writes. A version-2 registration (none exist before the note is chained)
    is compliant by construction and is listed as such."""
    out: dict[str, dict] = {}
    states: dict[str, str] = {}
    regs: list[dict] = []
    for e in entries:
        t = e.get("entry_type")
        p = e.get("payload", {})
        if t == "strategy_registered":
            regs.append(p)
            states[p["strategy_id"]] = "registered"
        elif t == "state_change" and p.get("strategy_id") in states:
            states[p["strategy_id"]] = p.get("to", states[p["strategy_id"]])
    for p in regs:
        fam = p.get("family", "?")
        sid = p["strategy_id"]
        g = out.setdefault(fam, {"compliant": [], "retrial": [], "reasons": {}, "states": {}})
        g["states"][sid] = states.get(sid, "registered")
        if p.get("version", 1) != 1 or v7_compliant_as_is(p):
            g["compliant"].append(sid)
        else:
            g["retrial"].append(sid)
            g["reasons"][sid] = retrial_reasons(p)
    return out


def render_report(classes: dict[str, dict], registry_path: str, generated_utc: str) -> str:
    n_reg = sum(len(g["compliant"]) + len(g["retrial"]) for g in classes.values())
    n_ok = sum(len(g["compliant"]) for g in classes.values())
    n_re = sum(len(g["retrial"]) for g in classes.values())
    by_reason: Counter = Counter()
    for g in classes.values():
        for rs in g["reasons"].values():
            by_reason.update(rs)
    lines = [
        "# exit-rules-v7 re-trial plan (DRY RUN)",
        "",
        f"Generated {generated_utc} from `{registry_path}` (read-only). Decision D15(b): everything is "
        "re-trialled under the version-2 grammar inside the unified re-run; a legacy registration whose "
        "engine behaviour is UNCHANGED under v7 (no retired block type, entry not `ma_cross*`) is compliant "
        "as registered and is NOT re-registered.",
        "",
        "## Totals",
        "",
        f"- registrations: {n_reg}",
        f"- compliant as registered: {n_ok}",
        f"- needs version-2 re-trial: {n_re}",
        "- re-trial reasons (a registration may carry more than one):",
    ]
    for reason, n in by_reason.most_common():
        lines.append(f"  - {reason}: {n}")
    lines += [
        "",
        "## Per family",
        "",
        "| family | compliant | re-trial | re-trial lifecycle | top reasons |",
        "|---|---|---|---|---|",
    ]
    for fam in sorted(classes):
        g = classes[fam]
        life: Counter = Counter(g["states"][s] for s in g["retrial"])
        life_s = " · ".join(f"{k} {v}" for k, v in sorted(life.items())) or "—"
        rc: Counter = Counter()
        for rs in g["reasons"].values():
            rc.update(rs)
        top = ", ".join(f"{r} ({n})" for r, n in rc.most_common(3)) or "—"
        lines.append(f"| {fam} | {len(g['compliant'])} | {len(g['retrial'])} | {life_s} | {top} |")
    lines += [
        "",
        "Firing the re-trial (the Composer re-declaring each family's exit set and registering version-2 "
        "specs as D9 re-trials) is Coen-gated and is NOT part of this report. No chain write happened.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default="registry_log.jsonl")
    ap.add_argument("--out", default="docs/runs/2026-09-03-exit-rules-v7-retrial-plan.md")
    ap.add_argument("--dry-run", action="store_true", help="classify and write the report (the only mode)")
    ap.add_argument("--fire", action="store_true", help="refused: Coen-gated")
    args = ap.parse_args(argv)
    if args.fire:
        raise SystemExit(FIRE_REFUSAL)
    if not args.dry_run:
        ap.error("this tool only runs with --dry-run (firing is Coen-gated)")
    reg = Registry(args.registry)
    classes = classify(reg.entries())
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = render_report(classes, args.registry, now)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    n_reg = sum(len(g["compliant"]) + len(g["retrial"]) for g in classes.values())
    n_ok = sum(len(g["compliant"]) for g in classes.values())
    print(f"registrations {n_reg} | compliant as registered {n_ok} | needs re-trial {n_reg - n_ok} "
          f"| families {len(classes)} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
