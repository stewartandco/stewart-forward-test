# Phase 3 — Replace the card count with a spend budget and a time budget

**Status:** SPEC for Coen. Nothing here is built. Companion to `2026-08-31-autonomous-pipeline-roadmap.md` (Phase 3); written after two days of live cycles supplied the numbers the roadmap only guessed at.

**One-line statement:** `TRIAGE_LIMIT` bounds one stage of five. The two things that actually hurt — the $20 cap and the PT4H wall — are bounded by nothing. Give each cycle a spend allowance and a deadline, make every stage respect them, and derive the card count from those instead of the other way round.

---

## 1. What the first two days measured (source: `logs/`, `artifacts/` mtimes, `budget_ledger.jsonl`)

### Money — September to 03 Sep 05:53, pipeline agent

| stage | USD | share | calls | per unit |
|---|---|---|---|---|
| triage | 5.66 | 60% | 966 | **$0.0059 / reviewer call**, ×3 reviewers ≈ **$0.018 / card** |
| composer | 3.83 | 40% | 6 | **$0.64 / cycle** (dry-run + real pair) |
| screen, gauntlet | 0.00 | — | — | metered at $0: compute, not LLM |
| **total** | **9.49** | | | 47% of the $20 cap, in **2 real cycles** |

So the cost model is two terms, and only the first is what `TRIAGE_LIMIT` throttles:

```
cycle_usd  ≈  0.018 × cards_reviewed  +  0.64
month_usd  ≈  0.018 × cards_reviewed_per_month  +  0.64 × cycles_per_month
```

### Time — the 2026-09-01 15:30 fx cycle (3 h 57 min, inside PT4H by 3 minutes)

| stage | wall | drove by |
|---|---|---|
| triage | ~38 min | 200 cards × 11.55 s |
| composer pair | ~17 min | one class |
| screen | **25 min** | **1,260** strategies registered, ~1.2 s each |
| gauntlet | **~150 min** | **499** strategies passed screen, **~18 s each** |

The 21:30 fire the same night was **killed by Task Scheduler at 01:30:02 — exactly the PT4H limit** (event 201/102 in the operational log, no terminator in the run log). Everything it did was discarded; its composer spend was not.

**The card count did nothing to prevent either.** Triage was 38 of 237 minutes. The gauntlet is 18 seconds per strategy with no cap on strategies, and one class's sweep can hand it 500 of them.

### Demand — how often cycles will fire from now on

Card arrival (`card_registered` per UTC day): 14, 40, 9, 19, 13, 56, 7 over the last active week → **~16 cards/day**, bursty. Every card routes to crypto (unrestricted class) plus its tagged classes. Crypto therefore crosses the 25-card threshold roughly **every 1–2 days**; the tagged classes every few days each.

Plugging into the model: ~480 cards/month × $0.018 ≈ **$8.6** of triage, plus ~15–25 cycles × $0.64 ≈ **$10–16** of composer → **$19–25 / month at steady state**, before any coverage expansion (Phase 2) multiplies the composer sweep.

**Correction to what I said on 09-02:** the backlog drain was not the whole story. Steady state lands *on* the $20 cap, not under it. The cap will park the loop somewhere in the second half of each month unless the rate is chosen deliberately.

---

## 2. What exists today, and where it is blind

| guard | where | what it bounds | blind spot |
|---|---|---|---|
| `TRIAGE_LIMIT = 200` | `loop.py` | triage cards per cycle | nothing downstream; **time and money both leak past it** |
| `MIN_TASK_WINDOW_S` (derived, 128.5 min) | `loop.py` | startup WARN if the task window is shorter | assumes `_REST_OF_CYCLE_S = 90 min`; the fx cycle's rest-of-cycle was **~190 min** |
| monthly cap `PIPELINE_CAP_USD = 20`, batch-stop at 80% | `budget.py`, `pipeline_budget` | whether a cycle may **start** | checked once, before the cycle; a cycle already running can carry spend straight through both lines |
| `ExecutionTimeLimit PT4H` | Task Scheduler | the whole task | a hard kill: discards work, banks nothing, chains nothing — and the loop cannot see it coming |
| `_gauntlet_orphans` preflight | `loop.py` | strategies in state `gauntlet` that already carry a verdict | exits 1 on sight — a deliberate stop mid-gauntlet **would trip it** on the next fire |

The last row is the constraint that shapes the design: **a time cap cannot simply stop the gauntlet partway**, because the chain currently treats "in gauntlet, has verdict, no state change" as corruption.

---

## 3. The design

Two budgets per cycle, both derived from things Coen sets, both enforced by the loop, both respected by the stages.

### 3.1 Spend budget: `CYCLE_USD_ALLOWANCE`

```
CYCLE_USD_ALLOWANCE = (monthly_cap × (1 − reserve)) / expected_cycles_per_month
```

- `monthly_cap` — **Coen's number** (today $20). The only input that is a business decision.
- `reserve` — headroom for the quarantine daily and hand runs that share the agent's band. Default 0.15.
- `expected_cycles_per_month` — derived from the schedule and the observed trigger rate, recomputed monthly from the ledger, never hand-typed. At 3 fires/day and today's arrival it is ~20.

