# CONTINUE HERE — Composer gen-3 / protocol-v3 rev 2

Paste this whole file into a fresh session.

## What you are doing

You are continuing the Stewart & Co research layer in
`E:\Users\Coen\Claude\stewart-forward-test`, branch
`claude/ai-agent-business-automation-0lzfd9`. Work from `research-layer/`.

**Immediate task: write the rev-2 implementation plan, then execute it
subagent-driven with two-stage review.** The design spec is approved and
committed; the plan is not yet written.

Read these first, in order:

1. `research-layer/docs/2026-08-16-gen3-design.md` — **the approved spec
   (rev 2). This is your source of truth.**
2. `research-layer/docs/plans/2026-08-16-gen3-v3.md` — the rev-1 plan. Its
   Tasks 1–3 WERE EXECUTED (they built `pipeline/cluster.py`); Tasks 4–6 are
   obsolete. Do not follow it. Keep it for the record.
3. `C:\Users\Coen\.claude\projects\E--Users-Coen-Claude\memory\project_research_layer.md`
   — vault note with full project history.

Save the new plan as
`research-layer/docs/plans/2026-08-16-gen3-v3-rev2.md` (a NEW file — do not
overwrite rev 1).

## Where the project stands

Funnel to date, all public on the chain: **56 strategies proposed across two
generations, 31 screened in, 0 quarantined.** Chain is ~1,985 entries and
VALID. Every gen-1 and gen-2 strategy is in `graveyard`, which is terminal.

Gen-3 has NOT started. No gen-3 spec is registered. No protocol-v3 note is
chained. The live registry is untouched by gen-3 work.

## Why rev 2 exists (do not relitigate this — it is settled)

Gen-2's gauntlet failed all 18 survivors, 10 of them **only** on the deflated
Sharpe gate. Investigation showed:

- `SR* = sqrt(V[SR]) x multiplier(N)`. At N=56 the implied hurdle was **1.86
  annualized Sharpe**; the best strategy ever registered is **1.42**.
- The hurdle is driven by `sqrt(V[SR]) = 0.80` — the spread of our own
  strategies' Sharpes (−1.36 to +1.42). **With only the best 30 registered
  the hurdle would have been 0.40.** Honestly registering failures is what
  made the gate unpassable.
- Rev 1 tried to fix this by counting effectively-independent trials via
  clustering. That module was built and works well (K=18 on the real
  registry, every cluster family-pure), but it only moves the hurdle
  1.86 → 1.72, so it would have changed no outcome.
- Coen's stated objective: strategies that are **robust, positive EV, and
  slow to decay** — good enough for one person to run and possibly sell. NOT
  famous-fund Sharpe. Five of the six gauntlet gates already test exactly
  that; DSR tests something else and was gating the cheapest, most reversible
  decision in the pipeline (entry to *paper* trading).

**Conclusion: relocate, don't recalibrate.** `DSR >= 0.95` is unchanged as a
threshold. It moves to the `quarantine → live` gate, computed on the
quarantine forward record. Quarantine becomes a real daily forward test.
Nothing is resurrected; the 56 stay buried; the state machine is unchanged.

## The rev-2 plan you need to write (6 tasks)

Baseline before Task 1: **188 passed** on the scoped command below. Each task
= TDD (write failing tests → confirm they fail → implement → run scoped suite
→ atomic commit). Give full code in every step; no placeholders.

**Task 1 — gauntlet drops the DSR gate** (`pipeline/gauntlet.py`, new tests in
a new `pipeline/test_gen3b.py`):
- `PROTOCOL = "gauntlet-protocol-v3"`.
- Add `FAIL_ORDER = ("oos_negative", "edge_decay", "mc_p05", "p_ruin",
  "cost_stress")` with a comment saying `dsr` is deliberately absent.
- Delete the two lines in `evaluate_spec` that `return False, "dsr", ...`.
  Keep computing `dsr` and keep it in `metrics`.
- Keep `DSR_MIN = 0.95`, re-comment it as gating quarantine→live.
- Wire the retained `from .cluster import effective_trials` into `run()`,
  replacing the protocol-v2 `trials_n = len(all_srs)` block; add
  `registered_n` to `evaluate_spec`'s signature and to metrics; put
  `effective_trials`/`registered_n`/`cluster_labels` into `group_context`.
