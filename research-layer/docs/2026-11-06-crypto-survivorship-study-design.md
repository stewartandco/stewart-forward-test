# Crypto survivorship study: measure first (design)

**Status: DRAFT 2026-11-06. Design only: no code, no chain write, no protocol note, nothing committed or pushed. Coen's nine calls are in s0. A fresh reviewer builder-checked the doc against the code (5 questions; 1 went to Coen as SV9, 4 settled in s10; 2 code descriptions corrected). The decision rule (s5) is fixed here, before any study number exists. The pre-registration note (s9) is chained verbatim, after Coen approves its exact text, before any outcome number exists (s4 sets the order).**

Origin: beats-hold design BH3 (`docs/2026-11-05-beats-hold-design.md` s0): "a measure-first crypto SURVIVORSHIP study ... is a SEPARATE, later design". The honesty limit it follows up is in `tools_select_crypto_universe.py` `RULE` and in the `CLASSES["crypto"]` comment in `pipeline/cells.py`: *"today's top-100 backtested to listing date is winners picked after the race. Declared, not corrected; forward quarantine is the honest arbiter."* This study measures how big that limit is.

## 0. Decisions (Coen, 2026-11-06, all as recommended)

| # | Decision | Call |
|---|---|---|
| SV1 | Comparison universe | **Point-in-time (PIT) CoinMarketCap ranking by market cap at 2023-12-31** (the screen/gauntlet cutoff). Not Binance volume rank (a different rule would confound the gap), not every archive pair (micro-caps our rule never reaches) |
| SV9 | How the PIT universe is built | **Walk the CMC ranks until 100 are admitted, as our selector does (`_admission_walk`), with the history test every swept cell actually passed** (first daily bar on or before 2022-12-31, Ruling 41), not the selector's own 730-days-before-selection test. Under the 730-day test, 2022 listings (APT, OP, APE, LDO, IMX) would land with the risers because of their AGE, not hindsight. A cut at rank 100 was rejected too: our own universe got ZEC #122, NEXO #118 and DASH #140 by walking past 100 |
| SV2 | Decision number | **Mean OOS Sharpe gap per template**, ours minus PIT, over every template-cell, screen pass or not. Pass-rate gaps are secondaries: per-cell 1d crypto has 0 of 50 gauntlet passes, so pass counts cannot carry a decision |
| SV3 | Materiality threshold | **tau = 0.10 annualised Sharpe** (a quarter of the 0.4 `SR_FLOOR`) |
| SV4 | Timeframes | **1d decides.** 4h, 12h and 1h run under the same rule and are reported, never decision-bearing |
| SV5 | Renamed pairs | **Splice to the successor ticker**, ratio-adjusted, from a declared and dated list fixed before any outcome number. They are fallers or survivors, not deaths |
| SV6 | Proof the rule came first | **Chain a `survivorship-study-v1: ` note** (arms nothing) before any outcome number exists. The result is chained afterwards as an evidence note |
| SV7 | If MATERIAL | **A follow-up design only.** Nothing changes automatically. The strata readout (s6) tells that design which fix fits |
| SV8 | Train-window deaths (LUNA classic, SRM, FTT's 2022 under an earlier pseudo-cutoff) | **Not in v1.** Under the real 2023-12-31 cutoff a pair dead before it could never reach the gauntlet. An earlier cutoff is a different protocol and a possible later study |

## 1. Why, and what the recon already showed (measured 2026-11-06, read-only; sizes only, no prices or returns)

### 1.1 The question, restated against how the pipeline works

Crypto is per cell (Switch 1/2): each strategy trades ONE asset at one timeframe. Survivorship therefore never changes any single strategy's evidence: BTC's history is BTC's history. What it changes is **which cells get swept**. Our 76 active cells came from the CoinGecko top-100 on **2026-08-28**, which is AFTER the whole gauntlet OOS window (from 2024-01-01; `screen.DEFAULT_CUTOFF` = `gauntlet_core.DEFAULT_CUTOFF` = 2023-12-31). Assets that did well in 2024-26 got in; assets that did badly fell out. If strategies on hindsight-picked assets show better OOS numbers, then per-cell crypto's screen and gauntlet results flatter what a hindsight-free sweep would produce, and quarantine fills with more artefacts per real edge.

So the measurable question is a **population** question: for the same strategy templates, how much better do OOS results look on our universe than on the universe a researcher could have picked on the cutoff date?

### 1.2 The groups

The free Binance archive (`data.binance.vision`, S3 listing of `data/spot/monthly/klines/`, listed 2026-11-06) holds **3,771 spot symbols, 776 ending in USDT**. Of those 776, 52 are leveraged tokens (`*UP/DOWN/BULL/BEARUSDT`), about 19 are stablecoins or fiat, and most of the rest never came near a top-100 rule. Delisted pairs ARE kept: SRMUSDT 1d runs 2020-08 .. 2022-11, and 1h is kept too.

The PIT walk under SV9 goes through the 2023-12-31 CMC ranking (`api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical?convertId=2781&date=2023-12-31&limit=200&start=1`: free, keyless, 200 rows with cmcRank, slug and tags, checked 2026-11-06). It skips:

- stablecoins (CMC `stablecoin` tag);
- coins with no Binance USDT pair;
- pairs not trading at the cutoff (last 1d month before 2023-12);
- pairs whose first bar is after 2022-12.

It reaches **100 admitted at about rank 152**. Month-level listing data were used, so the builder re-derives the exact list (P5).

| group | n | members |
|---|---|---|
| **A** = ours (`ACTIVE_CELLS["crypto"]`) | 76 | |
| **B** = PIT | 100 | |
| **S**: in both | 66 | the 53 of the CMC top-100, plus 1INCH BAT COMP DASH DYDX GNO JASMY NEXO PAXG QTUM SFP TWT ZEC (ranks 101-152 on the cutoff) |
| **R**: ours only, risers | 10 | CVX DCR DGB ENS JST RAY RSR SUN YFI ZEN |
| **F**: PIT only, still trading, fell out | 22 | ASTR CELO ENJ FLOW FTT GLMR GMT GMX HOT ILV IOTX KAVA KSM LUNA MINA OSMO ROSE SC SKL TRB WOO ZIL |
| **N**: PIT only, renamed (old ticker's last 1d month, then the successor's first) | 7 | MATIC 2024-09 -> POL 2024-09; RNDR 2024-07 -> RENDER 2024-07; FTM 2025-01 -> S 2025-01; MKR 2025-09 -> SKY 2025-09; EOS 2025-05 -> A 2025-05; KLAY 2024-10 -> KAIA 2024-10; FXS 2026-01 -> FRAX 2026-01 |
| **D**: PIT only, delisted (provisional, P3 confirms each) | 5 | XMR (last month 2024-02), XEM (2024-06), ELF (2025-04), KDA (2025-11), LRC (2026-04) |

The S list's first 53 are BTC ETH BNB SOL XRP ADA AVAX DOGE DOT TRX LINK ICP SHIB LTC BCH UNI ATOM NEAR XLM OP FIL ETC INJ HBAR APT IMX VET LDO STX EGLD ALGO RUNE GRT QNT AAVE SAND THETA SNX AXS NEO MANA XTZ IOTA CAKE GALA LUNC XEC CFX CHZ CRV AR APE FET. **Distinct cells = 76 + 34 = 110.**

Reading: the bias to measure is mostly **rank churn**, not delisting. 10 of our 76 are hindsight risers, while 34 PIT pairs dropped out: 22 that still trade, 7 renamed, and 5 delisted. Our universe is 13% risers; B is 34% non-survivors of the 2026 selection.

The 24 declared-but-inactive assets of our 100 (first bar 2023 or 2024) are excluded on both sides by the same history test, so the comparison is on the 76 the pipeline actually sweeps.

### 1.3 Quarantine is answered by a count, not a simulation

Crypto quarantine on the live chain (2026-11-06) holds **105 strategies**:

- 102 are legacy pooled BTCUSD+ETHUSD;
- 3 are per cell (CRVUSDT 12h x2, CAKEUSDT 4h x1).

**All 105 sit on S cells, which are present in both universes.** Their forward records start after the 2026-08-28 selection, so universe composition touches neither their admission nor their record. **No outcome of this study changes any quarantined strategy.** The live gate stays the arbiter.

What the study speaks to:

- what FUTURE per-cell crypto sweeps should expect;
- the hindsight-picked share of the active grid. 36 cells have been swept at 1d so far, and exactly one of them is an R cell: JST. DASH, NEXO, PAXG and ZEC are S under SV9.

## 2. Goals, non-goals, anti-goals

**Goals**
1. One pre-registered number, the 1d mean OOS Sharpe gap (s5), with a 95% interval and a verdict from a rule written before the number exists.
2. A strata readout (S, R, F, N, D) that says WHICH churn drives any gap, so a follow-up design can pick the right fix.
3. Zero metered spend. No side effects on the chain or the pipeline beyond the two notes in s9.

**Non-goals**
- Fixing anything. This design changes no gate, universe, `ACTIVE_CELLS` entry, manifest or protocol (SV7).
- Re-judging any registered strategy, quarantine row or verdict.
- Train-window deaths under an earlier cutoff (SV8), 15m/30m, tradfi survivorship (delisted ETFs).

**Anti-goals (a study that runs without error but does one of these has failed)**
- A study number is used to pick, rank, register or promote a strategy. The study is measurement, not search: its sims are never registered, never selectable, and so never enter the trial denominator N.
- The decision rule, template set, cell lists or splice list change after any outcome number is visible.
- Either universe's data come from a different source than the other's (P1).
- The harness diverges from the real engine (P2), so the gap measures the harness.

## 3. The measurement

**Templates (frozen).**
- T = the 80 distinct `blocks` lists (canonical JSON, sorted keys) among registered per-cell crypto strategies at 1d: 16 families, registered 2026-10-18 .. 2026-11-04, each on 12 registered cells (960 = 80 x 12). They are taken as of a pinned registry entry index named in the pre-registration note.
- Intraday arms: the 20 distinct templates at each of 4h, 12h and 1h (4 families, registered 2026-11-04).
- A template-cell spec is a registered spec of that template with `universe.assets` replaced by the target cell and every other field unchanged. The cost model is `cells.CLASSES["crypto"]["cost_model"]` (10 bps commission + 5 bps slippage per side) for every cell. Its strategy id is the composer's own content id for that spec (s10.4), so on a template's own 12 cells it IS the registered id.

**Cells and bars.**
- A = the 76 of `ACTIVE_CELLS["crypto"]`; B = the 100 of s1.2. Formation date = the cutoff, 2023-12-31.
- Every cell runs to a common end of **2026-10-31** (the last complete archive month at design time), or to its last bar if earlier (D pairs, and N predecessors before their splice).
- Each cell's FIRST bar is the later of the archive's first bar and, where one exists, the pipeline's `data/<SYM>_1d.csv` first bar (SUN's pipeline file starts 2021-06-18). That way the harness reproduces the registered train windows (P2). A B-only cell starts at its archive first bar after P4.

