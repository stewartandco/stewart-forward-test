"""Triage eval: does the live panel agree with Coen's own card judgements?

MEASUREMENT ONLY (harness audit 2026-11-08, Tier 2 #9). It never writes to the chain or
loop_state and lives outside `pipeline/`, like reextract_shadow. It replays the PRODUCTION
panel (triage_batch.review_card + panel_verdict, same prompt, same model) on the cards whose
final review is Coen's:
  - every card he rejected as claim_not_supported (overreach): the governed set;
  - a seeded random draw of the cards he accepted.
Duplicate rejects are fingerprint work, not a panel judgement, and are left out.

What is governed (D34): PRECISION. A FALSE ACCEPT is a card Coen judged to overreach that the
panel accepts unanimously; any one fails the run (Coen's bar, 2026-11-08). False rejects (his
accepts the panel rejects under D44's dissent-rejects) are reported with a Wilson 95% interval,
never gated: D34 rules out any acceptance-rate bar. The one sanity floor (Coen, 2026-11-08):
a run with more than 10% INCOMPLETE panels (replies that never parsed: a broken model or SDK)
fails, because such a run rejects everything and would otherwise "pass".

Honest scope: 69 of the 104 overreach labels reached Coen from the D31 Tier 3 queue, i.e. an
earlier panel had already dissented on them; only 35 (reviewed before D31, 2026-08-16) are
independent of any panel. Results report false accepts for both subsets.

The gate (pipeline/test_triage_eval_gate.py) is keyed on RULE_SHA, which covers the review
prompt AND the decision rule (panel_verdict, review_card, PANEL_SIZE, MAX_VOTE_ATTEMPTS), plus
the model. It requires at least MIN_RUNS complete clean runs on the pinned label set, and ANY
run on file with a false accept blocks the key for good: rerunning until one passes cannot
turn it green. The live model snapshot is watched separately by the Sentinel.

    python -m tools.eval_triage --layer <live research-layer> --dry-run    # sample + cost, no API
    python -m tools.eval_triage --layer <live research-layer>              # paid (~USD 4.32)

--layer must be the LIVE research layer even when run from a worktree. The run refuses
without its budget ledger or while a loop cycle runs. It takes no chain lock: it never writes
the chain, and an hours-long hold would stall the worker's and scanner's chain writes.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import platform
import random
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pipeline import halt as _halt
from pipeline import triage_batch as _tb
from pipeline.prompt_stamp import prompt_sha
from pipeline.triage_batch import DEFAULT_MODEL, REVIEW_PROMPT_SHA, panel_verdict

LAYER = Path(__file__).resolve().parent.parent
EVAL_DIR = LAYER / "docs" / "evals" / "triage"
SEED = 20261108
N_ACCEPTS = 150
MIN_RUNS = 2                     # Coen 2026-11-08: two clean runs per rule + model
MAX_INCOMPLETE_RATE = 0.10       # Coen 2026-11-08: the broken-model floor
D31_DATE = "2026-08-16"          # from then on, cards reached Coen only through panel dissent
USD_PER_CARD = 0.017             # measured 2026-11-08: USD 0.0056 per triage call, 3+ per panel
# Coen's labels ended 2026-09-04 (D44 made triage automatic), so the set is fixed. A change
# here means the chain changed under the eval: re-pin deliberately, never silently.
LABEL_PIN = {"n_overreach": 104, "n_accepted": 650, "digest": "e5022c5c695534b0"}
# Coen 2026-11-08: at most 3 REVISED prompt/rule versions are evaluated on these labels; each
# result records its attempt. The original production rule is the baseline, not an attempt.
# After 3 failed versions: new labels, more context for the panel, or a structural change,
# never a 4th prompt tuned on the same 104 cards.
BASELINE_RULE = "1c1877d42075dc02"
MAX_REVISED_VERSIONS = 3
# THE RATCHET (Coen 2026-11-08, after v1-v3 all failed an absolute zero-false-accept bar: two
# overreach cards are accepted by every prompt, so zero is unreachable for this panel).
# A candidate lands only if EACH of MIN_RUNS runs is no worse than the production prompt's
# measured run: false accepts <= PRODUCTION['false_accepts'] (strict; precision is what D34
# governs) and false rejects <= PRODUCTION['false_rejects'] + FR_TOLERANCE (they swing about
# 8 points between runs). When a candidate lands, PRODUCTION moves to the WORSE of its runs,
# in the same commit, so the bar only ever tightens. PRODUCTION's own rule passes the gate.
PRODUCTION = {"rule": "1c1877d42075dc02", "model": "claude-sonnet-5",
              "false_accepts": 6, "false_rejects": 96, "measured": "run 1, 2026-11-08"}
FR_TOLERANCE = 8


def rule_sha() -> str:
    """The gate key: the review prompt plus every part of the decision rule."""
    return prompt_sha(_tb.REVIEW_PROMPT_SHA, _tb.PANEL_SIZE, _tb.MAX_VOTE_ATTEMPTS,
                      inspect.getsource(_tb.panel_verdict), inspect.getsource(_tb.review_card))


def load_labels(registry) -> dict[str, tuple[dict, str, str]]:
    """{card_id: (card, "accepted"|"overreach", "independent"|"panel_flagged")} for cards whose
    LAST review is Coen's."""
    last: dict[str, tuple[dict, str]] = {}
    for e in registry.entries():
        if e["entry_type"] == "card_reviewed":
            last[e["payload"]["card_id"]] = (e["payload"], str(e.get("ts_utc") or ""))
    cards = registry.cards()
    out = {}
    for cid, (p, ts) in last.items():
        if str(p.get("reviewed_by", "")).lower() != "coen" or cid not in cards:
            continue
        origin = "independent" if ts[:10] < D31_DATE else "panel_flagged"
        if p["status"] == "accepted":
            out[cid] = (cards[cid], "accepted", origin)
        elif p.get("reject_reason") == "claim_not_supported":
            out[cid] = (cards[cid], "overreach", origin)
    return out


