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
| [`verify_registry.py`](./verify_registry.py) | Chain walker + invariant checker for `registry_log.jsonl`, mirroring the root `verify.py`. Needs the `pipeline/` package beside it (see below) |
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

# 5. Gauntlet the screen survivors (--dry-run first, then real)
python -m pipeline.gauntlet --dry-run
python -m pipeline.gauntlet

# 6. Verify the chain any time
python verify_registry.py registry_log.jsonl
```

`verify_registry.py` re-walks the hash chain and then checks nine invariants:

1. Chain integrity (`prev_entry_hash` links, genesis = 64 zeros).
2. `verdict` / `state_change` entries reference a registered `strategy_id`.
3. Strategies cite ≥ 1 `card_id` that was registered **and** accepted.
4. `strategy_registered` payloads carry no results fields.
5. Lifecycle transitions follow the state machine; terminal states are final.
6. Strategy blocks reference previously registered block types.
7. Every `quarantine_decision` references a strategy **currently** in
   `quarantine`, and `(strategy_id, date, asset)` is unique.
8. No two `strategy_registered` entries share a `composition_fingerprint` —
   "a buried composition never returns", checkable from the chain rather than
   trusted to the Composer's in-process guard.
9. `quarantine_data_snapshot` dates are unique, and every
   `quarantine_decision` is covered by an **earlier** snapshot naming its
   asset with a well-formed digest.

Invariant 8 is why the script is **not** self-contained: it imports
`composition_fingerprint` from `pipeline/composer.py` rather than
reimplementing it, so the two hashes cannot drift. Copy the whole directory,
not just the script and the log. It still pulls in no third-party
dependency — only Python's standard library.

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
- **Gauntlet gates are pre-declared and falsifiable.** SCHEMA's two
  unfalsifiable literal gates were amended on-chain BEFORE any verdict
  (`gauntlet-protocol-v1` note): MC P05 terminal > 1.0 and DSR >= 0.95. One
  quarantine slot per sibling group. Under `gauntlet-protocol-v3` selection
  was point-winner (highest deflated Sharpe); `gauntlet-protocol-v4` retires
  point-winner selection for neighbourhood-floor plateau selection — a
  candidate only survives if it and every one-step neighbour on every swept
  axis sit on the family's performance plateau — and adds two more
  pre-declared gates: a train-window Sharpe floor and a CSCV/PBO
  overfitting check across the sibling group. Passers not selected are
  graveyarded as `sibling_not_selected`, visibly distinct from gate failure,
  under either selection rule.
- **A buried composition cannot come back; a buried idea can.** `graveyard` is
  terminal and the composition-fingerprint guard blocks re-registration in any
  state, permanently. Under `gauntlet-protocol-v3` that guard is applied per
  SIBLING rather than per family: a colliding sibling is dropped and its
  family survives on the rest. With 56 compositions already buried in a modest
  grammar, a family-level guard would have killed whole families on one
  collision and silently prevented rediscovery. Applied per sibling it becomes
  a robustness filter — a real idea returns at neighbouring parameters, and one
  that worked only at the exact buried point was overfit. `verify_registry.py`
  invariant 8 re-checks fingerprint uniqueness from the chain itself, so the
  guarantee no longer rests on the Composer's in-process guard.
- **The gauntlet tests robustness; quarantine does the out-of-sample work.**
  Protocol-v3 retired the deflated-Sharpe gate from the gauntlet. Measured on
  this registry its implied hurdle had reached **1.86 annualized Sharpe against
  a best-ever-achieved 1.42** — driven by `sqrt(V[SR])`, the spread of our own
  registered strategies, so honestly registering failures is what made it
  unpassable. With only the best 30 registered the hurdle would have been 0.40.
  The threshold is unchanged at 0.95 and now gates `quarantine → live`, where
  genuinely fresh evidence exists to compute it on. Every gauntlet verdict
  still records the deflated Sharpe, its variance and hurdle inputs — but as
  of `gauntlet-protocol-v4` siblings are no longer ranked by it; sibling
  selection is neighbourhood-floor plateau selection instead (see above).
- **Quarantine is a real daily forward test.** Each quarantined strategy posts
  one `quarantine_decision` per asset per trading day, computed from bars up to
  that day only, preceded by a `quarantine_data_snapshot` recording the SHA-256
  of the price files behind it. Paper-trading forward on bars that did not
  exist at selection time cannot be gamed by search — and because the runner is
  deliberately idempotent and backfillable, selective *recording* is guarded
  separately: `--review` reconstructs the days a strategy owed from the price
  files themselves and reports every unrecorded date plus how long after its bar
  each row was actually chained.
- **Edge decay is measured per unit of opportunity.** v1 compared raw
  per-trade edge across the fence, which could not distinguish strategy
  decay from a shrinking opportunity set — over 2024+ passive buy-and-hold
  decayed harder than any of the 13 gen-1 candidates. v2 normalizes by each
  window's realized volatility and records both readings, so any v2 verdict
  can be re-derived under the v1 rule.

### Non-crypto asset classes (SP4)

The market-expansion sub-project
(`docs/2026-08-24-market-expansion-sp4-design.md`, track addenda under
`docs/`) generalises the composer, screen and gauntlet path to non-crypto
asset classes, one class per track. `pipeline/cells.py`'s `CLASSES` is the
authoritative declaration of every class's assets, timeframes, session, cost
model, eras and excluded block types; declaring a class there is not the
same as activating it, see the activation note below.

- Track 1: `fx` (12 FRED pairs, daily fixes, session `fx_5d`, single-fix
  bars) -- ACTIVATED 2026-08-24, first real generation the same day.
- Track 2a: `equity_etf` (16 Tiingo-backed equity-index ETFs, daily OHLCV,
  session `us_equity_5d`) -- declared, **not yet active**.

```bash
# 1. Snapshot the pinned series for one class from the trading-systems free
#    lane into research-layer/data/, verified against the producer's
#    manifest sha256. Refuses, naming each id, on anything unpinned,
#    hash-mismatched, or carrying a failed integrity verdict; writes nothing
#    partial.
python -m pipeline.tradfi_data snapshot --classes fx
python -m pipeline.tradfi_data snapshot --classes equity_etf