**Per template-cell, two engine runs** (`engine.run_spec`, the real engine):
1. **Train run**, on bars cut at 2023-12-31, exactly as the screen does (`screen.load_bars` + `SpecJob(GATE_MIN_TRADES)`). It gives the screen outcome, train trades and net_pnl. A full-history run cannot give these: its metrics cover the whole span, and an open position is marked to market, not counted as a trade (engine.py:598-599, 842-844).
2. **Full run**, to the common end. It gives the OOS series:
   - `m(t,c)` = the annualised Sharpe of the strategy's OOS daily returns, from the same code path the `beats_hold` gate uses for `strategy_sharpe`. The strategy side goes through `gauntlet_core.daily_returns_with_dates` (`bars.daily_marks`), the hold side through `beats_hold.hold_oos_returns` (`bars.daily_bars`), and the Sharpe is taken on the dates the two share (beats_hold.py:96-99).
   - A None Sharpe (fewer than 30 returns, or zero variance) is **0.0** in the primary mean, and the None counts are reported per stratum.
   - The hold Sharpe on the same dates is recorded too.

**Gauntlet secondary.** Exactly as the pipeline does it: only screen passers get `gauntlet_core.evaluate_standalone(spec, bars, "2023-12-31", perturb=True, battery="v6.2")`, the seven-gate battery, unmodified (it makes its own base, stressed and perturbation runs). The battery is never edited for the study: whatever it does with a short series (a D pair) is what it would do live.

