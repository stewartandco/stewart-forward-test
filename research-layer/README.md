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

## Status

- **v1 — specification only.** No pipeline code in this directory yet.
- The registry chain (`registry_log.jsonl`) will live alongside this spec once
  the first agent runs; `verify_registry.py` already validates the example log.
