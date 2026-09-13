# SP5 Phase 1: Data + Benchmarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the universe manifest, the crypto data fetcher, edge numbering, the fx benchmark flip, the pooled-crypto benchmark audit, and the variation coverage map — Phase 1 of `docs/2026-08-28-market-data-universe-design.md` (D1, D3, D11, D12). Moves NO denominator: no cells.py grid change, no composer change, no chain writes.

**Architecture:** All new code is research-layer-native (no cross-repo imports). Fetching mirrors `pipeline/tradfi_data.py` conventions (explicit runs, snapshot manifest, verify-then-write). Audit/report tools mirror `tools_benchmark_backfill_report.py` (read-only, chain-derived cohorts, hard expected-N refusal, markdown into `docs/runs/`).

**Tech stack:** Python stdlib only (urllib, json, csv, hashlib) + pytest. No new dependencies. No LLM calls anywhere in this phase (zero metered spend).

---

## Conventions for every task (read first)

- Work in the worktree: `E:\Users\Coen\Claude\stewart-forward-test-sp5\research-layer` (branch `feat/sp5-phase1`). NEVER touch `E:\Users\Coen\Claude\stewart-forward-test` (the live tree — a resident scanner and scheduled tasks write there).
- Run tests from the layer root: `python -m pytest pipeline -q` (full) or `python -m pytest pipeline/test_<x>.py -q` (targeted). System python; no venv.
- Git: SCOPED adds with explicit paths only (`git add <file> <file>`), NEVER `git add -A`. One commit per task with the given message. Concurrent sessions share this machine.
- NEVER write to `registry_log.jsonl`, `logs/`, or any file in the live tree. Phase 1 is chain-READ-ONLY (reads need no chain lock — `chainlock.py:15`).
- Network calls are FORBIDDEN in tests — monkeypatch every HTTP boundary. Real network runs happen only in Task 7 (supervised, main session).
- Date strings: this repo has two CSV date formats (`YYYY-MM-DD` and `YYYY-MM-DD HH:MM:SS`). ALWAYS compare via `[:10]` slices (the `_date_le` convention, `gauntlet.py:141-158`).
- `python -c "import pipeline.cells"` must stay clean after every task (import-time assertions are load-bearing).

## File map

| File | Task | Role |
|---|---|---|
| `tools_select_crypto_universe.py` (create, layer root) | 1 | one-time universe selection -> pinned manifest |
| `pipeline/test_universe_select.py` (create) | 1 | selection logic tests (network-free) |
| `data/crypto_universe_manifest.json` (create, Task 7 run) | 7 | THE pinned universe (committed; `data/*.csv` is gitignored, json is not) |
| `pipeline/crypto_data.py` (create) | 2 | grid data fetcher + snapshot manifest + CLI |
| `pipeline/test_crypto_data.py` (create) | 2 | fetcher tests (network-free) |
| `pipeline/registry.py` (modify, append helpers) | 3 | edge numbering (D11) |
| `pipeline/test_registry_numbering.py` (create) | 3 | numbering tests |
| `pipeline/cells.py` (modify: fx dict + comment ONLY) | 4 | fx `benchmark: "self"` (D3) |
| `pipeline/gauntlet.py` (modify: `_benchmark_relative` basis) | 4 | per-class basis strings |
| `pipeline/test_cells.py`, `pipeline/test_gauntlet_classes.py` (modify) | 4 | pin updates |
| `tools_benchmark_backfill_report_crypto.py` (create, layer root) | 5 | pooled-crypto audit (read-only) |
| `pipeline/test_crypto_backfill_report.py` (create) | 5 | audit tests on synthetic chain |
| `tools_variation_coverage_report.py` (create, layer root) | 6 | coverage map (D12, read-only) |
| `pipeline/test_variation_coverage.py` (create) | 6 | coverage tests |
| `docs/runs/2026-08-28-crypto-pooled-benchmark-report.md` (Task 7 run) | 7 | audit deliverable |
| `docs/runs/2026-08-28-variation-coverage-report.md` (Task 7 run) | 7 | coverage deliverable |

