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
| `gauntlet` | Full validation battery | ALL of, in this order (protocol-v4; see amendments below): (a) **train-window Sharpe floor** — annualized Sharpe on the pre-cutoff equity curve ≥ 0.4; (b) OOS walk-forward net positive; (c) **edge decay > −25%** (per-trade edge OOS vs IS); (d) Monte Carlo 2000 resamples, P05 equity > 0; (e) P(ruin @ risk budget) < 5%; (f) costs at 2× assumed slippage still net positive; (g) **CSCV/PBO** — probability of backtest overfitting across the sibling group < 0.20, with > 0.50 killing the whole group regardless of any individual member's other results *(both fixed lines WITHDRAWN by protocol-v5, which tests the observed value against the family's own permutation null and fails closed below four distinct configurations — see the v5 amendment below)*; (h) **plateau/neighbourhood gate** — the candidate and every one of its one-step neighbours on every swept axis must sit on the sibling group's performance plateau (replaces point-winner sibling selection). *Historical: the pre-v3 table listed a fifth criterion, deflated Sharpe (Bailey & López de Prado) > 0 given sibling_group size, at this gate — amended by protocol-v3: that test moved to the `quarantine → live` gate and has not gated here since. protocol-v3 also ranked survivors by deflated Sharpe; protocol-v4 replaces that ranking with the neighbourhood-floor plateau selection in (h).* |
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

**Amendment, protocol-v4 (`pipeline/gauntlet.py`).** Adds three gates to the
gauntlet battery and changes nothing else in the state machine: a
train-window Sharpe floor (`SR_FLOOR = 0.4`), a CSCV overfitting probability
gate over the sibling group (`PBO_PASS = 0.20` fails a member, `PBO_KILL =
0.50` kills the whole group), and a plateau/neighbourhood gate that replaces
protocol-v3's point-winner (highest deflated Sharpe) sibling selection with
neighbourhood-floor selection — a candidate only survives if it and every
one-step neighbour on every swept axis clear the same plateau. protocol-v4
is a strict superset of protocol-v3: every v3 gate is retained unchanged and
three are added, so no strategy that failed under v3 can newly pass under
v4. The plateau gate only ever fires on a family with a swept **dense**
block type (`pipeline.composer.SWEEPABLE_TYPES`); see
`diagnose_protocol_v4.py` for the standing audit of what protocol-v4 would
have done to the chain to date, including the currently-open gap that no
chained family sweeps a dense axis yet. Design record:
`docs/2026-08-17-gate-standard-design.md` (current); the gen-3 note,
`docs/2026-08-16-gen3-design.md`, is retained as the historical record of
protocol-v3's own changes and is superseded by the v4 note for anything
protocol-v4 amends.

