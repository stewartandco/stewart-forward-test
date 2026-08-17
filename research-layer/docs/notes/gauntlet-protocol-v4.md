gauntlet-protocol-v4

Pre-declared 2026-08-18, before any strategy has been evaluated under it and
before any generation-4 specification exists. Supersedes protocol-v3 for every
candidate registered after this note is chained. It does not apply to anything
already decided.

## What this changes and why

The research layer and the `trading-systems` pipeline judged candidates by
different standards. A candidate's fate depended on which pipeline found it.
This note makes one standard, so that nine systems clearing one bar is a
statement that can be made honestly rather than a statement requiring an
explanation of which bar each one cleared.

Direction of travel is one way: the research layer adopts the SOP's standard.
The SOP is the stricter and better-sourced document and its gates do not move.

## Gates retained from protocol-v3, unchanged

`oos_negative`, `edge_decay`, `mc_p05`, `p_ruin`, `cost_stress`.

The deflated Sharpe is still computed and still recorded. Under v3 it stopped
gating this stage, moving to the quarantine-to-live gate where forward evidence
exists to compute it on. Under v4 it also stops ranking siblings. It now
influences no decision at this stage and is retained as a recorded number.

## Gates added

**`sharpe_floor` — train-window annualized Sharpe below 0.4 is an auto-reject**,
regardless of every other gate. Taken from the SOP, where a backtested Sharpe
under 0.4 is treated as presumptively false.

This gate is knowingly non-binding today, and that is stated here rather than
discovered later. Every one of the 43 strategies that has ever reached the
gauntlet scored at least 0.577 on the train window; all 24 specifications below
0.4 had already died at the screen. It is adopted because it makes the two
pipelines textually identical and because it will bind if the screen is ever
loosened.

**`pbo` — probability of backtest overfitting, by CSCV, S=16, all 12,870
splits.** Pass is below 20%. Between 20% and 50% fails the configuration. Above
50% means the selection process is actively harmful, and kills the entire
sibling group on that universe rather than the single configuration — the SOP's
rule, adopted verbatim.

Two properties of how it is computed here:

- **The train window only.** The 2024-onward holdout has already been consumed
  three times and this protocol does not consume it a fourth.
- **Every sibling enters the matrix, including those that died at the screen.**
  Computing PBO over survivors only would filter the matrix on performance and
  understate overfitting, which defeats the gate.

When a family is killed, each member's recorded reason remains its own first
failure in gate order, and the family kill is recorded as a separate flag on
every member. A strategy that independently failed `oos_negative` is not
relabelled as a PBO casualty; the chain would otherwise permanently hide that it
was separately broken.

**`plateau` — neighbourhood selection, replacing point-winner selection.** This
is the gate this protocol exists for. The SOP forbids selecting the best single
configuration and requires selection by neighbourhood quality, on the reasoning
that a lone peak surrounded by cliffs is the signature of overfitting rather
than of edge.

A candidate qualifies only if all of the following hold:

1. Its family has at least one swept dense axis. A family with none fails with
   `no_swept_axis`. Without a neighbourhood there is no robustness evidence, and
   every other clause would pass vacuously.
2. Every swept axis has a sibling one step below **and** one step above it.
   Failing this is `edge_of_grid`. A candidate at the boundary of a grid, or one
   whose neighbouring grid point was never registered as a sibling, has never
   been perturbed in that direction and cannot claim a plateau from there.
3. The candidate and every one of its one-step neighbours score at least 90% of
   the family's best score.
4. No neighbour died at the screen on `trade_count`. Turnover is a structural
   property of a configuration rather than a noisy metric: a 24-trade sibling
   can post a flattering per-trade Sharpe while being untradeable.

Among qualifying candidates that passed every other gate, the winner is the one
whose **worst score across itself and its neighbours** is highest. Ties break on
the lexicographically smallest strategy id. The selection function reads no
point metric of any kind.

Two consequences follow and are accepted deliberately: a two-value sweep can
never produce a survivor, because both points are grid edges; and on a
three-value sweep only the middle point is eligible. The Composer's validator
therefore requires at least three values on every swept axis, so a family cannot
be registered in a shape that is structurally unpromotable.

The single objective used throughout is the **train-window annualized Sharpe**,
computed identically for every sibling regardless of its screen or gauntlet
outcome.

## Recorded, not gated

Matching the SOP, which reports rather than gates these. Each records which
window it was computed on, so no reader has to infer it.

