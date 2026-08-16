# Research Layer v1 — Schema Specification

Three core objects and one chained log:

1. **Research card** — one testable claim extracted from a source, grounded by
   a verbatim quote.
2. **Strategy spec** — a candidate strategy composed of typed blocks, citing
   the cards that inspired it.
3. **Lifecycle** — the states a strategy moves through, and the gate criteria
   for each transition.
4. **Registry log** — the append-only hash chain that records card
   registrations, strategy births, verdicts, and state changes.

Conventions shared with the root forward-test log:

- **Canonical JSON**: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
- **Entry hash**: SHA-256 hex digest of the canonical JSON of the entry.
- **Chain**: each entry's `prev_entry_hash` equals the hash of the previous
  entry; genesis is 64 zeros.
- **IDs**: `card_id` / `strategy_id` are the first 16 hex chars of the SHA-256
  of the object's canonical JSON *excluding* the id field itself. IDs are
  therefore content addresses: change the content, change the id.

---

## 1. Research card

The atomic unit of machine reading. One card = one claim. A source that yields
twelve claims yields twelve cards, each independently groundable.

```jsonc
{
  "card_id": "3f9a1c22b40d8e17",          // content address (see Conventions)
  "version": 1,
  "created_utc": "2026-08-05T02:14:09Z",

  "source": {
    "type": "paper",                       // paper | book | blog | forum | filing | dataset_doc
    "title": "Advances in Financial Machine Learning",
    "authors": ["M. López de Prado"],
    "year": 2018,
    "locator": "ch. 3, pp. 43-49",         // page/section/chapter — REQUIRED
    "url": null,                           // url or doi or isbn — at least one
    "doi": null,
    "isbn": "978-1119482086",
    "credibility_tier": "peer_reviewed"    // peer_reviewed | practitioner | gray
  },

  "claim": "Triple-barrier labeling produces more robust classifier targets than fixed-horizon returns for intraday futures data.",

  "quote": "…the triple-barrier method labels an observation according to the first barrier touched…",
  // ^ verbatim passage from the source that grounds the claim. This is the
  //   honesty guard: a card with no quote, or a quote that cannot be located
  //   at source.locator, is invalid and must be rejected at review.

  "tags": {
    "asset_classes": ["futures"],          // futures | equities | crypto | fx | options | rates | commodities | cross
    "topics": ["labeling", "ml", "overfitting"],
    "horizon": "intraday"                  // intraday | daily | weekly | monthly | multi_month
  },

  "testability": {
    "score": 0.8,                          // 0-1, generator's estimate; reviewed by human on triage
    "data_required": ["MNQ 1m bars 2015-2025"],
    "notes": "Needs labeled event sampling; cheap to test on existing futures data."
  },

  "links": ["9c04d7aa1e5f2b88"],           // card_ids this card cites or contradicts
  "relation": {"9c04d7aa1e5f2b88": "extends"},  // extends | contradicts | duplicates | cites

  "extraction": {
    "agent": "reader",
    "model": "claude-sonnet-5",
    "pipeline_version": "r1.0.0",
    "run_id": "2026-08-05-nightly"
  },

  "review": {
    "status": "accepted",                  // pending | accepted | rejected
    "reviewed_by": "coen",                 // human triage — required before a card may be cited
    "reviewed_utc": "2026-08-05T09:30:00Z",
    "reject_reason": null                  // off_topic | quote_not_found | claim_not_supported | duplicate
  }
}
```

Design notes:

- **Quote-or-die.** The verbatim `quote` + `locator` pair is what separates a
  knowledge base from a hallucination log. Review tooling should open the
  source at `locator` and confirm the quote exists before `accepted`.
- **Cards are immutable once registered.** Corrections are new cards linked
  with `relation: "supersedes"`; the registry records both.
- **Off-topic ingestion is expected** (Threepio's corpus visibly contained
  real-estate noise). `review.status: rejected` + `off_topic` keeps the funnel
  honest without silently deleting anything.

---

## 2. Strategy spec

A candidate strategy is a *composition of typed blocks* — not freeform code.
The block grammar is what makes machine generation, sibling enumeration, and
auditing tractable.