# 2. Ship-bar step 3 (spec s8): a fixture-driven compose -> screen ->
#    gauntlet dry run on a THROWAWAY registry, no API call, nothing written
#    to the live chain. --asset-class defaults to fx; pass any declared
#    non-crypto class. Requires step 1 to have already run for that class;
#    refuses loudly, naming the command above, if that class's snapshot is
#    missing.
python tools_dryrun_fx.py --workdir <scratch-dir>
python tools_dryrun_fx.py --workdir <scratch-dir> --asset-class equity_etf
```

`tools_dryrun_fx.py` seeds its own throwaway research cards and strategy
specs, class-tagged to whichever `--asset-class` was passed, and prints a
summary: specs composed, screen pass/fail with burial reasons, gauntlet
verdicts per gate, how many of that class's verdicts carry an `era_summary`,
and the trials alignment mode. It exits 0 only when every stage ran and at
least one spec reached a gauntlet verdict; every artifact it produces lands
under the given `--workdir`, never under `registry_log.jsonl`, `artifacts/`
or `logs/`.

**Activation is Coen's call, not a code change to make lightly.**
`pipeline/cells.py`'s `LIVE_CLASSES` reads `("crypto", "fx")` as of Track 1's
activation; `equity_etf` is declared in `CLASSES` but deliberately absent
from `LIVE_CLASSES`. Declaring a class puts no trial into any denominator by
itself; only adding it to `LIVE_CLASSES` does, because that is the switch
that lets a real generation sweep that class's cells for the first time.
Flipping it is therefore a denominator event (spec §2 and §7): it ships as
its own reviewed commit, only after Coen has reviewed a clean dry run (step 2
above) and given the go for that class's first real generation (spec §8,
step 4).

Offline tests (no API key needed): `python -m pytest pipeline/`

## Status

- **Specification + Reader + Composer + Screen + Gauntlet + Quarantine**, with
  `gauntlet-protocol-v4` superseding v3 for all future verdicts (v4 adds a
  train-window Sharpe floor, a CSCV/PBO overfitting gate, and neighbourhood-
  floor plateau selection in place of point-winner selection; see
  `SCHEMA.md` §3). v1, v2 and v3 verdicts stand and the strategies they
  buried stay buried; the lifecycle state machine is unchanged and
  `graveyard` remains terminal.
- **Quarantine is built.** `python -m pipeline.quarantine --date YYYY-MM-DD`
  appends a day's decisions; `--review` audits completeness and backfill lag
  and writes nothing. Graduation is a separately human-gated decision, and the
  `quarantine → live` gate cannot bind until a strategy accrues 60 trading
  days.
- **Generations 1 and 2 closed at 56 proposed → 31 screened-in → 0
  quarantined.** The full funnel, including every kill and its reason, is
  computable from `registry_log.jsonl` alone. Generation 2's gauntlet failed
  all 18 survivors, 10 of them on the deflated-Sharpe gate alone — the
  measurement that produced protocol-v3.
- The live registry chain (`registry_log.jsonl`) is created on the Reader's
  first non-dry-run write; `verify_registry.py` validates it and the example log.
