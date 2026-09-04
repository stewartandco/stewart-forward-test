# SP4: gauntlet performance preconditions + benchmark-relative control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** The gauntlet pass drops from ~26 h (eq-gen1) to a target of well under 2 h without changing ANY recorded number's meaning, and gauntlet verdicts on drifting-underlying classes additionally record a benchmark-relative edge (Coen 2026-08-26: "implement this" for both).

**Hazards (unchanged, load-bearing):** resident scanner writes `sources/*.jsonl` (never `git add -A`, never touch, never `reset --hard`, never stash); `registry_log.jsonl` + `artifacts/` are production (tests on tmp registries only); crypto-recorded numbers must remain BYTE-IDENTICAL where a task claims no numeric change; every optimisation needs a same-answer regression proof, not a hand-wave. No em-dashes in new prose. Python 3.14 on PATH, run from `research-layer/`.

**Verified hot spots (2026-08-26, py-spy on the live 26 h run + code reads):**
- `engine.py:28` and `:82`: `statistics.stdev` per bar in rolling loops — stdlib statistics uses exact-Fraction arithmetic; this multiplies across every sim (candidates, 1,815 registry re-runs, perturbation neighbours). Dominant cost.
- `gauntlet.py:442-446`: every registered strategy re-simulated every pass for clustering (grows with the chain forever).
- `pbo.permutation_null`: 200 draws x CSCV per sibling group (~10 h at eq scale) for a recorded-not-gated statistic.
- Perturbation stage: `sensitivity` re-runs neighbours through the same slow engine.
- Zero progress output: 26 h black box.

---

### Task P4 (FIRST — biggest win, smallest diff): float rolling stats in the engine

**Files:** `pipeline/engine.py`, `pipeline/test_engine_classes.py`

- [ ] Replace both `statistics.stdev` call sites with a float implementation: rolling window stdev via explicit two-pass float math per window (simple, allocation-free: `m = sum(w)/n; var = sum((x-m)**2 for x in w)/(n-1)`), sample (n-1) semantics IDENTICAL to `statistics.stdev`. Drop the `import statistics` if nothing else uses it.
- [ ] **Same-answer proof (capture-first):** BEFORE editing, run the Task-3 crypto fixture AND an fx fixture through the current engine capturing full trades+equity; pin both in a test with a comment; after the edit they must reproduce to `abs=1e-9` per value (float vs Fraction rounding differs in the last ulps; 1e-9 on returns/equity is far below any recorded precision — state this in the test comment; if any pinned gauntlet-level metric shifts at recorded precision, STOP and report).
- [ ] Microbenchmark in the commit body: time `simulate_asset` on 8,000 synthetic bars with `vol_target`, before vs after (expect >=50x).
- [ ] Full named tests: `python -m pytest pipeline/test_engine_classes.py pipeline/test_pipeline.py pipeline/test_gauntlet.py pipeline/test_gauntlet_classes.py -q`. Scoped commit: `perf(sp4): float rolling stats in the engine, same-answer pinned (P4)`.

### Task P1: cache the registry-wide re-simulations

**Files:** `pipeline/simcache.py` (new), `pipeline/test_simcache.py` (new), `pipeline/gauntlet.py`

