# CONTINUE: research-layer clustering performance fix (then the small-items queue)

Written 2026-08-28 by the market-expansion session at handoff. Read the vault first
(MEMORY.md -> `project_market_expansion.md`, the 2026-08-26/27/28 sections), then this.
Repo: `E:\Users\Coen\Claude\stewart-forward-test`, work from `research-layer/`, branch
`claude/ai-agent-business-automation-0lzfd9`. HEAD at handoff: `fb80231`.

## The problem, measured (not estimated)

The 2026-08-27 bond+metal gauntlet run printed the pipeline's first per-stage timings:

    [gauntlet] stage timings: clustering 45524.8s, candidate eval 1194.3s (6 worker(s)),
               pbo 483.4s, artifacts 202.3s, total 47455.6s

Clustering = **96% of a 13.2h run**, over 5,235 registered strategies, and it grows
quadratically with the chain forever (every generation adds hundreds of rows). Everything
else is already fast (the P1-P5 perf batch, commits 74b9703..b0846f4). Observed phase
split from live stack sampling: the O(n^2) correlation matrix took roughly 2.5-3h; the
agglomerate + silhouette sweep took the remaining ~7-9h (it is O(n^2) per k over many k
values -- the worse half). Both must be fixed.

## Where the code is

`pipeline/cluster.py` (pure stdlib): `correlation` (:20-34, hand loop per pair),
`distance` (:36, rho clamped), `distance_matrix` (:41+, every ordered pair),
`agglomerate` (hot genexpr at :77), `labels_for_k`, `silhouette`;
`effective_trials` (:136) picks k maximising mean(S)/stdev(S) over k in [2, n-1].
Caller: `pipeline/gauntlet.py` `run()` -- the sim cache (pipeline/simcache.py) already
serves every registered spec's dated daily-returns series cheaply, intersection-aligned
(`intersect_returns`), so the matrix input is a ready-made rectangular float array.
numpy IS available (pandas is a dependency); scipy is NOT verified -- prefer numpy-only.

## The fix (two options; option A first)

**A. Vectorise (recommended, no protocol change):** build the aligned series into one
numpy matrix; correlation matrix via a single BLAS call (demean, normalise, X @ X.T);
distance = sqrt(0.5*(1-rho)) elementwise with the clamp; rewrite agglomerate/silhouette
on numpy (precompute the condensed distance matrix ONCE; incremental average-linkage;
vectorised silhouette per k). THE CONSTRAINT: `trials_n` feeds deflated Sharpe on every
verdict (recorded). Float summation order differs between BLAS and the hand loop, so
prove one of:
  (1) k and cluster labels IDENTICAL to the old implementation on regression fixtures
      INCLUDING a real-scale one (build a fixture from a few hundred real cached series;
      old-vs-new labels equal, trials_n equal, trials_sr_var within 1e-9), or
  (2) if ties genuinely flip under ulp differences, STOP and report -- that becomes a
      declared protocol note, not a silent change.
Keep the old implementation importable (e.g. `_correlation_ref`) so tests can compare.

**B. Declared clustering-population rule (fallback, ONLY if A is insufficient):** capping
or sampling which strategies enter `effective_trials` changes what trials_n MEANS -- that
is a protocol change and requires its own pre-registration (v6 record-don't-gate
discipline; write the pre-reg doc first, Coen approves before it runs). Do not reach for
B without measuring A first.

Target: full-registry clustering under ~10 minutes at 5,235 (it is one 5,235x~6,900
correlation matrix -- BLAS does this in seconds; agglomerate/silhouette dominate after).
Ship bar: timed rerun proof. There are NO candidates waiting (last generation completed),
so prove it with a tmp-registry copy of the REAL chain: gauntlet on a copied registry +
real data + warm sim cache, old wall vs new wall, verdict metrics identical.

## Process (standing, this workspace)

superpowers:subagent-driven-development: fresh implementer per task with full task text,
adversarial spec+quality reviewer, fix loop. Plan first (superpowers:writing-plans) into
`docs/plans/`. Hazards (all load-bearing): a RESIDENT scanner writes `sources/*.jsonl` in
this tree -- never `git add -A`, never touch `sources/`, never `reset --hard`, never
stash; `registry_log.jsonl` (15,666 entries, VALID) + `artifacts/` are production --
tests/probes on tmp copies only; scoped explicit-path commits, verify `git show --stat
HEAD` after each (shared index: concurrent commits have swept files before); no em-dashes
in new prose; foreground commands cap at ~600s -- run the full suite in two chunks
(`pipeline/test_[a-h]*.py` then the rest) and NEVER have a subagent wait on a background
notification (it will stall; five agents did). Known allowed test failure: ONLY
`test_scanner.py::test_committed_watchlist_loads_and_gate_tracks_verification`
(live-data-coupled; a fix chip exists). Python 3.14 system PATH, no venv.

## After clustering lands, in order

1. Rerun the timed proof, record the new number in the vault
   (`project_market_expansion.md`) + MEMORY.md line.
2. Fix the scanner watchlist test (the standing chip): make
   `test_scanner.py::test_committed_watchlist_loads_and_gate_tracks_verification`
   hermetic (it reads the LIVE `sources/verified_sources.json` at :65; use a tmp fixture
   copy). After it: the full suite should be 100% green for the first time in a week.
3. STOP THERE. Do not start Track 3 / futures work: that is gated on Coen's Norgate
   readout (~2026-08-30; trial hard stop ~09-13; USD 270 decision is his). The clustering
   fix exists precisely so a futures generation turns around in under an hour when he
   says go.

## Context you should NOT rediscover (verified this week)

- The estate's free-lane result: fx 4 gate-passes observing, equity 96 = beta (0/96 beat
  own-ETF buy-and-hold, median excess -48.5%, `docs/runs/2026-08-26-eq-gen1-benchmark-
  report.md`), bond+metal 0/303. Quarantine pool 120. No new edge claims -- deliberate.
- `benchmark_relative` is recorded on every drifting-class verdict (pre-reg in
  `docs/2026-08-24-sp4-track2a-addendum.md`); the snapshot adapter verifies the PINNED
  PREFIX (spec s10.12) because manifests pin first-sha while parquets live.
- ENGINE_REV ("e2") is a cache-key component -- bump it if any change alters simulated
  numbers. The sim cache key also covers resolved periods_per_year.
- The parallel-eval "flake" was clock-dependent fixture identity, fixed by pinning
  created_utc (ec22241). If it reappears, suspect fixture identity, not the pool.
