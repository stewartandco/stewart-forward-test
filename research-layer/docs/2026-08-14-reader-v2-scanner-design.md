# Reader v2 — continuous verified-source scanner (D23, contract v1.1)

Authorized by `stewartandco-agents/DECISIONS.md` D23 (2026-08-14). Upgrades the Reader
from hand-fed corpora to 24/7 scanning of a Coen-verified source watchlist, with a
throughput-first two-stage funnel and a USD 25/month standing budget. The card protocol,
honesty guard, pending-registration rule, and triage flow are **unchanged** — v2 is a new
front end that feeds the existing extraction pipeline.

## Files

| Path | Role | Committed? |
|---|---|---|
| `sources/verified_sources.json` | The watchlist — the standing corpus designation | yes |
| `sources/discovery_queue.jsonl` | Off-list sources queued as Tier 3 proposals for Coen | yes |
| `logs/seen_items.jsonl` | Append-only per-item event store (dedup + crash-safe resume) | no (runtime) |
| `logs/budget_ledger.jsonl` | One row per model call: tokens + USD | no (runtime) |
| `logs/screen_log.jsonl` | Stage-1 decisions incl. one-line rejection reasons | no (runtime) |
| `logs/reader_actions.jsonl` | Hash-chained action log (AGENT_STATUS_CONVENTION sec. 2) | no (runtime) |
| `logs/status.json` | Dashboard artifact (AGENT_STATUS_CONVENTION sec. 1) | no (runtime) |
| `logs/digest_YYYYMMDD.txt` | Daily human-readable scanner report | no (runtime) |
| `pipeline/watchlist.py` | Watchlist load/validate + verified gate + discovery queue | yes |
| `pipeline/feeds.py` | RSS/Atom parsing (stdlib) + HTML listing link diff + fetch | yes |
| `pipeline/seen.py` | Seen-item event store | yes |
| `pipeline/budget.py` | Token metering, monthly ledger, 80% warn, hard cap | yes |
| `pipeline/relevance.py` | Stage-1 relevance screen (strict intake parameters) | yes |
| `pipeline/scanstatus.py` | status.json + digest + hash-chained action log | yes |
| `pipeline/scanner.py` | Orchestrator: poll cycle, funnel, extraction, main loop | yes |
| `run_scanner.ps1` | OS-detached launcher (Start-Process; harness kills session children) | yes |

## Design decisions

1. **Verified gate is mechanical.** A source is pollable only if `added_by == "coen"`
   AND `verified_date` is non-null. The seed list ships with `verified_date: null` on
   every entry, so the scanner refuses to poll anything until Coen's verification pass
   stamps dates. This is the Tier 3 corpus gate as code, not prose.
2. **Polling costs no tokens.** RSS/Atom where the source has a feed; HTML listing diff
   (anchor extraction vs. seen store) otherwise. Item identity = sha256 of
   `source_id + canonical link`, first 16 hex.
3. **Two-stage funnel (D23 throughput-first).** Stage 1: `claude-sonnet-5` screens
   batches of up to 20 items on title+summary against STRICT intake parameters
   (kill real-estate content, alpha-free blogspam, product marketing, mechanism-free
   macro news; keep testable claims about signals, edges, portfolio construction,
   execution, risk, microstructure, regime detection). Every decision is logged with a
   one-line reason. Stage 2: full fetch + the EXISTING extraction path
   (`reader.SYSTEM_PROMPT` / `EXTRACTION_SCHEMA`, honesty guard, `register_card`
   pending) at `claude-sonnet-5` (the contract's approved cost lever; Opus stays the
   one-off-corpus CLI default).
4. **Budget = USD 25/month hard cap, 80% alert.** Every model call appends a ledger row
   (model, input/output/cache tokens, USD at sticker prices — Sonnet 5 $3/$15 per MTok,
   which slightly overstates spend while the intro discount runs; conservative by
   design). At >= 80% status flips WARN; at cap screening and extraction stop, items
   park as `deferred_budget`, polling continues, and the scanner self-resumes on month
   rollover.
5. **Paywalls.** HTTP 401/402/403 or a known-paywall domain marks the item `paywalled`;
   it is flagged in the digest and never fetched again. No credentialed fetching, ever.
6. **Discovery queue.** URLs the funnel encounters that are off-watchlist are appended
   (deduped on normalized URL) to `sources/discovery_queue.jsonl` as Tier 3 proposals.
   The scanner never fetches them.
7. **Crash safety.** All state lives in append-only JSONL (seen store, ledger, chains).
   Startup replays the seen store; an item's latest status wins. Registry appends only
   on success, so a crash mid-extraction re-runs that item.
8. **Status artifact.** `logs/status.json` per the convention, with `items` = the funnel
   counters (`sources_polled`, `items_seen_24h`, `screened_pass`, `extracted`,
   `cards_registered`, `budget` OK/WARN) and `pending_tier3` = discovery proposals +
   cards awaiting triage. Daily digest lists new finds by source, rejection summary,
   discoveries queued, and spend, so the /ops dashboard digest tab works unchanged.
9. **Action log chains.** `logs/reader_actions.jsonl` reuses the registry chain format
   (`Registry.append`); `scanstatus.verify_chain` walks it. Events: scanner_started /
   poll_new_items / screen_batch / cards_registered / budget_alert / digest_written /
   scanner_stopped. Empty polls are not chained (noise).
10. **Git discipline unchanged.** The scanner writes locally only; commits and pushes of
    the registry stay session-gated per the existing protocol. Never main, never root
    artifacts.

## Coen gates (Tier 3, per contract v1.1)

- Watchlist entries enter only via Coen verification (stamp `verified_date`).
- First live run happens only after the seed list is verified; then a 48h report
  (seen / screened / extracted / registered / spend) before tuning the intake bar.
