# Scope 3 — One commercial phase

**Status:** scoped, not designed. Needs its own brainstorm → spec → plan.
**Depends on:** Scope 2 (you sell a designated system, not a candidate).
**Independent of:** Scope 4.

## Problem

Coen's stated end goal includes selling TV indicators via Whop. The
`trading-systems` SOP treats this as a first-class constraint — **"Pine is the
PRODUCT"** is a standing constraint from day one, there is a Pine-codeability
gate at kickoff (Phase 1), a Pine mapping line in the spec sheet (Phase 1b), and
a whole build-and-parity phase (Phase 6) that is closed at 6/6 scripts and
641/641 exact trades.

**The research layer has none of this.** No Pine-codeability gate, no Pine build,
no parity harness. It can therefore generate, screen, gauntlet and quarantine a
strategy that cannot be sold — spending the full pipeline cost on something that
fails the commercial requirement at the last step.

Today's three quarantined strategies happen to be Pine-codeable (daily bars,
channel breakout, ATR stop, r-multiple target, time stop, vol-percentile filter,
fixed-fraction or vol-target sizing — all computable from OHLCV in Pine). That is
luck, not process. Nothing stops generation 4 from producing something that
isn't.

## Scope IN

- **Pine-codeability as a gate, enforced early.** Every block type in the
  research layer's grammar carries a declared Pine mapping, and a block without
  one cannot enter a spec. This is cheap now — the grammar is 15 types — and gets
  expensive later.
- **A Pine build + parity harness for research-layer strategies**, reusing the
  discipline already proven in `trading-systems`: v6 strategies,
  `calc_on_every_tick=false`, bar-close confirmed entries, `lookahead_off` on
  every `request.security`, no `varip`, calibrated commission and slippage set in
  strategy properties, and a mechanical no-repaint grep checklist.
- **A parity run as a gate, not a report.** `trading-systems` treats divergence
  as a bug to fix before anything else. Same rule.
- Buyer-facing description standard: honest IS/holdout split, and for the
  research layer specifically, honest disclosure that the 2024+ holdout has been
  consumed three times.

## Scope OUT

- The sale itself: Whop mechanics, pricing, tiering, listing copy.
- AFSL / general-advice footing. That is a Coen step, already flagged in SOP
  Phase 6 and in the portal review.
- Re-doing the six existing Pine scripts. They are parity-confirmed and closed.
- Selling anything currently in quarantine. Sale is gated on incubation success,
  and the three research-layer systems have zero forward days.

## Decisions needed before this can be specced

1. **Is Pine-codeability a hard constraint on the grammar, or a filter at
   designation?** Hard constraint is cleaner and cheaper now, but permanently
   forecloses research-layer strategies that use inputs Pine cannot compute. The
   SOP already takes the hard line ("anything unmappable kills the spec here").
   Adopting it means the research layer can never explore, say, on-chain or
   funding-rate *signals* — funding stays a cost model, as the SOP requires.
2. **Where does the parity harness live?** `trading-systems/strat/pine_export.py`
   already does this well. Re-implementing it in `stewart-forward-test` is a
   second implementation of a proven thing; using it across trees violates the
   no-code-import rule. Possible resolution: the harness is a *shared tool* that
   consumes an exported trade list, so neither tree imports the other's code —
   both export a CSV of `(entry, exit)` pairs and the differ is origin-agnostic.
3. **Does a research-layer strategy get a Pine script before or after
   quarantine?** `trading-systems` builds Pine at Phase 6, before Phase 7
   incubation. The research layer's quarantine *is* incubation. Building Pine
   first would let the TV backtest and the paper record be cross-checked against
   each other, which is strictly more evidence.
4. **What is the sellable unit?** Single scripts sold at entry tier, A1 as
   set-only (an honesty requirement baked into its headers — the legs failed DSR
   individually), bundle as service tier and not Pine-able. Where does a
   research-layer strategy sit, and can a *sibling group* be sold as a set the
   way A1 is?

## Success criteria

- No strategy can reach designation without a verified Pine mapping.
- A research-layer strategy has a parity-confirmed Pine script produced by the
  same discipline and to the same evidentiary standard as the existing six.
- One documented answer to "what do I sell, at what tier, with what disclosure."

## Hazards

- **The rounding and margin traps are already documented and will recur.** Python
  `round()` is banker's, Pine `math.round` is half-away — this landed exactly on
  the boundary for 74 of 106 ETH-S trades. Pine v6 `strategy.entry` has no
  `qty_percent`, and default `margin_long=100` silently trims >1× entries. Any
  new parity work should start from these known divergence modes rather than
  rediscovering them.
- Feed sensitivity is real: threshold signals matched only 75–87% on the wrong
  (USD vs USDT) feed. The research layer trades `BTCUSD`/`ETHUSD` while
  `trading-systems` uses USDT pairs — confirm which feed a research-layer Pine
  script must target before building.
