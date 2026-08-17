# CONTINUE HERE — unifying the two trading-system pipelines

Paste this whole file into a fresh session.

## What you are picking up

Coen runs two pipelines that produce trading systems:

- **`E:\Users\Coen\Claude\trading-systems`** — six registered systems with a
  witnessed forward record, Pine parity 6/6 at 641/641 trades, a live order
  router at step 3 of a 7-step activation runbook, and two scheduled daily jobs.
  Its process is `SOPs/sop-trading-system-build.md` in the vault, 7 phases.
- **`E:\Users\Coen\Claude\stewart-forward-test`** (branch
  `claude/ai-agent-business-automation-0lzfd9`, work in `research-layer/`) — a
  public hash-chained literature-driven pipeline. Generation 3 shipped
  2026-08-17: funnel **80 proposed → 43 screened-in → 3 quarantined**, the first
  survivors this funnel has ever produced.

**Coen's goal, verbatim:** *"what I want at the end of the gauntlet/paper trading
process is successful edges that I can run, either standalone, or as part of an
optimized portfolio, or could potentially create TV indicators for and sell via
Whop. At this point, I just want the process to be uniform between what I've
already done, and what we're currently building."*

## Read these first, in order

1. `research-layer/docs/unify/README.md` — the scoping set's overview. Contains
   the central finding, the measured gap between the two processes, and why
   absorbing one into the other was rejected. **Read this before anything else.**
2. The four scope files it links: `1-gate-standard.md`,
   `2-designation-registry.md`, `3-commercial-phase.md`,
   `4-incubation-portfolio.md`.
3. `C:\Users\Coen\.claude\projects\E--Users-Coen-Claude\memory\SOPs\sop-trading-system-build.md`
   — the existing 7-phase process. This is the stricter document and mostly what
   the research layer should be adopting.
4. Vault notes `project_research_layer.md` and `project_paper_trading_bot.md`.

## Your task

**Take ONE scope file at a time through `superpowers:brainstorming` → spec →
`superpowers:writing-plans` → `superpowers:subagent-driven-development` with
two-stage review.** Do not attempt more than one in a session; each is a genuine
design problem with unresolved decisions listed in its own file.

**Recommended order: 1 → 2 → then 3 and 4 in either order.** Scope 1 is first
because it is the only one that changes what reaches quarantine, and 2, 3 and 4
all assume its output.

The scope files are deliberately **scoped, not designed**. Each lists decisions
that must be made before it can be specced. Those are real open questions, not
rhetorical ones — several are coupled, and Scope 1 flags its own coupling
explicitly.

## State at handoff (2026-08-17)

- `stewart-forward-test`: chain **2307 entries VALID**, funnel 77 graveyard / 3
  quarantine, **371 offline tests green**, all gen-3 work pushed through
  `7f48faf`.
- Quarantined 2026-08-17, **0 forward days**: `9b6753a48c4d0ccd` (breakout,
  fixed_fraction), `ad654fd8097717bd` (breakout, vol_target 0.4),
  `ef7712f41e2188e2` (tstat asymmetric payoff). The first two are a **matched
  sizing control** — identical entries, 76 trades each, differing only in the
  risk block.
- **Time-gated, not work-gated:** the first recordable forward day is
  2026-08-18, whose complete daily bar exists only after 00:00 UTC 2026-08-19.
  Then `python -m pipeline.quarantine --date YYYY-MM-DD` daily. This is
  independent of the unification work and should not wait for it.

## Hard rules that have already bitten these repos

- **A CONCURRENT SESSION shares the `stewart-forward-test` branch, working
  directory and git index.** At handoff it had one unpushed commit (`ef305ea`)
  and 20 untracked USDT CSVs from its own `data_import.py`. It has previously
  swept another session's staged files into its own commit. Always
  `git add <files> && git commit -m "..."` in ONE command, never leave files
  staged, and run `git show HEAD --stat` afterwards.
- **Never import code across trees.** `trading-systems/CLAUDE.md` forbids it;
  copying data is fine. This constrains Scope 3's parity harness directly.
- **Scoped test command** for the research layer (the scanner's suite belongs to
  another session):
  ```bash
  python -m pytest pipeline/test_pipeline.py pipeline/test_composer.py pipeline/test_screen.py pipeline/test_gauntlet.py pipeline/test_gen2.py pipeline/test_gen3.py pipeline/test_gen3b.py -q
  ```
- Never touch root `forward_test_log.jsonl`, root `verify.py`, or
  `research-layer/registry_log.jsonl` outside a gated, Coen-approved chain write.
- `trading-systems`' suite is ~907 tests and takes **~45 minutes**. Run targeted
  files while developing; the whole thing once before shipping.
- Verify the chain before AND after every write. Check the scanner is idle first
  (`logs/status.json` `next_run`, and the mtime of `registry_log.jsonl`).

## Process notes that have paid for themselves

Subagent-driven development with two-stage review caught a real defect in
roughly every task across five builds — **including three defects in the
controller's own prescribed plan code** during gen-3 alone: a test asserting
`1.0 > 1.0` against a saturated normal CDF, a date guard using
`datetime.strptime` which silently accepts `"2023-1-22"`, and a snapshot
uniqueness check placed outside the lock. When an implementer reports a
discrepancy, **verify the arithmetic yourself before deciding** — every time this
happened, the implementer was right.

Two further lessons from gen-3 worth carrying:

- **Checking that each figure is true is necessary and not sufficient.** A
  prompt-evidence task passed a figure-by-figure fact check and still shipped two
  false claims, because the questions not asked were "what does this paragraph
  omit?" and "does this stated comparison have more than one arm?"
- **A gate that produces survivors immediately after being loosened deserves
  more scrutiny, not less.** All ten of gen-3's gauntlet passers have DSR below
  0.95 and would have failed under protocol-v2. That is recorded honestly rather
  than presented as vindication, and the 2024+ holdout has now been consumed
  three times.

## Two open pre-declaration questions, still deliberately undecided

Both must be settled **before** the results they would affect exist:

1. Whether a graveyarded composition may ever be re-tested on genuinely new data.
   Currently: never — the fingerprint guard blocks it and verifier invariant 8
   now enforces that from the chain itself.
2. The `quarantine → live` gate's final calibration. The formulation is
   specified and the threshold (DSR ≥ 0.95) is unchanged, but the number it is
   computed against is not chosen, and the gate cannot bind for 60 trading days.
