# Gate standard — one protocol for both pipelines (Scope 1)

**Status:** design approved by Coen 2026-08-17. Not yet planned, not yet built.
**Scope file:** `docs/unify/1-gate-standard.md`
**Successor protocol:** `gauntlet-protocol-v4` (to be chained; not yet written)
**Direction of travel:** the research layer adopts the `trading-systems` SOP's
standard. The SOP's gates do not move toward the research layer.

## Why this exists

A candidate's fate currently depends on which pipeline found it. The SOP
(`SOPs/sop-trading-system-build.md`, Phase 4) runs eight pre-committed
statistical gates; the research layer's gauntlet runs five. They overlap on cost
stress and trade-level resampling and diverge elsewhere, and on one point they
contradict outright: the SOP forbids point-winner selection, and
`gauntlet.select_survivors` performs it.

The goal is one named standard both pipelines cite, with every surviving
difference argued on-chain rather than left as an accident.

## Facts verified at source before designing

Every number here was read from the live repo on 2026-08-17, not from the scope
file or from memory. Several contradict what the scope file assumed.

1. **Sibling group sizes are 4/6/8/12, not "4".** Twelve groups, 80 specs.
   Gen-3 is 8/4/4/4/4. `composer.SIBLING_CAP_DEFAULT = 25`, so small sweeps are
   the model's choice under the system prompt line "Small, motivated sweeps beat
   exhaustive ones" — not a hard limit.
2. **The declared grids are themselves coarse**, which is the real obstacle to
   plateau selection. `channel_breakout.lookback = [20,55,100]`,
   `atr_stop.mult = [1.5,2.0,3.0]`, `regime_ma.ma_len = [100,200]`,
   `fixed_fraction.f = [0.01,0.02]`, `atr_stop.atr_len = [14]` (one value).
3. **A chained block type's schema cannot be edited.**
   `composer.preflight_block_types` (composer.py:415) compares the whole
   `params_schema` for equality and aborts the run on any difference. Widening an
   existing grid is therefore forbidden; density requires *new* types. The same
   function only compares types present in both code and chain, so adding new
   keys is safe.
4. **All 80 artifact bundles carry `equity.csv`**, 2329 daily rows on the train
   window, including the 37 that died at screen. CSCV is therefore buildable
   today, over the full family rather than the survivors, from committed
   artifacts, without touching the 2024+ holdout.
5. **The SOP's `SR < 0.4` auto-reject does not bind here.** Train-window
   annualized Sharpe across all 80: min −1.290, median +0.917, max +1.709. All 43
   gauntlet-reachers sit at ≥ +0.577; all 24 sub-0.4 specs died at the screen. The
   quarantined three are +1.3441, +1.3281, +0.9663.
6. **`trials_n` is already clustered, not raw.** `gauntlet.py:324` calls
   `cluster.effective_trials()` over every registered strategy including the
   buried ones. Gen-3 recorded `4 clusters over 80 registered`.
7. **Gen-3 screen mortality was entirely `trade_count`** — 12 of 24, zero
   `net_negative`. This is what makes the cliff rule below consequential.

## Decisions

### D1 — Densify selectively (Coen, 2026-08-17)

Dense twin block types are added for the axes plateau selection needs. Coarse
types stay chained, stay usable at fixed values, and keep all 80 historical
fingerprints valid. They stop being sweepable.

Rejected: full densification (a 25-row trials matrix is still far short of what
CSCV is normally run on, so the extra grammar buys little) and no densification
(plateau selection would be uncomputable and PBO would be near-meaningless at
N=4).

### D2 — N counts evaluated configs only; leakage is declared, not priced (Coen, 2026-08-17)

`N` remains the count of configurations whose performance was actually observed,
clustered to effective trials as today. Two additions:

- **A chained declaration that the Composer's prior-knowledge leakage is real,
  unmeasured, and uncorrectable by DSR.** The model has read the literature on
  this asset class, so its priors already encode which strategies historically
  worked on this data. Inflating `N` would look like a correction while
  correcting nothing.
- **The batch-gate drift is logged**, because it is mechanically observable and
  currently lost. Gen-3's approved dry-run batch was 20 specs; the real run
  chained 24, with a directional mirror dropped. Gen-1 was 33 → 22.

Rejected: requiring the model to emit considered-and-rejected families. It is an
unverifiable self-report — the model decides what to confess — and it mixes
never-scored configs into a statistic defined over scored ones.

**Recorded reasoning risk.** The argument that Bailey-LdP's `N` counts only
trials whose Sharpe was observed is the fourth argument in this project's history
for why a DSR input should be more forgiving than it first appears. The previous
three were motivated reasoning. This one is written down so a later reader can
attack it rather than inherit it.