- [ ] `simcache.py`: a content-addressed on-disk cache under `research-layer/simcache/` (gitignored — add to `.gitignore`, scoped commit allowed for that one line). Key = sha256 of `(strategy_id, data_sha256 per asset of the spec's cells, ENGINE_REV)`. `ENGINE_REV` is a new module constant in `engine.py` (string, bumped by hand on ANY engine numeric change; P4 bumps it to `"e2"` — document the contract in both files). Value = the daily returns-with-dates series the clustering needs (JSON, compact), NOT full trades (small, sufficient). API: `get(key) -> series | None`, `put(key, series)`, atomic write (tmp+rename).
- [ ] `gauntlet.py` clustering loop: consult the cache before re-simulating a registered spec; write on miss. CANDIDATES (this pass's own evaluations) are never cached reads — they are always fresh sims (their full metrics are needed anyway); only the registry-wide clustering re-runs use the cache. Record `"sim_cache": {"hits": n, "misses": m}` in each verdict's metrics (recorded, additive).
- [ ] Same-answer proof: a tmp-registry gauntlet run twice — second run's verdicts byte-identical to the first except the cache counters; a poisoned cache entry (wrong series injected) is DETECTED if you include a cheap self-check (store the series sha in the entry; verify on read; treat mismatch as miss + delete). Tests for hit/miss/poison/ENGINE_REV-bump-invalidates.
- [ ] Scoped commit: `perf(sp4): content-addressed sim cache for registry-wide clustering re-runs (P1)`.

### Task P2+P3+P5 (one task, all gauntlet.py): parallel evaluations, PBO draws, progress output

**Files:** `pipeline/gauntlet.py`, `pipeline/pbo.py`, `pipeline/test_gauntlet_classes.py`

- [ ] P2: evaluate candidates with `concurrent.futures.ProcessPoolExecutor` (workers = `max(1, os.cpu_count() - 2)`; keep 2 cores free — Morpheus/gbp/SDCA share this machine, cite the workspace rule). Each worker gets (spec, its bars) and returns the metrics dict; results merged in DETERMINISTIC spec order regardless of completion order. Seeded stochastic steps (MC bootstrap) must produce identical numbers in- vs out-of-process (seeds are content-derived already — assert one candidate's metrics equal a single-process run in a test). Windows spawn semantics: guard the pool behind the existing `if __name__` entry + a module-level worker function (no closures).
- [ ] P3: `--pbo-null-draws` default 200 -> 50, and the null is computed ONLY for groups where >=1 member passed the gate battery (dead groups get `null_draws: 0`, verdict "not_measured_dead_group" — a new honest label, distinct from "underpowered"). Rationale comment: recorded-not-gated statistic (chain entries 2513-2515 context); 200-draw nulls remain available via the flag for a deliberate deep run.
- [ ] P5: per-stage wall-time lines to stdout as each stage completes (`[gauntlet] clustering done in 312s (cache 1710 hits / 105 misses)`, `[gauntlet] evaluated 120/650 ...` every 25 candidates, `[gauntlet] pbo group 12/80 ...`). Unbuffered-friendly: `print(..., flush=True)`.
- [ ] Same-answer proof for P2 (parallel == serial on a tmp registry), P3 changes only null_draws/labels for dead groups (a passing group's percentile at 50 draws differs from 200 — that is a DECLARED protocol parameter change, recorded in the plan and the verdict's `null_draws`; existing chain untouched).
- [ ] Scoped commit: `perf(sp4): parallel candidate evaluation, gated PBO nulls at 50 draws, per-stage progress (P2 P3 P5)`.

### Task B1: benchmark-relative control (recorded, not gated)

**Files:** `pipeline/gauntlet.py`, `pipeline/cells.py`, `pipeline/test_gauntlet_classes.py`; pre-registration text appended to `docs/2026-08-24-sp4-track2a-addendum.md`

- [ ] Pre-registration first (commit the doc edit before the code): for every gauntlet verdict on a cell whose class declares `benchmark: "self"` (new class field: equity_etf yes; bond/metal at 2b; fx/crypto `None` — declared, per class), record `metrics["benchmark_relative"] = {"window": "oos", "strategy_net": <oos net>, "buy_hold_net": <same-window buy-and-hold net of the cell's own asset, one round trip of the class cost model>, "excess": strategy minus buy-hold, "basis": "price returns, dividends excluded on both sides"}`. RECORDED, NOT GATED (v6 philosophy); a future protocol change may gate on it only via its own pre-registration.
- [ ] Implement + tests (fixture where B&H is known analytically; equity verdict carries the block, crypto/fx verdicts carry `benchmark_relative: null` — wait: absent key or null? ABSENT for classes with benchmark None, per the no-null-placeholder convention; pin that).
- [ ] One-off recorded analysis of the EXISTING 96 (no chain mutation): a script `tools_benchmark_backfill_report.py` (layer root) that recomputes benchmark_relative for the 96 eq quarantine occupants from their committed artifacts + the pinned data and writes `docs/runs/2026-08-26-eq-gen1-benchmark-report.md` (table: sid, cell, family, oos net, B&H net, excess; summary counts of excess>0). This is a report, not chain data — the chain gets benchmark_relative only for future verdicts.
- [ ] Scoped commit: `feat(sp4): benchmark-relative control recorded on drifting-class verdicts + eq-gen1 backfill report (B1, Coen 2026-08-26)`.

### Task V (controller): validation pass

- [ ] Full suite; rerun the fx dry-run harness (should now be minutes) and compare its verdict numbers to the pre-perf run captured 2026-08-24 (same fixture, same registry inputs: numbers must match at recorded precision except null_draws/progress/cache counters).
- [ ] Time a synthetic full-scale pass; record the new wall time in the vault; declare 2b unblocked if under target.

## Self-review
Placeholders: none — every task carries its own same-answer proof requirement. Order: P4 first (ENGINE_REV bump feeds P1's key), then P1, then P2/P3/P5, then B1 (touches the gauntlet last, after the perf dust settles). Type consistency: ENGINE_REV contract named in P4 and P1; benchmark class field declared in cells and consumed in gauntlet; "not_measured_dead_group" label distinct from "underpowered" everywhere it appears.