---

### Task 1: Universe selection tool

**Files:** Create `tools_select_crypto_universe.py` (layer root), `pipeline/test_universe_select.py`.

The tool implements spec s2 (D1). Two pure functions carry ALL logic so tests never touch the network; `main()` only wires I/O.

- [ ] **Step 1: Write failing tests** (`pipeline/test_universe_select.py`):

```python
import json, pytest
from tools_select_crypto_universe import classify_exclusion, build_manifest, PinnedManifestError, write_manifest

def _coin(rank, cid, sym, mcap=1e9, price=100.0):
    return {"market_cap_rank": rank, "id": cid, "symbol": sym,
            "current_price": price, "market_cap": mcap}

def test_stablecoins_excluded_by_declared_list():
    assert classify_exclusion(_coin(3, "tether", "usdt")) == "stablecoin (declared list)"

def test_wrapped_and_staked_excluded_by_pattern():
    assert classify_exclusion(_coin(5, "wrapped-bitcoin", "wbtc")) == "wrapped/staked/bridged (pattern)"
    assert classify_exclusion(_coin(6, "lido-staked-ether", "steth")) == "wrapped/staked/bridged (pattern)"

def test_peg_heuristic_flags_unlisted_usd_peg():
    assert classify_exclusion(_coin(40, "some-new-usd", "xusd", price=1.001)) == "stablecoin (peg heuristic)"

def test_normal_coin_not_excluded():
    assert classify_exclusion(_coin(1, "bitcoin", "btc")) is None

def test_build_manifest_admits_exactly_100_with_reasons_and_incumbents():
    coins = [_coin(i, f"coin{i}", f"c{i}") for i in range(1, 131)]
    coins[0] = _coin(1, "bitcoin", "btc"); coins[1] = _coin(2, "ethereum", "eth")
    coins[2] = _coin(3, "tether", "usdt")
    coins[3] = _coin(4, "solana", "sol"); coins[4] = _coin(5, "ripple", "xrp")
    coins[5] = _coin(6, "binancecoin", "bnb")
    # binance_meta: symbol -> first 1d open (None = no USDT pair)
    meta = {c["symbol"].upper() + "USDT": "2020-01-01" for c in coins}
    meta["C9USDT"] = None                       # no pair -> excluded
    meta["C10USDT"] = "2025-06-01"              # <2y history -> excluded
    m = build_manifest(coins, meta, now_utc="2026-08-28T00:00:00Z")
    assert len(m["admitted"]) == 100
    assert {a["binance_symbol"] for a in m["admitted"]} >= {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"}
    reasons = {e["reason"] for e in m["excluded"]}
    assert "no Binance USDT spot pair" in reasons
    assert "history < 2y" in reasons
    assert all(e.get("reason") for e in m["excluded"])
    ranks = [a["rank"] for a in m["admitted"]]
    assert ranks == sorted(ranks)               # manifest order = mcap order

def test_write_manifest_refuses_overwrite(tmp_path):
    p = tmp_path / "u.json"; p.write_text("{}")
    with pytest.raises(PinnedManifestError):
        write_manifest({"admitted": []}, p)
```

- [ ] **Step 2:** `python -m pytest pipeline/test_universe_select.py -q` -> FAIL (module missing).
- [ ] **Step 3: Implement** `tools_select_crypto_universe.py`. Required contracts:

```python
"""One-time crypto universe selection (SP5 D1). Declared rule, pinned output.
READ side effects only: writes exactly one new file, data/crypto_universe_manifest.json,
and REFUSES to overwrite it (re-selection is a new declared event)."""
STABLECOIN_IDS = frozenset({"tether","usd-coin","dai","binance-usd","true-usd",
    "first-digital-usd","ethena-usde","usds","paypal-usd","frax","usdd","gemini-dollar",
    "paxos-standard","liquity-usd","susds","usual-usd","ondo-us-dollar-yield"})
WRAPPED_MARKERS = ("wrapped","staked","bridged","restaked","wbeth","wsteth","steth",
    "reth","cbeth","cbbtc","weeth","rseth","ezeth","meth","oseth","lseth","tbtc","solvbtc")
PEG_BAND = 0.02   # |price-1| < band AND ("usd" in id or symbol) -> peg heuristic

def classify_exclusion(coin) -> str | None: ...   # returns reason or None, exactly the strings tested above
def build_manifest(coins, binance_meta, now_utc) -> dict:
    # walk by ascending market_cap_rank; skip classify_exclusion hits (recorded);
    # skip no-pair (None in binance_meta) and first-1d newer than now-730d (recorded);
    # admit until 100. Manifest keys: selected_utc, rule (one-paragraph prose of D1),
    # source ("coingecko /coins/markets keyless"), admitted (rank,id,symbol,
    # binance_symbol,market_cap,first_1d_utc), excluded (rank,id,symbol,reason).
    # Assert the 5 incumbents are admitted (hard error otherwise).
class PinnedManifestError(RuntimeError): ...
def write_manifest(manifest, path): ...           # refuses existing path
def main():
    # real network (Task 7 only): GET coins/markets pages 1-2 (per_page=250,
    # vs_currency=usd, order=market_cap_desc), 3s sleep between calls (CoinGecko
    # keyless is ~5-15 req/min); for each non-excluded candidate GET binance
    # klines (interval=1d, limit=1, startTime=0) for first-bar date, 0.25s sleep;
    # then build_manifest + write_manifest(data/crypto_universe_manifest.json).
```

- [ ] **Step 4:** targeted tests PASS; then `python -m pytest pipeline -q` green.
- [ ] **Step 5: Commit:** `git add tools_select_crypto_universe.py pipeline/test_universe_select.py` ; message `feat(sp5): universe selection tool - declared top-100 rule, pinned manifest (T1)`

### Task 2: Crypto grid data fetcher

**Files:** Create `pipeline/crypto_data.py`, `pipeline/test_crypto_data.py`.

- [ ] **Step 1: Write failing tests.** Monkeypatch `crypto_data._http_get_json` (the ONLY network boundary). Cover, minimum:

```python
# pagination: two 1000-row pages then short page -> one merged CSV, rows == sum
# resume: existing CSV with last date D -> first request startTime == ms(D)+TF_MS["1d"]
# partial bar: kline with close_time > now_ms is dropped
# 429 path: _http_get_json raises RateLimited(retry_after=1) once -> retried, succeeds
# 418 path: raises BinanceBanned -> fetch_all aborts, partial .tmp cleaned up, error names "do NOT re-run"
# atomic write: monkeypatch os.replace to raise -> no truncated final CSV left
# snapshot manifest: fetch two symbols -> data/crypto_snapshot_manifest.json has both keys,
#   pre-existing unrelated key survives (key-wise merge, tradfi_data.py:294-305 convention)
# tf guard: fetch(tf="1w") raises ValueError (not in cells.TIMEFRAMES)
# default assets: no --assets -> reads data/crypto_universe_manifest.json admitted binance_symbols;
#   missing manifest -> clean error naming Task 1
# csv format: header date,open,high,low,close,volume; dates YYYY-MM-DD HH:MM:SS (data_import convention)
```

- [ ] **Step 2:** run -> FAIL. 
- [ ] **Step 3: Implement.** Contracts:

```python
BASE = "https://api.binance.com/api/v3/klines"
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
         "4h": 14_400_000, "12h": 43_200_000, "1d": 86_400_000}
# TF_MS here is fetch-step arithmetic for THIS module. The declared research
# grid is cells.TIMEFRAMES; fetch refuses any tf outside it. (Same separation
# as trading-systems cfg.timeframes vs TF_MS - never equalise.)
class RateLimited(Exception): retry_after: float
class BinanceBanned(Exception): ...
def _http_get_json(url): ...        # urllib, timeout 30; 429/418 -> RateLimited/BinanceBanned(Retry-After capped 120)
def _get_with_retry(url, tries=3): ...  # linear backoff 2*attempt; BinanceBanned NEVER retried
def fetch_symbol(symbol, tf, data_dir, now_ms=None): ...
    # resume from existing CSV tail; paginate limit=1000, sleep 0.2s/page;
    # drop unclosed final bar; merge+dedupe+sort; atomic .tmp -> os.replace;
    # returns {"rows": n, "sha256": h, "first_date": d0, "last_date": d1}
def update_snapshot_manifest(data_dir, results): ...  # key-wise merge, sorted keys
def main(argv=None): ...
    # subcommand: fetch --timeframes 1d[,4h...] [--assets A,B] [--data-dir X] [--manifest X]
    # whole-run abort on BinanceBanned with exit 1 and the ban message
```

- [ ] **Step 4:** targeted green, full suite green. 
- [ ] **Step 5: Commit:** `git add pipeline/crypto_data.py pipeline/test_crypto_data.py` ; `feat(sp5): crypto grid fetcher - ban-safe, incremental, snapshot provenance (T2)`

### Task 3: Edge numbering (D11)

**Files:** Modify `pipeline/registry.py` (append two functions at module level, nothing existing touched), create `pipeline/test_registry_numbering.py`.

- [ ] **Step 1: Tests:**

```python
from pipeline.registry import edge_numbers, edge_label
def test_numbers_follow_chain_order_one_based():
    entries = [
        {"entry_type": "strategy_registered", "payload": {"strategy_id": "aaa"}},
        {"entry_type": "verdict", "payload": {"strategy_id": "aaa"}},
        {"entry_type": "strategy_registered", "payload": {"strategy_id": "bbb"}},
    ]
    assert edge_numbers(entries) == {"aaa": 1, "bbb": 2}
def test_label_is_hash_prefixed_zero_padded_and_grows():
    assert edge_label(7) == "#0007"
    assert edge_label(123456) == "#123456"
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** exactly that (iterate entries, count `strategy_registered`, 1-based; `f"#{n:04d}"`). Docstring: "D11 (SP5): the chain's append-only order defines a stable sequential number per strategy; renumbering is impossible by construction. Display-layer only - never part of identity, provenance, or N accounting."
- [ ] **Step 4:** suite green. **Step 5: Commit:** `git add pipeline/registry.py pipeline/test_registry_numbering.py` ; `feat(sp5): edge numbering - chain-order sequential labels (D11) (T3)`

### Task 4: fx benchmark flip (D3)

**Files:** Modify `pipeline/cells.py` (fx dict value + the lines 91-100 comment block ONLY — no other class, no grid fields), `pipeline/gauntlet.py` (`_benchmark_relative` return), `pipeline/test_cells.py`, `pipeline/test_gauntlet_classes.py`.

- [ ] **Step 1: Update pins to the NEW truth first (they fail red):**
  - `test_cells.py:175`: `assert cells.CLASSES["fx"]["benchmark"] == "self"`; the per-class benchmark test (`test_benchmark_declared_per_class`, :169-180) updated: crypto None; fx/equity_etf/bond_etf/metal_etf "self".
  - `test_gauntlet_classes.py:1011-1024` (`test_benchmark_relative_absent_for_none_classes`): now crypto-only.
  - `:1052-1075` (`test_crypto_and_fx_verdicts_carry_no_benchmark_key`): rename to `test_crypto_verdicts_carry_no_benchmark_key`, crypto-only.
  - NEW `test_fx_benchmark_recorded_with_carry_basis`: build an fx spec + single_fix bars (open==high==low==close), run the `_benchmark_relative` path, assert the block exists, `excess == strategy_net - buy_hold_net` and `basis == "price returns, carry excluded on both sides"`.
  - NEW `test_benchmark_basis_is_per_class`: equity_etf basis unchanged (`"price returns, dividends excluded on both sides"`).
- [ ] **Step 2:** run -> the new/updated tests FAIL against current code.
- [ ] **Step 3: Implement.**
  - `cells.py` fx entry: `"benchmark": "self"`. REWRITE the rationale comment (spec s7): the old "fx has no long-only drift to separate from skill" justification is replaced, e.g.: `fx flips to "self" under SP5 (docs/2026-08-28-market-data-universe-design.md s7): recorded-not-gated means a control is strictly more information than none. LIMITATION, declared: a USD-per-foreign hold's true return driver is carry, which a price-only control cannot see - the basis string says so on every verdict.` Keep crypto's `None` + its comment untouched (crypto flips at Phase 3, not here).
  - `gauntlet.py`: above `_benchmark_relative`, add `BENCHMARK_BASIS = {"fx": "price returns, carry excluded on both sides", "crypto": "price returns, staking/funding yield excluded on both sides"}` and `_DEFAULT_BASIS = "price returns, dividends excluded on both sides"`; the return's `"basis"` becomes `BENCHMARK_BASIS.get(asset_class, _DEFAULT_BASIS)`. (crypto entry is dormant until Phase 3 - declared now so the flip is one line later.)
