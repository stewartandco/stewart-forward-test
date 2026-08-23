"""Stage-1 relevance screen (D23 throughput-first funnel).

Cheap Sonnet-class call on title/summary batches against STRICT intake
parameters. Only passes proceed to full fetch + extraction. Every decision is
logged with a one-line reason (auditable funnel); the budget meter is checked
BEFORE any call and charged after.

D27 case 3 adds `screen_source`: one metered Sonnet call judging a whole
source (titles + about text) for probation admission, reusing this module's
budget/logging/fatal-error conventions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

BATCH_SIZE = 20
SCREEN_MAX_TOKENS = 4000

# Errors that will not fix themselves on the next batch: retrying them is the
# 2026-08-15 runaway (105k logged decisions in 2h on an empty credit balance).
FATAL_API_MARKERS = ("credit balance is too low", "billing", "quota",
                     "authentication_error", "invalid x-api-key",
                     "permission_error")


class ApiCreditExhausted(RuntimeError):
    """Billing/credential failure: abort the run, do not retry."""

INTAKE_PARAMETERS = """\
You are the intake screen for Stewart & Co.'s quantitative research pipeline.
You see item titles and summaries from a verified source watchlist and decide,
for each, whether it earns a full extraction into research cards. Extraction
is expensive and the corpus is already large, so the bar is HIGH: keep only
items that look like they carry a specific, backtestable finding.

KEEP requires all three:
  1. A specific claimed relationship or effect (not a topic, not a question,
     not general commentary) about signals/edges, portfolio construction,
     execution, risk, microstructure, or regime detection.
  2. Enough specificity that a rule could be written from it - an instrument
     or asset class, a direction, a horizon, or a stated condition.
  3. Some indication of evidence or method: data, a backtest, a study, a
     model, or quantified results.

KILL (one-line reason) anything else, including: real-estate content;
alpha-free blogspam or listicles; product, course, tool or service marketing;
macro or market news without a stated mechanism; market recaps and daily
commentary; opinion, philosophy or career pieces; educational explainers of
well-known concepts; link roundups and newsletters that only summarise other
posts; book reviews; podcast, webinar, conference or event announcements; job
posts; and software-release housekeeping.

Also rate testability 0-1: how directly the item's apparent claim could
become a backtest rule (1.0 = explicit rule with named data and results;
0.5 = a concrete claim needing interpretation; 0.2 = vague or qualitative).
Items below 0.5 are logged but not extracted, so rate honestly rather than
generously.

Be strict: when a title/summary gives no evidence of a specific testable
finding, kill it. Never invent content that is not in the title or summary.
Return one decision per item id, using exactly the ids given."""

# Keeps below this testability are recorded but never fetched/extracted.
EXTRACT_MIN_TESTABILITY = 0.5

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "keep": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "testability": {"type": "number"},
                },
                "required": ["id", "keep", "reason", "testability"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


def build_screen_prompt(items: list[dict]) -> str:
    lines = ["Screen these items:\n"]
    for it in items:
        lines.append(f"id: {it['item_id']}")
        lines.append(f"source: {it['source_id']}")
        lines.append(f"title: {it['title']}")
        lines.append(f"summary: {it.get('summary') or '(none)'}")
        lines.append("")
    return "\n".join(lines)


def parse_screen_response(items: list[dict], data: dict) -> dict[str, tuple[str, str]]:
    """id -> (status, reason). Items the model failed to decide are deferred
    (they will be re-screened next cycle), never silently dropped or kept."""
    known = {it["item_id"] for it in items}
    out: dict[str, tuple[str, str]] = {}
    for d in data.get("decisions", []):
        if d["id"] not in known:
            continue
        if not d["keep"]:
            out[d["id"]] = ("screen_kill", d["reason"])
            continue
        # Missing score = pass (older/looser responses stay backwards compatible)
        score = d.get("testability")
        score = EXTRACT_MIN_TESTABILITY if score is None else float(score)
        status = ("screen_keep" if score >= EXTRACT_MIN_TESTABILITY
                  else "screen_keep_low")
        reason = d["reason"] if status == "screen_keep" else \
            f"{d['reason']} (testability {score:.2f} < {EXTRACT_MIN_TESTABILITY})"
        out[d["id"]] = (status, reason)
    for it in items:
        if it["item_id"] not in out:
            out[it["item_id"]] = ("deferred_screen", "screen_missing")
    return out


def _log_decisions(log_path: Path, model: str, decisions: dict[str, tuple[str, str]]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with log_path.open("a", encoding="utf-8") as f:
        for iid, (status, reason) in decisions.items():
            f.write(json.dumps({"ts_utc": ts, "item_id": iid, "model": model,
                                "decision": status, "reason": reason},
                               ensure_ascii=False) + "\n")


def screen_items(client, model: str, items: list[dict], meter,
                 log_path: str | Path,
                 batch_size: int = BATCH_SIZE) -> dict[str, tuple[str, str]]:
    log_path = Path(log_path)
    out: dict[str, tuple[str, str]] = {}
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        if not meter.can_spend():
            decisions = {it["item_id"]: ("deferred_budget", "monthly cap reached")
                         for it in batch}
            out.update(decisions)
            _log_decisions(log_path, model, decisions)
            continue
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=SCREEN_MAX_TOKENS,
                system=INTAKE_PARAMETERS,
                output_config={"format": {"type": "json_schema",
                                          "schema": SCREEN_SCHEMA}},
                messages=[{"role": "user", "content": build_screen_prompt(batch)}],
            )
        except Exception as exc:
            message = str(exc).lower()
            decisions = {it["item_id"]: ("deferred_screen", f"api_error: {exc}"[:200])
                         for it in batch}
            out.update(decisions)
            _log_decisions(log_path, model, decisions)
            if any(marker in message for marker in FATAL_API_MARKERS):
                print(f"  FATAL api error, aborting run: {exc}", file=sys.stderr)
                raise ApiCreditExhausted(str(exc)) from exc
            print(f"  screen call failed: {exc}", file=sys.stderr)
            continue
        meter.record_call(model, msg.usage, purpose="screen",
                          agent="reader")
        if msg.stop_reason == "refusal":
            decisions = {it["item_id"]: ("deferred_screen", "refusal") for it in batch}
        else:
            text = next(b.text for b in msg.content if b.type == "text")
            decisions = parse_screen_response(batch, json.loads(text))
        out.update(decisions)
        _log_decisions(log_path, model, decisions)
    return out


# ---------------- D27 case 3: source-level screen ----------------------------

SOURCE_SCREEN_SYSTEM = INTAKE_PARAMETERS + """

