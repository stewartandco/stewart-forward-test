# Composer v1 — design spec (2026-08-06)

The second pipeline agent: compose candidate strategy specs from accepted
research cards and pre-register them into the hash-chained registry, before
any results exist. Approved scope and decisions from the kickoff session:

| Decision | Choice |
|---|---|
| Scope | Composer agent only — no screening engine, no gauntlet |
| Universe | Crypto daily bars: BTCUSD and/or ETHUSD, timeframe `1d`, session `24x7` |
| Block grammar | Hand-curated v1, code-defined; Composer restricted to registered types |
| Registration gate | Batch-level only (`--dry-run` preview → approved real run). No per-spec curation — pre-registration cherry-picking is silent subsetting and biases the funnel |
| Architecture | Two-stage: model ideates families, code enumerates siblings deterministically |

## Architecture

```
accepted cards ──▶ [model: propose idea families] ──▶ [code: validate families]
(from registry)         one structured call               drop invalid, loudly
                                                              │
registry_log.jsonl ◀── [code: register] ◀── [code: expand sweeps into siblings]
  block_type_registered (idempotent)          cartesian product, deterministic ids
  strategy_registered (one per sibling)       shared sibling_group_id
```

The model does the one thing that needs judgment — linking research claims to
block compositions. Everything that must be honest-by-construction (sibling
enumeration, the multiple-testing denominator, id assignment, registration)
is deterministic code.

## 1. Block grammar v1 (`pipeline/blocks.py`)

`BLOCK_TYPES`: a code-defined dict keyed by `(role, type)`. Each entry declares
`params_schema`: per-param `{type, min, max, grid}` where `grid` is the closed
list of values the Composer may sweep over. All computable from daily OHLCV.

| role | type | params (grid) |
|---|---|---|
| entry | `ma_cross` | fast {5,10,20}, slow {50,100,200}; constraint fast < slow |
| entry | `channel_breakout` | lookback {20,55,100}, direction {long, both} |
| entry | `zscore_reversion` | lookback {20,60,90}, z_entry {1.5,2.0,2.5}, direction {long, both} |
| entry | `trend_scan` | max_lookback {60,90,120}, t_min {2.0,3.0} (per card `3d4db3693d78d717`, trend-label method) |
| regime | `regime_ma` | ma_len {100,200} (long-only above MA) |
| filter | `vol_percentile` | lookback {90,180}, max_pctile {0.8,0.9,1.0} |
| stop | `atr_stop` | atr_len {14}, mult {1.5,2.0,3.0} |
| stop | `pct_stop` | pct {0.05,0.10,0.15}. **RETIRED** (D15, 2026-09-03): a fixed percent is not an indicator-placed stop. Refused for `version >= 2`; executed unchanged for legacy `version: 1` specs; the chained schema is immutable so the entry stays here |
| stop | `swing_stop` | lookback {10,20,40} (v7, D15 2026-09-03): stop = lowest low (long) / highest high (short) over the `lookback` bars before the signal bar |
| stop | `ma_stop` | ma_len {20,50,100} (v7): stop = SMA(close, ma_len) at the signal bar |
| stop | `channel_stop` | lookback {20,55,100} (v7): stop = lower channel (long) / upper channel (short) over the `lookback` bars before the signal bar |
| stop | `band_stop` | lookback {20,40,60}, mult {1.5,2.0,2.5,3.0} (v7): stop = SMA - mult x stdev (long) / SMA + mult x stdev (short) at the signal bar |
| target | `r_multiple` | r {1.0,1.5,2.0,3.0} |
| exit | `time_stop` | max_bars {10,20,40}. **RETIRED** (D15, 2026-09-03): exits on the calendar, not the market. Refused for `version >= 2`; executed unchanged for legacy `version: 1` specs; the chained schema is immutable so the entry stays here |
| exit | `ma_crossunder` | fast {5,8,13,20,34}, slow {50,80,130,200}; constraint fast < slow (v7): exit when fast SMA is below slow at close t (long) / above (short); a state test, filled at open t+1 |
| exit | `channel_exit` | lookback {10,20,40} (v7): exit when close t < lowest low over the prior `lookback` bars (long) / > highest high (short) |
| exit | `zscore_revert` | lookback {20,40,60,90}, z_exit {0.0,0.5,1.0} (v7): exit when z(close) >= -z_exit (long) / <= +z_exit (short) |
| exit | `tstat_decay` | max_lookback {60,90,120}, t_exit {0.0,0.5,1.0} (v7): exit when the best t-stat over windows 20..max_lookback (largest magnitude, like the entry) falls to <= t_exit (long) / rises to >= -t_exit (short) |
| exit | `regime_flip` | ma_len {50,100,150,200,250} (v7): exit when close t is below SMA(ma_len) (long) / above (short); a state test, not a cross |
| risk | `fixed_fraction` | f {0.01,0.02} |
| risk | `vol_target` | ann_vol {0.20,0.40}, lookback {30} |

On every non-dry run the Composer first chains a `block_type_registered`
entry (`{role, type, params_schema}`) for each grammar type not already in
the registry — idempotent, so run 1 registers 12 and later runs register only
grammar additions. Grammar changes happen ONLY by editing `blocks.py`, which
makes them auditable through the chain.

