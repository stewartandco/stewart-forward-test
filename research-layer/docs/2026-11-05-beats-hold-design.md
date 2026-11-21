# Beats-hold: a strategy must beat holding its own asset (design)

**Status: DRAFT 2026-11-05, written before any code; builder-checked by a fresh reviewer against the code (6 questions: 4 settled in the doc, 2 decided by Coen as BH4/BH5). Not chained, not built, not committed. Every gate change here is a protocol change and lands only after its note is chained verbatim (s5).**

## 0. Decisions (Coen, 2026-11-05)

| # | Decision | Call |
|---|---|---|
| BH1 | Where the rule bites | BOTH stages: a new standalone gauntlet gate (stops new arrivals) AND the graduation arm of the live gate (catches the strategies already in quarantine) |
| BH2 | What "beat" means | RISK-ADJUSTED: the strategy's Sharpe must beat the hold's Sharpe over the same window. Not raw return (it would kill genuine risk-reduction edges: same return, half the drawdown) |
| BH4 | Graduation bar | BEAT BOTH: the forward record must beat zero (v1's PSR test, unchanged) AND its own asset's hold (s4). One p-value per strategy = the larger of the two, then Benjamini-Hochberg as today. Keeps v1's "a record with no trades cannot clear the graduation arm" true, and mirrors the gauntlet, which keeps `oos_negative` beside `beats_hold` |
| BH5 | Go-live | DORMANT SWITCH: code merges first and keeps running v6.1 / v1 until each note is on the chain, then switches by itself on its next run (the composer's intraday-hold pattern). No refusal, no window where the chain and the running code disagree |
| BH3 | Order | This design first. A measure-first crypto SURVIVORSHIP study (Binance's public archive holds 776 USDT spot pairs incl. delisted, vs our 100) is a SEPARATE, later design; nothing here touches the universe |

## 1. Why (measured 2026-11-05, read-only, live chain)

Of 894 strategies in quarantine, by the recorded B1 control (`metrics.benchmark_relative`, OOS raw-return excess over own-asset hold):

| class | beat hold | did not | no control recorded |
|---|---|---|---|
| equity_etf | 0 | 551 | 96 (registered before B1) |
| metal_etf | 0 | 65 | 0 |
| fx | 71 | 2 | 4 |
| crypto | 3 | 0 | 102 (legacy pooled BTCUSD+ETHUSD) |

Two thirds of quarantine is beta. This is the eq-gen1 finding (0/96, `docs/runs/2026-08-26-eq-gen1-benchmark-report.md`) grown six-fold. It was declared "recorded, not gated" by B1 and is surfaced in Morpheus as a headline, but nothing stops it:

- **Gauntlet:** the six v6.1 gates (`gauntlet_core.FAIL_ORDER`) are all absolute. A SPY timing rule that made money in a rising market passes them while losing to SPY.
- **Graduation:** `livegate.forward_psr` tests the forward record against ZERO (`sr_star = 0.0`), Benjamini-Hochberg over the eligible cohort. A beta strategy in a bull market has a small p-value, so it can graduate; and many small p-values at once make BH MORE lenient for everyone in the cohort.

Caveat carried from the backfill reports: the recorded excess is raw return, at the strategy's own `notional_frac` exposure, with the control charged one round trip. That is why BH2 picks Sharpe (scale-invariant to exposure) over raw excess, and why the 0/616 count above is a motivation, not the new rule's predicted outcome.

## 2. Goals, non-goals, anti-goals

**Goals**
1. No strategy enters quarantine, and none graduates, unless its risk-adjusted return beats holding the asset(s) it trades over the same window.
2. Every class with an own-asset hold is covered, including the strategies registered before their class had a control (the 96 equity_etf, the 102 legacy pooled crypto).
3. Existing chained verdicts and history are untouched.

**Non-goals**
- Re-judging the 29k graveyard or the 894 quarantined at the gauntlet stage. The graduation arm covers the 894 going forward; the D8 unified re-run is where gauntlet verdicts get re-judged, under its own note.
- Sleeves (HOUSE-CORE and the 5 incubating): judged by their own pre-registered criteria (Build 3 Q1). Unchanged.
- The kill arm (cone p01, `MIN_FORWARD_TRADES`): unchanged.
- Universe / survivorship (BH3).
- Morpheus UI surfaces (a "vs hold" column in the paper book, the BENCHMARK VERDICT headline wording): a follow-up morpheus-hub commit wave once the gate lands, not part of this build.

**Anti-goals (a failure even if it technically works)**
- A rule that reads anything beyond the strategy's own cell. v6's founding principle ("every input to every gate is a property of the strategy alone") must hold: the hold of the strategy's own asset over its own window IS such a property; a sibling, family or cohort statistic is not.
- A silent change of meaning: any verdict produced under the new gate must say so in its protocol label.
- Raw return sneaking back in as the decider anywhere (BH2).

## 3. The gauntlet gate (`beats_hold`)

- **Inputs:** the strategy's OOS daily returns (the same `daily_returns_with_dates(res["equity"])` series `evaluate_standalone` already builds, dates after the cutoff by `_date_le`) and the hold's OOS daily returns from the same `spec_bars`, same date fence, on the same dates. Hold = buy at the first OOS bar's open, daily close-to-close after; one round trip of the class cost model (the B1 formula) charged at entry and exit.
- **Multi-asset specs:** hold = the mean of the per-asset rebased hold LEVELS (equal capital at the start, no rebalancing), which is exactly the engine's combine rule (`engine.py:837`, mean of equity levels) and `livegate.forward_series`'. Only reachable by the legacy pooled crypto specs, which can never be candidates again; implement it once because graduation (s4) needs it.
- **Rule:** `annualized Sharpe(strategy OOS) > annualized Sharpe(hold OOS)`, both via the existing `_annualized_sharpe_from_returns` (daily marks, `TRADING_DAYS` per the pipeline). A point comparison, no margin, consistent with the other five point gates. If either Sharpe is None (fewer than 30 returns, or zero variance) the gate FAILS: absence of evidence does not pass a gate (protocol-v4's rule).
- **Placement: a SEPARATE v6.2 battery, never an edit of `FAIL_ORDER`.** `FAIL_ORDER` and `evaluate_gates` are shared with the legacy hand-run `pipeline/gauntlet.py` (`gauntlet.py:78`, `:231`), which labels its verdicts v6; appending there would add gate seven to v6-labelled verdicts (an anti-goal). So: the six-gate result is computed exactly as today and kept as its own value; a new `FAIL_ORDER_V62 = FAIL_ORDER + ("beats_hold",)` decides the v6.2 verdict; only the worker uses it. `beats_hold` is last, so every existing fail reason keeps its meaning and order.
- **Recorded:** `metrics.beats_hold = {strategy_sharpe, hold_sharpe, basis, window: "oos"}` on every verdict, pass or fail. `benchmark_relative` (raw excess) stays recorded exactly as today.
- **Classes:** every class whose `CLASSES` entry declares `benchmark: "self"` (crypto, fx, equity_etf, bond_etf, metal_etf today). A class with `benchmark: None` skips the gate (recorded absent, the B1 no-null-placeholder convention). fx's hold is "long the foreign currency, carry excluded", per its existing basis string.
- **Protocol label:** verdicts carry `gauntlet-protocol-v6.2` (s5). `FLOOR_PROTOCOLS` does NOT change: it lists protocols whose verdicts carry a cluster `trials_n`, and v6.2 verdicts, like v6.1, carry none (`gauntlet_stats.py:287-293`).
- **Every exact-match `== "gauntlet-protocol-v6.1"` site must accept v6.2**, or v6.2 verdicts silently fall out of the machinery. Known sites (builder greps for the rest): `gauntlet_stats.py:380` (stats never written for v6.2), `registry.py:292` (stats snapshot), `gauntlet_worker.py:190/201` (orphan repair), `loop.py:697-701` (the 20:00 loop would FAIL `gauntlet_orphan` on every fire), `verify_registry.py:581` (invariant 11 would read the chain INVALID). The clean form is one `V61_FAMILY = (v6.1, v6.2)` tuple imported everywhere, pinned by a test that greps for any remaining literal.

## 4. The graduation arm (live gate)

- **Forward hold series:** built from the quarantine rows themselves: each row records the asset's `price` at the day's last bar. Rows start the day AFTER entry (`quarantine.py:43`), and the strategy's own forward returns already drop the first step (`livegate.py:72-74`), so the hold curve is rebased at the FIRST forward row and its returns start on day two, matching the strategy series date for date. No cost is charged to the forward hold (consistent with the forward record itself, which carries no extra entry cost). Combined across assets by `forward_series`' rule (mean of levels, incomplete dates dropped). No new data feed, no new file.
- **Test:** one-sided, H0: SR(strategy) <= SR(hold), on the paired daily return series. Method: Ledoit-Wolf (2008) studentized circular block bootstrap of the Sharpe difference (robust to fat tails and autocorrelation; crypto and the vol-targeted books are neither normal nor iid). Seeded from the strategy id exactly as the cone is (`int(sid,16) % 2**31`). Block length and bootstrap paths: declared constants in `livegate.py`, chosen by the builder, recorded in the protocol note before chaining. **Rejected:** PSR with `sr_star = hold Sharpe` (treats the hold's Sharpe as known when it is estimated on the same 60+ days, and ignores the correlation between the two).
- **Combined with zero (BH4):** `p_zero = 1 - forward_psr(rets)` (exactly today's), `p_hold` from the paired test; the strategy's p-value is `max(p_zero, p_hold)` (an intersection-union test: rejecting both nulls at once, valid without any correlation assumption between them). A never-trading book has `p_zero` near 1, so it cannot graduate.
- **Cohort:** the combined p-values feed the existing `benjamini_hochberg` over the eligible survivors, unchanged. Kill first, then graduation, unchanged.
- **Report:** `write_report` adds hold Sharpe, strategy Sharpe, and the p-value columns; the existing PSR-vs-zero stays as a recorded column (so the assessment shows both for the transition period).

**Honest cost, stated before building:** graduation gets harder (it now needs both tests). Against zero, clearing PSR 0.95 on 60 days already needs an annualized Sharpe near 4.14; a paired test against a correlated hold needs a Sharpe DIFFERENCE of a similar size, so a timing strategy closely tracking its asset may take years longer to graduate, and many never will. That is the rule doing its job: a strategy that cannot be told apart from holding the asset should not be the reason real money moves.

**Declared consequence (both stages):** a SHORT or low-correlation strategy whose Sharpe is positive but below its asset's hold Sharpe fails, even though it could diversify a book. The rule asks "better than holding this asset", not "adds something to a portfolio". A portfolio-level test (alpha against the hold, or contribution to the H whole-pool optimizer) is a different question and out of scope here.

## 5. Protocol records (Lane A; chained verbatim, Coen approves exact text, BEFORE code ships)

1. `docs/notes/gauntlet-protocol-v6.2.md` (prefix `gauntlet-protocol-v6.2: `): states that v6.2 is v6.1 PLUS gate seven, and that everything chained for v6.1 applies to v6.2 verdicts unchanged: amendment-1 (SR* floor, PBO scope), amendment-2 (per-cell effective trials), the intraday daily-marks note, the family-kill ban (invariant 12) and the stats linkage (invariant 11). Those notes are keyed on `v6.1` prefixes, so the inheritance must be in the v6.2 text, not assumed. Then: `beats_hold` as gate seven, read only from the strategy's own cell (consistent with v6's principle), the rule of s3. Until it is chained the worker judges with the v6.1 battery and labels v6.1, exactly as today; from its first run after the note lands it judges and labels v6.2 (BH5; the switch reads the chain once per run, like `composer.intraday_held`). **Prefix collision check is part of the build:** every existing check keys on `gauntlet-protocol-v6.1:` / `-amendment-N:` first-line prefixes; the builder confirms none of them matches the v6.2 line and that verifier invariants 11 and 12 still hold for v6.2 verdicts (invariant 11 points stats entries at "an EARLIER v6.1 gauntlet verdict": it must accept v6.2).
2. `docs/notes/quarantine-live-protocol-v2.md` (prefix `quarantine-live-protocol-v2: `): ADDS the own-asset hold to the graduation arm (must beat zero AND the hold, combined p of s4), keeps the kill arm word for word. Until it is chained `livegate` runs v1 unchanged; from its first run after, v2 (BH5).

The two notes are independent: either can land first. Neither edits v6.1 or v1 text. The merge-day runbook: merge (behaviour unchanged, proven by the s6 parity run), then Coen chains each note when ready; the next worker / Sunday livegate run switches. Tests pin both sides of each switch (note absent = old behaviour byte for byte; note present = new).

## 6. Constraints

- Live tree hazards (research-layer/CLAUDE.md): no merge/checkout/reset during a worker git step, the `registry_log.jsonl` merge guard, chain.lock for any hand chain write, scoped `git add` only.
- Gauntlet speed: the hold series is one pass over bars already in memory; no new simulation. Must not move the 1h/4h/12h priors measurably (the builder measures one round per timeframe before/after).
- **Parity proof (what counts), amended at planning 2026-11-05:** the v6.2 battery is OPT-IN (`evaluate_standalone(..., battery="v6.1")` by default, byte-identical to today; only the worker passes `"v6.2"`, and only once the note is chained). So `tools/daily_parity.py` and `tools/gauntlet_parity.py` run UNCHANGED, on the default path, before and after: same tool code both sides, and a clean compare proves the default path did not move. (The first draft had extended the tools, because `daily_parity.py:154` hashes the whole `metrics` dict and `gauntlet_parity.py:134` compares `passed`; making the battery opt-in removes that need.) Plus a test that the v6.2 result's six-gate fields equal the v6.1 result's, bit for bit, on the same spec.
- `livegate` writes nothing on its own; chaining any state change stays as today.
- Tests: the touched modules' suites plus new ones for the gate (pass/fail/None cases, multi-asset basket, class without benchmark), the bootstrap (seeded determinism, a known-difference fixture that must reject, a hold-identical fixture that must not), and the refusals before each note.

## 7. Left to the builder

- Block length and path count for the bootstrap (declared constants, recorded in the v2 note).
- Where the hold-series helper lives (one implementation shared by gauntlet and livegate, never two copies).
- Report layout details.

## 8. Calls made by Claude (not Coen's decisions; check these)

- `beats_hold` goes last, in a separate v6.2 battery; the shared `FAIL_ORDER` is untouched.
- None Sharpe (too few returns / zero variance) FAILS the gate.
- Point comparison at the gauntlet (no margin, no test), paired bootstrap at graduation: the gauntlet's OOS window is years long and the other gates are point gates; the 60-day forward record is short and already judged by a statistical test.
- Multi-asset hold = mean of per-asset hold levels, no rebalancing (the engine's own rule).
- Forward hold starts on day two, no cost, matching the strategy's forward series.
- The parity proof of s6 (extend the tools first, same tool code both sides).
- Ledoit-Wolf bootstrap over PSR-vs-hold-Sharpe.
- Protocol name v6.2 (a new gate is more than an amendment).