SOURCE MODE. You are now judging a whole SOURCE, not items. You see its domain,
its 10 most recent item titles, and the start of its landing/about text. Decide
whether a recurring reader of this source would expect testable trading /
portfolio-construction / execution / risk / market-microstructure / regime
research that would pass the item bar above at least occasionally. Macro or
market commentary without mechanisms, politics, gold-bug or doom sites, product
or course marketing, news recaps, and general finance journalism are NOT research
sources. Return research_source true/false, a one-line reason, and the asset
classes the source mostly covers. Ignore the per-item output instructions
above; the schema you are given is the only output."""

SOURCE_SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "research_source": {"type": "boolean"},
        "reason": {"type": "string"},
        "asset_classes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["research_source", "reason", "asset_classes"],
    "additionalProperties": False,
}
SOURCE_SCREEN_MAX_TOKENS = 400


def build_source_screen_prompt(domain: str, titles: list[str], about: str) -> str:
    lines = [f"Source domain: {domain}", "", "Recent item titles:"]
    lines += [f"- {t}" for t in titles[:10]] or ["- (none found)"]
    lines += ["", "Landing/about text (truncated):", about[:300] or "(none)"]
    return "\n".join(lines)


def parse_source_screen(data: dict) -> dict | None:
    """Strict: a missing or non-boolean verdict is malformed (None), never a pass."""
    verdict = data.get("research_source") if isinstance(data, dict) else None
    if not isinstance(verdict, bool):
        return None
    classes = data.get("asset_classes")
    return {"research_source": verdict,
            "reason": str(data.get("reason", ""))[:300],
            "asset_classes": [str(c) for c in classes] if isinstance(classes, list) else []}


def screen_source(client, model: str, meter, domain: str, titles: list[str],
                  about: str, log_path: str | Path) -> dict | None:
    """One metered Sonnet call. Returns the parsed verdict, or None when the
    budget is closed, the call failed, the model refused, or the output was
    malformed - the caller treats None as 'not admitted, try again later'."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _log(verdict, reason):
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts_utc": ts, "domain": domain, "model": model,
                                "verdict": verdict, "reason": reason},
                               ensure_ascii=False) + "\n")

    if not meter.can_spend():
        _log(None, "monthly cap reached")
        return None
    try:
        msg = client.messages.create(
            model=model, max_tokens=SOURCE_SCREEN_MAX_TOKENS,
            system=SOURCE_SCREEN_SYSTEM,
            output_config={"format": {"type": "json_schema",
                                      "schema": SOURCE_SCREEN_SCHEMA}},
            messages=[{"role": "user",
                       "content": build_source_screen_prompt(domain, titles, about)}])
    except Exception as exc:
        _log(None, f"api_error: {exc}"[:200])
        if any(m in str(exc).lower() for m in FATAL_API_MARKERS):
            print(f"  FATAL api error, aborting run: {exc}", file=sys.stderr)
            raise ApiCreditExhausted(str(exc)) from exc
        print(f"  screen call failed: {exc}", file=sys.stderr)
        return None
    meter.record_call(model, msg.usage, purpose="source_screen", agent="reader")
    if msg.stop_reason == "refusal":
        _log(None, "refusal")
        return None
    try:
        text = next(b.text for b in msg.content if b.type == "text")
        parsed = parse_source_screen(json.loads(text))
    except (StopIteration, ValueError, TypeError):
        parsed = None
    if parsed is None:
        _log(None, "malformed")
        return None
    _log(parsed["research_source"], parsed["reason"])
    return parsed
