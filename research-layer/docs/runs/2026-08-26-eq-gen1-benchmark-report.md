# eq-gen1 benchmark-relative backfill report

One-off analysis, NOT chain data. Recomputes SP4 Task B1's `metrics["benchmark_relative"]` (a same-OOS-window buy-and-hold of the cell's own asset, net of one round trip of the class's cost model) for the 96 equity_etf strategies eq-gen1 already carried into quarantine, from their committed gauntlet artifacts and the pinned `data/` CSVs -- these strategies were verdicted before B1's `gauntlet.py` wiring existed, so their chain entries never carry this key retroactively; only a verdict chained after B1 shipped does. Pre-registered in `docs/2026-08-24-sp4-track2a-addendum.md` ("Pre-registration: benchmark-relative control (B1, Coen 2026-08-26)"). RECORDED, NOT GATED -- nothing here changes any strategy's quarantine state.

Generated 2026-08-27 UTC, 96 strategies.

| sid | cell | family | oos strategy_net | buy_hold_net | excess |
|---|---|---|---|---|---|
| 7691c71d36ae7381 | EFA_1d | dip_reversion_in_uptrend | +6.1329% | +45.1706% | -39.0377% |
| ce722df15f17f6eb | EFA_1d | dip_reversion_in_uptrend | +12.1195% | +45.1706% | -33.0512% |
| ecef0a9fc47c0132 | EFA_1d | dip_reversion_in_uptrend | +9.4616% | +45.1706% | -35.7090% |
| 6bb8ac80cdc572cb | EWC_1d | dip_reversion_in_uptrend | +36.8621% | +70.7625% | -33.9004% |
| e9d3687bcc29bdce | EWC_1d | dip_reversion_in_uptrend | +26.4494% | +70.7625% | -44.3131% |
| efaef00685731331 | EWC_1d | dip_reversion_in_uptrend | +37.0425% | +70.7625% | -33.7200% |
| 7143045376b811b1 | MDY_1d | dip_reversion_in_uptrend | +26.4451% | +38.8108% | -12.3658% |
| 9befc7c70654c781 | MDY_1d | dip_reversion_in_uptrend | +13.7675% | +38.8108% | -25.0434% |
| ae1e3350baa20623 | MDY_1d | dip_reversion_in_uptrend | +29.1408% | +38.8108% | -9.6700% |
| c29de2ea37b1dc14 | MDY_1d | dip_reversion_in_uptrend | +17.1273% | +38.8108% | -21.6836% |
| cb681faa0d6d6138 | MDY_1d | dip_reversion_in_uptrend | +18.8756% | +38.8108% | -19.9352% |
| fb583e245f3fcd89 | MDY_1d | dip_reversion_in_uptrend | +28.4784% | +38.8108% | -10.3325% |
| 5b1ac768e2df233b | SPY_1d | dip_reversion_in_uptrend | +10.7915% | +62.1338% | -51.3424% |
| 85b894e748398f51 | SPY_1d | dip_reversion_in_uptrend | +12.3125% | +62.1338% | -49.8213% |
| 8a4f450c79289508 | SPY_1d | dip_reversion_in_uptrend | +12.9308% | +62.1338% | -49.2030% |
| 93d5767215d0cf87 | SPY_1d | dip_reversion_in_uptrend | +6.4847% | +62.1338% | -55.6491% |
| d32f639f53c4e62e | SPY_1d | dip_reversion_in_uptrend | +8.0200% | +62.1338% | -54.1138% |
| e8d1ac8ec55e20a2 | SPY_1d | dip_reversion_in_uptrend | +7.1299% | +62.1338% | -55.0040% |
| e99ae86c6390d149 | SPY_1d | dip_reversion_in_uptrend | +13.9318% | +62.1338% | -48.2020% |
| fcc17778b03bdbe3 | SPY_1d | dip_reversion_in_uptrend | +10.5469% | +62.1338% | -51.5869% |
| 789cea47976ae0b2 | DIA_1d | regime_gated_range_breakout | +7.6576% | +41.9611% | -34.3034% |
| c41cc3b37ca62f8b | EWH_1d | regime_gated_range_breakout | +8.5923% | +37.7181% | -29.1258% |
| 050d59f3bcb9d5d1 | EWY_1d | regime_gated_range_breakout | +59.7043% | +174.3714% | -114.6672% |
| 408ad3bf8b8fa64f | EWY_1d | regime_gated_range_breakout | +75.0934% | +174.3714% | -99.2780% |
| 47fb6b15cd4878db | EWY_1d | regime_gated_range_breakout | +43.9442% | +174.3714% | -130.4272% |
| 8b5e8b5d5e11b72f | EWY_1d | regime_gated_range_breakout | +59.7043% | +174.3714% | -114.6672% |
| b0a63c53d5241521 | EWY_1d | regime_gated_range_breakout | +59.7043% | +174.3714% | -114.6672% |
| 05e62e18eccd8472 | QQQ_1d | regime_gated_range_breakout | +33.6127% | +75.7534% | -42.1407% |
| 0cf46082b3f72834 | QQQ_1d | regime_gated_range_breakout | +24.5713% | +75.7534% | -51.1821% |
| 1d306b97902801e2 | QQQ_1d | regime_gated_range_breakout | +21.2376% | +75.7534% | -54.5158% |
| 21d99b1248c50727 | QQQ_1d | regime_gated_range_breakout | +21.5263% | +75.7534% | -54.2272% |
| 27674aa494999521 | QQQ_1d | regime_gated_range_breakout | +30.9461% | +75.7534% | -44.8073% |
| 3573d9e0f0edb685 | QQQ_1d | regime_gated_range_breakout | +24.6017% | +75.7534% | -51.1517% |
| 388d0098087efec1 | QQQ_1d | regime_gated_range_breakout | +20.9474% | +75.7534% | -54.8060% |
| 3a6b23910ad5d918 | QQQ_1d | regime_gated_range_breakout | +22.8900% | +75.7534% | -52.8634% |
| 3bf09fb5a636d25a | QQQ_1d | regime_gated_range_breakout | +27.8022% | +75.7534% | -47.9512% |
| 4ea1dad4275a5180 | QQQ_1d | regime_gated_range_breakout | +29.5349% | +75.7534% | -46.2185% |
| 87ba620d3c2bf690 | QQQ_1d | regime_gated_range_breakout | +20.8464% | +75.7534% | -54.9070% |
| a0448071ca164b27 | QQQ_1d | regime_gated_range_breakout | +10.8160% | +75.7534% | -64.9374% |
| a7c8f588dc436003 | QQQ_1d | regime_gated_range_breakout | +25.3015% | +75.7534% | -50.4520% |
| acd2ba87ecbe27a3 | QQQ_1d | regime_gated_range_breakout | +28.7850% | +75.7534% | -46.9684% |
| d1798d6c4139ae04 | QQQ_1d | regime_gated_range_breakout | +33.7759% | +75.7534% | -41.9775% |
| e606af57b5acf0e5 | QQQ_1d | regime_gated_range_breakout | +21.7280% | +75.7534% | -54.0254% |
| eb38c5f4a28d8c72 | QQQ_1d | regime_gated_range_breakout | +20.9908% | +75.7534% | -54.7626% |
| eb940fb59d9b2646 | QQQ_1d | regime_gated_range_breakout | +17.1318% | +75.7534% | -58.6217% |
| f1fce8fd8f3dd268 | QQQ_1d | regime_gated_range_breakout | +23.7767% | +75.7534% | -51.9767% |
| 042464355b3ddb3b | SPY_1d | regime_gated_range_breakout | +15.0796% | +62.1338% | -47.0542% |
| 1588fde4c951a2a3 | SPY_1d | regime_gated_range_breakout | +19.8813% | +62.1338% | -42.2525% |
| 51a1ff87bdfe5f07 | SPY_1d | regime_gated_range_breakout | +23.8733% | +62.1338% | -38.2606% |
| 848d576600cefc62 | SPY_1d | regime_gated_range_breakout | +20.3219% | +62.1338% | -41.8120% |
| 8b9e032f9bf44412 | SPY_1d | regime_gated_range_breakout | +15.2678% | +62.1338% | -46.8661% |
| 8d0a84d3a29a075c | SPY_1d | regime_gated_range_breakout | +14.2183% | +62.1338% | -47.9155% |
| b7408e180c9c1b8c | SPY_1d | regime_gated_range_breakout | +18.9418% | +62.1338% | -43.1921% |
| bddd9f9336da191f | SPY_1d | regime_gated_range_breakout | +10.6463% | +62.1338% | -51.4875% |
| c5dc7d26d59b364d | SPY_1d | regime_gated_range_breakout | +8.9423% | +62.1338% | -53.1915% |
| c722e88dda302e03 | SPY_1d | regime_gated_range_breakout | +11.4592% | +62.1338% | -50.6746% |
| cf69709b0463cc08 | SPY_1d | regime_gated_range_breakout | +6.0228% | +62.1338% | -56.1110% |
| de4903902864d205 | SPY_1d | regime_gated_range_breakout | +14.1340% | +62.1338% | -47.9999% |
| f1c081b3cf7ef0ca | SPY_1d | regime_gated_range_breakout | +13.1535% | +62.1338% | -48.9804% |
| 11d6db80dc759dbb | DIA_1d | tstat_trend_long_pooled | +19.2538% | +41.9611% | -22.7073% |
| 1b186a26aeef4e78 | DIA_1d | tstat_trend_long_pooled | +19.2538% | +41.9611% | -22.7073% |
| 2777184e46fd70d7 | DIA_1d | tstat_trend_long_pooled | +19.2538% | +41.9611% | -22.7073% |
| 94511baf362ee5a4 | DIA_1d | tstat_trend_long_pooled | +19.2538% | +41.9611% | -22.7073% |
| ad4ebfb46c8f08cc | DIA_1d | tstat_trend_long_pooled | +19.2538% | +41.9611% | -22.7073% |
| fe0cf79022dca2f3 | DIA_1d | tstat_trend_long_pooled | +19.2538% | +41.9611% | -22.7073% |
| 0f835748333252cb | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| 129a0b861886ccd4 | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| 1816746df2390478 | EWY_1d | tstat_trend_long_pooled | +68.8625% | +174.3714% | -105.5089% |
| 4493e4344a28540c | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| 4d9b27c6dc9f86e9 | EWY_1d | tstat_trend_long_pooled | +68.8625% | +174.3714% | -105.5089% |
| 5faaa1017264f51f | EWY_1d | tstat_trend_long_pooled | +68.8625% | +174.3714% | -105.5089% |
| 6a370b098fbc116d | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| 6ccc0699ca17d67e | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| 706568092c974637 | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| 84b3a36415f36b3c | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| c2f7e1a31cafb336 | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| d1ed7084bdf0d571 | EWY_1d | tstat_trend_long_pooled | +89.8538% | +174.3714% | -84.5177% |
| 18cc692e97de650b | QQQ_1d | tstat_trend_long_pooled | +10.6932% | +75.7534% | -65.0602% |
| ae08e37e02fa22cf | QQQ_1d | tstat_trend_long_pooled | +10.6932% | +75.7534% | -65.0602% |
| fa0c81dee6c2d3fc | QQQ_1d | tstat_trend_long_pooled | +10.6932% | +75.7534% | -65.0602% |
| 0b800be2f18154ae | SPY_1d | tstat_trend_long_pooled | +13.3832% | +62.1338% | -48.7507% |
| 157b5473d1b73e67 | SPY_1d | tstat_trend_long_pooled | +13.3832% | +62.1338% | -48.7507% |
| 1584d4cba7e6231f | SPY_1d | tstat_trend_long_pooled | +21.3260% | +62.1338% | -40.8078% |
| 2538bcf40580e4dc | SPY_1d | tstat_trend_long_pooled | +21.3260% | +62.1338% | -40.8078% |
| 2bf6015dca69054c | SPY_1d | tstat_trend_long_pooled | +13.3832% | +62.1338% | -48.7507% |
| 444f1e13fe9c2aa7 | SPY_1d | tstat_trend_long_pooled | +21.3260% | +62.1338% | -40.8078% |
| 529f648c90b8de1b | SPY_1d | tstat_trend_long_pooled | +14.2838% | +62.1338% | -47.8500% |
| 72df228d70dd3a65 | SPY_1d | tstat_trend_long_pooled | +27.4028% | +62.1338% | -34.7310% |
| 776c63c7399a660c | SPY_1d | tstat_trend_long_pooled | +27.4028% | +62.1338% | -34.7310% |
| a904ddf7e249a472 | SPY_1d | tstat_trend_long_pooled | +27.4028% | +62.1338% | -34.7310% |
| b795702c03fb0535 | SPY_1d | tstat_trend_long_pooled | +27.4028% | +62.1338% | -34.7310% |
| bfb8b59d602fe29c | SPY_1d | tstat_trend_long_pooled | +27.4028% | +62.1338% | -34.7310% |
| dcb9b4711b0501d0 | SPY_1d | tstat_trend_long_pooled | +14.2838% | +62.1338% | -47.8500% |
| e5b543c817ecec33 | SPY_1d | tstat_trend_long_pooled | +27.4028% | +62.1338% | -34.7310% |
| f8e27c436fbb99b8 | SPY_1d | tstat_trend_long_pooled | +14.2838% | +62.1338% | -47.8500% |
| c6b9b6a979661050 | IWM_1d | vol_filtered_ma_cross_speed | +14.6113% | +50.3913% | -35.7800% |

## Summary

- n = 96
- n with excess > 0: 0 / 96
- median excess: -48.4763%
