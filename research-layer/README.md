# Stewart & Co. Research Layer — v1 Specification

An automated, verifiable strategy-research pipeline: agents read quantitative
research, extract testable claims, compose candidate strategies from typed
blocks, and push survivors through a validation gauntlet into the existing
forward-test log.

The design principle is the same one that governs the forward-test log in the
repo root: **nothing is trusted, everything is anchored.** Every research card,
every generated strategy, and every verdict is registered in an append-only
hash chain *before* results exist, so the full funnel — including every idea
that died — is provable after the fact.

## Documents

| File | Contents |
|---|---|
| [`SCHEMA.md`](./SCHEMA.md) | Full v1 spec: research cards, strategy block grammar, lifecycle states, registry entry format |
| [`schemas/research_card.schema.json`](./schemas/research_card.schema.json) | JSON Schema — research card |
| [`schemas/strategy_spec.schema.json`](./schemas/strategy_spec.schema.json) | JSON Schema — strategy specification |
| [`schemas/registry_entry.schema.json`](./schemas/registry_entry.schema.json) | JSON Schema — registry log entry (the hash-chained unit) |
| [`verify_registry.py`](./verify_registry.py) | Standalone chain walker for `registry_log.jsonl`, mirroring the root `verify.py` |
| [`examples/`](./examples) | Worked examples: a research card, a strategy spec, and a valid chained registry log |
| [`pipeline/`](./pipeline) | Reader pilot: registry writer, Claude-powered card extraction, and human triage CLI |

## The one-paragraph pitch

Products in this space (see: "Threepio") demonstrate AI pipelines that read
papers and mass-generate strategies, but their claims terminate at a backtest
dashboard — unverifiable by construction. This layer inverts that: strategy
*births* are pre-registered into a public hash chain before any backtest runs,
verdicts (pass, kill, graveyard) are chained as they happen, and graduation
means entering the existing cryptographically anchored forward-test log. The
result is not "trust my dashboard" but "verify my funnel": anyone can compute
how many candidates were tried, how many died at which gate, and how the
survivors performed out of sample — with GitHub commit timestamps as the
third-party witness.

## Pipeline overview

```
 SOURCES            KNOWLEDGE            GENERATION           VALIDATION                DEPLOYMENT
┌─────────┐      ┌──────────────┐      ┌─────────────┐      ┌────────────────┐      ┌──────────────────┐
│ papers  │─────▶│ research     │─────▶│ strategy    │─────▶│ screen         │─────▶│ quarantine       │
│ books   │ read │ cards        │ cite │ specs       │ test │  └─ gauntlet   │ pass │ (paper trading,  │
│ blogs   │      │ (claims +    │      │ (typed      │      │      └─ kill / │      │  forward-test    │
│ filings │      │  verbatim    │      │  blocks,    │      │        pass    │      │  log)            │
└─────────┘      │  quotes)     │      │  pre-       │      └───────┬────────┘      └────────┬─────────┘
                 └──────────────┘      │  registered)│              ▼                        ▼
                                       └─────────────┘         GRAVEYARD                   LIVE
                                                          (public, counted,           (graduated
                                                           part of the chain)          systems)
```

Every arrow emits a registry entry. See `SCHEMA.md` for the entry types and
gate criteria.

## Pipeline agents (`pipeline/`)

Two agents so far: the Reader (sources → quote-grounded cards → human triage) and the Composer (accepted cards → pre-registered sibling strategy specs). Requires `pip install anthropic jsonschema` (plus `pypdf` for
PDF sources) and an `ANTHROPIC_API_KEY`.

### Reader v2 — continuous scanner (D23)

The Reader also runs as a 24/7 scanner over the Coen-verified source watchlist
(`sources/verified_sources.json`): token-free polling (RSS/Atom or HTML diff)
→ cheap relevance screen with strict intake parameters → full fetch + the same
card extraction/honesty-guard/pending-registration path as the CLI. Budget:
USD 25/month hard cap, alert at 80%; at cap extraction stops, polling
continues. Off-list sources it encounters queue as Tier 3 proposals in
`sources/discovery_queue.jsonl` and are **never** fetched; paywalled items are
flagged, never fetched with credentials. Dashboard artifacts land in `logs/`
(`status.json`, `digest_YYYYMMDD.txt`, hash-chained `reader_actions.jsonl`).
Design: `docs/2026-08-14-reader-v2-scanner-design.md`.

```bash
# one poll cycle (also the smoke test); refuses to run until Coen has
# verification-stamped watchlist entries (the Tier 3 corpus gate)
python -m pipeline.scanner --once

# resident loop: launch OS-detached, NEVER as a session child
powershell -ExecutionPolicy Bypass -File run_scanner.ps1
```

```bash
cd research-layer

# 1. Extract cards from a source (--dry-run to preview without writing)
python -m pipeline.reader paper.pdf --title "Some Paper" --source-type paper \
    --author "A. Author" --year 2021 --url https://example.org/paper

# 2. Review pending cards — accept/reject each ([u] undoes); decisions buffer
#    in memory and chain only on the final [w]rite confirmation
python -m pipeline.triage --reviewer coen

# 3. Compose strategy specs from accepted cards (--dry-run first, then real)
python -m pipeline.composer --max-families 8 --dry-run
python -m pipeline.composer --max-families 8

# 4. Fetch/refresh daily data, then screen proposed strategies
python -m pipeline.data_fetch
python -m pipeline.screen --dry-run
python -m pipeline.screen

# 5. Verify the chain any time
python verify_registry.py registry_log.jsonl
```

Mechanics worth knowing:

- **The honesty guard is code, not prompt.** The model is asked for verbatim
  quotes, and `pipeline/common.py:quote_in_source` then checks each quote is an
  exact substring of the source (whitespace-normalized). Claims whose quotes
  fail the check are dropped before registration and reported.
- **Cards register as `pending`** and cannot be cited by strategies until a
  human accepts them in triage — `Registry.register_strategy` enforces it.
- **Every write chains.** The `Registry` class appends `card_registered`,
  `card_reviewed`, `strategy_registered`, `verdict`, and `state_change`
  entries with the same canonical-JSON SHA-256 linkage as the root log, and
  enforces the lifecycle state machine on writes (verify_registry.py enforces
  it again on reads).
- **The Composer cannot invent blocks.** Strategy specs compose only block
  types registered in the chain (`pipeline/blocks.py` is the source of
  truth); sibling enumeration is deterministic code, so the multiple-testing
  denominator is a fact of record, not model whim. Invalid families are
  dropped loudly and counted.
- **Screen results are pre-protocoled.** The gate (>=40 trades, net-positive
  after costs), the 2023-12-31 train fence, and the execution conventions are
  chained as a `screen-protocol-v1` note BEFORE any verdict exists;
  `pipeline/screen.py` refuses real runs without it. Verdict artifacts
  (trades, equity, config) are committed under `artifacts/` and hashed
  on-chain.

Offline tests (no API key needed): `python -m pytest pipeline/`

## Status

- **v1 — specification + Reader + Composer + Screen.** The gauntlet battery
  is not built yet; the lifecycle beyond the screen verdict is exercised only
  by tests and examples.
- The live registry chain (`registry_log.jsonl`) is created on the Reader's
  first non-dry-run write; `verify_registry.py` validates it and the example log.