### D3 — A screen-death enters the neighbourhood at its real value, with a `trade_count` carve-out (Coen, 2026-08-17)

Every sibling scores on one continuous objective computed identically, so a bad
neighbour drags the worst-neighbour score down on its merits. The carve-out: a
neighbour that failed the screen on `trade_count` is a hard cliff. Turnover is a
structural property of a configuration, not a noisy metric, and a 24-trade
sibling can post a flattering per-trade Sharpe while being untradeable.

Rejected: treating a screen-death as missing data (discards the most informative
neighbours precisely because they are bad) and treating every screen-death as an
automatic veto (given gen-3's mortality pattern this would veto most of what
reached quarantine, without the densification that makes a cliff mean anything).

### D4 — Retroactivity: already closed, not re-opened

The successor standard does **not** apply to the three strategies in quarantine.
They keep their protocol-v3 verdicts. Decided by Coen 2026-08-17 and already
chained as `quarantine-standard-asymmetry` (entry 2308, commit `1b5da5e`),
pre-declared before this standard existed so the exemption cannot have been
chosen after seeing whom it helps. The protocol-v4 note **cross-references** that
note and does not restate it.

## Gate set

### Kept from protocol-v3, untouched

`oos_negative`, `edge_decay`, `mc_p05`, `p_ruin`, `cost_stress`. DSR stays
computed, recorded, and non-gating.

### Added as gates

| Gate | Threshold |
|---|---|
| `sharpe_floor` | train-window annualized SR ≥ 0.4, auto-reject |
| `pbo` | < 20% pass; 20–50% fails the config; **> 50% kills the whole sibling group** on that universe (see below) |
| `plateau` | qualification rule below, incl. the `trade_count` cliff veto |

**Fail order:** `sharpe_floor, oos_negative, edge_decay, mc_p05, p_ruin,
cost_stress, pbo, plateau`. Cheapest auto-reject first; the two family-level
gates last, since both need every sibling's curve in hand.

`sharpe_floor` is knowingly non-binding today (fact 5). It is adopted because it
makes the two protocols textually identical and because it will bite if the
screen is ever loosened.

### Added as recorded numbers, not gates

Matching the SOP, which reports rather than gates these:

- **Harvey-Liu haircut**, nonlinear, stated in the verdict.
- **Purged walk-forward**, 3 folds on the train window, purge gap **200 bars**
  (the grammar's longest lookback: `ma_cross.slow` and `regime_ma.ma_len` both
  reach 200). Corroboration only, never selection: 2-of-3 majority plus
  catastrophic veto.
- **Regime-conditional split**, one ruler declared for the whole protocol: BTC
  close versus its 200-day MA for direction, with a chop bucket on spread
  magnitude. Short-side strategies additionally report the parabolic bucket
  separately.

### PBO computation

CSCV, S=16, all C(16,8)=12870 splits. **Train window only** — the 2024+ holdout
has already been consumed three times and this design does not consume it a
fourth. Matrix rows = **every sibling in the family, including screen-deaths**,
using the `equity.csv` already committed for all 80. Computing PBO over passers
only would filter on performance and understate overfitting, defeating the gate.

**What "kills the sibling group" means mechanically.** PBO > 50% graveyards
*every* sibling in the group with cause `pbo_family_kill`, including siblings
that individually passed every other gate, and no member may be selected for
quarantine. It does not reach beyond that group: other families sharing a block
type are unaffected, and the existing fingerprint guard already prevents
re-registering the exact compositions. This is the SOP's rule that PBO > 50%
means the selection process is actively harmful, so the idea family dies on that
universe rather than the single config.

## Grammar fork

### The rule that makes density enforceable

> **A family may only sweep axes that live on a dense block type.**

Enforced in `composer.validate_family`, not in prose. Without it, a family mixing
one dense axis with one coarse axis manufactures fake cliffs — `lookback` 55 →
100 is a different strategy, not a perturbation.

### Dense twins

| New type | Dense grid |
|---|---|
| `entry/channel_breakout_dense` | `lookback [20,35,55,75,100]`, `direction [long,both]` |
| `entry/ma_cross_dense` | `fast [5,8,13,20,34]`, `slow [50,80,130,200]` |
| `entry/trend_scan_dense` | `max_lookback [60,75,90,105,120]`, `t_min [2.0,2.5,3.0]` |
| `stop/atr_stop_dense` | `atr_len [14]`, `mult [1.5,2.0,2.5,3.0,3.5]` |
| `target/r_multiple_dense` | `r [1.0,1.5,2.0,2.5,3.0]` |
| `filter/vol_percentile_dense` | `lookback [90,120,150,180]`, `max_pctile [0.6,0.7,0.8,0.9,1.0]` |
| `regime/regime_ma_short_dense` | `ma_len [50,100,150,200,250]` |
| `entry/zscore_reversion_dense` | `lookback [20,40,60,75,90]`, `z_entry [1.5,1.75,2.0,2.25,2.5]`, `direction ["long","both"]` |

