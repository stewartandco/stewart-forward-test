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

## Reader pilot (`pipeline/`)

The first agent, per the roadmap: read sources → extract quote-grounded cards
→ human triage. Requires `pip install anthropic jsonschema` (plus `pypdf` for
PDF sources) and an `ANTHROPIC_API_KEY`.

```bash
cd research-layer

# 1. Extract cards from a source (--dry-run to preview without writing)
python -m pipeline.reader paper.pdf --title "Some Paper" --source-type paper \
    --author "A. Author" --year 2021 --url https://example.org/paper

# 2. Review pending cards — accept/reject each ([u] undoes); decisions buffer
#    in memory and chain only on the final [w]rite confirmation
python -m pipeline.triage --reviewer coen

# 3. Verify the chain any time
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

Offline tests (no API key needed): `python -m pytest pipeline/test_pipeline.py`

## Status

- **v1 — specification + Reader pilot.** Composer/gauntlet agents are not
  built yet; the lifecycle beyond `card_reviewed` is exercised only by tests
  and examples.
- The live registry chain (`registry_log.jsonl`) is created on the Reader's
  first non-dry-run write; `verify_registry.py` validates it and the example log.