- Migrate: add `"registered_n"` to the exact metrics key-set assertions in
  `test_gauntlet.py::test_all_gates_pass` and
  `test_gen2.py::test_metrics_carry_raw_and_normalized`; replace
  `test_gauntlet.py::test_dsr_fails_on_weak_curve` (it asserts a reason that
  can no longer occur) with one asserting a low DSR no longer gates; update
  `chain_gauntlet_note` to the v3 string.
- Key new test: a strategy with `trials_n=500, var=4.0` (DSR ≈ 0) that passes
  all five robustness gates must **pass**.

**Task 2 — composer rule 7 becomes sibling-level** (`pipeline/composer.py`):
- Currently any expanded sibling colliding with a registered fingerprint drops
  the WHOLE family. With 56 compositions buried, a 12-sibling family would
  often die wholesale, preventing the rediscovery this generation depends on.
- New behavior: drop colliding siblings individually, keep the family, print
  `family X: 12 expanded, 3 already registered, 9 new`. Drop the family only
  if ALL siblings collide (message: `every sibling already registered`).
- **Intra-family duplicate siblings (mirrored sweep axes) still kill the
  family** — that is a malformed proposal, not a collision.
- Exact compositions remain permanently unregisterable. This makes the guard
  a robustness filter: a real idea comes back at neighbouring params.

**Task 3 — composer prompt carries gen-2 evidence**: replace the "What
happened in generation 1" paragraph with measured gen-2 outcomes (reversion
lost 46–66%; short-only fired 10–19 trades in seven years; 12 of 18 held
positive vol-normalized decay; `vol_target` produced every worst ruin/MC
result), ending "Draw your own conclusions from those facts." Grammar
unchanged.

**Task 4 — `pipeline/quarantine.py` (new)**: daily forward runner.
- `python -m pipeline.quarantine --date YYYY-MM-DD` appends one
  `quarantine_decision` per quarantined strategy per asset:
  `{strategy_id, date, asset, action, price, position_frac, equity}`.
- Computed from bars **up to and including that date only**; raise if the date
  has no bar (never invent a decision for a non-trading day).
- **Idempotent per (strategy_id, date, asset)** — re-running a date is a
  no-op, so missed days can be backfilled.
- `--review` reports days accrued vs `MIN_TRADING_DAYS = 60` and **writes
  nothing**; graduation is a separately human-gated decision.

**Task 5 — verifier + SCHEMA**:
- `verify_registry.py` gains invariant 7: a `quarantine_decision` must
  reference a strategy currently in `quarantine`, and
  `(strategy_id, date, asset)` must be unique.
- `SCHEMA.md`: add the `quarantine_decision` entry-type row, and a
  protocol-v3 amendment paragraph recording that DSR moved from
  `gauntlet → quarantine` to `quarantine → live` and why.
- Live 1,985-entry chain must still validate.

**Task 6 — live sequence (CONTROLLER-ONLY, Coen-gated at three points)**:
1. README mechanics bullets + push Tasks 1–5.
2. Chain the `gauntlet-protocol-v3` note (draft text is in the spec's
   rationale — it must state: supersedes v2; the 56 stay buried; the state
   machine is unchanged; the 1.86-vs-0.40 evidence; DSR still computed and
   still ranks siblings but no longer gates; DSR moves to quarantine→live at
   threshold 0.95 unchanged, computed on the forward record; that gate cannot
   bind for 60 trading days and **this note does not authorise any live
   transition**; quarantine's decision format and graduation criteria; the
   sibling-level fingerprint change).
3. Composer gen-3 `--dry-run` → **Coen gate** → real run (capture output to
   `docs/runs/2026-08-16-composer-gen3.txt`).
4. Screen dry-run → **Coen gate** → real run.
5. Gauntlet v3 dry-run → **Coen gate** → real run. Survivors enter
   `quarantine`.
6. If anything reaches quarantine, run the daily runner once to prove the
   loop, then report what a daily cadence would need.

## Hard rules

- **Scoped test command** (the scanner's suite is another session's and must
  be excluded):
  ```bash
  python -m pytest pipeline/test_pipeline.py pipeline/test_composer.py pipeline/test_screen.py pipeline/test_gauntlet.py pipeline/test_gen2.py pipeline/test_gen3.py pipeline/test_gen3b.py -q
  ```
- **NEVER** touch root `forward_test_log.jsonl`, root `verify.py`, or
  `research-layer/registry_log.jsonl` outside Task 6's gated steps.