## 4. Preconditions and order (stop rules)

Everything in step 1 below reads data or reproduces numbers ALREADY on the chain. Nothing in it computes an OOS number, a hold return, or any number on a B-only cell.

**Step 1: before the pre-registration note.**
- **P1 One data source, proven equal.** Both universes are built from the archive (`data/spot/monthly/klines/<SYM>/<tf>/`, every zip verified against its `.CHECKSUM`).
  - For the 76 A cells, every archive 1d bar must equal the pipeline's `data/<SYM>_1d.csv` bar on every shared date (OHLCV, float-exact after parse).
  - The archive's spot timestamps switch from milliseconds to **microseconds from 2025-01-01**, and parsing must handle both.
  - Any mismatch: STOP and report. Never pick a source per group.
- **P2 The harness is the engine.** For all 960 registered per-cell 1d crypto strategies on their own cells, the harness's train run must reproduce the chained screen verdict (pass/fail, trades, net_pnl). The train window ends 2023-12-31, so the data vintage at verdict time does not matter, and these numbers are already on the chain. Any miss: STOP.
- **P3 Splices and deaths declared.** For each of the 7 renames: the swap date, the conversion ratio and its Binance announcement URL.
  - Expected ratios are 1:1 except MKR -> SKY at 1:24,000. The builder confirms every ratio from the announcement.
  - At each boundary, |ln(successor first close x ratio / predecessor last close)| must be below 0.05; otherwise STOP for Coen.
  - Each D pair is classified as delisted or renamed, with its announcement URL. A reclassified one moves stratum, and the move is recorded here.
  - Only USDT pairs are spliced. The archive also holds old-Skycoin `SKY*`/`SKYCOIN*` symbols, a collision hazard.
