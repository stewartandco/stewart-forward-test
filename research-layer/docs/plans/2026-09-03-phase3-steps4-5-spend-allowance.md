# Phase 3, steps 4–5 — per-cycle spend allowance; the card count becomes a derived ceiling

**Spec:** `2026-09-03-phase3-spend-and-time-throttle.md` §3.1. **Inputs from Coen (2026-09-03):** monthly cap **USD 40** (D39, `budget.PIPELINE_CAP_USD`); reserve **0.15**; a budget park **counts as a clean day**. Everything else below is measured, never hand-typed.

## What this replaces

`TRIAGE_LIMIT = 200` is a card count standing in for two things it does not measure: money and clock. The clock half is done (steps 1–3, merged `34c06b9`). This is the money half. After it, the loop asks one question before it spends: *how much may this cycle cost?* — and derives how many cards that buys.

## Design

### The allowance

```
expected_cycles   = max(CYCLES_FLOOR=10, cycles completed in the trailing 30 days)
CYCLE_USD_ALLOWANCE = PIPELINE_CAP_USD × (1 − RESERVE) / expected_cycles
```

- `cycles completed in the trailing 30 days` comes from a new `state["cycles"]` list in `loop_state.json` — `{run_id, ts_utc, asset_class}` appended on every `cycle_complete` — not from parsing the run log. Floor 10 so a quiet month cannot inflate the allowance into one enormous cycle.
- At today's numbers: 40 × 0.85 / 20 ≈ **USD 1.70 per cycle**. That is the honest arithmetic of a USD 40 cap at ~20 cycles a month, and it is smaller than the USD 5.14 the first crypto cycle cost at 200 cards. The card count will fall to what the money buys; the backlog drains across more, cheaper cycles. That is the intended effect, stated up front.

### Unit costs, measured by the loop itself

Rather than mining the ledger by purpose, the loop reads its own spend deltas — `_spent()` is a fresh ledger read — at three points it already has: cycle start, after triage, after the composer pair.

```
usd_per_card       = (spent_after_triage − spent_at_start) / reviewed      (reviewed from triage_result.json)
composer_pair_usd  = spent_after_composer − spent_after_triage
```

Stored as trailing means (last `CALIBRATION_WINDOW=10` samples) in `state["calibration"]`. **Priors before any sample:** `usd_per_card 0.018`, `composer_pair_usd 0.64` — the 2026-09-01/02 measurements. A sample from a cycle where `reviewed == 0` is not recorded (no division by nothing).

### The triage count, derived

```
triage_cards = clamp( floor((allowance − composer_pair_usd) / usd_per_card), 1, TRIAGE_CEILING )
```

`TRIAGE_LIMIT` becomes `TRIAGE_CEILING = 200` — the window-fit safety maximum steps 1–3 already guard — with `TRIAGE_LIMIT` kept as an alias so the Gate-2 window test keeps asserting the ceiling fits the window. The loop passes the derived number as `--limit` and reports it (`triage_limit_used`). At the priors and USD 1.70: (1.70 − 0.64) / 0.018 = **58 cards**.

### The post-triage park: `deferred_cycle_budget`

After triage, before the composer dry-run (the point where the existing `deferred_budget` mid-cycle check already sits):

```
cycle_spent = spent_after_triage − spent_at_start
if cycle_spent + composer_pair_usd > allowance:  park
```

- Outcome `deferred_cycle_budget`, **overall OK** (Coen: a park counts as a clean day). Distinct from `deferred_budget` (the monthly batch-stop / hard cap, WARN), and from `no_new_accepted_cards`.
- **Banks the watermark for what triage reviewed** — Phase 1's rule, `watermark_after_triage` is already computed at this point. **Defect found while reading for this plan:** the *existing* mid-cycle `deferred_budget` park after triage calls `record_park` and never banks, so the cards triage just paid for are re-paid on the next fire. Fixed in the same change: both parks bank the reviewed cards.
- Records the park (`record_park`) so the class rotates to the back, as today.

### Status items, every cycle path that reaches triage

`cycle_usd_allowance`, `cycle_spent`, `triage_limit_used`, `usd_per_card`, `composer_pair_usd`, `expected_cycles`. The digest can then show *why* a cycle was the size it was.

## Tasks (RED → GREEN → commit, in the worktree)

1. **`pipeline/allowance.py`** — pure functions: `expected_cycles(state, now, floor)`, `cycle_allowance(cap, reserve, expected)`, `triage_count(allowance, composer_pair_usd, usd_per_card, ceiling)`, `Calibration` (trailing means with priors; `record_triage(spent_delta, reviewed)`, `record_composer(spent_delta)`; to/from `state["calibration"]`). Unit tests with fixed numbers, including the floor, the clamp at 1 and at the ceiling, and the zero-reviewed skip.
2. **Loop wiring** — derive `--limit`; take the three `_spent()` readings; the `deferred_cycle_budget` park (banking); the existing `deferred_budget` post-triage park banks too; append `state["cycles"]` on `cycle_complete`; status items. `FakeRunner` gains a `spend` hook that appends ledger rows on triage/composer calls so tests can simulate cost; the stage-argv test asserts the *derived* limit; the window test asserts the ceiling.
3. **Docs** — CLAUDE.md "Triage cost controls" rewritten around the allowance; roadmap Phase 3 marked complete; spec §3.1 annotated with the self-measured unit costs (the spec said "from the ledger's trailing month"; the loop's own deltas are the same measurement with no purpose-string archaeology).

## Not in scope

Raising the cap (that is D39's successor, Coen's); the Reader's own budgeting; Phase 2 activation, which Coen sequenced *after* this lands.