- **CONCURRENT SESSION**: a Reader v2 scanner shares this repo, working
  directory AND git index. It has already swept another task's staged files
  into its own commit. Always `git add <files> && git commit -m "..."` in ONE
  command; never leave files staged. Never stage `scanner.py`, `feeds.py`,
  `budget.py`, `relevance.py`, `scanstatus.py`, `seen.py`, `watchlist.py`,
  `test_scanner.py`, `report.py`, `sources/`, `run_scanner.ps1`, `logs/`, or
  `registry_log.jsonl`. After each commit, `git show HEAD --stat` and confirm.
- Before any chain write, check the scanner is idle: `logs/status.json`'s
  `next_run`, and the mtime of `registry_log.jsonl`. Verify the chain before
  AND after every write.
- `pipeline/cluster.py` and `pipeline/test_gen3.py` are **retained unchanged**
  from rev 1. Their 13 tests must keep passing.
- API key: `sed -n 's/^ANTHROPIC_API_KEY=//p' /e/Users/Coen/Claude/morpheus-hub/backend/.env | tr -d '\r'`;
  set `PYTHONIOENCODING=utf-8`; pipe long runs to a file.

## Process notes that have paid for themselves

Subagent-driven development with two-stage review has caught a real defect in
roughly every other task across four builds, including **five in the
controller's own prescribed plan code**. Keep it. When an implementer reports
a discrepancy, verify the arithmetic yourself before deciding — three times
the plan was wrong and the implementer was right.

Two live open questions, both deliberately undecided and both requiring
**pre-declaration before the results they would affect exist**:

1. Whether a graveyarded composition may ever be re-tested on genuinely new
   data (currently: never; the fingerprint guard blocks it).
2. The live gate's final calibration — formulation is specified, the number
   is not, and it cannot bind for 60+ trading days.

---

## ALSO REQUESTED (2026-08-16, Coen) — integrate the existing paper-traded systems

Coen has **strategies already in paper trading from earlier, separate work**
that he wants integrated into this stack. Do NOT bolt this on ad hoc — it is
a design question and must go through `superpowers:brainstorming` → spec →
plan → subagent execution like every other build here. It can be scoped
before or after gen-3; ask Coen which he wants first.

**Where they live** (a different project from this repo):
`E:\Users\Coen\Claude\trading-systems\` — see the vault notes
`project_paper_trading_bot.md` (the status note) and
`SOPs/sop-trading-system-build.md` (its 7-phase build process incl. witnessed
incubation). As of the vault index: 5 registered systems incubating (A1,
XRP-B1, R5, Q9 on decay-watch, plus BUNDLE-EW-4 `4e9281c0…` SR 1.96 with a
breadth overlay), plus HOUSE-CORE (VT-EW, `1c07ba89…`) registered 2026-08-06.
Pine layer is 6/6 parity-confirmed. Automation already runs daily:
`15_PaperBot` 08:10 and `16_TestnetBot` 08:12 via Task Scheduler.

**The integrity problem to solve — state it plainly in whatever you design.**
These systems did not come out of this funnel. They have no research cards,
no `strategy_spec` in the block grammar, no composition fingerprint, and no
screen or gauntlet verdict. The research-layer chain currently guarantees
that anything in `quarantine` got there by passing pre-registered gates. If
externally-originated systems are imported as ordinary quarantine entries,
that guarantee silently becomes false and the funnel's central claim —
"verify my funnel, every survivor passed these gates" — is laundered.

Options worth brainstorming (not a decision, a starting set):

1. **Import with explicit external provenance.** A new entry type or a
   required `origin: "external"` + `origin_ref` field, a distinct lifecycle
   entry point, and funnel statistics that report internal and external
   populations separately so the 56→31→0 figure stays honest.
2. **Cross-reference only.** The research layer records that these systems
   exist and where their track record lives, but they never enter its
   lifecycle. Cleanest for the chain's claims; does least for Coen.
3. **Retro-qualify.** Run them through the existing screen and gauntlet on
   the same data and fence, and let them enter quarantine only if they pass —
   with the caveat, stated on-chain, that their parameters were chosen
   before those gates existed, so their "out-of-sample" period is not
   genuinely out of sample for them.

Whatever is chosen, the same standing rules apply: pre-declare it in a chained
note before importing anything, never let external systems inflate or dilute
the funnel counts without a visible split, and never re-judge existing
verdicts. Also confirm with Coen whether the two paper-trading loops
(`15_PaperBot` and this repo's new `pipeline/quarantine.py`) should stay
independent or converge — running two unsynchronised daily writers against
different logs is exactly the concurrency hazard that has already bitten this
repo twice.