- **Harvey-Liu multiple-testing haircut**, nonlinear, on the train window.
  Harvey and Liu give three adjustments; this uses Bonferroni, the most
  conservative. Because the number is reported rather than gating, conservatism
  costs nothing.
- **Purged walk-forward**, three folds on the train window, purge gap 200 bars —
  at least the grammar's longest lookback. Reported as fold results plus the
  SOP's two summary flags, majority pass and catastrophic veto.
- **Regime-conditional split** on the out-of-sample window, under one ruler
  declared for the whole protocol: BTC close against its 200-day moving average,
  with a 5% band around the average counted as chop rather than weak trend.

**A declared divergence.** The SOP's Phase 5 checklist treats purged walk-forward
majority pass and catastrophic veto as binding on designation. Here they are
recorded and do not gate. This is stated as a difference rather than presented
as equivalence. Promoting them to gates is a one-line change and would be a
tightening, permitted freely by the ratchet below.

## Ratchet position

**This protocol only tightens.** Three gates are added; none is removed; no
threshold is loosened. It therefore carries none of the evidence-and-argument
burden that protocol-v3 had to discharge when it relocated the deflated Sharpe.

The standing rule is unchanged: a future protocol may tighten freely, and any
loosening requires the evidence and the argument on the chain before the results
it would affect exist.

## Retroactivity

This standard does not apply to the three strategies already in quarantine. They
keep their protocol-v3 verdicts. That was decided and chained separately, before
this protocol existed, as the `quarantine-standard-asymmetry` note — registry
entry 2308, commit `1b5da5e`. It is cross-referenced here rather than restated,
because it was deliberately pre-declared before the successor standard existed
so that the exemption could not have been chosen after seeing whom it helps.

## What is knowingly not corrected

**The Composer's prior-knowledge leakage is real, unmeasured, and uncorrectable
by the deflated Sharpe at any value of N.** The model that proposes strategy
families has read the literature on this asset class, so its priors already
encode which strategies historically worked on this data. N counts
configurations whose performance was actually observed, clustered to effective
trials. Inflating it to account for the model's internal search would look like
a correction while correcting nothing, because a configuration the model
considered and discarded was never backtested and so never inflated any observed
maximum. This is declared as a permanent limitation of the pipeline rather than
priced into a number.

Separately, the drift between the batch approved at the dry-run gate and the
batch actually registered is now logged. It is mechanically observable and was
previously lost. It is not a multiple-testing trial count, for the reason just
given, and is recorded as provenance rather than as a denominator.

## Differences from the SOP that survive, and why

1. **Stage at which the deflated Sharpe is applied.** Both pipelines use a
   threshold of 0.95. `trading-systems` applies it at designation against a
   known search burden of N=1737. The research layer applies it at the
   quarantine-to-live gate, on the forward record. Each applies it where the
   evidence to compute it honestly exists.
2. **Deflation of N.** `trading-systems` uses the raw trial count. The research
   layer clusters to effectively independent trials. This is defensible but it is
   a real difference in the denominator and is named here rather than hidden
   inside a shared threshold.
3. **Sample-size floor.** The SOP requires that under roughly 100 trades the
   shortfall is declared and every gate tightened. The research layer's screen
   admits at 40 trades. This gap was previously undeclared.

## Stated before any result exists

Adding gates raises the probability that generation 4 produces no survivors.
Generation 3 was the first to produce any, and this standard is strictly harsher
than the one that produced them. **A zero-survivor generation 4 is an acceptable
outcome.**

Three of these rules have never been exercised against real data. The plateau
ratio of 0.9, the both-sides neighbour requirement, and the 20% PBO threshold are
uncalibrated, and the write-free ratchet diagnostic cannot calibrate them: no
strategy currently on the chain uses a dense block type, so no existing family
has a swept axis for the plateau rule to act on. The diagnostic can exercise the
Sharpe floor and PBO against history and nothing else. This is a real gap in the
evidence behind this protocol and it is recorded here rather than discovered
later.

**Pre-committed consequence.** If generation 4 produces no survivors, a
per-gate breakdown of what died where will be published on this chain **before
any threshold is discussed or any successor protocol is drafted.** The purpose
is to make a zero informative: it must be possible to tell whether the gates
caught something real or whether a threshold was simply set too high, and that
determination has to be made from the published breakdown rather than from
whichever answer would be more convenient once the numbers are known.