**Corrected 2026-08-18.** This table originally listed only the first four and
claimed they "cover exactly the axes gen-1 through gen-3 actually swept." That
was wrong. Enumerating every swept axis across all 12 chained families found
four non-risk axes with no twin — `r_multiple.r` (swept by 4 families),
`vol_percentile.max_pctile` (2), `regime_ma_short.ma_len` (2), and
`zscore_reversion.lookback`/`.z_entry` (1). One consequence was concrete:
`tstat_trend_both_asymmetric_payoff`, one of the three strategies in
quarantine, swept `r_multiple.r`, so its family shape could not have been
expressed as a sweep under the original four. Coen chose to add all four
(2026-08-18). Risk axes remain twin-less by design.

`zscore_reversion_dense` deliberately keeps `direction ["long","both"]` rather
than gaining `"short"`: the engine emits a zscore short only when `direction ==
"both"`, so a `"short"` grid value would produce no signals rather than an
error. `channel_breakout_dense` is long/both for the same reason.

### Risk axes

**Excluded from the plateau and handled as labelled arms.** A plateau in position
sizing is not a robustness property — sizing rescales an edge, it does not change
whether the edge is real. Gen-3 found the right structure without being told: the
matched sizing control was two families with identical entries, not one swept
axis. That becomes the rule.

### Sibling cap

`SIBLING_CAP_DEFAULT` 25 → **60**, fitting two dense geometry axes (5×5) against
a sizing arm. Compute is not the constraint: at 1d across two assets a spec is
roughly two seconds, so 250 specs is minutes, and CSCV over 12,870 splits is
cheap.

## Selection rule

Stated in the SOP's own terms so the two documents read the same. The rule has
two roles and the implementation must keep them separate:

**The objective**, used identically everywhere below and for every sibling
regardless of its screen or gauntlet outcome: **train-window annualized Sharpe**,
computed from the committed `equity.csv` (fact 4). One objective, one formula, no
per-outcome variants.

**Qualification (a gate, per config):**

1. `best` = highest objective score in the family.
2. `plateau(F)` = every sibling scoring ≥ 0.9 × `best`.
3. A candidate qualifies only if **it and all of its ±1-step geometry
   neighbours** are in `plateau(F)`, **and every swept axis has a sibling one
   step below AND one step above it** (reason `edge_of_grid` otherwise).

   **Added 2026-08-18 (Coen).** The original wording said "all of its ±1-step
   neighbours", which is vacuously satisfied when a neighbour does not exist —
   so a candidate at the edge of a grid qualified on half the evidence. That
   is not academic: in a real fixture a candidate scoring 1.00 with one
   neighbour at 0.95 tied with one scoring 0.98 whose neighbours were 0.95 and
   0.99, and won the tie-break. The candidate with less evidence was
   advantaged, partially reinstating the point-winner bias this gate exists to
   remove. Both failure shapes disqualify: sitting at grid index 0 or `len-1`,
   and sitting mid-grid with a neighbouring grid point that was never
   registered as a sibling.

   Two consequences, accepted deliberately: a **two-value sweep can never
   produce a survivor** (both points are edges), and on a three-value sweep
   only the middle point is eligible. `composer.validate_family` therefore
   requires at least three values per swept axis, so the Composer cannot waste
   a generation on a structurally unpromotable family.
4. Any neighbour that died at screen on `trade_count` is a **cliff** and
   disqualifies the candidate outright (D3).

**Selection (replaces the DSR sort):**

5. Among qualifying gauntlet passers, select `argmax` of the **worst score across
   the candidate and its neighbours**. Ties break on lexicographic `sid`, keeping
   the existing determinism convention.

`select_survivors` (gauntlet.py:178) currently sorts on `-dsr` and takes
`ranked[0]`. After this it never reads a point metric to choose, only a
neighbourhood minimum.

**Neighbour definition:** configs differing from the candidate by exactly ±1 grid
index on exactly one dense geometry axis, that exist in the family's sweep.

## Chain mechanics

Two chain writes, in this order, each preceded and followed by a chain verify and
a scanner-idle check (`logs/status.json` `next_run`, plus the mtime of
`registry_log.jsonl`):

1. **`gauntlet-protocol-v4` note, pre-declared, before a single gen-4 spec
   exists.** Cross-references `quarantine-standard-asymmetry` for retroactivity.
   States the three added gates and thresholds, the plateau rule verbatim, the
   three surviving differences below, and — before any gen-4 number exists — that
   this raises the chance of a zero-survivor generation and that zero is an
   acceptable outcome.
