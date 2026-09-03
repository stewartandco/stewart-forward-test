# Exit rules v7 — no time gating, declared exits, indicator-placed stops (design)

**Status: APPROVED by Coen 2026-09-03 (D15, in the UI session).** Branch `feat/d15-exit-rules` (worktree `stewart-forward-test-d15`), base `920f9fc`. Lane B (this document). Lane A: `docs/notes/exit-rules-v7.md` MUST be chained BEFORE the first `version: 2` registration exists.

## 0. Decision (D15) and its sub-decisions

| # | Decision (Coen, 2026-09-03) |
|---|---|
| D15 | **The only forbidden exit is the calendar.** `("exit","time_stop")` is retired from the Composer grammar. Time gating exits on the calendar, not the market — the same reason every S&C system was recalibrated off time gates. |
| D15(a) | Valid exits, every one **declared per family**: (1) a **stop-loss whose level is indicator-placed** (mandatory; `pct_stop`, a fixed percent with no indicator, is retired); (2) an **R-multiple target** (optional); (3) **indicator-event signal exits** (optional; "crossover in, crossunder out" is the canonical example — the SAME indicator as the entry is allowed for a signal exit). |
| D15(b) | **Disposition A:** everything is re-trialled under the new grammar inside the unified re-run (D8/D9). Nothing is deleted; old verdicts stand as history; a legacy registration whose engine behaviour is UNCHANGED under v7 is declared compliant as it stands and is NOT re-registered. |

**Why (root cause, verified at source 2026-09-03):** `time_stop` entered in block grammar v1 (`d0b328a`, 2026-08-06) as a default, never a decision. It was present from the FIRST registration on the chain (`fae172f81ba68878`, generation 0, 40 bars); generation 2 then leaned on it to pass the `trade_count` screen gate (`GATE_MIN_TRADES = 40`; 1,418 kills today), because a forced exit lifts turnover. Result (recount 2026-09-03 evening, 6,050 registrations): 5,038 carry a time stop (40 bars: 2,695 · 20: 1,931 · 10: 412), 55 families pin one value and none sweeps it, survivors skew to 40 (61% of quarantine); 449 registrations carry none of the three retired/undeclared exit paths (the compliant-as-is set). Two more undeclared exit paths were found in the same audit: `pct_stop` on 1,968 registrations (a fixed percent, not an indicator), and the engine's hardcoded crossunder exit for `ma_cross*` entries (`engine.py` ~:324, `exit_reason == "signal"`) on 1,265 registrations — outside the grammar, on no chain entry. Verdict metrics record NO exit-reason counts, so the chain cannot say how often any of these closed a trade.

## 1. Grammar (`pipeline/blocks.py`)

Chained `block_type_registered` schemas are immutable (`composer.preflight_block_types` aborts a run if `BLOCK_TYPES` disagrees with a chained schema), so retired types **stay in `BLOCK_TYPES` unchanged** and are refused by policy, never deleted:

- `RETIRED_TYPES: dict[tuple[str,str], str] = {("exit","time_stop"): "D15 exit-rules-v7: exits on the calendar, not the market", ("stop","pct_stop"): "D15 exit-rules-v7: a fixed percent is not an indicator-placed stop"}`.
- `validate_block(role, btype, params, *, version)` — for `version >= 2` a retired type is an error (`"exit/time_stop retired under exit-rules-v7"`); for `version == 1` (legacy specs re-validated by tools) it stays valid. `grammar_summary()` omits retired types from the Composer prompt and appends one line naming them as retired.

New block types (all sweepable; every param grid has ≥ 3 contiguous values; all computable from daily OHLCV):

