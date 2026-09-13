# Pooled-crypto benchmark backfill report

READ ONLY: this script never writes to registry_log.jsonl or artifacts/. It writes exactly one new file, this report.

One-off analysis, NOT chain data. Compares each pooled-crypto quarantine strategy's committed OOS performance (compound(contributions(oos_trades.csv)) -- the chained trade set, never a re-simulation) to three buy-and-hold controls, each net of ONE round trip of the spec's own committed cost_model (per_side = commission_per_side + slippage_ticks, cost = 2 * per_side):

1. `btc_hold`: last OOS close / first OOS open - 1 on BTCUSD bars.
2. `eth_hold`: the same on ETHUSD bars.
3. `basket_hold`: 50/50 daily-rebalanced, matching the engine's mean-combine: over the SHARED OOS calendar (dates present in both assets' OOS bars), per-day return of each asset = close_t / close_{t-1} - 1 (close-to-close); day-1 return of each asset = close_1 / open_1 - 1 (entry at the first shared OOS open); basket day return = mean of the two; basket_net = compound of basket day returns - 2 * per_side.

OOS window: bars with date strictly after the committed config.json cutoff, compared via [:10] date slices only.

All 20 strategies share one committed cutoff (2023-12-31), so the three control values are constant across rows; each excess column compares 20 strategy returns to a single control value.

Generated 2026-08-28 UTC, 20 strategies.

| edge | sid | sibling_group_id | strategy_net | btc_hold | eth_hold | basket_hold | excess_btc | excess_eth | excess_basket |
|---|---|---|---|---|---|---|---|---|---|
| #0065 | 9b6753a48c4d0ccd | channel_breakout_both_fixedfrac-2026-08-17-gen3 | +74.4982% | +89.4890% | +9.7387% | +52.0405% | -14.9908% | +64.7595% | +22.4577% |
| #0070 | ad654fd8097717bd | channel_breakout_both_voltarget_control-2026-08-17-gen3 | +376.5521% | +89.4890% | +9.7387% | +52.0405% | +287.0631% | +366.8134% | +324.5116% |
| #0077 | ef7712f41e2188e2 | tstat_trend_both_asymmetric_payoff-2026-08-17-gen3 | +45.9074% | +89.4890% | +9.7387% | +52.0405% | -43.5816% | +36.1687% | -6.1331% |
| #0111 | 9619c45910f81628 | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +89.8583% | +89.4890% | +9.7387% | +52.0405% | +0.3693% | +80.1196% | +37.8178% |
| #0113 | ec2a4a062fdade66 | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +49.8308% | +89.4890% | +9.7387% | +52.0405% | -39.6582% | +40.0921% | -2.2097% |
| #0114 | 79f1795f82c10ff1 | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +83.4988% | +89.4890% | +9.7387% | +52.0405% | -5.9902% | +73.7601% | +31.4583% |
| #0115 | 815e500ddf61804c | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +55.0535% | +89.4890% | +9.7387% | +52.0405% | -34.4355% | +45.3148% | +3.0131% |
| #0116 | b11520f5fcb39ec2 | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +46.3636% | +89.4890% | +9.7387% | +52.0405% | -43.1253% | +36.6250% | -5.6768% |
| #0117 | e43b1ba649ba4049 | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +88.5943% | +89.4890% | +9.7387% | +52.0405% | -0.8947% | +78.8556% | +36.5539% |
| #0118 | 1e119eb8c24aa07f | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +64.2279% | +89.4890% | +9.7387% | +52.0405% | -25.2611% | +54.4892% | +12.1875% |
| #0119 | f893e40c702b239c | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +49.5716% | +89.4890% | +9.7387% | +52.0405% | -39.9174% | +39.8329% | -2.4689% |
| #0120 | f3ebc4780df5b7ee | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +81.4086% | +89.4890% | +9.7387% | +52.0405% | -8.0804% | +71.6699% | +29.3681% |
| #0121 | 0a4dfa50cadb0d1a | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +59.7978% | +89.4890% | +9.7387% | +52.0405% | -29.6912% | +50.0591% | +7.7573% |
| #0122 | 7721aab07a2109da | symmetric_channel_breakout_fixedfrac-2026-08-22-gen5 | +46.6693% | +89.4890% | +9.7387% | +52.0405% | -42.8197% | +36.9306% | -5.3712% |
| #0127 | e6c9cf03c6335acc | tstat_trend_scan_symmetric_fixedfrac-2026-08-22-gen5 | +51.7968% | +89.4890% | +9.7387% | +52.0405% | -37.6922% | +42.0581% | -0.2437% |
| #0128 | 898aea1ac2256755 | tstat_trend_scan_symmetric_fixedfrac-2026-08-22-gen5 | +63.7744% | +89.4890% | +9.7387% | +52.0405% | -25.7146% | +54.0357% | +11.7340% |
| #0129 | cf6be2c4586d0b96 | tstat_trend_scan_symmetric_fixedfrac-2026-08-22-gen5 | +48.6724% | +89.4890% | +9.7387% | +52.0405% | -40.8166% | +38.9337% | -3.3680% |
| #0146 | a203e819409818a1 | volregime_trend_voltarget_arm-2026-08-22-gen5 | +54.8845% | +89.4890% | +9.7387% | +52.0405% | -34.6045% | +45.1458% | +2.8440% |
| #0152 | 1923769fa764c8ac | volregime_trend_fixedfrac_arm-2026-08-22-gen5 | +29.0639% | +89.4890% | +9.7387% | +52.0405% | -60.4250% | +19.3253% | -22.9765% |
| #0153 | 3e039e9bf42edb19 | volregime_trend_fixedfrac_arm-2026-08-22-gen5 | +52.6283% | +89.4890% | +9.7387% | +52.0405% | -36.8607% | +42.8896% | +0.5878% |

## Summary

- n = 20
- vs btc_hold: n with excess > 0 = 2 / 20, median excess = -34.5200%
- vs eth_hold: n with excess > 0 = 20 / 20, median excess = +45.2303%
- vs basket_hold: n with excess > 0 = 12 / 20, median excess = +2.9285%

## Survivorship and honesty notes

- This cohort is the SURVIVORS: 20 quarantine passes out of every pooled-crypto strategy the pipeline ever generated. Comparing survivors to buy-and-hold overstates the pipeline; the graveyard is not in this table.
- The eq-gen1 precedent (docs/runs/2026-08-26-eq-gen1-benchmark-report.md): 0/96 equity quarantine passes beat their own ETF's buy-and-hold -- an absolute gate passing beta. If all 20 crypto strategies sit below all three controls here, that is the same finding, and it is the finding.
- Control-cost asymmetry: strategy_net embeds per-trade costs on every committed trade, while the controls pay ONE round trip and the daily-rebalanced basket is charged no rebalancing costs -- control costs are understated, so negative excess is partly cost asymmetry at the margin; positive excess is the stronger signal. strategy_net is also at the strategy's actual notional_frac exposure vs a 100%-invested control (the same convention as the live oos_negative gate).
- RECORDED, NOT GATED: nothing here changes any strategy's quarantine state. This is a report, not chain data.
- Edge numbers are D11 display labels (chain registration order), never identity or N accounting.
