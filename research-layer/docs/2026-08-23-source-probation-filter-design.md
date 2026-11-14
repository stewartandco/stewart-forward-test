# Source admission: probation-by-yield filter replaces human verification (D27 case 3)

**Status: design APPROVED by Coen 2026-08-23 in session (§1–§5 each approved). Governance: amends
D27 in `stewartandco-agents/DECISIONS.md` (case 3) and retires the D26 approval write path; to be
ratified there when this ships. Not built. BUILT 2026-08-24 on branch feat/source-probation
(Tasks 1-12). MERGED + LIVE 2026-08-23 16:0x AWST (branch HEAD 69def3f fast-forwarded; scanner restarted, contract 1.8; Morpheus master c1dfbe9, hub restarted, sw mh-v12). FIRST PASS (cycle 1, 20 of 114): 1 admitted on probation (edgealchemy.robotwealth.com), 8 prefilter blocks, 11 source-screen blocks, 14 Sonnet calls USD 0.083; 97 proposals remain for the following cycles.**

## 0. Decision and scope

Coen (2026-08-23): *"remove the human verification, and implement a filter to do quality control
instead."* Scope = the single-citation discovery proposals that D27 still routes to the D26 panel.
D27 cases 1 (scout-researched) and 2 (cited by ≥ 2 distinct verified sources) are unchanged.

**Explicitly out of scope (recorded 2026-08-23):** the Composer keeps drawing on the whole card
corpus regardless of `asset_classes` (Coen: "no restriction"). Card → cell routing by asset class is
a design rule for market-expansion sub-project 4 (research applied to all markets), not this spec.

## 1. Evidence the design is calibrated on (measured 2026-08-23, `logs/seen_items.jsonl`)

- Discovery queue 399: approved 171 · proposed 114 (all single-citation) · blocked 102 · auto_admitted 12.
- Corpus-wide keep-rate under the D28 bar ≈ 3 % (189 keeps / ~6,600 screened). Median verified
  source keeps **0 %**; AQR 0/56, Faber 0/54, Kinlay 0/107. The six D27 auto-admits keep 0/117.
- Sources that matter are unmistakable on keep **count**: Carver 20/25, Quantitativo 15/25,
  Quantifiable Edges 15/18, Financial Hacker 8/10, Robot Wealth 7/12, Flirting with Models 7/10.
- Among sources with ≥ 40 screened items, ~50 % have ≥ 1 keep; with ≥ 100, 67 %.
- `deferred_screen` backlog: 122,289 items. A probation source screened in queue order would never
  resolve. Cost basis (D28): screen USD 0.0012/item, extract USD 0.072/item.

Consequence: rate thresholds are meaningless at a 3 % base rate; the filter uses keep counts over a
fixed window, with a cheap LLM source screen in front so junk never reaches the screening budget.

## 2. Admission filter (§1 approved)

Runs inside the daily scanner (`21_ReaderScanner`, unattended under D27/D29) for every `proposed`
entry that D27 cases 1–2 do not admit:

1. **Deterministic pre-filter (no cost).** BLOCK when any of: domain on the junk blacklist
   (`watchlist.py` junk platforms) · hostname begins `store.` `shop.` `cms.` `app.` `login.` `my.` ·
   URL unreachable (HTTP ≥ 400 or timeout, one retry) · no RSS/Atom feed **and** fewer than 5 same-site
   article links on the index page (dating index links is not reliable from HTML; the source
   screen and the yield window carry the quality burden — clarification recorded at build,
   2026-08-24) (reuse `feeds.py` parse/extract primitives).
   Reason recorded verbatim on the queue entry.
2. **Source screen (one Sonnet-5 call, ≈ USD 0.003).** Input = 10 latest item titles + first 300
   chars of the landing/about text. Prompt = `INTAKE_PARAMETERS` reframed at source level: *would
   a recurring reader of this source expect testable trading / portfolio / execution / risk /
   microstructure / regime research?* Structured output `{research_source: bool, reason,
   asset_classes[]}`; malformed or missing output = **not admitted, logged `source_screen_malformed`,
   retried next run, blocked after 3 malformed runs.** `false` → status `blocked`, chain event
   `source_auto_blocked` rule `source-screen`. `true` → admitted on probation (§3).

## 3. Probation by yield (§2 approved)

- Watchlist entry: `added_by: auto-d27-probation`, new field `tier: "probation"`, `poll_minutes:
  DEFAULT_POLL_MINUTES_AUTO (360)`, `feed` set if found else HTML-diff on url, `notes` carrying the
  admit date, rule, and the source-screen reason.
