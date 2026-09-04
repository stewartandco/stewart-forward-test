# CONTINUE HERE — generation 4, the first run under protocol-v4

Paste this whole file into a fresh session.

## What you are picking up

`E:\Users\Coen\Claude\stewart-forward-test`, branch
`claude/ai-agent-business-automation-0lzfd9`, work in `research-layer/`.

A public hash-chained, literature-driven pipeline that turns research cards into
trading strategies: Reader → triage → Composer → screen → gauntlet → quarantine.
Generation 3 shipped 2026-08-17 and produced the first three strategies ever to
reach quarantine.

**Protocol-v4 was built and chained on 2026-08-18.** It reconciles this pipeline's
gates with the stricter `trading-systems` SOP, so a candidate now faces the same
named gates regardless of which pipeline found it. Nothing has been run under it.

**Your job is to run generation 4.** This is a live sequence, not a build. Every
stage is Coen-gated and three of them write to the chain.

## State at handoff (verified 2026-08-18, not taken from notes)

- Chain **2329 entries, VALID**. Funnel: 80 registered, 77 graveyard, 3 quarantine.
- **624 accepted research cards** available to the Composer.
- Scoped suite **470 passed**. `trading-systems` 934 passed / 1 skipped.
- HEAD `34927e5`, **2 commits unpushed**.
- `\StewartCo\23_QuarantineDaily` live, daily 08:20 local (00:20 UTC). Day 1 of 60
  records 2026-08-19. Independent of gen-4 — do not touch it.
- A **concurrent scanner** appends `card_registered` / `card_reviewed` to
  `registry_log.jsonl` continuously. It is not yours.

## Read these first

1. The chained note itself — `docs/notes/gauntlet-protocol-v4.md`, which is
   byte-for-byte what is on the chain at entry 2324. **Read it before running
   anything**; it is what you are being held to.
2. `docs/2026-08-17-gate-standard-design.md` — the readable design companion.
3. Vault: `project_research_layer.md` (live state) and
   `SOPs/sop-trading-system-build.md` Phase 4 (the standard v4 adopted).

## What protocol-v4 changed, in one page

**Eight gates**, in fail order: `sharpe_floor, oos_negative, edge_decay, mc_p05,
p_ruin, cost_stress, pbo, plateau`. The five middle ones are protocol-v3's,
unchanged. Three are new:

- **`sharpe_floor`** — train-window annualized Sharpe ≥ 0.4. Knowingly
  non-binding today; adopted so both pipelines read identically.
- **`pbo`** — CSCV, S=16, all 12,870 splits, **train window only**, matrix
  includes every sibling *including screen deaths*. Pass < 0.20; 0.20–0.50 fails
  the config; **> 0.50 kills the whole sibling group**. A killed member keeps its
  own first failure as its reason and carries `pbo_family_kill` as a flag.
- **`plateau`** — replaces point-winner selection. `select_survivors` reads no
  point metric at all. A candidate qualifies only if its family has a swept dense
  axis (`no_swept_axis` otherwise), **every swept axis has a registered sibling
  one step below AND above it** (`edge_of_grid` otherwise), it and all neighbours
  score ≥ 0.9 × family best, and no neighbour died at screen on `trade_count`.
  Winner = highest worst-score-across-self-and-neighbours; ties → smallest sid.

**Recorded, not gated:** Harvey-Liu haircut (train), purged walk-forward (train,
3 folds, 200-bar purge — a *declared divergence*, the SOP treats it as binding),
regime split (OOS, BTC vs 200d MA, 5% chop band). Each carries a `window` key.

**Grammar:** 8 dense twin block types exist (`*_dense`). Only they are sweepable.
Coarse types remain usable at fixed values. `validate_family` requires **≥3
values per swept axis, contiguous on the declared grid**. Sibling cap 60.

## Decisions already closed — do NOT re-litigate, do NOT re-ask Coen

1. **Retroactivity.** The three in quarantine keep their protocol-v3 verdicts.
   Chained as `quarantine-standard-asymmetry`, entry 2308, commit `1b5da5e`,
   pre-declared *before* v4 existed. The v4 note records that
   `9b6753a48c4d0ccd` would have failed v4's PBO gate at 0.342. It keeps its
   place. Reopening this is the retroactive re-judging that keeps 77 strategies
   buried — forbidden.
2. **Dense twins, all eight.** Coen 2026-08-18.
3. **Both-sides neighbours** (`edge_of_grid`). Coen 2026-08-18. A two-value sweep
   can never produce a survivor; on a three-value sweep only the middle point is
   eligible. Accepted deliberately.
4. **One sibling group PER CELL.** Coen 2026-08-18, built and tested —
   `expand_universe` suffixes the group id with the cell. A cell is the unit of
   **survival**, not of competition.
5. **N counts evaluated configs only**, clustered to effective trials. The
   Composer's prior-knowledge leakage is declared on-chain as uncorrectable by
   DSR at any N, not priced into a number.

## The sequence

Every stage: **verify the chain before and after**, check the scanner is idle
(`logs/status.json` `next_run`, plus `registry_log.jsonl` mtime), and stop for
Coen's approval where marked. Commit atomically, scoped paths, never leave
anything staged, `git show HEAD --stat` after.

**0. Pre-flight**