- [ ] **Step 4:** `python -c "import pipeline.cells"` clean; `python -m pytest pipeline/test_cells.py pipeline/test_gauntlet_classes.py -q` green; full suite green.
- [ ] **Step 5: Commit:** `git add pipeline/cells.py pipeline/gauntlet.py pipeline/test_cells.py pipeline/test_gauntlet_classes.py` ; `feat(sp5): fx benchmark self - honest per-class basis strings (D3) (T4)`

### Task 5: Pooled-crypto benchmark audit (read-only)

**Files:** Create `tools_benchmark_backfill_report_crypto.py` (layer root), `pipeline/test_crypto_backfill_report.py`. Read `tools_benchmark_backfill_report.py` FIRST and mirror its shape (cohort-from-chain, refusal, committed-artifacts-only, README banner).

- [ ] **Step 1: Tests** (synthetic chain + artifacts in tmp_path):

```python
# cohort: strategy_registered(crypto, assets [BTCUSD,ETHUSD]) + state_change(to=quarantine)
#   -> in cohort; equity_etf or graveyard -> out
# refusal: EXPECTED_N mismatch -> SystemExit(2), no report file written
# basket math, hand-computed pin: btc closes [100,110,99], eth closes [200,210,231] over
#   two OOS daily steps -> r_btc=[+0.10,-0.10], r_eth=[+0.05,+0.10];
#   basket = (1.075)*(1.0)-1 = 0.075 gross; net = 0.075 - 2*per_side
# per-asset control: (last_close/first_open - 1) - 2*per_side, from the spec's OWN cost_model
# date normalization: bars dated "2026-08-01 00:00:00" vs cutoff "2026-08-01" -> excluded from OOS
# edge numbers: report lines carry edge_label() from Task 3
# read-only: after a full run, the synthetic registry file's bytes are unchanged
```