2. **Block types chain on the first non-dry Composer run**, through the existing
   `block_type_registered` mechanism. No new machinery.

The write-free diagnostic runs **after the note and before the Composer run**, so
its output is on the record before any gen-4 spec exists.

**Ratchet position (a property of the note, not a separate write): v4 only
tightens.** Three gates added, none removed, no threshold loosened. It therefore
carries none of the evidence-and-argument burden protocol-v3 had to discharge,
and the note says so explicitly.

**Concurrent-session discipline.** A concurrent session shares this branch,
working directory and git index. Every commit is
`git add <paths> && git commit -m "..."` as one command, nothing is ever left
staged, and `git show HEAD --stat` runs after each. At time of writing the
scanner is live and appending to `registry_log.jsonl`; every chain write waits
for a quiet window.

## The diagnostic

`diagnose_protocol_v4.py`, matching the `diagnose_protocol_v2.py` precedent.
Write-free. Reports what v4 *would* have done to the existing 80, purely as a
ratchet check that the new gate has teeth. It changes no verdict, writes nothing,
and re-judges nothing. If a majority of already-buried specs would now pass, the
standard is tightened **before** gen-4 runs.

This exists because the plateau bar is hard and untested: requiring a candidate
*and all its neighbours* to clear 90% of the family best may be brutal on a real
family. The diagnostic is how that is discovered before the standard is chained
rather than after.

## What changes in `trading-systems`

**No code moves between trees, and no gate changes.** `trading-systems/CLAUDE.md`
forbids cross-tree imports; copying data is fine.

What it gets is a pointer: the vault's `SOPs/sop-trading-system-build.md` and the
repo's docs both name protocol-v4 as the shared gate standard, so the two
processes cite one document instead of two.

### Three differences that survive, argued on-chain

1. **DSR stage.** Both use ≥ 0.95. `trading-systems` applies it at designation
   off a known N=1737; the research layer applies it at `quarantine → live` on
   the forward record (protocol-v3). Each applies it where the evidence to
   compute it honestly exists.
2. **N deflation.** `trading-systems` uses raw trial count. The research layer
   clusters to effective trials. Defensible, but a real difference in the
   denominator that must not hide inside a shared threshold.
3. **Sample-size floor.** The SOP says under ~100 trades, declare it and tighten
   every gate. The research layer's screen floor is 40 trades. This gap is
   currently undeclared; this is the first document to own it.

## Testing

TDD red-first, subagent-driven with two-stage review. Scoped command:

```
python -m pytest pipeline/test_pipeline.py pipeline/test_composer.py pipeline/test_screen.py pipeline/test_gauntlet.py pipeline/test_gen2.py pipeline/test_gen3.py pipeline/test_gen3b.py -q
```

New suites:

- **CSCV / PBO**, known-answer cases: PBO near 0.5 under pure noise; near 0 for a
  genuinely dominant config; behaviour at small N documented rather than assumed.
- **Neighbour enumeration and the cliff veto**, including families where a
  neighbour is absent from the sweep.
- **Selection determinism and order-independence**, matching the `cluster.py`
  precedent.

Two regressions that pin what is most likely to break silently:

- **The 80 existing composition fingerprints are unchanged** after the dense
  types are added. Fact 3 argues this is safe; the test makes it evidence.
- **The diagnostic writes nothing**, asserted mechanically rather than by reading
  the code and believing it.

The full `trading-systems` suite (907 tests, ~45 minutes) runs once before
anything ships, even though this scope should not touch it — because "should not"
is not evidence.

## Success criteria

- One chained protocol note both pipelines' documentation points at.
- A research-layer candidate and a `trading-systems` candidate face the same named
  gates at the same thresholds, or the difference is one of the three argued above.
- `gauntlet.select_survivors` no longer picks a point winner.
- The live chain still validates; no existing verdict changes.
- All 80 existing fingerprints unchanged.

## Explicitly out of scope

- Re-judging anything already decided. The 77 buried strategies stay buried;
  gen-1/2/3 verdicts stand. This is the project's most load-bearing rule.
- The `quarantine → live` DSR gate calibration. Still deliberately unchosen, and
  it cannot bind for 60 trading days. Separate pre-declaration.
- Changing `screen-protocol-v1` or the training fence.
- Changing `trading-systems` gates.
- Running gen-4. This design ends at a chained standard and a green build; the
  live sequence is separately Coen-gated.

## Stated before the results exist

Adding gates raises the chance of another zero-survivor generation. Gen-3 was the
first generation ever to produce survivors, and this standard is strictly harsher
than the one that produced them. **A zero-survivor gen-4 is an acceptable
outcome.** It is recorded here, before the standard is chained and before any
gen-4 number exists, so it cannot be rationalised afterwards.
