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
never gated: a lost card is a cost, a false accept is the one failure no downstream gate catches.

The result is written to docs/evals/triage/<prompt_sha>__<model>__<utc>.json and committed.
pipeline/test_triage_eval_gate.py fails unless a complete, passing result exists for the
CURRENT triage_batch.REVIEW_PROMPT_SHA and DEFAULT_MODEL, so a prompt or model change cannot
land without a fresh eval.

    python -m tools.eval_triage --layer <live research-layer> --dry-run    # sample + cost, no API
    python -m tools.eval_triage --layer <live research-layer>              # paid (~USD 4)

--layer must be the LIVE research layer even when run from a worktree: the budget ledger it
meters against lives in <layer>/logs, and a worktree has none (reextract_shadow's lesson).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pipeline import halt as _halt
from pipeline.triage_batch import DEFAULT_MODEL, REVIEW_PROMPT_SHA, panel_verdict

LAYER = Path(__file__).resolve().parent.parent
EVAL_DIR = LAYER / "docs" / "evals" / "triage"
SEED = 20261108
N_ACCEPTS = 150
USD_PER_CARD = 0.017      # measured 2026-11-08: USD 0.0056 per triage call, 3+ calls per panel


def load_labels(registry) -> dict[str, tuple[dict, str]]:
    """{card_id: (card, "accepted"|"overreach")} for cards whose LAST review is Coen's."""
    last: dict[str, dict] = {}
    for e in registry.entries():
        if e["entry_type"] == "card_reviewed":
            last[e["payload"]["card_id"]] = e["payload"]
    cards = registry.cards()
    out = {}
    for cid, p in last.items():
        if str(p.get("reviewed_by", "")).lower() != "coen" or cid not in cards:
            continue
        if p["status"] == "accepted":
            out[cid] = (cards[cid], "accepted")
        elif p.get("reject_reason") == "claim_not_supported":
            out[cid] = (cards[cid], "overreach")
    return out


def select_sample(labels: dict, n_accepts: int = N_ACCEPTS, seed: int = SEED) -> list[str]:
    overreach = sorted(c for c, (_, lab) in labels.items() if lab == "overreach")
    accepts = sorted(c for c, (_, lab) in labels.items() if lab == "accepted")
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


def evaluate(sample: dict[str, tuple[dict, str]], review_fn, can_spend, halted) -> dict:
    """Replay the panel on each sampled card. review_fn(card) -> votes (triage_batch shape)."""
    false_accepts, false_rejects = [], []
    n = {"accepted": 0, "overreach": 0}
    bases: dict[str, int] = {}
    served: set[str] = set()
    stopped = None
    for cid, (card, label) in sample.items():
        reason = halted()
        if reason is not None:
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
        if label == "overreach" and decision == "accepted":
            false_accepts.append(cid)
        elif label == "accepted" and decision == "rejected":
            false_rejects.append(cid)
    complete = stopped is None and sum(n.values()) == len(sample)
    rate = len(false_rejects) / n["accepted"] if n["accepted"] else 0.0
    return {
        "complete": complete,
        "stopped": stopped,
        "pass": complete and not false_accepts and n["overreach"] > 0,
        "n_overreach": n["overreach"],
        "n_accept": n["accepted"],
        "false_accepts": sorted(false_accepts),
        "false_rejects": sorted(false_rejects),
        "false_reject_rate": round(rate, 4),
        "false_reject_rate_ci95": [round(x, 4) for x in wilson(len(false_rejects), n["accepted"])],
        "bases": bases,
        "served_models": sorted(served),
    }


class EvalMeter:
    """The pipeline meter, but eval spend is booked as purpose 'triage-eval' (review_card
    hardcodes 'triage'), so it never reads as production triage in the ledger."""

    def __init__(self, meter):
        self._m = meter

    def can_spend(self) -> bool:
        return self._m.can_spend()

    def record_call(self, model, usage, purpose, agent=None):
        return self._m.record_call(model, usage, "triage-eval", agent=agent)


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


def write_result(doc: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{doc['prompt_sha']}__{doc['model']}__{stamp}.json"
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), prefix=".eval-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path


def gate_status(out_dir: Path, prompt_sha: str, model: str) -> tuple[bool, str]:
    """(ok, why): ok only if a committed result for THIS prompt and model is a complete, clean run."""
    hint = ("run: python -m tools.eval_triage --layer <live research-layer>, then commit "
            "the result under docs/evals/triage/")
    files = sorted(Path(out_dir).glob(f"{prompt_sha}__{model}__*.json")) if Path(out_dir).exists() else []
    if not files:
        return False, f"no triage eval for prompt {prompt_sha} on {model}; {hint}"
    reasons = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reasons.append(f"{f.name}: unreadable")
            continue
        if (d.get("prompt_sha") == prompt_sha and d.get("model") == model and d.get("complete") is True
                and d.get("pass") is True and d.get("false_accepts") == [] and int(d.get("n_overreach") or 0) > 0):
            return True, f.name
        reasons.append(f"{f.name}: not a complete clean run (pass={d.get('pass')}, "
                       f"complete={d.get('complete')}, false_accepts={d.get('false_accepts')})")
    return False, "; ".join(reasons) + f"; {hint}"


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
    ids = select_sample(labels, a.n_accepts, a.seed)
    sample = {cid: labels[cid] for cid in ids}
    n_over = sum(1 for _, lab in sample.values() if lab == "overreach")
    print(f"triage eval: {n_over} overreach + {len(sample) - n_over} accept cards "
          f"(of {len(labels)} Coen-labelled), seed {a.seed}, model {a.model}, prompt {REVIEW_PROMPT_SHA}; "
          f"about USD {len(sample) * USD_PER_CARD:.2f}")
    if a.dry_run:
        print("dry run: no API call, nothing written")
        return 0

    review_fn, meter = _live_review(a.model, a.layer)
    out = evaluate(sample, review_fn, can_spend=meter.can_spend,
                   halted=lambda: _halt.halt_reason(a.layer / "logs"))
    import anthropic
    doc = {"prompt_sha": REVIEW_PROMPT_SHA, "model": a.model, "seed": a.seed, "n_accepts_requested": a.n_accepts,
           "labelled_total": len(labels), "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "sdk": f"anthropic {anthropic.__version__}", "python": platform.python_version(), **out}
    path = write_result(doc, a.out_dir)
    print(f"{'PASS' if out['pass'] else 'FAIL'}: false accepts {len(out['false_accepts'])} of {out['n_overreach']} "
          f"overreach; false rejects {len(out['false_rejects'])} of {out['n_accept']} accepts "
          f"({out['false_reject_rate']:.1%}, 95% CI {out['false_reject_rate_ci95'][0]:.1%}-"
          f"{out['false_reject_rate_ci95'][1]:.1%}); stopped={out['stopped']}; -> {path}")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(run())
