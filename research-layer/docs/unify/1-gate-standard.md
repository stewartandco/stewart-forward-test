# Scope 1 — One gate standard

**Status:** scoped, not designed. Needs its own brainstorm → spec → plan.
**Depends on:** nothing. Do this first.
**Changes:** `stewart-forward-test/research-layer` (mostly). Possibly a
threshold reconciliation in `trading-systems`.

## Problem

A candidate's fate currently depends on which pipeline found it, not on how good
it is. The `trading-systems` SOP Phase 4 runs eight pre-committed statistical
gates; the research layer's gauntlet runs five. They overlap on cost stress and
trade-level resampling, and diverge everywhere else. Worse, on one point they
actively contradict: the SOP forbids point-winner selection and the gauntlet
performs it.

Uniformity here is not cosmetic. It is the difference between "these nine
systems all cleared one bar" and "these nine cleared two different bars, and I'd
have to explain which."

## Scope IN

- **Reconcile the two gate sets into one pre-declared protocol** that any
  candidate must clear regardless of origin. Expect this to be mostly the
  research layer adopting the SOP's standard, since the SOP is the stricter and
  better-sourced document.
- Specifically, add to the research layer:
  - **PBO via CSCV, S=16, pass < 20%** — with the SOP's kill rule that PBO > 50%
    kills the whole idea family on that asset, not just the config.
  - **Harvey-Liu haircut**, nonlinear, stated in the verdict; **backtested
    annualized SR < 0.4 auto-rejects** regardless of other gates.
  - **Purged walk-forward as corroboration only**, never selection: purge gap ≥
    max indicator lookback, 2-of-3 fold majority + catastrophic veto.
  - **Regime-conditional report** split by regime ruler.
  - **Neighbourhood/plateau selection replacing point-winner selection** in
    `gauntlet.select_survivors`.
- Decide and pre-declare the **trials_log equivalent**: what N is, and whether
  the Composer model's own internal search counts toward it.
- Chain the result as a pre-declared protocol note **before** any generation runs
  under it, with the ratchet rule (a protocol change may TIGHTEN; loosening
  requires the evidence and the argument on-chain, as protocol-v3 did).

## Scope OUT

- **Re-judging anything already decided.** The 77 buried strategies stay buried;
  gen-1/2/3 verdicts stand. This is the project's most load-bearing rule.
- The `quarantine → live` DSR gate calibration. Still deliberately unchosen, and
  it cannot bind for 60 trading days. Separate pre-declaration.
- Changing the screen (`screen-protocol-v1`) or the training fence.
- Changing `trading-systems` gates to match the research layer. Direction of
  travel is the other way.

## Decisions needed before this can be specced

1. **Does the new standard apply to the three already in quarantine?** They
   entered under protocol-v3 having cleared five gates. Applying a stricter
   standard retroactively would re-judge decided evidence — normally forbidden.
   Not applying it means the first three quarantine members are held to a weaker
   bar than everything after them, permanently. Neither is free. **My lean:** let
   them stand, record the asymmetry explicitly in the protocol note, and let
   quarantine's forward record be the leveller — it is the same evidence either
   way after 60 days.
2. **What is N?** The SOP says N is read from a `trials_log` of every config ever
   scored, never estimated, and that "CSCV/DSR computed over hidden trials is
   itself a lie." The research layer's Composer performs an unlogged internal
   search (the model considers and discards families before emitting any). That
   search is real and currently invisible. Options: count only expanded siblings
   (today's behaviour, honest but understated); require the model to emit
   considered-and-rejected families so they can be logged; or declare the
   discrepancy on-chain and move on.
3. **Is PBO computable at all here?** CSCV needs a trials × time performance
   matrix. The research layer has one row per registered sibling, which for a
   4-sibling family is a very thin matrix. Check whether S=16 splits are
   meaningful at this sibling count before committing to the gate — this may
   force larger sweeps, which is itself a design change.
4. **Plateau selection needs a neighbourhood to select over.** Today's sweeps are
   2 axes × 2 values. That is not a plateau. Adopting neighbourhood selection
   probably requires the Composer to emit denser grids, which raises N, which
   feeds back into (2). These three decisions are coupled.

## Success criteria

- One chained protocol note that both pipelines' documentation points at as the
  gate standard.
- A research-layer candidate and a `trading-systems` candidate face the same
  named gates with the same thresholds, or every difference is explicitly
  justified on-chain.
- `gauntlet.select_survivors` no longer picks a point winner.
- The live chain still validates; no existing verdict changes.

## Hazards

- **This is a loosening/tightening event and must be pre-declared.** Chaining the
  note after seeing which candidates it would help is exactly the failure
  protocol-v3 went to lengths to avoid.
- Adding gates to the gauntlet raises the chance of another 0-survivor
  generation. That is an acceptable outcome and should be said out loud before
  the run, not after.
- Decisions 2, 3 and 4 are coupled. Specifying them independently will produce a
  contradiction.
