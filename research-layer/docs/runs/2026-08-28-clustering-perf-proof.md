# Clustering perf fix -- ship-bar proof (2026-08-28)

Plan: `docs/plans/2026-08-28-clustering-perf.md`. Commits under proof:
`0897308..8aed4df` (numpy fast path in `pipeline/cluster.py` + dispatcher +
timing line) and `6c38de5` (identity tool). Branch
`claude/ai-agent-business-automation-0lzfd9`.

## Baseline (2026-08-27 bond+metal gauntlet, real run)

    [gauntlet] stage timings: clustering 45524.8s, candidate eval 1194.3s
               (6 worker(s)), pbo 483.4s, artifacts 202.3s, total 47455.6s

Clustering was 96% of the 13.2h wall over 2,775 registered strategies
(the vault's earlier "5,235" figure was wrong; every recorded verdict says
`registered_n: 2775`).

## Identity proof on real cached series (`tools_verify_cluster_identity.py`)

Run 1, `--n 400` (400 real simcache series, 2,234 common days):

    new path: k=2, var=2.0006942584463418e-05, 0.6s
    reference: k=2, var=2.0006942584463418e-05, 235.0s
    PASS: k and labels identical, var bit-identical; speedup x367

Independent coordinator rerun at `--n 150`: PASS, bit-identical, x128.

Run 2, `--full` (all 2,472 usable simcache entries, new path only):

    new path: k=172, var=0.0006125095276747956, 26.7s

## Timed gauntlet rerun on a tmp copy of the real chain

Recipe: `head -n 15060 registry_log.jsonl` (prefix ends just before the
first 2026-08-27 gauntlet verdict; the 303 bond+metal candidates return to
`gauntlet` state; chain prefix loads clean, 2,775 registered), simcache
copied, artifacts to tmp, real `data/`, CLI defaults. Real mode (verdicts
written to the tmp chain), nothing in production touched (verified: live
chain byte-clean vs HEAD after the run).

    [gauntlet] effective_trials 37.3s (pure clustering, inside the clustering stage)
    effective trials: 2 clusters over 2775 registered strategies
    sim cache: 2317 hit(s), 155 miss(es) over 2472 non-candidate registered strategies
    303 evaluated: 0 -> quarantine, 303 gate-fail -> graveyard.
    [gauntlet] stage timings: clustering 929.0s, candidate eval 1169.2s
               (6 worker(s)), pbo 459.9s, artifacts 193.1s, total 2767.2s

- Pure clustering (matrix + agglomerate + silhouette sweep + reps):
  **45,525s-era ~12.6h -> 37.3s.** The rest of the 929.0s clustering stage
  is sim-cache JSON reads plus 155 cache-miss re-simulations, unchanged by
  this plan. (The 37.3s also includes `_reps_variance`'s pure-Python
  Sharpe pass, by design.)
- Whole gauntlet: 47,455.6s -> 2,767.2s (17x); the next generation's
  gauntlet is expected well under 1h at current chain size.

## Verdict identity (the ship bar)

All 303 rerun verdicts compared field-by-field against the recorded
2026-08-27 verdicts (excluding only the warmth-dependent
`metrics.sim_cache` counters):

    compared 303 verdicts
    HARD differences: 0
    soft (tolerated) differences: 0

Zero differences of any kind: verdict strings, trials_n, trials_sr_var,
deflated_sharpe, era summaries, benchmark_relative, pbo fields and
artifacts_hash all reproduce EXACTLY (no float drift, not merely within
tolerance).

## Identity engineering that made this hold (found in review, fixed pre-proof)

- Constant-nonzero return rows classify zero-variance
  summation-order-dependently between the paths; `_returns_matrix` routes
  such input to the reference path (cb764da).
- Byte-identical duplicate series hit the rho=1 clamp differently under
  BLAS vs the reference's pow/multiply mix (reference itself lands 1-1ulp
  on ~0.09% of duplicate lists); duplicate-row pairs are pinned to the
  reference-computed distance (8aed4df). Pre-fix this flipped k/labels/var
  on duplicate-heavy pools (49/300 adversarial fixtures); post-fix 0/300.
- Residual known divergence class: values within ulps of a half-1e-12
  rounding boundary (module docstring); did not manifest on any real data
  probe. This proof is the recorded-data gate.

## Perf notes for future scale

- Adversarial cluster structures (one tight cluster absorbing everything)
  push the cache-invalidation worst case toward ~8-9 min extrapolated at
  n~5,000; typical structure is 1-2 min. Budgeted, acceptable.
- The clustering stage is no longer the wall: candidate eval (1,169s) and
  sim-cache reads now dominate. Next perf item, if ever needed: cache the
  JSON parse (or store binary), not the clustering.
