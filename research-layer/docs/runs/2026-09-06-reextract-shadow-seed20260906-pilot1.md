# Re-extract shadow run 2026-09-06

**Verdict: AMBER** (6.80 novel accepted per document; green needs >= 2.0, red is < 0.5)

Sample: 5 document(s) at seed 20260906, python 3.14.3, extractor claude-opus-5, panel claude-sonnet-5; 0 errored, 0 stopped at the budget. Spend USD 1.16.

| measure | old corpus | this run |
|---|---:|---:|
| accept rate (judged cards only) | 86% | 83% |
| undecided (old: pending share / new: unjudged count) | 25% | 0 |

Claims proposed 41, dropped by the honesty guard 0, duplicates of held cards 0, novel 41 (accepted 34, escalated 7, unjudged 0).

**Limitation:** duplicate detection is `claim_fingerprint`, which is normalised but not semantic, so a paraphrase of a held claim counts as novel and reaches the panel. Read the novel figure with that in mind; the escalation reasons below are where paraphrases surface.

## Per document

| document | band | old A/R/P | proposed | guard | dupe | novel | acc | esc | unj | USD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A Tale of Two Prices | passed | 4/0/0 | 8 | 0 | 0 | 8 | 5 | 3 | 0 | 0.243 |
| Does the Equity Term Structure Respond to Mo | passed | 5/0/0 | 4 | 0 | 0 | 4 | 3 | 1 | 0 | 0.198 |
| The Unintended Consequences of Rebalancing | stalled | 0/1/6 | 11 | 0 | 0 | 11 | 9 | 2 | 0 | 0.249 |
| Memorial Week History And The One Day That's | stalled | 0/2/1 | 4 | 0 | 0 | 4 | 4 | 0 | 0 | 0.071 |
| Liquidity Fades as Treasuries Age | passed | 9/0/0 | 14 | 0 | 0 | 14 | 13 | 1 | 0 | 0.403 |

## Escalation reasons (every dissenting reviewer)

- The quote states the pairs trade is 'a bet on convergence' but does not assert that convergence is the exclusive condition for profitability, so the claim's added phrase 'profitable only if the spread...subsequently narrows' overstates the quote's claim.
- The quote merely states the pairs trade is a bet that the spread will narrow, but the claim adds the stronger logical assertions that it is 'profitable only if' this happens and that 'its returns should be explained by spread convergence,' which are inferences not stated in the quote.
- The quote only states that fundamentally-driven spread divergence does not converge; it never contrasts this with 'flow-driven divergences' converging, so the added comparison 'unlike flow-driven divergences' is not supported by the verbatim quote.
- The quote only states that fundamental-reason divergences don't converge; it does not mention or characterize 'flow-driven divergences' as converging, so the contrast added in the claim ('unlike flow-driven divergences') is not supported by the verbatim text.
- The quote only states that fundamentally-driven spread divergences don't converge; it never mentions or contrasts 'flow-driven divergences' converging, so the added comparison is not supported by the verbatim quote.
- The quote is a personal reflection stating that statistical tools like cointegration/correlation 'aren't as useful as you'd hope' due to estimation error and non-stationarity, but it does not make the specific claim that 'statistically selected pairs should underperform expectations out of sample' "
- The quote is a casual first-person remark that cointegration/correlation tests 'aren't as useful as you'd hope' due to estimation error and non-stationarity, but it does not state or imply that statistically selected pairs 'should underperform expectations out of sample'—that predictive/normative conclusion is an added inference not supported by the verbatim quote.
- The quote only reports the author's personal finding that cointegration/correlation tests were less useful than hoped due to estimation error and non-stationarity, but it does not state or imply that statistically selected pairs 'should underperform expectations out of sample' as a general predictive rule, which is an added generalization beyond the quote.
- The claim mislabels the quote's 'risk premium' as the 'equity risk premium' and omits that the quote is specifically about the average risk premium's uniform shift, but more critically it presents this as a general finding without the paper's context of 'some support for the information channel of MPS' - however the more substantive issue is the claim asserts these are 'similar movements' for positive surprises without the quote's hedging language 'potentially suggesting' being dropped, though this is a minor omission; the core claims about magnitude, uniformity, and insignificance are faithfully restated from the quote.
- The quote only states that the mean reversion portfolio is used 'as a baseline' without specifying what it is a baseline for, so the claim's added specifics 'front-running strategy's diversification benefit' are not supported by the quote.
- The quote only states that the mean reversion portfolio will be used 'as a baseline' without specifying that it's being compared against a 'front-running strategy' or measuring a 'diversification benefit', which are details the claim adds without support from this quote.
- The quote only states that the mean reversion portfolio is used 'as a baseline' for comparison, but it does not mention a 'front-running strategy' or measuring its 'diversification benefit', which are specifics added by the claim not supported by the quote.
- The quote never specifies that the ~8bp cost applies to 'long-term investors' or characterizes the rebalancing as 'mechanical'; these qualifiers are not supported by the verbatim text.
- The quote uses the hedged/predictive phrase 'should attract more trading interest,' but the claim asserts this as a definite fact ('attract more trading interest'), turning a prediction into a stated result.
- The quote uses hedging language ('should attract more trading interest') expressing an expected/predicted relationship, but the claim states it as a definitive fact ('attract more trading interest'), turning a stated expectation into an asserted result.
- The quote uses hedging language ('should attract more trading interest'), a predicted/expected outcome, whereas the claim asserts it as an established fact ('attract more trading interest'), turning a proposed inference into a confirmed result.
