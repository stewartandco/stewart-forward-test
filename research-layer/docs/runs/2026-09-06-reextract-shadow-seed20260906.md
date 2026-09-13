# Re-extract shadow run 2026-09-06

**Verdict: GREEN** (2.40 novel accepted per document; green needs >= 2.0, red is < 0.5)

Sample: 5 document(s) at seed 20260906, python 3.14.3, extractor claude-opus-5, panel claude-sonnet-5; 0 errored, 0 stopped at the budget. Spend USD 1.01.

| measure | old corpus | this run |
|---|---:|---:|
| accept rate (judged cards only) | 86% | 86% |
| undecided (old: pending share / new: unjudged count) | 25% | 0 |

Claims proposed 38, dropped by the honesty guard 0, duplicates of held cards 0, restatements of held cards 24, judge failures 0, novel 14 (accepted 12, escalated 2, unjudged 0).

**Limitation:** duplicate detection is `claim_fingerprint` (normalised, not semantic) followed by a one-call semantic check against cards held from the SAME document. A restatement of a card held from a DIFFERENT document, or one the judge misses, still counts as novel and reaches the panel; the restatements section shows what the check caught. Paraphrase across documents is the remaining gap.

## Per document

| document | band | old A/R/P | proposed | guard | dupe | rest | novel | acc | esc | unj | USD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A Tale of Two Prices | passed | 4/0/0 | 7 | 0 | 0 | 4 | 3 | 1 | 2 | 0 | 0.189 |
| Does the Equity Term Structure Respond to Mo | passed | 5/0/0 | 5 | 0 | 0 | 3 | 2 | 2 | 0 | 0 | 0.196 |
| The Unintended Consequences of Rebalancing | stalled | 0/1/6 | 10 | 0 | 0 | 3 | 7 | 7 | 0 | 0 | 0.315 |
| Memorial Week History And The One Day That's | stalled | 0/2/1 | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0.046 |
| Liquidity Fades as Treasuries Age | passed | 9/0/0 | 12 | 0 | 0 | 10 | 2 | 2 | 0 | 0 | 0.267 |

## Escalation reasons (every dissenting reviewer)

- The quote only states that a pairs trade is 'a bet on convergence' where you're betting the spread narrows, but the claim adds an unsupported methodological conclusion ('so the strategy's edge can be tested as the tendency of divergent spreads to converge') and asserts a strict necessary condition ('profitable only if') that go beyond the simple descriptive statement in the quote.
- The quote only states that a pairs trade is a bet on the spread narrowing, but the claim adds an unsupported extension—turning this into a testable hypothesis ('so the strategy's edge can be tested as the tendency of divergent spreads to converge')—which is not asserted or implied in the verbatim quote.
- The quote merely states that a pairs trade is a bet that the spread will narrow, but the claim adds an unsupported methodological leap—'so the strategy's edge can be tested as the tendency of divergent spreads to converge'—which introduces a specific testing framework and the qualifier 'divergent spreads' not present in the quote.
- The quote merely describes the strategy of identifying co-moving/diverging instruments and trading deviations, but does not assert that the pattern 'will continue' or that such trading 'is profitable' — these are unsupported additions.
- The quote only describes identifying co-moving instruments and trading deviations, but the claim adds unsupported assertions that the behavior 'will continue' and that such trading 'is profitable,' which are not stated in the quote.
- The quote merely describes identifying co-moving/diverging instruments and trading deviations, but does not assert that this behavior 'will continue' (a predictive claim) or that such trading is 'profitable' (a performance claim), both of which are unsupported additions.

## Restatements caught by the semantic dedupe (never sent to the panel)

- [1] Both claims state that price divergences caused by forced/price-insensitive trading (rather than fundamentals) between correlated assets are noise that tends to mean-revert.
- [2] Both claims assert that spread divergences caused by genuine fundamental repricing do not converge, contrasting with flow-driven (noise) divergences that do.
- [3] Both claims assert that cointegration/correlation tests are less reliable/useful for pair selection due to estimation error and non-stationarity.
- [0] Both claims assert that persistent arbitrage opportunities are rare and get eliminated once other participants trade against/enter them, just generalized from crypto markets to arbitrage in general.
- [3] The new claim combines held claims 3 and 4, both of which are already stated separately and identically regarding the upward-sloping risk premium term structure converging to 3.6% and stable ~3% dividend growth rates.
- [0] The new claim essentially repeats claim 0's assertion that negative monetary surprises cause a 25bp parallel, statistically insignificant rise in the equity risk premium, merely adding a summary of claim 1's similar finding for positive surprises rather than introducing new information.
- [2] Both claims state that dovish surprises reduce expected dividend growth by over 50bps at short maturities and 20bps at long maturities, with no such pattern for hawkish surprises.
- [0] The new claim restates claim 0's core assertion (long-short strategy shorting equities/long bonds achieving Sharpe >1 over 1997-2023), adding only extra robustness details not contradicted elsewhere.
- [1] Both claims state that replicating the front-running rebalancing strategy yields a Sharpe ratio of 0.94, close to the original paper's value.
- [2] The new claim's general assertion that blending 50/50 improves performance is a restatement of the specific improvement in Sharpe ratio (from 1.08 to 1.30) reported in claim 2.
- [0] Both claims describe the same SPX Memorial Day week bullish tendency from 1983-2009 with weakening in the last 16 years; the new claim adds detail about the 1970s but conveys the same core assertion as claim 0.
- [0] Claim 0 already defines Memorial Day week performance as measured from the Friday before to the Friday after, which is the same measurement definition stated in the new claim.
- [1] Both claims assert that Thursday of Memorial Day week has a persistent upside tendency in SPX, matching held claim 1 which states the same with a 70% win rate.
- [2] Both claims state that Thursday of Memorial Day week has a 70% win rate and a steady/strong upward move in SPX, making it a favorable seasonal trade.
- [0] Both state that on-the-run Treasuries, under 4% of outstanding debt, account for 65% of average daily trading volume, with the new claim just adding the $30 trillion figure.
- [1] Claim 1 already states the same finding (off-the-run relies more on dealer-to-customer trading vs interdealer/ATS relative to on-the-run) using the same Jan-June 2024 data, just without the specific dollar figures.
- [8] The new claim exactly repeats the 18% statistic about offsetting customer trading activity within fifteen-minute intervals for off-the-run Treasury securities already stated in claim 8.
- [2] Both claims report identical average daily trading volume figures ($56.3B, $5.5B, $1.6B) for on-the-run, first off-the-run, and second off-the-run 2-year Treasury notes.
- [3] Both claims report the identical drop in average daily trade count from ~37,900 to 465 for 5-year and ~37,523 to 661 for 10-year notes as they go off-the-run.
- [4] Both claims report the exact same statistics on average trade size increasing from on-the-run to first off-the-run for 5-year, 10-year, and 2-year Treasury securities.
- [5] The new claim restates the exact same 2-year sector spread figures (0.66bp, 1.22bp, 2.23bp) as held claim 5, adding only a general observation about doubling that doesn't introduce new distinct data.
- [6] This new claim provides the specific numeric detail behind the same assertion in claim 6 — that during March 2020 stress, effective spreads widened much more for off-the-run than on-the-run 2-year notes — making it a more detailed restatement rather than a new relationship.
- [7] Both claims report the same CTD-status effect on additional daily trading volume with identical figures ($1.31bn 5-year, $0.75bn 10-year, $0.51bn 2-year).
- [7] Both claims state that CTD status increases trading interest/volume in off-the-run Treasury securities compared to otherwise similar off-the-run securities, with claim 7 providing the specific quantitative figures.