```jsonc
{
  "strategy_id": "b7e2f0a94d1c6e35",       // content address
  "version": 1,
  "created_utc": "2026-08-05T03:02:41Z",

  "name": "MNQ ORB 15m 1.25R cut15:30 Mon-Tue-Thu-Fri orb<=75p",
  // ^ human-readable compact descriptor, generated from blocks; display only

  "family": "orb_breakout",                // taxonomy key; families are registered once and reused
  "universe": {
    "assets": ["MNQ"],
    "asset_class": "futures",
    "timeframe": "15m",
    "session": "RTH"
  },

  "blocks": [
    {"role": "entry",  "type": "orb_breakout",     "params": {"window_min": 15, "direction": "both"}},
    {"role": "filter", "type": "day_of_week",      "params": {"allow": ["Mon", "Tue", "Thu", "Fri"]}},
    {"role": "filter", "type": "orb_size_max",     "params": {"max_points": 75}},
    {"role": "stop",   "type": "structure",        "params": {"ref": "orb_opposite"}},
    {"role": "target", "type": "r_multiple",       "params": {"r": 1.25}},
    {"role": "exit",   "type": "time_cut",         "params": {"at": "15:30"}},
    {"role": "risk",   "type": "fixed_contracts",  "params": {"n": 1}}
  ],
  // roles: entry | filter | exit | stop | target | risk | regime
  // exactly one entry; at least one stop; at least one risk. Others optional/repeatable.

  "provenance": {
    "card_ids": ["3f9a1c22b40d8e17", "9c04d7aa1e5f2b88"],  // REQUIRED, non-empty:
    // every strategy must trace to at least one accepted research card
    "parent_strategy_id": null,            // set for mutations/siblings
    "sibling_group_id": "orb-mnq-2026w32", // all variants enumerated in one batch share this
    "generation": 0                        // 0 = seeded from cards; n = nth mutation round
  },

  "generator": {
    "agent": "composer",
    "model": "claude-sonnet-5",
    "pipeline_version": "g1.0.0",
    "run_id": "2026-08-05-nightly"
  },

  "cost_model": {
    "commission_per_side": 0.62,
    "slippage_ticks": 1
  }
  // NOTE: no results fields. A strategy spec NEVER contains performance data —
  // results live in registry verdict entries, keyed by strategy_id + stage.
  // This is what makes pre-registration meaningful.
}
```

Design notes:

- **Specs are born before results exist.** The `strategy_registered` entry is
  chained *before* the first backtest runs. There is no way to register a
  strategy retroactively without breaking the chain's timestamps.
- **Siblings are first-class.** The `sibling_group_id` makes the
  multiple-testing denominator explicit: "1 winner from a group of 101" is a
  fact of record, not a footnote.
- **Block vocabulary is versioned.** New block types are added by registering
  a `block_type_registered` entry (see registry) so the grammar's growth is
  itself auditable.

---

## 3. Lifecycle

```
                       ┌─────────────────────────────────────────────────┐
                       │                   GRAVEYARD                     │
                       │  (terminal; buried_at records the stage)        │
                       └─────▲─────────▲──────────▲──────────▲───────────┘
                        fail │    fail │     fail │     fail │
 ┌──────────┐   ┌────────────┴┐   ┌────┴─────┐   ┌┴─────────────┐   ┌───┴──────┐   ┌─────────┐
 │ PROPOSED │──▶│  SCREENED   │──▶│ GAUNTLET │──▶│  QUARANTINE  │──▶│   LIVE   │──▶│ RETIRED │
 └──────────┘   └─────────────┘   └──────────┘   └──────────────┘   └──────────┘   └─────────┘
  registered     fast, cheap       expensive       paper-traded in     real          voluntary
  before any     first pass on     validation      the public          capital       or forced
  results        training data     battery         forward-test log                  exit
```

| State | Meaning | Gate to advance (v1 defaults — tighten over time) |
|---|---|---|
| `proposed` | Spec registered; no results | automatic → screened when compute is scheduled |
| `screened` | Fast single pass over training data | net P&L > 0 after costs; ≥ 100 trades; else graveyard. Cheap-first: nothing else runs until this passes |
| `gauntlet` | Full validation battery | ALL of: (a) OOS walk-forward net positive with **edge decay > −25%** (per-trade edge OOS vs IS); (b) Monte Carlo 2000 resamples, P05 equity > 0; (c) P(ruin @ risk budget) < 5%; (d) **deflated Sharpe** (Bailey & López de Prado) > 0 given sibling_group size — *amended by protocol-v3 below: this test moved to the `quarantine → live` gate and no longer gates here*; (e) costs at 2× assumed slippage still net positive |
| `quarantine` | Paper-traded; decisions posted daily to the forward-test log like any production system | ≥ 60 trading days AND realized edge within Monte Carlo P25–P75 cone of the gauntlet projection — *amended by protocol-v3 below: the cone criterion is not applied at this gate* |
| `live` | Trading real capital | ongoing: rolling 90-day edge must stay above P05 cone, else auto-retire to graveyard with `reason: live_decay` |
| `retired` | Deliberately shut down while healthy | terminal |
| `graveyard` | Failed at any gate | terminal; `buried_at` + machine-readable `reason` required |