- **Priority screening:** probation items are screened ahead of the deferred backlog, capped at
  **40 items per probation source per run**; the backlog still receives the remainder of the run's
  screen budget (never starved to zero).
- **Window = 40 screened items.** ≥ 2 keeps (`screen_keep` or `screen_keep_low`) → **promoted**:
  `tier: "verified"`, `added_by: auto-d27-probation→promoted`, `verified_date` = promotion date.
  0 keeps → **revoked**: entry removed, domain status `blocked`, reason `probation-yield 0/40`.
  Exactly 1 keep → window extends to 80; ≥ 2 by 80 promotes, otherwise revoked `probation-yield 1/80`.
- **Hard stop:** 90 days on probation without resolution → revoked `probation-timeout`; the queue row
  becomes `timed_out` and returns to `proposed` only when a NEW distinct citer appears (a timeout is
  not a block; a repeat of the original citer does not reopen it — clarification recorded at build,
  2026-08-24, so a quiet source is not re-screened every 90 days).
- **D27 case 2 outranks yield:** a second distinct verified citer during probation promotes at once.
- **Coen's kill authority unchanged:** blocking a domain or deleting a watchlist entry revokes
  permanently; the filter never reopens a `blocked` entry.
- Rationale for 2-of-40: at the 3 % base rate an average source clears it ~20 % of the time; a
  Carver-class source always; junk that slipped the screen essentially never. One-of-40 is hit 70 %
  of the time by chance and was rejected.

## 4. Backlog, budget, visibility, governance (§3–§4 approved)

- **First run** processes the 114 pending single-citation proposals through §2; expected one-off
  cost ≤ 114 × 0.003 + survivors × 40 × 0.0012 ≈ **< USD 3**, metered against the Reader scanner
  budget (USD 50/month, D28) through the existing meter. Steady state: pennies per week.
- **The 102 blocked stay blocked.** Hand blocks are permanent.
- **Morpheus /ops reader panel:** Approve/Block controls removed; replaced by three real counts
  `ON PROBATION n · PROMOTED (30 d) n · AUTO-BLOCKED (30 d) n`, each expandable to its chain rows;
  one per-source **revoke** control remains (the only write path, = Coen's kill authority). The
  connector reads chain events; nothing is computed that the chain did not record.
- **Ops Sentinel digest** gains one line per day: `probation admitted / promoted / revoked / blocked`,
  so an admit-everything or revoke-everything defect is visible within 24 h.
- **Governance:** D27 case (3) ratified in `stewartandco-agents/DECISIONS.md` at ship time, with
  the D26 approval write path retired (approvals queue retained read-only as history). D29 applies:
  this extends a stage the scanner already runs unattended; noted against Sentinel graduation
  (~2026-08-27).

## 5. Components

| unit | responsibility |
|---|---|
| `pipeline/probation.py` (new) | pre-filter, source-screen call + parse, probation state machine (`evaluate(entry, stats, today) → action`), promote/revoke/timeout, pure functions over dicts; no I/O except via injected callables |
| `pipeline/scanner.py` | call site after `process_auto_admissions`; priority screening of probation items (40/source/run); per-source screened/keep counters from `SeenStore` |
| `pipeline/watchlist.py` | `tier` field (default `verified` for legacy entries), revoke = remove + block |
| `pipeline/relevance.py` | source-level prompt variant + schema; shares `INTAKE_PARAMETERS` text |
| `pipeline/report.py` / digest | the one-line daily summary |
| Morpheus `connectors/research_chain` + ops panel | counts from chain events; revoke control posts the existing block action |

## 6. Testing (§5 approved)

TDD, red first, in `research-layer/pipeline/test_probation.py`:
- pre-filter: blacklist hit · blocked subdomain · unreachable (stubbed HTTP) · no feed + thin index · feed found
- source screen parse: `true` / `false` / malformed (x3 → blocked) / missing field
- state machine: 2-of-40 promote · 0-of-40 revoke · 1-of-40 → extend · 2-of-80 promote · 1-of-80 revoke · 90-day timeout · case-2 citation mid-probation promotes · Coen block mid-probation wins · re-proposal after timeout allowed, after block refused
- priority screening: 40/source/run cap; backlog never starved to zero in a run
- chain events: every transition emits exactly one event with rule + reason; `added_by` never `coen`
- idempotency: a second run over the same state changes nothing
- opt-in live smoke (`RL_LIVE=1`): three real domains (known-good blog, known-junk, unreachable)

## 7. Open / carried

- Asset-class card → cell routing: sub-project 4 rule 1 (not here).
- Whether probation sources should be excluded from the Composer's seed pool until promoted: **no**
  (Coen: no restriction); kept items from probation sources are ordinary cards.