```bash
cd E:\Users\Coen\Claude\stewart-forward-test\research-layer
python verify_registry.py registry_log.jsonl
python -m pytest pipeline/test_pipeline.py pipeline/test_composer.py pipeline/test_screen.py pipeline/test_gauntlet.py pipeline/test_gen2.py pipeline/test_gen3.py pipeline/test_gen3b.py pipeline/test_gen4.py pipeline/test_pbo.py pipeline/test_plateau.py -q
```

Expect VALID and 470 passed. Also check the budget before spending: the Composer
runs on an Anthropic key and D33 gives the pipeline 20 USD/month against a shared
ledger with per-agent caps (`pipeline/budget.py`, `agent` field required).

**1. Composer dry-run — writes nothing. → COEN GATE**

```bash
python -m pipeline.composer --dry-run --run-id 2026-08-XX-gen4
```

Show Coen the proposed families: their cited cards, block compositions, swept
axes and sibling counts. **Check every swept axis is on a `*_dense` type with ≥3
contiguous grid values** — the validator enforces it, but a batch that gets
rejected wholesale wastes the run. Batch-gate drift is real and now logged to
`logs/batch_drift.jsonl`: gen-3's approved dry run was 20 specs and the real run
chained 24. Expect drift; report it rather than being surprised by it.

**2. Composer real run — CHAIN WRITE. → COEN GATE before proceeding**

Registers `block_type_registered` (first non-dry run for the dense types) and
`strategy_registered` per spec.

**3. Screen — CHAIN WRITE. → COEN GATE**

```bash
python -m pipeline.screen --dry-run     # then without --dry-run
```

`screen-protocol-v1` is UNCHANGED: train fence ≤ 2023-12-31, gate ≥ 40 trades and
net > 0 after costs.

**4. Gauntlet — CHAIN WRITE. → COEN GATE**

```bash
python -m pipeline.gauntlet --dry-run   # then without --dry-run
```

It will refuse a real run unless the v4 note is on the chain. It is (entry 2324),
so the guard should pass. The dry run prints per-group PBO lines before any
verdict — read them.

## If generation 4 produces zero survivors

**This is an acceptable outcome and it is pre-declared on the chain.** The note
also carries a promise you must keep:

> if generation 4 produces no survivors, a per-gate breakdown of what died where
> will be published on this chain BEFORE any threshold is discussed or any
> successor protocol is drafted.

So: publish the breakdown first. Do not propose a threshold change, do not draft
protocol-v5, and do not let Coen's disappointment or your own reach for the
nearest loosening. The point is to make a zero informative — to distinguish
"the gates caught something real" from "a threshold was set too high" — from the
published evidence rather than from whichever answer is more convenient once the
numbers are known.

**Why this matters more than usual here:** three of v4's rules have never touched
real data. The plateau ratio of 0.9, the both-sides neighbour requirement and the
0.20 PBO threshold are uncalibrated, and the write-free ratchet diagnostic
structurally cannot calibrate them — no strategy on the chain uses a dense block
type, so no existing family has a swept axis for the plateau rule to act on.
Generation 4 is the first evidence any of them will ever have.

The ratchet: v4 may be **tightened** freely. Any loosening requires the evidence
and the argument on-chain *before* the results it would affect exist.

## Hard rules that have already bitten these repos

- **A concurrent session may share this branch, working directory and git index.**
  Always `git add <explicit paths> && git commit -m "..."` in ONE command. Never
  `git add -A`. Never leave anything staged. `git show HEAD --stat` after.
  Committing `registry_log.jsonl` inherently sweeps in the scanner's card rows —
  that is expected and correct on a shared append-only file.
- **Never touch** root `forward_test_log.jsonl`, root `verify.py`, or
  `research-layer/registry_log.jsonl` outside a gated, Coen-approved chain write.
- **Never import code across trees** (`trading-systems/CLAUDE.md`). Copying data
  is fine.
- **Do not run `pipeline/test_scanner.py`** — another session's.
- **Never use PowerShell here-strings (`@'...'@`) in the Bash tool.** It once
  produced a commit whose subject line was literally `@`.
- **Chained notes are pure ASCII.** `canonical_json` uses `ensure_ascii=False`, so
  non-ASCII lands raw in the chain file. Every existing note is ASCII; match it.
- `content[0]` may be a `ThinkingBlock` — use
  `next(b.text for b in msg.content if b.type == "text")`. Cost last time: 15 of
  20 votes silently dropped.
- `max_tokens=300` truncates JSON mid-object once a thinking block eats the
  budget. 1500 works.
- Live-run recipe: set `PYTHONIOENCODING=utf-8` and pipe output to a file.

## Process notes that have paid for themselves

Subagent-driven development with two-stage review has caught a real defect in
roughly every task across six builds, and across the whole protocol-v4 build
**every single defect originated in the specification, not in implementer code**.
The worst were: a fingerprint test asserting `f(p) == f(p)`; a tie-break comparing
Python *lists* so the longer strategy id won (15 tests passed over it because
every fixture used one-character ids); a family-kill that clobbered a member's own
failure reason and would have written false history into an append-only chain;
and an end-to-end test that "proved" v4 correct **while relying on the very
edge-of-grid loophole v4 removes**.

**When an implementer reports a discrepancy, verify the arithmetic yourself
before deciding.** Every time that happened, the implementer was right.

**Never relay a line from a note or index as fact.** On 2026-08-18 a handoff note
listed four items as open; all four had already shipped. Open the source.
