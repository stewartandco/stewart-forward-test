# Gauntlet Battery v1 — design spec (2026-08-14)

The fourth pipeline stage: run the 13 `gauntlet`-state strategies through the
five-gate validation battery on the untouched 2024+ holdout, chain verdicts,
and apply the pre-declared sibling-selection rule. Quarantine *wiring* (daily
posting to the forward-test log) is explicitly out of scope — survivors land
in `quarantine` state on the registry chain only.

Kickoff decisions (Coen-approved):

| Decision | Choice |
|---|---|
| Scope | Gauntlet verdicts + state changes only; no forward-test-log integration |
| Sibling selection | ONE quarantine slot per sibling group: highest DSR among passers (tie → lexicographically smallest strategy_id); other passers → graveyard `sibling_not_selected` |
| Monte Carlo gate | SCHEMA's literal "P05 equity > 0" is unfalsifiable under no-leverage sizing — amended pre-results to **P05 terminal equity > 1.0** (5th-percentile path still profitable), 2,000 seeded bootstrap resamples of pooled trades |
| Ruin gate | Ruin = equity ≤ 0.5× start at any point in a resample path; **P(ruin) < 5%** |
| DSR gate | SCHEMA's "DSR > 0" is vacuous (DSR is a probability) — amended pre-results to **DSR ≥ 0.95**, computed on the full-run daily curve with trials = `sibling_group_n` |
| OOS structure | Specs are frozen at registration (nothing to re-fit), so walk-forward degenerates to a single OOS pass: full-history run, trades attributed IS/OOS by entry date vs 2023-12-31 |

## 1. Modules

| File | Responsibility |
|---|---|
| `pipeline/stats.py` | Pure stdlib statistics: `normal_cdf` (math.erf), `sharpe_daily` (annualized √365), `moments` (skew/excess kurtosis), `psr` (Probabilistic Sharpe Ratio with higher-moment corrections), `expected_max_sharpe` (Bailey–LdP E[max SR] under N trials), `dsr`, `bootstrap_paths` (seeded resampler returning terminal-equity distribution + ruin count) |
| `pipeline/gauntlet.py` | CLI: protocol guard, orphan check, per-spec battery, two-phase chain writes, selection, artifacts |

CLI: `python -m pipeline.gauntlet [--registry registry_log.jsonl] [--data-dir data] [--artifacts-dir artifacts] [--cutoff 2023-12-31] [--dry-run]`

## 2. Battery (per spec, in order)

Each spec runs `engine.run_spec` twice over the FULL bar history (all data,
no fence — the holdout is now being consumed, by design): once at the spec's
registered `cost_model`, once with slippage doubled
(`slippage_ticks × 2`). Trades attribute as IS (entry_date ≤ 2023-12-31) or
OOS (entry_date > 2023-12-31).

Full-history-run note: IS-attributed trades may differ marginally from the
screen artifacts at the fence boundary (a position open across it); the
gauntlet uses its own full-run attribution consistently for both edges.

| Gate | Test | Fail reason |
|---|---|---|
| (a1) OOS positive | net P&L of OOS-attributed trade contributions > 0 | `oos_negative` |
| (a2) Edge decay | `(oos_edge − is_edge) / abs(is_edge) × 100 > −25` where each edge = mean per-trade `return_net × notional_frac` contribution... see below | `edge_decay` |
| (b) Monte Carlo | 2,000 bootstrap resamples (with replacement, length = pooled trade count) of pooled IS+OOS per-trade portfolio contributions, compounded sequentially from 1.0; 5th percentile of terminal equity > 1.0 | `mc_p05` |
| (c) Ruin | within each resample path, min equity ≤ 0.5 = ruined; ruined/2000 < 0.05 | `p_ruin` |
| (d) Deflated Sharpe | DSR ≥ 0.95 (see §3) | `dsr` |
| (e) Cost stress | OOS net P&L at 2× slippage > 0 | `cost_stress` |

Five gates, six checks (gate (a) has two clauses). PASS = all six. Fail
reason recorded = FIRST failed check in the fixed order above.

**Trade contribution definition.** The engine's `trades` carry `return_net`
(per-notional). The portfolio contribution of a trade is
`return_net × notional_frac` where `notional_frac = notional / equity_at_entry`
— the engine must be extended to record `notional_frac` per trade (small
`engine.py` addition: include it in the trade dict; existing tests updated
only by addition, no behavioral change to equity math). Edges and MC resample
this contribution series; compounding is `equity *= (1 + contribution)` —
a deliberate, documented simplification of the engine's additive booking
(`equity += notional × net`), equivalent at these position sizes and
self-consistent within the MC.

**Edge decay sign convention.** decay is computed only when `is_edge > 0`
(guaranteed: every gauntlet-state spec passed the screen's net-positive gate
on IS data — but the full-run IS attribution could in principle flip a
boundary case negative; if `is_edge ≤ 0`, gate (a2) fails with reason
`edge_decay`).

