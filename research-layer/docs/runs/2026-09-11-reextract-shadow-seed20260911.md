# Re-extract shadow run 2026-09-11

**Verdict: GREEN** (4.60 novel accepted per document; green needs >= 2.0, red is < 0.5)

Sample: 5 document(s) at seed 20260911, python 3.14.3, extractor claude-opus-5, panel claude-sonnet-5; 0 errored, 0 stopped at the budget. Spend USD 1.80.

| measure | old corpus | this run |
|---|---:|---:|
| accept rate (judged cards only) | 80% | 85% |
| undecided (old: pending share / new: unjudged count) | 20% | 0 |

Claims proposed 65, dropped by the honesty guard 2, duplicates of held cards 0, restatements of held cards 36, judge failures 0, novel 27 (accepted 23, escalated 4, unjudged 0).

**Limitation:** duplicate detection is `claim_fingerprint` (normalised, not semantic) followed by a one-call semantic check against cards held from the SAME document. A restatement of a card held from a DIFFERENT document, or one the judge misses, still counts as novel and reaches the panel; the restatements section shows what the check caught. Paraphrase across documents is the remaining gap.

## Per document

| document | band | old A/R/P | proposed | guard | dupe | rest | novel | acc | esc | unj | USD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Advances in Financial Machine Learning: Lect | passed | 15/3/0 | 16 | 0 | 0 | 15 | 1 | 1 | 0 | 0 | 0.484 |
| Are markets that are good for trend good jus | passed | 12/2/0 | 10 | 0 | 0 | 6 | 4 | 3 | 1 | 0 | 0.271 |
| The Derivative Payoff Bias | stalled | 0/2/6 | 20 | 2 | 0 | 7 | 11 | 8 | 3 | 0 | 0.456 |
| EOG Resources (EOG): Turning a 9.4% Target I | stalled | 1/1/3 | 6 | 0 | 0 | 4 | 2 | 2 | 0 | 0 | 0.229 |
| productDec 29, 2025Intraday AI Signals on ES | passed | 5/0/1 | 13 | 0 | 0 | 4 | 9 | 9 | 0 | 0 | 0.356 |

## Escalation reasons (every dissenting reviewer)

- The quote merely says 'the same pattern of the relationship is present' and 'the vol line has a worse intercept,' without specifying that the relationship is between 'adjusted price drift SR' and 'trend-following SR,' or that this holds at 'the other two trend speeds,' or that 'vol' refers to an 'asset class' line — these specific details are not supported by the verbatim quote alone.
- The quote only states 'the same pattern of the relationship is present' without specifying what the relationship is (adjusted price drift SR vs trend-following SR) or mentioning 'other two trend speeds' - these specific details are asserted in the claim but not present in the verbatim quote.
- The claim specifies 'the other two trend speeds' as the context for the pattern, but the verbatim quote contains no mention of trend speeds at all—this specific detail appears to be added context not supported by the quote itself.
- The quote never specifies 'S&P 500 index derivatives'; the claim adds this asset class detail not present in the verbatim quote, and it changes 'tends to be' into a definitive 'has been'.
- The claim specifies 'S&P 500 index derivatives,' an asset class not mentioned in the verbatim quote, and hardens the quote's tentative 'tends to be' into a definitive 'has been', both of which overstate the source's claim.
- The quote only lists the mechanical steps (long Thursday close, reverse short Friday open, close before Friday noon) but does not state the purpose/rationale of "capturing the overnight drift" and "avoiding the intraday reversal" - this explanatory framing is an addition not supported by the verbatim quote.
- The quote only lists the mechanical steps (long at Thursday close, reverse to short at Friday open, close before Friday noon) but does not state the rationale that this is meant to 'capture the overnight drift and avoid the intraday reversal,' which the claim adds as an unsupported explanation.
- The quote only lists the mechanical steps (long at Thursday close, reverse short at Friday open, close before Friday noon) without stating any rationale, so the added phrase 'to capture the overnight drift and avoid the intraday reversal' is an interpretive addition not supported by the verbatim quote.
- The quote only says 'this demand imbalance' with no mention of 'overnight' or that it is 'driving the price spike,' so the claim adds unsupported details about timing and causal effect on price.
- The quote only says 'This demand imbalance is strongest on Triple Witching days and off-quarterly expirations' without specifying it is an 'overnight' imbalance or that it is 'driving the price spike'—these added descriptors are not supported by the verbatim quote.
- The claim adds the qualifiers 'overnight' and 'driving the price spike' which are not present in the verbatim quote, which only states that 'this demand imbalance' (unspecified as overnight or price-spike-related) is strongest on those days.

## Restatements caught by the semantic dedupe (never sent to the panel)

