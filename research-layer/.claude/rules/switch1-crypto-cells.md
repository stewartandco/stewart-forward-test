---
paths:
  - "pipeline/cells.py"
  - "pipeline/test_switch1.py"
  - "pipeline/test_cells.py"
  - "pipeline/test_qcells.py"
  - "tools/grid_switch.py"
  - "tools/test_grid_switch.py"
  - "docs/notes/crypto-grid-record-v1.md"
---
## Switch 1 (2b-3 plan 1, 2026-10-17): crypto goes per-cell at 1d
- Design: `docs/2026-10-17-crypto-grid-activation-design.md`. Switch 1 is SP5
  Phase 3 as planned (the trial-denominator event): `ACTIVE_CELLS["crypto"]` =
  76 USDT assets x `("1d",)`; crypto routes per cell through
  `expand_family_for_class` with `benchmark: "self"` (flipped in the SAME commit,
  see the cells.py comment block for why neither half is safe alone); the
  composer's pooled path (`expand_family`, `ALLOWED_ASSETS`, `UNIVERSE_BASE`,
  `COST_MODEL`) is DELETED and `cells.CLASSES["crypto"]["cost_model"]` is the
  one source (10 bps commission + 5 bps slippage per side). Switch 2
  (intraday) followed in 2b-3 plan 5 (section "Switch 2").
- **The pooled path is retired.** Every pre-switch crypto strategy (533 at
  2026-10-17: 431 graveyard, 102 quarantine) is a pooled `[BTCUSD, ETHUSD]`
  spec; they stay on the chain and re-simulate forever; nothing about them is
  re-judged. A pooled spec that still needs the GAUNTLET after the switch RAISES
  there (`_benchmark_relative` wants exactly one asset per cell), which is why
  merge day gates on the preflight below. The two parity tools
  (`tools/daily_parity.py`, `tools/gauntlet_parity.py`) call
  `evaluate_standalone(record_benchmark=False)` for a legacy pooled crypto spec
  only, so they can re-run registered pooled specs; the hand-run
  `pipeline.gauntlet` registry pass would still raise on a pooled spec in
  `gauntlet` state (hand runs already need Coen's say-so).
  Every other 1d result (tradfi expansion, every registered spec's
  re-simulation, every quarantine row) is byte-identical across the switch.
- **Per-cycle cap counts cells for every class** (`specs_per_combo =
  max(1, len(sweep_cells(...)))`, never 1 for crypto). `_short_asset` strips
  `USDT`, so strategy names read `BTC`, not `BTCUSDT`.
- **Known limit: a retired or delisted pair needs a declared exit.** Once a dead
  USDT cell has registered strategies, its file stops growing, so the loop's
  freshness preflight parks every crypto fire at `stale_data`, and
  `gauntlet_stats` refuses `cells_not_comparable` REGISTRY-WIDE, for all
  classes (`pipeline/gauntlet_stats.py` ~682-691), once the vintage passes the
  cell's last bar. Nothing reads a retirement note: the grid record's
  "RETIRED by a new note" is policy, not code. Tradfi has the same latent
  exposure (a delisted ETF or a dead FX series). **The exit is
  `cells.RETIRED_CELLS` (2b-3 plan 3, Task 6; empty as committed).** It maps a
  cell key (`qcells.cell_key`: the bare asset at 1d, `ASSET@TF` otherwise) to
  `{"retired_utc": "YYYY-MM-DD", "note_prefix": "<chain note prefix>"}`.
  A retired cell is dropped from `screen.assert_cells_comparable`'s
  `data_end` (so the preflight's rules 1-2, `gauntlet_stats` and the gauntlet
  worker stop refusing over it; `loop._lagging_cells` skips it too), and the
  `feed_health` detectors in `crypto_refresh` and the quarantine fetch step
  skip it. It must NOT be in `ACTIVE_CELLS` (import-time check,
  `cells.assert_retired_cells_valid`), so no new spec can name it. Its
  strategies stay registered, counted in N, simulated on the bars that exist
  (`comparable_cells` still lists the cell) and keep their forward record;
  quarantine simply has no new bar to record. **Procedure to retire a cell:**
  a reviewed commit that adds the cell to `RETIRED_CELLS` (and removes it from
  `ACTIVE_CELLS` in the same commit) plus the Coen-approved retirement note
  chained verbatim; the code change is the switch and the note is the record,
  never one without the other. `ACTIVE_CELLS` is an assets x timeframes
  product, so retiring `X@<tf>` means removing asset X from EVERY timeframe of
  the class in that commit (not just that one); fine for a delisting, which
  kills every timeframe of the pair. `note_prefix` is a review convention, not
  code-enforced: the import check only requires a non-empty string, so the
  reviewer confirms the note is chained and its prefix matches. The refresh
  stops fetching the cell because it is no longer active, and
  `quarantine --fetch-owed` skips a retired intraday cell (one console line,
  no fetch, no retry wait, no `intraday_fetch_failed` item). Retiring a crypto
  cell removes it from `ACTIVE_CELLS["crypto"]` in the same commit, which
  changes the 76-asset count, the rotation `% n` arithmetic and the
  `test_switch1.py` pins; all are updated in that commit. A retired cell whose
  price file is MISSING or EMPTY still blocks via the preflight's rule 3 (a
  different failure, alarmed on purpose). Pinned by `test_retired_cells.py`.