- [ ] **Step 2:** FAIL. **Step 3: Implement.** Key deltas from the eq script: `EXPECTED_N = 20`; cohort filter `asset_class == "crypto"` and `payload["to"] == "quarantine"` (assert every universe == `["BTCUSD","ETHUSD"]`); THREE controls per strategy (BTCUSD hold, ETHUSD hold, 50/50 daily-rebalanced basket = compound of per-day mean of the two assets' close-to-close returns over the shared OOS calendar, entered at first OOS open, one round trip of the spec's cost_model on each control); every date compare via `[:10]` (fixes the eq script's raw-compare asymmetry — note this in the module docstring); report columns: edge#, sid, sibling group, strategy_net, three control nets, three excesses; summary: n excess>0 and median excess per control. Banner: "READ ONLY: writes exactly one file, this report."
- [ ] **Step 4:** suite green. **Step 5: Commit:** `git add tools_benchmark_backfill_report_crypto.py pipeline/test_crypto_backfill_report.py` ; `feat(sp5): pooled-crypto benchmark audit - three controls, read-only (D3) (T5)`

### Task 6: Variation coverage map (D12)

**Files:** Create `tools_variation_coverage_report.py` (layer root), `pipeline/test_variation_coverage.py`.

- [ ] **Step 1: Tests** (synthetic registrations; no network, no real chain):

```python
# structure key: sorted (role, type) pairs, params ignored ->
#   two registrations differing only in params share a structure
# per-param coverage: structure with entry block whose schema grid is [10,20,50,100],
#   registrations at 10 and 20 -> tested [10,20], untested [50,100]
# snapped identity: params 2 and 2.0 count as ONE tested value (composer.composition_fingerprint snap rule)
# combo counts: declared_combos = product of grid sizes over swept params observed for
#   that structure; tested_combos = distinct snapped tuples; report both
# per cell: same structure on BTCUSD_1d and ETHUSD_1d listed per cell
# read-only: registry bytes unchanged after run
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** walk `strategy_registered` payloads; structure key from blocks' (role, type); per structure x cell: count registrations, collect per-param tested values (snapped via the grids in `composer.BLOCK_TYPES`), list untested grid values, compute declared vs tested combo counts. Emit markdown: one section per structure, table per cell, then a global summary (structures seen, total declared points touched %, top-10 most-covered / least-covered structures). Docstring: "D12: the declared per-block grids are the reasonable fence; this report shows what inside the fence has never been asked. Proposer steering is OUT (spec s7b)."
- [ ] **Step 4:** suite green. **Step 5: Commit:** `git add tools_variation_coverage_report.py pipeline/test_variation_coverage.py` ; `feat(sp5): variation coverage map - tested vs declared grid points (D12) (T6)`

### Task 7: Supervised real runs (MAIN SESSION ONLY — not a subagent task)

- [ ] Run `python tools_select_crypto_universe.py` (real network) in the worktree; sanity-review the manifest (incumbents present, exclusions plausible, exactly 100); commit `data/crypto_universe_manifest.json`.
- [ ] Run `python -m pipeline.crypto_data fetch --timeframes 1d` (~100 assets, ~400 requests, minutes); verify every admitted symbol has a `data/<SYM>_1d.csv` and a snapshot-manifest entry; commit `data/crypto_snapshot_manifest.json` (CSVs stay gitignored).
- [ ] Run the audit and coverage tools against the worktree's copy of the real chain; commit both reports under `docs/runs/`; deliver both to Coen.
- [ ] Whole-branch review, full suite green, then merge gate (Coen-visible): merge `feat/sp5-phase1` into the designated branch, re-run the two fetch commands in the LIVE tree (incremental = fast), verify suite green there.

## Self-review notes

- Spec coverage: D1 -> T1/T7, D3 -> T4/T5, D11 -> T3 (+report wiring in T5), D12 -> T6. D2/D4-D10 are Phase 2/3 by design (spec s10). Morpheus UI numbering is a separate morpheus-hub wave (spec s10 Phase 2), not in this plan.
- The fx flip's dormant crypto BENCHMARK_BASIS entry writes nothing today (crypto benchmark is None); pinned by the crypto-absence test kept in T4.
- Tasks 1/2/3 are independent; 4 is independent; 5 depends on 3; 6 depends on nothing but reads composer.BLOCK_TYPES. Order: 1, 2, 3, 4, 5, 6, 7.
