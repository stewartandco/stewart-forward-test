"""Stage-1 relevance screen (D23 throughput-first funnel).

Cheap Sonnet-class call on title/summary batches against STRICT intake
parameters. Only passes proceed to full fetch + extraction. Every decision is
logged with a one-line reason (auditable funnel); the budget meter is checked
BEFORE any call and charged after.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

BATCH_SIZE = 20
SCREEN_MAX_TOKENS = 4000

INTAKE_PARAMETERS = """\
You are the intake screen for Stewart & Co.'s quantitative research pipeline.
You see item titles and summaries from a verified source watchlist and decide,
for each, whether it is worth full extraction into research cards.

KEEP only items likely to contain testable claims about: trading signals or
edges, portfolio construction, execution, risk management, market
microstructure, or regime detection. The bar is a claim that could in
principle be backtested with market data.

KILL (with a one-line reason) anything that is: real-estate content,
alpha-free blogspam or listicles, product/course/service marketing, macro news
without a stated mechanism, job posts, event announcements, or pure
software-release housekeeping with no research content.

Be strict: when a title/summary gives no evidence of a testable claim, kill
it. Never invent content that is not in the title or summary. Return one
decision per item id, using exactly the ids given."""

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
                },
                "required": ["id", "keep", "reason"],
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
        status = "screen_keep" if d["keep"] else "screen_kill"
        out[d["id"]] = (status, d["reason"])
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
            print(f"  screen call failed: {exc}", file=sys.stderr)
            decisions = {it["item_id"]: ("deferred_screen", f"api_error: {exc}"[:200])
                         for it in batch}
            out.update(decisions)
            _log_decisions(log_path, model, decisions)
            continue
        meter.record_call(model, msg.usage, purpose="screen")
        if msg.stop_reason == "refusal":
            decisions = {it["item_id"]: ("deferred_screen", "refusal") for it in batch}
        else:
            text = next(b.text for b in msg.content if b.type == "text")
            decisions = parse_screen_response(batch, json.loads(text))
        out.update(decisions)
        _log_decisions(log_path, model, decisions)
    return out