def label_digest(labels: dict) -> str:
    over = sorted(c for c, v in labels.items() if v[1] == "overreach")
    acc = sorted(c for c, v in labels.items() if v[1] == "accepted")
    blob = json.dumps({"overreach": over, "accepted": acc}, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def select_sample(labels: dict, n_accepts: int = N_ACCEPTS, seed: int = SEED) -> list[str]:
    overreach = sorted(c for c, v in labels.items() if v[1] == "overreach")
    accepts = sorted(c for c, v in labels.items() if v[1] == "accepted")
    drawn = random.Random(seed).sample(accepts, min(n_accepts, len(accepts)))
    return overreach + sorted(drawn)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def evaluate(sample: dict[str, tuple], review_fn, can_spend, halted) -> dict:
    """Replay the panel on each sampled card. review_fn(card) -> votes (triage_batch shape)."""
    false_accepts, false_rejects = [], []
    fa_by_origin: dict[str, list[str]] = {"independent": [], "panel_flagged": []}
    n = {"accepted": 0, "overreach": 0}
    n_over_by_origin = {"independent": 0, "panel_flagged": 0}
    bases: dict[str, int] = {}
    served: set[str] = set()
    stopped = None
    for cid, entry in sample.items():
        card, label = entry[0], entry[1]
        origin = entry[2] if len(entry) > 2 else "independent"
        if halted() is not None:
            stopped = "halted"
            break
        if not can_spend():
            stopped = "budget"
            break
        votes = review_fn(card)
        served.update(v["served_model"] for v in votes if v.get("served_model"))
        decision, basis = panel_verdict(votes)
        bases[basis] = bases.get(basis, 0) + 1
        n[label] += 1
        if label == "overreach":
            n_over_by_origin[origin] += 1
            if decision == "accepted":
                false_accepts.append(cid)
                fa_by_origin[origin].append(cid)
        elif decision == "rejected":
            false_rejects.append(cid)
    scored = sum(n.values())
    complete = stopped is None and scored == len(sample)
    incomplete_rate = bases.get("incomplete_panel", 0) / scored if scored else 0.0
    rate = len(false_rejects) / n["accepted"] if n["accepted"] else 0.0
    return {
        "complete": complete,
        "stopped": stopped,
        "pass": (complete and not false_accepts and n["overreach"] > 0
                 and incomplete_rate <= MAX_INCOMPLETE_RATE),
        "n_overreach": n["overreach"],
        "n_overreach_by_origin": n_over_by_origin,
        "n_accept": n["accepted"],
        "false_accepts": sorted(false_accepts),
        "false_accepts_by_origin": {k: sorted(v) for k, v in fa_by_origin.items()},
        "false_rejects": sorted(false_rejects),
        "false_reject_rate": round(rate, 4),
        "false_reject_rate_ci95": [round(x, 4) for x in wilson(len(false_rejects), n["accepted"])],
        "incomplete_panel_rate": round(incomplete_rate, 4),
        "bases": bases,
        "served_models": sorted(served),
    }


class EvalMeter:
    """The pipeline meter, but eval spend is booked as purpose 'triage-eval' and ALWAYS under
    agent 'pipeline' (an agent-less 'triage-eval' row would be invisible to the cap)."""

    def __init__(self, meter):
        self._m = meter

    def can_spend(self) -> bool:
        return self._m.can_spend()

    def record_call(self, model, usage, purpose, agent=None):
        return self._m.record_call(model, usage, "triage-eval", agent="pipeline")


def _live_review(model: str, layer: Path):
    """(review_fn, meter) on the real client, the reader key and the LIVE ledger."""
    import anthropic

    from pipeline.budget import BudgetMeter, PIPELINE_CAP_USD
    from pipeline.scanner import DEFAULT_READER_ENV, _load_api_key
    from pipeline.triage_batch import review_card

    _load_api_key(DEFAULT_READER_ENV)
    client = anthropic.Anthropic()
    meter = EvalMeter(BudgetMeter(layer / "logs" / "budget_ledger.jsonl",
                                  monthly_cap_usd=PIPELINE_CAP_USD, agent="pipeline"))
    return (lambda card: review_card(client, model, card, meter)), meter


def _safe(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


def write_result(doc: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = out_dir / f"{doc['rule_sha']}__{_safe(doc['model'])}__{stamp}.json"
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), prefix=".eval-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def qualifies(d: dict) -> bool:
    """One run meets the ratchet: a complete run on the pinned labels, no worse than PRODUCTION."""
    return (d.get("complete") is True
            and d.get("label_digest") == LABEL_PIN["digest"]
            and d.get("n_overreach") == LABEL_PIN["n_overreach"]
            and int(d.get("n_accept") or 0) >= N_ACCEPTS
            and float(d.get("incomplete_panel_rate", 1.0)) <= MAX_INCOMPLETE_RATE
            and len(d.get("false_accepts") or []) <= PRODUCTION["false_accepts"]
            and len(d.get("false_rejects") or []) <= PRODUCTION["false_rejects"] + FR_TOLERANCE)


def _clean(d: dict, rule: str, model: str) -> bool:
    return d.get("rule_sha") == rule and d.get("model") == model and qualifies(d)


def attempt_number(out_dir: Path, rule: str) -> int:
    """Which revised version `rule` is (1-based), counting distinct rule keys already on file
    in order of their first result; 0 for the baseline."""
    if rule == BASELINE_RULE:
        return 0
    first: dict[str, str] = {}
    for f in sorted(Path(out_dir).glob("*__*__*.json")) if Path(out_dir).exists() else []:
        key, _model, stamp = f.stem.split("__", 2)
        if key != BASELINE_RULE:
            first[key] = min(first.get(key, stamp), stamp)
    order = sorted(first, key=lambda k: first[k])
    return order.index(rule) + 1 if rule in order else len(order) + 1


def gate_status(out_dir: Path, rule: str, model: str) -> tuple[bool, str]:
    """(ok, why): ok for the PRODUCTION rule itself; otherwise only with MIN_RUNS runs for this rule +
    model that meet the ratchet, and NO run on file for it that does not (no cherry-picking)."""
    hint = ("run twice: python -m tools.eval_triage --layer <live research-layer>, then commit "
            "the results under docs/evals/triage/")
    if rule == PRODUCTION["rule"] and model == PRODUCTION["model"]:
        return True, f"production baseline ({PRODUCTION['measured']})"
    out_dir = Path(out_dir)
    files = sorted(out_dir.glob(f"{rule}__{_safe(model)}__*.json")) if out_dir.exists() else []
    docs = []
    for f in files:
        try:
            docs.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            return False, f"{f.name}: unreadable result; {hint}"
    bad = [name for name, d in docs if d.get("complete") is True and not qualifies(d)]
    if bad:
        return False, (f"a run for rule {rule} on {model} is worse than production ({', '.join(bad)}): "
                       "this prompt/rule is blocked for good; change it and evaluate the new one")
    clean = [name for name, d in docs if _clean(d, rule, model)]
    if len(clean) < MIN_RUNS:
        return False, (f"{len(clean)} of {MIN_RUNS} required clean runs for rule {rule} on {model} "
                       f"(files: {len(docs)}); {hint}")
    return True, ", ".join(clean)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--layer", type=Path, required=True,
                    help="the LIVE research layer (its logs/ holds the budget ledger)")
    ap.add_argument("--registry", type=Path, default=None, help="default: <layer>/registry_log.jsonl")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--n-accepts", type=int, default=N_ACCEPTS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out-dir", type=Path, default=EVAL_DIR)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    from pipeline.registry import Registry
    labels = load_labels(Registry(a.registry or a.layer / "registry_log.jsonl"))
    digest = label_digest(labels)
    ids = select_sample(labels, a.n_accepts, a.seed)
    sample = {cid: labels[cid] for cid in ids}
    n_over = sum(1 for v in sample.values() if v[1] == "overreach")
    pinned = digest == LABEL_PIN["digest"]
    print(f"triage eval: {n_over} overreach + {len(sample) - n_over} accept cards "
          f"(of {len(labels)} Coen-labelled; label digest {digest}"
          f"{'' if pinned else ' DOES NOT MATCH the pin ' + LABEL_PIN['digest']}), seed {a.seed}, "
          f"model {a.model}, rule {rule_sha()}; about USD {len(sample) * USD_PER_CARD:.2f}")
    if a.dry_run:
        print("dry run: no API call, nothing written")
        return 0
    if not pinned:
        print("REFUSED: the labelled set differs from LABEL_PIN; re-pin it deliberately first")
        return 2
    attempt = attempt_number(a.out_dir, rule_sha())
    if attempt > MAX_REVISED_VERSIONS:
        print(f"REFUSED: this would be revised version {attempt}; at most {MAX_REVISED_VERSIONS} are "
              "evaluated on these labels (Coen 2026-11-08). Rethink: new labels, more panel context, "
              "or a structural change.")
        return 2

    logs = a.layer / "logs"
    ledger = logs / "budget_ledger.jsonl"
    if not ledger.exists():
        print(f"REFUSED: no ledger at {ledger} - is --layer the live research-layer? "
              "An absent ledger is never 'zero spend this month'.")
        return 2
    # No chain.lock: the eval never writes the chain, and holding the lock for an hours-long
    # run would defer the gauntlet worker's and the scanner's chain writes all that time.
    from tools.reextract_shadow import ensure_no_cycle_running
    try:
        ensure_no_cycle_running(logs)
    except RuntimeError as exc:
        print(f"REFUSED: {exc}")
        return 2
    review_fn, meter = _live_review(a.model, a.layer)
    out = evaluate(sample, review_fn, can_spend=meter.can_spend,
                   halted=lambda: _halt.halt_reason(logs))
    import anthropic
    doc = {"rule_sha": rule_sha(), "prompt_sha": _tb.REVIEW_PROMPT_SHA, "model": a.model, "seed": a.seed,
           "attempt": attempt,
           "n_accepts_requested": a.n_accepts, "labelled_total": len(labels), "label_digest": digest,
           "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "sdk": f"anthropic {anthropic.__version__}", "python": platform.python_version(),
           "production": dict(PRODUCTION), "fr_tolerance": FR_TOLERANCE, **out}
    doc["qualifies"] = qualifies(doc)
    path = write_result(doc, a.out_dir)
    fa = out["false_accepts_by_origin"]
    print(f"{'QUALIFIES' if doc['qualifies'] else 'WORSE THAN PRODUCTION'} (production "
          f"{PRODUCTION['false_accepts']} FA / {PRODUCTION['false_rejects']} FR, +{FR_TOLERANCE} FR tolerance); "
          f"absolute bar {'PASS' if out['pass'] else 'FAIL'}: false accepts {len(out['false_accepts'])} of "
          f"{out['n_overreach']} overreach (independent {len(fa['independent'])} of "
          f"{out['n_overreach_by_origin']['independent']}, panel-flagged {len(fa['panel_flagged'])} of "
          f"{out['n_overreach_by_origin']['panel_flagged']}); false rejects {len(out['false_rejects'])} "
          f"of {out['n_accept']} ({out['false_reject_rate']:.1%}, 95% CI "
          f"{out['false_reject_rate_ci95'][0]:.1%}-{out['false_reject_rate_ci95'][1]:.1%}); incomplete "
          f"panels {out['incomplete_panel_rate']:.1%}; stopped={out['stopped']}; -> {path}")
    return 0 if doc["qualifies"] else 1


if __name__ == "__main__":
    sys.exit(run())