| key | params (grid) | engine semantics |
|---|---|---|
| `("stop","swing_stop")` | `lookback` int [10, 20, 40] | stop = lowest low (long) / highest high (short) over the `lookback` bars BEFORE the signal bar (i.e. bars `[i-lookback, i)` at signal time `i`) |
| `("stop","ma_stop")` | `ma_len` int [20, 50, 100] | stop = SMA(close, `ma_len`) at the signal bar |
| `("stop","channel_stop")` | `lookback` int [20, 55, 100] | stop = lower channel (long) / upper channel (short) over `[i-lookback, i)` at signal time |
| `("stop","band_stop")` | `lookback` int [20, 40, 60], `mult` float [1.5, 2.0, 2.5, 3.0] | stop = SMA − mult·stdev (long) / SMA + mult·stdev (short) at the signal bar |
| `("exit","ma_crossunder")` | `fast` int [5, 8, 13, 20, 34], `slow` int [50, 80, 130, 200] | exit when the fast SMA is below the slow SMA at close `t` (long) / above (short); a state test, not a cross; evaluated on close `t`, filled at open `t+1` like entries |
| `("exit","channel_exit")` | `lookback` int [10, 20, 40] | exit when close `t` < lowest low over `[t-lookback, t)` (long) / > highest high (short) |
| `("exit","zscore_revert")` | `lookback` int [20, 40, 60, 90], `z_exit` float [0.0, 0.5, 1.0] | exit when z(close) ≥ −`z_exit` (long entered on a negative z) / ≤ +`z_exit` (short) |
| `("exit","tstat_decay")` | `max_lookback` int [60, 90, 120], `t_exit` float [0.0, 0.5, 1.0] | exit when the best t-stat over windows 20..`max_lookback` (step 10; largest magnitude, like the entry) falls to ≤ `t_exit` for a long / rises to ≥ −`t_exit` for a short |
| `("exit","regime_flip")` | `ma_len` int [50, 100, 150, 200, 250] | exit when close `t` is BELOW the SMA(`ma_len`) (long) / ABOVE (short); a state test evaluated at each close, not a cross |

Stop rules shared by ALL stop types (existing and new): the stop is fixed at entry (no trailing — a separate decision if ever wanted); a stop that lands on the wrong side of (or exactly at) the entry price makes the signal **ineligible** (no trade, counted in metrics as `stop_invalid`), exactly as today's `abs(entry_px - stop) > 0` rule; with several stop blocks the tightest wins (unchanged). `atr_stop` / `atr_stop_dense` stay (an ATR is an indicator).

`stop_invalid` counts signal-bars, not distinct opportunities: a signal that stays lit across consecutive bars while the stop level is on the wrong side is counted once per bar.

`SWEEPABLE_TYPES` gains every new type above. `CONSTRAINTS` gains `ma_crossunder: fast < slow`.

## 2. Registration marker (`version: 2`)

- `schemas/strategy_spec.schema.json`: `"version": {"enum": [1, 2]}` (was `const 1`).
- The Composer stamps `version: 2` on every spec it builds from now on (both expanders). A `version: 2` spec must have: exactly one entry (unchanged), ≥ 1 stop (unchanged), ≥ 1 risk (unchanged), **no retired type**, target optional, exit blocks optional (0..n signal exits).
- `composition_fingerprint` includes `version`: the same block list is a different trial under a different engine. Rule (D15(b)): a legacy `version: 1` composition whose behaviour is unchanged under v7 (no retired type AND entry not `ma_cross*`) is v7-compliant as registered; its fingerprint is recorded in BOTH forms so a `version: 2` re-registration of it is refused as a duplicate (`retrial_verdict` → `NOT_BURIED`/`WINDOW_*` as today).
- `verify_registry.py`: after the chained `exit-rules-v7` note (identified by its first line), every `strategy_registered` MUST be `version: 2`, carry no retired type, and every block type must be chained (unchanged rule). Before the note, `version: 1` entries stand.

## 3. Engine (`pipeline/engine.py`)

`simulate_asset(blocks, bars, cost_model, periods_per_year, *, version)`:

- **Legacy path (`version == 1`)** is byte-for-byte today's behaviour: deadline time stop, `pct_stop`, and the implicit `ma_cross*` crossunder exit. Required because the quarantine forward runner (`quarantine.py::observe_day`) re-simulates every quarantined legacy sid daily; their observation must not change under them. `run_spec` passes `spec["version"]`.
- **v7 path (`version >= 2`)**: no deadline (an `exit/time_stop` block raises `ValueError`, as does `stop/pct_stop`); no implicit exit; declared `exit` blocks evaluated per bar on close `t` → fill at open `t+1`, AFTER barrier checks in this order: gap-through stop, gap-through target, intrabar stop, intrabar target, then signal exit. `exit_reason` values: `stop`, `target`, `signal:<type>`.
- **Open at sample end (both paths, made explicit):** an open position is never a closed trade; it is marked to market in the equity curve (today's behaviour, now stated) and reported as `open_at_end: true` in metrics with its unrealised return.
- `run_spec` metrics gain `exit_reasons: {"stop": n, "target": n, "signal:<type>": n, "time": n}` (only keys that occurred), `open_at_end: bool`, `stop_invalid: n` (signals dropped because the stop was not on the adverse side).

## 4. Gauntlet and screen

- `gauntlet.evaluate_spec` metrics gain `exit_reasons_is`, `exit_reasons_oos` (counts over the IS / OOS trade lists via one helper `engine.exit_reason_counts(trades)`), and `open_at_end` where the OOS book ends open. No gate reads them; recorded, not gated (same doctrine as `benchmark_relative`).
- Screen verdict metrics carry `exit_reasons` through `run_spec` unchanged.
- `GATE_MIN_TRADES = 40` is unchanged. Expected consequence, stated here so it cannot be presented as a surprise: without forced exits, trade counts fall and more families fail `trade_count`. That is honest. The Composer must find turnover through the entry and exit signals it declares, not through the calendar.

## 5. Composer prompt and family rules (`pipeline/composer.py`)

- `grammar_summary()` lists live types only + a retired line.
- The family-proposal instructions state D15 verbatim: every family declares its full exit set (stop mandatory and indicator-placed; target optional; signal exits optional); no time stop of any kind; the exit signal MAY use the entry's indicator (crossover in / crossunder out).
- `validate_family` adds: retired type → error; an `exit` block may appear 0..n times.

## 6. Re-trial of existing families (D15(b), tool only — FIRING IS COEN-GATED)

`tools_retrial_families_v7.py`: for every distinct family on the chain, classify each of its compositions as **compliant-as-is** (no retired type, entry not `ma_cross*`) or **needs re-trial**. For the latter, propose the v7 exit set through the Composer (one metered LLM call per family, ~55 families) using the family's own cards and blocks, and enqueue the resulting `version: 2` specs as D9 re-trials for the unified re-run (`split_for_cycle` queue). `--dry-run` writes the classification report to `docs/runs/2026-09-03-exit-rules-v7-retrial-plan.md` and nothing else. The real run is a chain write inside a pipeline cycle; it is not part of this branch's ship bar.

## 7. Chain events, in order

1. `docs/notes/exit-rules-v7.md` chained as a `note` (Lane A) — ratchet: **TIGHTENS** (a whole class of exits is forbidden; nothing is loosened) — BEFORE any `version: 2` registration.
2. New block types chained by the Composer's existing first-real-run path (`registry.register_block_type` for every `BLOCK_TYPES` key not yet chained).
3. First `version: 2` registrations.

## 8. Ship bar

- `pytest pipeline -q` green (baseline recorded first); `verify_registry.py` VALID against a copy of the live chain (read-only; never point tests at the live chain).
- Engine parity: (a) a COMMITTED golden test (`pipeline/test_exit_rules_v7.py`) replays three synthetic legacy fixtures covering the time / stop / implicit-signal exit paths and pins trades + equity byte-for-byte; (b) the REVIEW step, not a committed test (it needs the live `data/` bars): the Task 2 reviewer replayed 36 real registrations spanning every entry / stop / exit / class bucket through the old and new engines and found 4,285 trades and 185,370 equity points byte-identical (scratch record `scratchpad/arch-d97cff5-t2/replay_*.json`, 2026-09-03). The claim on the chained note is only (a).
- Retrial tool dry run produces the classification report over the real chain (read-only).
- Two-stage review per task; whole-branch review; merge only outside a live cycle window and after the peer session's `feat/simcache-arrays` merge (rebase onto it); Lane A note chained before merge; NEVER edit the live tree during a cycle.
- Morpheus follow-up (own pass): Inspector block composition already renders by role; `exit_reasons` surfaces on the Autojournal once verdicts carry it.

## 9. Out of scope
Trailing stops; changing `GATE_MIN_TRADES`; firing the re-trial (Coen-gated, inside the unified re-run); any gauntlet gate change; the loop schedule.