Rules:

- **No skipping.** Every strategy passes through every state in order.
- **Every transition is a chained registry entry** carrying the metrics that
  justified it. A `PASS` with no metrics payload is invalid.
- **Graveyard is public and counted.** The honest funnel ratio
  (proposed → live) is computable by anyone from the registry alone.
- **Quarantine = the existing forward-test log.** Quarantined strategies post
  daily decisions with `"system": "RL-<strategy_id>"` entries, hash-chained
  like SDCA/MRS/MARS/TARS/DHRS. Graduation to live is announced in the
  registry, and the quarantine track record stays public forever.

**Amendment, protocol-v3 (2026-08-16).** The deflated-Sharpe test moved from the
`gauntlet → quarantine` gate to the `quarantine → live` gate. The threshold
`DSR ≥ 0.95` is unchanged; only its inputs and its position change. Reason: the
gauntlet's job is to identify strategies that are robust, positive-EV and slow
to decay, which its other five gates test directly. DSR answers a different
question — could a skeptical outsider distinguish this from the luckiest of N
coin flips — and at N=56 its implied hurdle reached 1.86 annualized Sharpe
against a best-ever-achieved 1.42, because `sqrt(V[SR])` was measured across our
own strategies' Sharpes (−1.36 to +1.42). With only the best 30 registered the
hurdle would have been 0.40: honestly registering failures is what made the gate
unpassable, and a system that punishes transparency is mis-specified. At
`quarantine → live` the statistic is computed on the quarantine forward record,
where real capital is at stake and genuinely fresh evidence exists. The gauntlet
still computes, records and ranks siblings by DSR — it simply no longer gates
there. **The lifecycle state machine is unchanged**: no new transitions,
`graveyard` stays terminal, and no previously buried strategy is revisited by
any mechanism.

The `quarantine → live` cone criterion in the table above is amended with it.
The stored cone is a terminal-equity distribution over a strategy's **full**
trade count, so a 60-day forward record producing a handful of trades is not
comparable to it, and applying it would be unsound. `python -m
pipeline.quarantine --review` therefore reports days accrued against the
60-day minimum as its only mechanical criterion, prints the stored cone as
information, and says in its own output that the two are not comparable. The
graduation comparison is pre-declared separately, in its own chained note,
alongside the relocated deflated-Sharpe test at that same gate.

---

## 4. Registry log — `registry_log.jsonl`

Append-only JSONL, one entry per line, chained with the exact algorithm of the
root forward-test log (canonical JSON → SHA-256, `prev_entry_hash`, genesis
`0×64`). Committed to this public repo on every write, so GitHub commit
timestamps witness every entry — the same two-anchor model as the trading log.

Common envelope:

```jsonc
{
  "version": 1,
  "ts_utc": "2026-08-05T03:02:41Z",
  "entry_type": "strategy_registered",     // see below
  "prev_entry_hash": "…64 hex…",
  "payload": { }                           // type-specific, see below
}
```

| `entry_type` | Payload | Emitted when |
|---|---|---|
| `card_registered` | full research card | reader agent commits a card (pre-review) |
| `card_reviewed` | `{card_id, status, reject_reason?}` | human triage decision |
| `strategy_registered` | full strategy spec | composer births a spec — **before any backtest** |
| `verdict` | `{strategy_id, stage, verdict: "pass"\|"fail", metrics{}, artifacts_hash}` | each gate evaluation |
| `state_change` | `{strategy_id, from, to, reason?}` | lifecycle transition |
| `quarantine_decision` | `{strategy_id, date, asset, action, price, position_frac, equity}` | daily quarantine forward runner — one row per strategy per asset per trading day |
| `quarantine_data_snapshot` | `{date, data_sha256: {asset: hex}, bars_sha256: {asset: hex}}` | once per date, immediately before that date's decision rows — the price data those decisions were computed from, hashed whole-file and bars-through-that-date |
| `block_type_registered` | `{role, type, params_schema}` | block grammar grows |
| `note` | `{text}` | rare human annotations (incidents, corrections) |