**Amendment, protocol-v5 (`pipeline/gauntlet.py`, `pipeline/pbo.py`).** Amends
**one** gate, (g), and leaves every other gate at v4's threshold and
`FAIL_ORDER` position. v4's fixed lines assumed a no-skill null of about 0.5.
That is false in this implementation at small **odd** family sizes: `pbo.py`
scores a split overfit when `omega <= 0.5` (BBLdP's own convention) and at odd
`n_configs` the median rank lands exactly on that boundary, making the
uniform-rank null `(n+1)/2n` — **0.600 at five configs, above v4's own 0.50
kill line**. Every one of the twelve sibling groups registered before
generation 4 was even; every one of generation 4's six was exactly five. Three
changes: (1) the boundary tie counts as a **half event** (`pbo.overfit_weight`),
making the null exactly 0.5 at every size; (2) the gate counts **distinct**
train-window curves rather than registered siblings and **fails closed** below
four (`PBO_MIN_DISTINCT`, reason `pbo_underpowered`), because siblings with
identical curves are one configuration seen twice and a low PBO there records
only that a tiny difference was persistent; (3) `PBO_PASS`/`PBO_KILL` are
**withdrawn** for a test against the family's own 200-draw permutation null —
pass at or below the 5th percentile (`PBO_PASS_PCTILE`), family kill at or
above the 95th (`PBO_KILL_PCTILE`). The burden is **reversed**: a family must
demonstrate its selection generalises rather than pass by not being convicted,
so a gate with no power now passes nothing instead of everything. The
member-level test and the group kill stay separate, exactly as v4's two
thresholds were, so `pbo_family_kill` never overwrites a member's own first
failure. Unlike v4, v5 contains a **loosening** and carries the full ratchet
burden: evidence chained at registry entry **2511**, argument at **2512**,
both before any generation-5 specification exists. Applied to generation 4 it
also returns zero survivors. `diagnose_protocol_v4.py` keeps v4's withdrawn
thresholds locally, so it still reports what **v4** would have done.

**Amendment, protocol-v6 (`pipeline/gauntlet.py`).** Encodes one principle,
Coen 2026-08-21: each individual edge is tested and judged on its own evidence,
regardless of how similar it is to another, and **every edge is standalone when
running through the gauntlet**. Three mechanisms decided a strategy's fate on
something other than its own performance; all three are removed from the
battery and kept as **recorded numbers**. (1) **Selection is retired** — every
gate passer proceeds to quarantine, `select_survivors` selects nothing, and the
`sibling_not_selected` transition is retired; 7 strategies, all in generation 3,
had passed every gate on their own evidence and were graveyarded under it.
(2) The **PBO gate and its family kill** stop gating, withdrawing criterion (g)
above entirely. (3) The **plateau gate** stops gating and stops selecting,
withdrawing criterion (h) above entirely. `FAIL_ORDER` is now six gates —
`sharpe_floor`, `oos_negative`, `edge_decay`, `mc_p05`, `p_ruin`, `cost_stress`
— and **every input to every one of them is a property of the strategy alone**.
`sibling_group_n` survives in the recorded metrics and was never read by a gate
even under v4/v5. Promoting every passer costs nothing statistically: the trials
denominator counts **registrations, not promotions** (gen-4 recorded
`trials_n=2` clusters over `registered_n=110`), so the deflated Sharpe is
unchanged, and the one-winner rule was a capacity decision presented as a
statistical one. Declared a **loosening** without qualification, evidence
chained first at entries **2503**, **2511** and **2513**, protocol at **2514**.
Two costs named on-chain: it partially undoes v4's reconciliation with the
`trading-systems` SOP, so the claim that both pipelines clear one named bar must
stop being made until repaired; and it loosens the gauntlet while the DSR gate
at `quarantine → live`, now the **only** place multiplicity is priced, remains
uncalibrated. Pre-committed on-chain: more survivors under v6 is evidence of a
looser gate, **not** of edge, and must not be reported as a breakthrough.

**Amendment, exit-rules-v7 (D15, 2026-09-03; `docs/2026-09-03-exit-rules-v7-design.md`).**
Retires calendar exits and fixed-percent stops from the Composer grammar
**in place**: chained `block_type_registered` schemas are immutable, so
`exit/time_stop` and `stop/pct_stop` stay registered and keep executing for
every legacy spec, and are refused for NEW registrations by policy
(`pipeline/blocks.py::RETIRED_TYPES`). New specs are stamped **`version: 2`**
and run a second engine path — barriers, then declared indicator-event signal
exits, no deadline, no implicit exit — while `version: 1` is byte-for-byte the
prior path (the quarantine forward runner re-simulates legacy sids daily; their
observation must not move under them). Every verdict now records **why each
trade closed** (`exit_reasons*`, `open_at_end`, `stop_invalid` above) — recorded,
never gated. **Verifier rule (invariant 10):** after the chained `exit-rules-v7`
note — the `note` entry whose text begins `exit-rules-v7:` — every
`strategy_registered` must carry `version: 2` and no retired type; before the
note, `version: 1` entries stand as history. Ratchet position: **TIGHTENS** (a
class of exits is forbidden; nothing is loosened). Pre-committed here: without
forced exits trade counts fall and more families fail `trade_count`; that is the
honest consequence, not a defect, and `GATE_MIN_TRADES` is unchanged.

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
| `quarantine_data_snapshot_supplement` | same shape as `quarantine_data_snapshot` | 2026-08-27 per-class-calendars addendum: extends a date's provenance for a class whose source published after the base snapshot was chained (FRED-fed FX lags the crypto calendar ~a week). Must follow a base snapshot for its date, name only assets the date does not already cover, and precede the rows it licenses |
| `block_type_registered` | `{role, type, params_schema}` | block grammar grows |
| `note` | `{text}` | rare human annotations (incidents, corrections) |

`verdict.metrics` minimums per stage:

- `screened`: `{trades, net_pnl, win_rate, max_dd}`, plus D15 (exit-rules-v7,
  2026-09-03): `{exit_reasons, open_at_end, stop_invalid}` carried through from
  `run_spec` unchanged — `exit_reasons` is `{reason: n}` over the closed trades
  (`stop`, `target`, `signal:<type>`, and `time`/`signal` on legacy `version: 1`
  specs; only keys that occurred), `open_at_end` whether any book ended with a
  position still open (marked to market in equity, never a closed trade),
  `stop_invalid` how many **signal-bars** were dropped because the indicator-placed
  stop was not on the adverse side of the entry. Persistent-signal entries
  (`trend_scan`, `channel_breakout`, `zscore`) fire on consecutive bars, so this
  counts bars, not distinct opportunities — never read it as "N trades lost".
  RECORDED, NOT GATED
- `gauntlet`: `{is_edge_per_trade, oos_edge_per_trade, edge_decay_pct, mc_p05_equity, p_ruin, deflated_sharpe, sibling_group_n, cost_stress_net_pnl}`, plus protocol-v4: `{train_sharpe, pbo, pbo_family_kill}`, plus protocol-v5: `{pbo_n_distinct, pbo_percentile, pbo_null_p05, pbo_null_p95, pbo_null_draws}`, plus protocol-v6: `{plateau_ok, perturbation}` (`perturbation` is the self-perturbation sensitivity record: each dense axis of the strategy's OWN parameters stepped one place along its own grid and the strategy re-run, with `worst_ratio` the sharpest one-step drop. RECORDED, NOT GATED) — an observed PBO cannot be read without the null it was judged against, so the verdict carries the null with it rather than forcing a later reader to recompute one — the plateau gate's qualification/selection outcome is recorded in the sibling-group's `state_change` reasons (`sibling_not_selected`) and in the gauntlet artifact bundle's `group_context`, not as a per-verdict metrics key
  — plus D15 (exit-rules-v7, 2026-09-03): `{exit_reasons_is, exit_reasons_oos,
  open_at_end}` (RECORDED, NOT GATED). The two count maps are
  `engine.exit_reason_counts` over the candidate's ONE simulated trade list split
  at the cutoff — the same `is_t`/`oos_t` lists the gates read, so IS + OOS sums
  to the run's closed-trade count. `open_at_end` is `run_spec`'s own figure for
  the full-sample run, which ends inside OOS, so it is the OOS book ending open.
  No gate reads any of the three; `FAIL_ORDER` is unchanged
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
— or, for a class backfilled after the base snapshot was chained, a
`quarantine_data_snapshot_supplement` — for its date naming its asset. The
runner recomputes each strategy's whole book from the first bar every day —
which is what makes a backfilled row identical to a live one — so the identity
of the price data is load-bearing: without it, a re-fetch or vendor
restatement would silently change what a reproduction yields for every
historical day.

Since the 2026-08-27 per-class-calendars addendum
(`docs/2026-08-27-quarantine-per-class-calendars-addendum.md`), the daily
runner records per spec: a spec whose universe is missing that date's bar is
deferred (loudly, exit 0) rather than refusing every other spec's day, and its
dates are backfilled by explicit `--date` runs once the bars publish. A
missing price file, a day where every eligible spec defers, or a restatement
of covered bars stays a hard refusal.

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
8. A repeated `composition_fingerprint` satisfies the **D9 re-trial** rule
   (`docs/notes/family-openness-v1.md`, chained), applied through the same
   implementation the composer uses, `composer.retrial_verdict`: every earlier
   registration of that fingerprint is currently buried, and the latest
   burying verdict's cutoff is ≥ 183 days behind the oldest referenced cell's
   data end. Otherwise it is a duplicate and fails — including a duplicate of
   a quarantine or live registration (which has no burying verdict and
   therefore no expiry) and two registrations of one composition inside one
   `generator.run_id` (same data by construction). The window leg reads
   off-chain evidence (`artifacts/<sid>/{gauntlet/,}config.json` for the
   cutoff, `data/<cell>.csv` for the data end, defaulting to beside the log,
   overridable with `--artifacts-dir`/`--data-dir`); when that evidence is
   missing it is reported unverified rather than failed, because the verifier
   is checking rather than deciding and must not call a chain corrupt over a
   pruned artifact bundle.

   > **KNOWN PROTOCOL GAP (2026-09-01), behaviour deliberately unchanged.** The
   > cutoff this window measures from is a GLOBAL CONSTANT, not a per-verdict
   > date: all 4,065 `config.json` bundles on the live chain carry
   > `cutoff = 2023-12-31`, zero exceptions. So the window is OPEN for all
   > 2,702 burials and SHUT for none — narrowest margin 964 days against a
   > 183-day requirement — and the first two live re-trials were 9-day
   > re-tests (buried 2026-08-22, re-registered 2026-08-31). As implemented,
   > D9 reads as "any buried composition is re-triable", which inflates N. The
   > note's *reasoning* ("the clock runs on the DATA") describes a clock
   > running from the burial's own data end, which the gauntlet bundle already
   > records. Changing it is a protocol decision for Coen and needs its own
   > chained note; the verifier's job is to agree with the composer, and it
   > does.
9. `quarantine_data_snapshot` dates are unique, both digest maps name the same
   assets, and every digest is a real 64-character lowercase SHA-256 — the
   same check the writer applies, so a hand-appended fake cannot license a
   date. Every `quarantine_decision` is covered by an **earlier** snapshot for
   its date naming its asset in both maps — no forward record exists without
   the provenance of the bars behind it. A
   `quarantine_data_snapshot_supplement` must follow a base snapshot for its
   date and be asset-disjoint from the date's prior coverage; it licenses
   decisions exactly as the base does.

Run: `python research-layer/verify_registry.py research-layer/examples/registry_log.example.jsonl`