## 3. Deflated Sharpe (gate d)

- Daily returns from the full-run combined equity curve (mark-to-market,
  T ≈ 3,280).
- `SR_hat` = mean/std of daily returns, annualized ×√365; computed
  non-annualized for PSR (PSR works in per-period units; annualization only
  for display).
- Trials N = the size of the spec's sibling group, COMPUTED as the count of
  `strategy_registered` entries sharing its `provenance.sibling_group_id`
  (12, 4, or 6 for the current batch; the verdict metric `sibling_group_n`
  records the value used). Variance across trials: sample
  variance of the observed per-period Sharpes of ALL siblings in the group
  (each sibling's full-run daily curve; computed once per group).
- `SR* = E[max SR]` per Bailey–LdP:
  `SR* = sqrt(V[SR]) × ((1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)))`, γ =
  Euler–Mascheroni ≈ 0.5772. Φ⁻¹ implemented as a stdlib rational
  approximation (Acklam/Moro-style) in `stats.py` with unit tests against
  known quantiles.
- `DSR = PSR(SR*)` with the standard higher-moment PSR:
  `PSR = Φ( ((SR_hat − SR*)·√(T−1)) / √(1 − γ₃·SR_hat + (γ₄−1)/4·SR_hat²) )`
  where γ₃ = skew, γ₄ = kurtosis (non-excess) of the daily returns.
- Gate: DSR ≥ 0.95.

Graveyarded siblings still contribute their Sharpe to the group's V[SR]
estimate (their curves are computed for this purpose only, not chained).

## 4. Two-phase writes + selection

Phase 1 — verdicts: for each of the 13, chain `verdict` (stage `gauntlet`,
pass/fail, metrics exactly `{is_edge_per_trade, oos_edge_per_trade,
edge_decay_pct, mc_p05_equity, p_ruin, deflated_sharpe, sibling_group_n,
cost_stress_net_pnl}`, artifacts_hash).

Phase 2 — state changes: failers → `graveyard` (reason = fail reason);
per sibling group, passers ranked by DSR desc then strategy_id asc: rank 1 →
`quarantine`, rest → `graveyard` reason `sibling_not_selected`.

Orphan health-check at startup (both modes): any strategy in `gauntlet` state
that already has a gauntlet-stage verdict = mid-run crash artifact → refuse
(exit 1) with an ORPHANED listing. PARTIAL WRITE stderr guard on both phases.

## 5. Protocol note

Before the first real run, one `note` with text starting
`gauntlet-protocol-v1:` records: the five-gate definitions (incl. both
pre-results amendments and their rationale — unfalsifiable literal gates),
the selection rule, IS/OOS attribution, the MC seed derivation
(`random.Random(int(strategy_id, 16))`), and the trade-contribution
compounding convention. `gauntlet.py` hard-refuses real runs without it;
dry-run allowed.

## 6. Artifacts

`artifacts/<strategy_id>/gauntlet/`:
- `oos_trades.csv` — the OOS-attributed trades (same columns as screen
  trades.csv + `notional_frac`)
- `mc_summary.json` — seed, resample count, P05/P25/P50 terminal, p_ruin
- `config.json` — protocol string, cutoff, data hashes, full metrics, group
  ranking context (group members + DSRs at selection time)

`artifacts_hash` = same `bundle_hash` convention as the screen (LF-normalized
bytes, fixed file order). Covered by existing `.gitattributes`.

## 7. Testing (offline)

- stats.py units: `normal_cdf` at 0/±1.96/±2.58; `psr` hand-case; inverse-CDF
  against known quantiles (0.975 → 1.95996…); `expected_max_sharpe` monotone
  increasing in N, with SR* = 0 at N = 1 by convention (DSR reduces to
  PSR(0)); seeded `bootstrap_paths` determinism (same seed → same P05) and
  ruin count on a hand-built path that dips below 0.5.
- Gate boundaries on synthetic trade lists: each of the six checks failing
  alone produces its reason; pass-all case advances.
- Selection: 3-passer group picks highest DSR; tie-break by id; single-passer
  group; zero-passer group (all graveyard).
- Guards: protocol note required for real runs; orphan (verdict-but-still-
  gauntlet) refused; dry-run writes nothing.
- Integration: tmp registry with a 2-sibling group (one engineered to pass,
  one to fail) → verdicts + transitions → `verify_registry.py` exits 0;
  funnel shows quarantine=1.
- Live-chain regression: the 435-entry registry still validates.
- engine.py `notional_frac` addition: existing simulator tests still pass;
  new assertion that `notional_frac` ≈ notional/equity at entry.

## Out of scope

Quarantine wiring (daily `RL-<id>` posting into the root forward-test log,
scheduler integration) — next sub-project. Mutation rounds (`generation ≥ 1`).
Any touch of the root log.