- [0] Both claims state the identical formula m = 2Z(z) - 1 for deriving bet size from a predicted probability using the standard normal CDF, with output range [-1,1].
- [3] Claim 3 already states that averaging bet sizes across active bets reduces turnover compared to overriding old bets, which is the same assertion as the new claim.
- [4] Claim 4 already states that averaging bet sizes still triggers small residual 'jitter' trades on nearly every prediction, matching the new claim's assertion about reduced but not eliminated turnover.
- [5] Both state that searching/testing many independent trials yields high Sharpe ratios by chance even absent true skill, matching claim 5's assertion about backtest selection across many trials.
- [5] Both claims state that the Sharpe ratio of a selected strategy should be deflated based on the number of backtest trials, and that overfitting probability should be estimated for the final result, matching claim 5's assertion about discounting Sharpe by trial count and estimating overfitting.
- [7] Claim 7 already states that bagging reduces forecast-error variance and overfitting, and that degraded performance under bagging indicates overfitting to few observations/outliers, matching the new claim exactly.
- [6] Both claims state that a pattern profitable only on a single security (not across an asset class/universe) is likely a false discovery, and this new claim adds the same recommendation implied by claim 6 to develop models across whole universes.
- [8] Claim 8 already states that profitability only on the single historical path indicates overfitting and robustness should be assessed via profitability across many simulated scenarios, matching the new claim.
- [9] Claim 9 states the exact same assertion: walk-forward results are biased by sequence, and reversal-induced performance changes indicate overfitting.
- [10] The new claim restates claim 10's assertion that CV results are independent of time ordering, so reversing the sequence should not materially change the result.
- [11] Both claims state that Combinatorial Purged Cross-Validation produces multiple backtest paths yielding a distribution of Sharpe ratios instead of a single likely overfit estimate.
- [13] Claim 13 already states that for small-half-life mean-reverting processes with zero equilibrium, Sharpe is maximized by combining small profit-taking with large stop-loss, which is the same assertion as the new claim.
- [14] Claim 14 already states that a tight stop-loss combined with a large profit-taking threshold is the worst-performing rule, which the new claim merely rephrases.
- [15] The new claim's 'positions tend to make money' corresponds to the positive long-run equilibrium case in claim 15, and both state the profit-taking threshold is higher plus the same half-life widening/narrowing relationship.
- [17] Claim 17 already states that the negative long-run equilibrium Sharpe surface is the rotated complement of the positive-equilibrium case, identical to the new claim.
- [0] Both claims state that bond futures achieve the highest Sharpe Ratio on a long-only, volatility-adjusted basis with a constant forecast of +10.
- [1] The new claim combines claims 1 and 2 verbatim, restating both the equity long-only Sharpe/trend-following relationship and the FX poor-long-only-but-good-trend-following relationship already stated separately.
- [5] This restates held claim 5's point that spot returns relate strongly to slow trend-following Sharpe while carry returns relate weakly, just adding the adjusted-price return as the strongest correlate.
- [3] Both claims assert the same regression finding: momentum64 SR vs adjusted price drift SR has a ~0.9 slope for bonds, FX, metals and vol, and a much weaker relationship for other asset classes (matching claim 3, with claim 4 covering the weaker part).
- [5] This claim just combines the already-held findings that spot returns strongly predict trend SR while carry returns are weakly related (claim 5) and that in vol markets the downtrend is driven by carry/vol premium (claim 6), adding no new information beyond these two existing claims.
- [7] Both state that secular price drift converts to trend-following Sharpe at roughly 0.7-0.9 units per unit of drift, consistent across asset classes except energies, equities and ags.
- [1] This is a direct restatement of claim 1, which states the same fact about a.m.-settled bias absence in p.m.-settled options.
- [3] The new claim reiterates the same post-publication ES futures long-leg (0.7 bps) and short-leg (-2.6 bps) returns with statistically insignificant t-stats already stated in claim 3.
- [5] Claim 5 already states that before publication, NQ futures had stronger and statistically significant 3rd Friday returns on both legs compared to ES, which is the same assertion as the new claim.
- [4] Claim 4 already states the identical post-publication NQ futures mean returns of 7.8 bps (long) and -15.1 bps (short), just with additional context about t-stat significance.
- [6] Claim 6 already states that the strategy's results were computed using a 3 bps transaction cost assumption, which the new claim merely repeats in isolation.
- [6] This new claim restates claim 6, which already states the same 81% total return, ~4.1% annualized return, and 6.0% maximum drawdown for the unleveraged strategy.
- [6] Claim 6 already states the strategy involves 12 trades per year, which the new claim merely restates.
- [4] Both claims assert the same general relationship that a strategy's actual annualized return can exceed its initially targeted lower annualized return, which is the core point of claim 4 about early exits producing higher-than-targeted returns.
- [2] Both claims describe the same trade: an 8.1% targeted annualized return closed early with $205 of $235 target profit, realizing 11.9% annualized return, differing only in mentioning the 94-day expiration.
- [3] Both claims assert that high-quality, low-volatility stocks like EOG and Berkshire Hathaway offer modest option premiums yet can still yield double-digit annualized returns from selling puts.
- [1] Claim 1 already states that the 94-day EOG put collected more premium than the 74-day version due to longer time to expiration, which is the same assertion as the new claim.
- [4] The new claim describes the identical intraday trading rule specification (1-minute signals, 5-minute cadence 09:45-15:30 ET, 1/70 capital sizing, close exit, 1bp cost) as claim 4, just framed as being a 'fully specified replicable rule' rather than 'can be backtested'.
- [0] This restates the same methodological detail already stated in claim 0 (and 1) that the Sep25-trained model tested on Q4 2025 constitutes an out-of-sample test, without adding new performance metrics.
- [2] This claim repeats the same ES full-year 2023-2025 Sharpe ratios and returns from claim 2, merely adding max drawdown figures as extra detail without asserting a new relationship.
- [3] The new claim repeats the same Sharpe ratios and annualized returns for NQ across 2023-2025 as claim 3, only adding max drawdown figures not present in the original but otherwise restating the same core assertion.