Enforcement, in cycle order:

1. **Pre-cycle** (exists): batch-stop / hard-cap gates unchanged.
2. **Triage limit derived, not declared:** `triage_cards = min(TRIAGE_CEILING, (allowance − composer_pair_usd) / usd_per_card)`. `TRIAGE_LIMIT` becomes `TRIAGE_CEILING` — a safety maximum the window arithmetic sets, no longer the control. Both unit costs come from the ledger's trailing month, not from constants.
3. **Post-triage, pre-composer:** if spend so far plus the composer pair's expected cost exceeds the allowance, **park here** with a new outcome `deferred_cycle_budget`, banking the watermark for what triage reviewed (Phase 1's rule already does this correctly). No composer spend. The class re-fires next tick with a fresh allowance.

### 3.2 Time budget: `CYCLE_DEADLINE`

```
CYCLE_DEADLINE = cycle_start + ExecutionTimeLimit − SAFETY_MARGIN
```

- `ExecutionTimeLimit` — read live from the task, as `_live_task_window_s` already does for the startup WARN.
- `SAFETY_MARGIN` — the commit step plus one gauntlet unit plus slack. Default 15 min.

Enforcement:

1. **Loop passes `--deadline-utc <iso>` to screen and to gauntlet.** Both already take a `--cutoff`; this is a sibling argument.
2. **Screen honours it by stopping cleanly.** Strategies not reached stay in `proposed`. That state is legal to leave and legal to resume — the next cycle for the class screens them first. No orphan, no chain repair.
3. **Gauntlet honours it by not *starting* a strategy it cannot finish**, not by abandoning one mid-run: before each strategy, `if now + per_strategy_estimate > deadline: stop`. Strategies not started stay in `screened`, which is likewise legal to leave and resume. **This is what keeps the orphan preflight true**: a strategy is only ever in `gauntlet` state while a run is actually working it, exactly as today.
4. **Loop reports `stopped_at_deadline: <stage>, <n_remaining>` in `pipeline_status.json`** on every such cycle, so the digest shows a cycle that *chose* to stop, not one that was killed. `overall: OK`. A cycle that leaves work behind is routine; a cycle that is killed is the defect this removes.
5. **The scheduler limit stays PT4H and stays the backstop.** With the deadline inside it, hitting the wall becomes evidence of a bug, not weather.

`per_strategy_estimate` for the gauntlet is the trailing mean from `artifacts/*/gauntlet/mc_summary.json` mtimes over the last N cycles, floor 10 s, ceiling 60 s — measured, like every other number here.

### 3.3 What this does to Phase 2 (coverage expansion)

Phase 2 makes the composer sweep wider. Under today's guards that means a longer gauntlet and a PT4H kill. Under this design it means **more cycles, each stopping cleanly at its deadline**, and the class draining its `screened` backlog across fires. That is the behaviour Coen asked for — "scan as much as possible" — bounded by clock and money instead of by a card count that was never measuring either.

---

## 4. Coen's decisions (the only inputs that are not derived)

1. **`monthly_cap`.** $20 holds the loop to roughly its current throughput and parks it late each month. Every dollar above that is ~55 triaged cards or ~1.5 extra cycles. Phase 2 will want more.
2. **`reserve`** (default 0.15) — how much of the band to keep for the quarantine daily and hand runs.
3. **Whether `deferred_cycle_budget` should count as a "clean day"** for the pin gate. Recommendation: yes — the loop did exactly what it was told.

Everything else is measured from the ledger and the artifacts.

---

## 5. Build order (each its own TDD pass, in the worktree)

1. **`--deadline-utc` in gauntlet** — stop-before-start rule, `screened` left resumable, test proves the orphan preflight stays green after a deadline stop.
2. **`--deadline-utc` in screen** — same shape.
3. **Loop computes and passes the deadline; reports `stopped_at_deadline`.** Test: a slow FakeRunner that overruns forces the stop and the status item.
4. **Ledger-derived unit costs** (`usd_per_card`, `composer_pair_usd`, `gauntlet_s_per_strategy`) with floors and ceilings — one helper, one test file.
5. **`CYCLE_USD_ALLOWANCE` and the post-triage park** (`deferred_cycle_budget`). `TRIAGE_LIMIT` → `TRIAGE_CEILING`, derived triage count. Update the Gate-2 window test to read the ceiling.
6. **Sentinel:** `stopped_at_deadline` and `deferred_cycle_budget` are OK outcomes, not drift.

Steps 1–3 remove the kill. Steps 4–5 remove the surprise bill. They are independent and 1–3 should land first: a cycle that cannot be killed is worth more than a cycle that is cheaper.

---

## 6. Not in scope

- Raising `ExecutionTimeLimit` above PT4H — it stays the backstop; a longer wall just hides the deadline logic's absence.
- Continuous / resident operation (Phase 4) — depends on this.
- The escalation queue (277 cards, 57% dissent) — a triage-quality question, not a throttle one, and it grows only as new cards arrive.