- **`cells.EXCLUDED_CELLS` (2b-3 plan 4, D-i Task 7; empty as committed).** A
  PER-CELL exclusion, beside `RETIRED_CELLS` and different from it: an intraday
  cell below the admission line leaves the sweep WITHOUT dropping its asset
  (`SUNUSDT@1h` out; SUNUSDT at 4h, 12h and 1d stay). Retired = every
  timeframe of an asset, a delisting; excluded = one cell, the asset stays.
  A `frozenset` of `qcells.cell_key` strings (bare asset at 1d, `ASSET@TF`
  otherwise). `active_cells` omits them, so everything that sweeps (the
  composer's `sweep_cells` / `expand_family_for_class`, `crypto_refresh`, the
  loop's window) follows; `CLASSES` is untouched (the cell stays declared) and
  only the sweep, hence the trial denominator, shrinks. With it empty
  `active_cells` is byte-for-byte the plain assets x timeframes product.
  Import check (`assert_excluded_cells_valid`, run at import AFTER
  `assert_retired_cells_valid`; what must be declared BEFORE that one is the
  `EXCLUDED_CELLS` constant, because it calls `active_cells`, which reads the
  constant): each key
  well-formed, a DECLARED cell of a declared class, and not also in
  `RETIRED_CELLS`. It accepts ANY declared cell, including one outside the
  current gate (`X@1h` while the gate is 1d only is a silent no-op); plan 5 did
  not tighten it (the Switch 2 commit left the check as it was). **Hazard:** an excluded cell that
  already has registered strategies stops being fetched (it is no longer
  active), so those strategies park at `stale_data` exactly like an
  unretired dead cell above: exclude only cells with no registered
  strategies. Growing the set is a denominator event in Coen's own reviewed
  commit; plan 5 read the phase-2 prefetch admission rows and excluded nothing
  (which is why the prefetch still fetches excluded cells). Tests: `test_cells.py`.
- **Merge-day facts (Task 12, Coen-gated, by day, shared tree):**
  1. `python -m tools.grid_switch preflight --registry <live registry> --loop-state
     <live loop_state.json>` must exit 0 BEFORE merging: it lists any legacy
     pooled crypto spec still proposed / screened / in `gauntlet` or waiting in
     the crypto sibling queue (exit 1), and refuses missing paths (exit 2).
  2. The grid record (`docs/notes/crypto-grid-record-v1.md`, DRAFT until Coen
     approves the exact text; LF bytes enforced by a narrow `.gitattributes` rule)
     is chained with `python -m tools.grid_switch chain-note --registry <live
     registry> --note docs/notes/crypto-grid-record-v1.md --prefix
     "crypto-grid-record-v1:"` BEFORE the first per-cell crypto verdict. It refuses
     (exit 2, nothing written) a CR byte, a wrong prefix, a prefix already on the
     chain (re-checked under chain.lock) and ANY existing `logs/chain.lock`; it
     appends once under chain.lock, runs verify_registry and exits 0 only on
     VALID. `--dry-run` runs the guards and writes nothing.
  3. Register `33_CryptoGridRefresh` the same day (section above).
  4. `CRYPTO_SYSTEM_PROMPT` (composer) is a DRAFT pending Coen; the plan itself
     makes no composer or triage model call (zero metered spend).