`verdict.metrics` minimums per stage:

- `screened`: `{trades, net_pnl, win_rate, max_dd}`
- `gauntlet`: `{is_edge_per_trade, oos_edge_per_trade, edge_decay_pct, mc_p05_equity, p_ruin, deflated_sharpe, sibling_group_n, cost_stress_net_pnl}`
- `quarantine` (graduation review): `{days, trades, realized_edge_per_trade, projection_percentile}`

A `quarantine_decision` records what a paper-traded strategy's book DID on that
date's bar and its state at that date's close — entries fill at the open on a
signal from the previous close, so the row is a record, not an instruction for
the next day. `action` is one of `hold`, `enter_long`, `enter_short`, `exit`;
`hold` covers both holding a position opened earlier and staying flat, which
`position_frac` distinguishes. `price` is that date's **close** — the daily
mark, NOT the fill price, which for an `exit` row will usually differ; realized
P&L is carried by `equity`, not by `price`. `position_frac` is the fraction of
equity committed **at entry** and stays constant for the life of the trade, so
it is not a live exposure figure. `equity` is rebased to 1.0 at the strategy's
quarantine-entry date, so a forward record always starts at 1.
`(strategy_id, date, asset)` is unique and the runner is idempotent, so a
missed day can be backfilled.

Every `quarantine_decision` must be preceded by a `quarantine_data_snapshot`
for its date naming its asset. The runner recomputes each strategy's whole book
from the first bar every day — which is what makes a backfilled row identical
to a live one — so the identity of the price data is load-bearing: without it,
a re-fetch or vendor restatement would silently change what a reproduction
yields for every historical day.

The snapshot hashes each asset's price file two ways, and the difference
matters:

- **`data_sha256`** is the SHA-256 of the whole CSV, the same value
  `screen.py` and `gauntlet.py` record in their artifact bundles. It is an
  honest record of what the file looked like when the rows were written, and
  reproduces with a plain `sha256sum`.
- **`bars_sha256`** covers only the bars those decisions actually used: the
  header line plus every data row dated ≤ that date, in file order,
  LF-normalized, one `\n` per line. It reproduces as
  `{ head -n 1 f.csv; awk -F, 'NR>1 && $1<="D"' f.csv; } | tr -d '\r' | sha256sum`.

**`bars_sha256` is the one the runner guards on.** Re-running a date whose
`bars_sha256` no longer matches is refused rather than recomputed, because
those rows could not be reproduced. A refresh that merely appends later bars
changes `data_sha256` but not `bars_sha256`, and is correctly a non-event —
which is what keeps a missed day backfillable.

`verdict.artifacts_hash` is the SHA-256 of the full backtest artifact bundle
(equity curve CSV, trade list, config), stored off-chain; the hash makes the
bundle tamper-evident without bloating the registry.

---

## Verification

`verify_registry.py` (this directory) walks the chain exactly like the root
`verify.py`, plus registry-specific invariants:

1. Chain integrity (hashes link, genesis correct).
2. Every `verdict` and `state_change` references a previously registered
   `strategy_id`.
3. Every `strategy_registered` payload cites ≥ 1 `card_id` previously
   registered and subsequently accepted.
4. No results-bearing fields inside `strategy_registered` payloads.
5. Lifecycle transitions follow the state machine (no skips, terminal states
   final).
6. Every block referenced by a `strategy_registered` payload was previously
   registered via `block_type_registered`.
7. Every `quarantine_decision` references a strategy **currently** in
   `quarantine` state, and `(strategy_id, date, asset)` is unique.
8. No two `strategy_registered` entries share a `composition_fingerprint`.
   This is rule 7 — a buried composition never returns — verified from the
   chain itself rather than trusted to the composer's in-process guard. The
   fingerprint covers universe and blocks only, so a re-registration under a
   fresh `strategy_id` is caught.
9. `quarantine_data_snapshot` dates are unique, and every `quarantine_decision`
   is covered by an **earlier** snapshot for its date naming its asset — no
   forward record exists without the provenance of the bars behind it.

Run: `python research-layer/verify_registry.py research-layer/examples/registry_log.example.jsonl`