- **P4 No hidden splices.**
  - **LUNAUSDT's archive runs unbroken from 2020-08: it holds Terra Classic, then Luna 2.0.** It is cut to Luna 2.0's listing date (2022-05-31, confirmed from the announcement), which still passes the history test.
  - Within the used span of every cell, any gap of more than 7 days between consecutive daily bars, or any single-day close ratio beyond x10 or /10, flags a ticker reuse or halt: STOP for that cell.
  - Every exclusion or cut is Coen's call and is recorded in the note.
- **P5 Inputs frozen.** The note carries the sha256 of:
  - the template list;
  - both cell lists (the builder's re-derived exact list; if it differs from s1.2 by more than month-boundary cases, STOP for Coen);
  - the CMC snapshot response;
  - the splice and death list;
  - each cell's first-bar rule.

**Step 2: chain the `survivorship-study-v1: ` note** (s9), Coen-approved, verbatim.

**Step 3: only now** run any B-only cell, any full run, any OOS or hold number. A cell found broken after the note is excluded only by a superseding chained note, never silently. The hashed list would otherwise be stale.

## 5. Decision rule (pre-registered; written before any study number exists)

**Estimate.** For template t:
- `M_A(t)` = the mean of m(t,c) over the cells of A;
- `M_B(t)` = the mean over the cells of B;
- `G(t) = M_A(t) - M_B(t)`.

**The primary estimate is G = the mean of G(t) over the 80 templates**, each template weighted equally.

**Interval.** A two-way bootstrap, 10,000 resamples, numpy `default_rng(20261106)`. Each resample:
- draws the 80 templates with replacement;
- independently, draws the cells **within each stratum** with replacement: S (66), R (10), F (22), N (7), D (5), at the sizes P5 freezes. The S draw is shared by both universes.

The interval [L, U] is the percentile 95% interval.

**Verdict (exhaustive):**

| verdict | condition |
|---|---|
| **MATERIAL** | G >= 0.10 and L > 0 |
| **NOT MATERIAL** | U < 0.10 (flagged **REVERSE** when also U < 0: our universe is HARDER than PIT, not easier) |
| **INCONCLUSIVE** | anything else (G >= 0.10 with L <= 0, or G < 0.10 with U >= 0.10) |

Only the 1d arm decides (SV4). The 4h/12h/1h arms are computed and reported with the same formula and interval, and labelled "secondary, not decision-bearing". No secondary can rescue or overturn the 1d verdict. A precondition failure on an intraday arm stops that arm only.

**Secondaries (reported, never decision-bearing):**
- the screen pass-rate gap;
- the v6.2 gauntlet pass-rate gap over all template-cells;
- the gap in (strategy minus hold) Sharpe;
- the hold-only gap (mean OOS hold Sharpe, A minus B, no strategy at all);
- per-stratum means of m (S, R, F, N, D);
- a per-family table;
- None counts per stratum.

## 6. What each outcome changes

| outcome | changes |
|---|---|
| **NOT MATERIAL** | Nothing in code, gates, universe or quarantine. The result note is chained as evidence. The crypto honesty limit gains a measured bound ("gap G [L, U], not material at tau 0.10"), carried into the next universe manifest's rule text when one is next written. Phase 3 (15m/30m) needs no survivorship pre-step. Study closed |
| **REVERSE** | As NOT MATERIAL, with the note saying the survivor universe was the harder one |
| **INCONCLUSIVE** | No action. Reported as "not established, either way"; the honesty limit stays "documented, not corrected", with the interval printed. The template set is already everything registered, so there is no top-up |
| **MATERIAL** | Nothing changes automatically (SV7). The result note is chained and a follow-up design opens, with the strata readout picking its direction. If **R** drives the gap (risers' strategies beat S), that points at point-in-time re-selection: drop hindsight risers at the next declared re-selection, a denominator event in Coen's own commit. If **F/N/D** drive it (fallers' strategies trail S), that points at keeping fallen pairs as backtest-only cells. Either way it is that design's call, not this one's. No quarantined strategy is touched (s1.3) |

In every row: no outcome changes the screen gate, the gauntlet battery or any quarantine record.

## 7. Constraints

- **Off-chain and read-only on the pipeline.**
  - The harness reads a COPY of the registry snapshot. It never touches `registry_log.jsonl`, `registry_log.d/`, `data/`, `artifacts/`, `logs/` or `chain.lock`.
  - Outputs and downloaded archive files go to a new gitignored study directory (e.g. `research-layer/_scratch_survivorship/`). Archive files go in their own empty subfolder and are read with `python -I`.
  - It never runs `git clean -x` and never reverts anything.
- **Chain writes are the two notes in s9 only.** Each is chained with `python -m tools.grid_switch chain-note` after Coen approves the exact text, honouring `logs/chain.lock`.
- **Zero metered spend.** There are no model calls: the templates come from the chain, so the composer is never invoked. The D41 budget is untouched.
- **Compute and timing.** CPU only.
  - **1d:** 80 x 110 x 2 = 17,600 engine runs (about 0.8-2 s each, so roughly 4-10 CPU-hours), plus the gauntlet on screen passers (about 20 s each, plus P2's 960 train runs).
  - **Intraday:** 20 x 110 x 3 x 2 = 13,200 runs plus their gauntlets, about 60 CPU-hours, in later daytime windows.
  - Hand runs only, never a Task Scheduler task.
  - Daytime **08:40-20:00 only**, never crossing 07:58-08:40 or 20:00-07:00.
  - A worker pool that fits the box alongside the half-hourly gauntlet worker.
  - OS-detached `Start-Process` for multi-hour runs, with python called by absolute path.
- **Downloads need Coen's OK at build time**, stating the file count and total size (the 1d monthly zips for 110 pairs are small; intraday is larger).
- **Process hygiene:** scoped kills only, never `taskkill /F /IM python.exe`.
- Nothing is pushed.

## 8. Left to the builder

- Harness structure (a `tools/` module like the re-extract shadow run, never a pipeline stage), parallelism, and the worker count within the box rule.
- The archive downloader (it hits S3, not `api.binance.com`, but pace it anyway) and the zip/CSV parse, under P1.
- Mapping CMC rows to Binance pairs (symbol + `USDT`, CMC `ETHDYDX` -> `DYDXUSDT`), checked by P1/P4. Applying the selector's own `classify_exclusion` alongside CMC's `stablecoin` tag, and its duplicate-symbol refusal.
- Report layout (a `docs/runs/` readout; `docs/runs/` is gitignored).

## 9. Records on the chain (Coen approves each exact text)

1. **After s4 step 1, before any outcome number:** `survivorship-study-v1: ` + the s5 decision rule verbatim, the P5 hashes, the pinned registry entry index for T, the splice and death list, and any P4 cuts or exclusions. The prefix arms nothing: no code reads it.
2. **After the run:** `survivorship-study-v1-result: ` + the verdict, G [L, U], the strata means and the precondition outcomes. Evidence only; it changes no protocol.

## 10. Calls made by Claude (not Coen's decisions; check these)

1. **The PIT formation date is the cutoff (2023-12-31)**, so "hindsight-free" means "what a researcher had when training stopped". The PIT walk's "trading at the cutoff" test is the selector's "active within 7 days" test applied at that date.
2. **Common OOS end 2026-10-31** for every cell, not each verdict's own vintage.
3. **None Sharpe = 0.0 in the primary mean** (a flat book earned nothing), with None counts reported.
4. **Template-cell strategy ids are the composer's content id for the transplanted spec.** That id seeds the gauntlet's Monte Carlo (`gauntlet_core.py:432`: `int(sid, 16) % 2**31`), so on a template's own cells the secondary reproduces the registered seeds. This affects only the gauntlet secondaries (mc_p05, p_ruin), never the primary.
5. **One cost model for every cell.** Fallen pairs were probably less liquid and dearer to trade, so this makes PIT look slightly better than it was and the gap slightly SMALLER. A NOT MATERIAL verdict is therefore a little less safe than it reads; MATERIAL is unaffected.
6. **Equal weight per template; the stratified two-way bootstrap and its seed.** Strata are resampled separately because they are what the readout explains.
7. **D pairs are simulated to their last bar with the battery unchanged.** A Binance delisting is a venue event (XMR still trades elsewhere); for a Binance-traded strategy it is a forced exit.
8. **The gauntlet runs only on screen passers**, as the pipeline does; the primary uses every template-cell.
9. **First-bar rule:** the later of the archive's and the pipeline file's first bar, so P2 can reproduce the registered train windows.
10. **The order in s4** (data and reproduction checks, then the note, then every outcome number), and a superseding note for any post-note exclusion.
11. **Intraday arms use the same 110 assets** at 4h/12h/1h, subject to P1/P4 on those timeframes' archive files. A failure there stops only that arm.
12. **The CMC snapshot is the ranking source** (free, keyless, checked live), while the original selector used CoinGecko, so the rank source differs between the two universes. Both are market-cap ranks. My understanding is that CoinGecko's free tier does not reach 2023-12-31 history; I did not verify that this session.