Rows marked **RETIRED** and **(v7)** are the D15 exit rules v7 change of
2026-09-03 (`docs/2026-09-03-exit-rules-v7-design.md`; chained note
`docs/notes/exit-rules-v7.md`): `time_stop` and `pct_stop` stay in
`BLOCK_TYPES` because their chained schemas are immutable, and are refused by
policy (`blocks.RETIRED_TYPES`) for `version >= 2` specs; the nine new
indicator-placed stops and indicator-event signal exits are sweepable and
are chained by the same first-real-run path when the Composer next runs.

## 2. Composer flow (`pipeline/composer.py`)

Mirrors `reader.py`'s structure (structured-output call, code-side guards,
`--dry-run` flag, loud drops).

**Model contract** — one streaming structured-output call. Input: compact
listing of all accepted cards (id, claim, topics, testability) + grammar
summary + universe constraints. Output schema: up to `--max-families` families:

```jsonc
{
  "families": [{
    "family": "zscore_reversion_regime",      // ^[a-z0-9_]+$
    "rationale": "one sentence",
    "card_ids": ["010bcfbdbae4e5fa", "..."],  // must be accepted cards
    "assets": ["BTCUSD"],                     // subset of {BTCUSD, ETHUSD}
    "blocks": [{"role": "...", "type": "...", "params": {...}}],  // base values
    "sweep": [{"block": 0, "param": "z_entry", "values": [1.5, 2.0, 2.5]}]
  }]
}
```

**Code-side validation per family** (violations drop the family with a printed
reason; a dropped family is counted in the run summary — never silent):

1. Every cited card_id is registered AND accepted (also re-enforced by
   `Registry.register_strategy`).
2. Every `(role, type)` exists in `BLOCK_TYPES`; every param is declared,
   in bounds, and on-grid; the fast<slow style constraints hold.
3. Exactly one entry, ≥1 stop, ≥1 risk (schema `allOf` re-checks).
4. Sweep axes reference existing block params; sweep values ⊆ declared grid.
5. Expanded sibling count ≤ `--sibling-cap` (default 25). Over-cap families
   are REJECTED, not clipped — clipping would let the model game the
   denominator.
6. Family name unique within the run.

**Deterministic expansion**: cartesian product of sweep axes in declaration
order; each sibling = base blocks with swept params substituted. Per spec:
`sibling_group_id = "<family>-<run_id>"`, `generation = 0`,
`parent_strategy_id = null`, `name` auto-built from blocks (e.g.
`"BTC 1d zscore_reversion lb60 z2.0 atr2.0 r1.5 tc20"`), `strategy_id` =
content address (`content_id`, existing helper). Same input → same ids,
asserted by tests.

**Cost model**: `commission_per_side = 0.001`, `slippage_ticks = 0.0005`,
both FRACTIONS of notional (10 bps + 5 bps). Known compromise: the schema
field name `slippage_ticks` is a futures-ism inherited from spec v1; for
crypto it carries a fraction. Revisit at schema v2 — do not amend the
committed schema mid-chain.

**Registration order**: missing `block_type_registered` entries first, then
`strategy_registered` per sibling (all siblings of all valid families, batch).
Every spec validates against `schemas/strategy_spec.schema.json` before any
write. Two failure layers, deliberately different: model-proposed families
that fail validation are DROPPED (counted, loud) and the run continues;
but if a code-expanded spec fails JSON-schema validation, that is a Composer
bug, and the whole batch aborts pre-write (all-or-nothing).

## 3. CLI

```
python -m pipeline.composer --max-families 8 --sibling-cap 25 \
    [--dry-run] [--model claude-opus-5] [--registry registry_log.jsonl] \
    [--run-id 2026-08-06-manual]
```

`--dry-run` prints families, sibling counts, and full sample specs; writes
nothing. Real run prints registered strategy_ids + a funnel-ready summary
(`N families proposed, M dropped (reasons), K specs registered in G groups`).
PIPELINE_VERSION `g1.0.0`, agent `composer`.

## 4. Verifier extensions (`verify_registry.py`)

1. Track `block_type_registered` entries; for each `strategy_registered`,
   every block's `(role, type)` must reference a previously registered block
   type.
2. Close an existing gap: invariant 3 says cards must be "registered and
   subsequently accepted", but the verifier only checks registration. Track
   `card_reviewed` status and fail strategies citing non-accepted cards.
   (The 167-card / 334-entry live chain has no strategies yet, so this
   tightening breaks nothing retroactively.)

## 5. Testing (offline, no API)

- Family validation: bad citation (pending/rejected/unknown card), unknown
  block type, off-grid param, out-of-bounds param, sweep over cap, missing
  stop/risk, duplicate family name — each drops with the right reason.
- Expansion: deterministic ids; sibling count = product of axis sizes;
  sibling_group_id shared; swept params vary, base params constant.
- Block registration idempotence: second run registers zero new types.
- Round trip: fixture family → expand → register into tmp registry →
  `verify_registry.py` exits 0; funnel shows `proposed=N`.
- Verifier extensions: strategy citing a rejected card fails; strategy using
  an unregistered block type fails; both pass on the happy path.
- Live-chain guard: extended verifier still validates the current 334-entry
  registry_log.jsonl (regression, run against a copy).

## Out of scope (next builds)

- Screening engine (executes block specs on data) — next sub-project.
- Gauntlet battery, mutation rounds (`generation > 0`), block-grammar
  expansion beyond v1.
- Automation/cadence — v1 is manual CLI, like the Reader.

## Corpus caveat (accepted at kickoff)

The 125 accepted cards are methodology-heavy and signal-light, so early runs
will produce few, small families. That is the honest state of the corpus;
the fix is ingesting signal-rich sources, not loosening composition.
