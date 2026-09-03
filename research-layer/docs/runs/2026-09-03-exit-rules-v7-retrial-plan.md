# exit-rules-v7 re-trial plan (DRY RUN)

Generated 2026-09-03T23:58:10Z from `C:/Users/Coen/AppData/Local/Temp/claude/E--Users-Coen-Claude/d6198d91-59f6-4318-bf62-1274eaf4ab0c/scratchpad/d15-t5b/registry_log.jsonl` (read-only). Decision D15(b): everything is re-trialled under the version-2 grammar inside the unified re-run; a legacy registration whose engine behaviour is UNCHANGED under v7 (no retired block type, entry not `ma_cross*`) is compliant as registered and is NOT re-registered.

## Totals

- registrations: 6075
- compliant as registered: 449
- needs version-2 re-trial: 5626
- re-trial reasons (a registration may carry more than one):
  - exit/time_stop: 5063
  - stop/pct_stop: 1968
  - implicit ma_cross exit: 1275

## Per family

| family | compliant | re-trial | re-trial lifecycle | top reasons |
|---|---|---|---|---|
| bear_regime_depth_shorts | 0 | 5 | graveyard 5 | exit/time_stop (5), implicit ma_cross exit (5) |
| bear_regime_ma_cross_short_fixedfrac | 0 | 15 | graveyard 15 | exit/time_stop (15), implicit ma_cross exit (15) |
| bear_regime_short_trend_faster | 0 | 4 | graveyard 4 | exit/time_stop (4), implicit ma_cross exit (4) |
| below_ma_downtrend_short | 0 | 400 | graveyard 400 | exit/time_stop (400) |
| below_ma_short_regime_trend | 0 | 5 | graveyard 5 | exit/time_stop (5) |
| below_ma_trend_short | 0 | 240 | graveyard 240 | exit/time_stop (240) |
| breakout_event_precision_filter | 0 | 6 | graveyard 6 | exit/time_stop (6) |
| breakout_stop_target_mesh_voltarget | 0 | 5 | graveyard 1 · quarantine 4 | exit/time_stop (5) |
| breakout_triple_barrier_geometry | 0 | 400 | graveyard 373 · quarantine 27 | exit/time_stop (400) |
| breakout_vol_state_filter | 5 | 0 | — | — |
| channel_breakout_both_fixedfrac | 0 | 4 | graveyard 3 · quarantine 1 | exit/time_stop (4) |
| channel_breakout_both_voltarget_control | 0 | 4 | graveyard 3 · quarantine 1 | exit/time_stop (4) |
| credit_breakout_calm_vol_only | 200 | 0 | — | — |
| dip_reversion_in_uptrend | 0 | 240 | graveyard 220 · quarantine 20 | exit/time_stop (240) |
| downtrend_regime_fast_short_cross | 0 | 5 | graveyard 5 | exit/time_stop (5), implicit ma_cross exit (5) |
| downtrend_regime_short_ma_cross | 0 | 5 | graveyard 5 | exit/time_stop (5), implicit ma_cross exit (5) |
| downtrend_regime_short_with_target | 0 | 15 | graveyard 15 | exit/time_stop (15), implicit ma_cross exit (15) |
| downtrend_short_only | 0 | 8 | graveyard 8 | exit/time_stop (8), implicit ma_cross exit (8) |
| ewmac_speed_ladder_long | 0 | 160 | graveyard 160 | implicit ma_cross exit (160) |
| exit_geometry_mesh | 0 | 15 | graveyard 15 | implicit ma_cross exit (15) |
| forced_flow_overshoot_reversion | 0 | 8 | graveyard 8 | stop/pct_stop (8), exit/time_stop (8) |
| fx_downtrend_short_ma | 0 | 300 | graveyard 300 | stop/pct_stop (300), exit/time_stop (300), implicit ma_cross exit (300) |
| fx_overshoot_reversion_small_target_wide_stop | 0 | 60 | graveyard 60 | exit/time_stop (60), stop/pct_stop (60) |
| fx_risk_currency_downtrend_short | 0 | 60 | graveyard 60 | exit/time_stop (60), stop/pct_stop (60) |
| fx_slow_ma_trend_both | 0 | 60 | graveyard 57 · quarantine 3 | stop/pct_stop (60), implicit ma_cross exit (60) |
| fx_slow_trend_long_ma | 0 | 300 | graveyard 289 · quarantine 11 | stop/pct_stop (300), exit/time_stop (300), implicit ma_cross exit (300) |
| fx_symmetric_trend_vol_conditioned | 0 | 180 | graveyard 179 · quarantine 1 | stop/pct_stop (180), exit/time_stop (180) |
| fx_trend_vol_state_filter | 0 | 60 | graveyard 60 | stop/pct_stop (60), implicit ma_cross exit (60) |
| fx_tstat_trend_scan_both | 0 | 60 | graveyard 59 · quarantine 1 | exit/time_stop (60), stop/pct_stop (60) |
| fx_tvalue_trend_long | 0 | 180 | graveyard 177 · quarantine 3 | stop/pct_stop (180), exit/time_stop (180) |
| fx_zscore_reversion_tight_target | 0 | 300 | graveyard 300 | stop/pct_stop (300), exit/time_stop (300) |
| regime_gated_range_breakout | 0 | 400 | graveyard 361 · quarantine 39 | exit/time_stop (400) |
| short_ma_cross_below_ma_regime | 0 | 15 | graveyard 15 | exit/time_stop (15), implicit ma_cross exit (15) |
| short_only_fast_macross | 0 | 5 | graveyard 5 | exit/time_stop (5), implicit ma_cross exit (5) |
| short_only_fast_macross_high_n | 0 | 5 | graveyard 5 | exit/time_stop (5), implicit ma_cross exit (5) |
| short_trend_speed_ladder | 0 | 5 | graveyard 4 · quarantine 1 | exit/time_stop (5), implicit ma_cross exit (5) |
| slow_trend_long_duration_and_credit | 0 | 200 | graveyard 200 | exit/time_stop (200) |
| slow_trend_short_duration_below_ma | 0 | 200 | graveyard 200 | exit/time_stop (200) |
| slow_tstat_trend_long_regime_filtered | 0 | 240 | graveyard 197 · quarantine 43 | exit/time_stop (240) |
| stop_geometry_scan_on_breakout | 0 | 5 | quarantine 5 | exit/time_stop (5) |
| symmetric_breakout_fixedfraction | 0 | 2 | graveyard 1 · quarantine 1 | exit/time_stop (2) |
| symmetric_breakout_voltarget_control | 0 | 5 | graveyard 1 · quarantine 4 | exit/time_stop (5) |
| symmetric_channel_breakout_dense | 0 | 15 | graveyard 4 · quarantine 11 | exit/time_stop (15) |
| symmetric_channel_breakout_fixedfrac | 0 | 15 | graveyard 4 · quarantine 11 | exit/time_stop (15) |
| symmetric_channel_breakout_voltarget_control | 0 | 5 | graveyard 2 · quarantine 3 | exit/time_stop (5) |
| symmetric_donchian_breakout | 0 | 4 | graveyard 4 | exit/time_stop (4) |
| symmetric_donchian_breakout_fixed_fraction | 0 | 5 | graveyard 1 · quarantine 4 | exit/time_stop (5) |
| symmetric_donchian_horizon | 0 | 2 | graveyard 2 | exit/time_stop (2) |
| symmetric_macross_speed_scan | 0 | 5 | graveyard 3 · quarantine 2 | exit/time_stop (5), implicit ma_cross exit (5) |
| symmetric_macross_stop_geometry | 0 | 5 | graveyard 5 | implicit ma_cross exit (5) |
| symmetric_macross_trend_fixedfrac | 0 | 8 | graveyard 8 | implicit ma_cross exit (8) |
| symmetric_slow_trend_dual_side | 0 | 12 | graveyard 12 | exit/time_stop (12) |
| symmetric_tstat_trend_ff | 0 | 5 | graveyard 5 | exit/time_stop (5) |
| symmetric_tstat_trend_scan | 0 | 2 | graveyard 1 · quarantine 1 | exit/time_stop (2) |
| symmetric_tstat_trend_voltarget | 0 | 5 | graveyard 5 | exit/time_stop (5) |
| t_stat_trend_scan_symmetric | 0 | 5 | graveyard 1 · quarantine 4 | exit/time_stop (5) |
| treasury_flow_reversion_small_target_wide_stop | 0 | 200 | graveyard 200 | exit/time_stop (200) |
| trend_scan_triple_barrier | 0 | 12 | graveyard 12 | exit/time_stop (12) |
| trend_tvalue_stop_geometry | 0 | 5 | graveyard 1 · quarantine 4 | exit/time_stop (5) |
| tstat_trend_both_asymmetric_payoff | 4 | 0 | — | — |
| tstat_trend_both_triple_barrier | 0 | 5 | graveyard 1 · quarantine 4 | exit/time_stop (5) |
| tstat_trend_long_pooled | 240 | 0 | — | — |
| tstat_trend_scan_symmetric | 0 | 15 | graveyard 12 · quarantine 3 | exit/time_stop (15) |
| tstat_trend_scan_symmetric_fixedfrac | 0 | 5 | graveyard 2 · quarantine 3 | exit/time_stop (5) |
| tstat_trend_triple_barrier | 0 | 5 | quarantine 5 | exit/time_stop (5) |
| two_sided_range_breakout | 0 | 6 | graveyard 6 | exit/time_stop (6) |
| uptrend_dip_reversion_extreme_tail | 0 | 400 | graveyard 384 · quarantine 16 | stop/pct_stop (400), exit/time_stop (400) |
| vol_conditioned_symmetric_ma_cross | 0 | 15 | graveyard 10 · quarantine 5 | exit/time_stop (15), implicit ma_cross exit (15) |
| vol_filtered_ma_cross_speed | 0 | 240 | graveyard 239 · quarantine 1 | implicit ma_cross exit (240) |
| vol_gated_symmetric_ma_cross | 0 | 5 | graveyard 4 · quarantine 1 | exit/time_stop (5), implicit ma_cross exit (5) |
| vol_state_conditioned_breakout | 0 | 5 | graveyard 1 · quarantine 4 | exit/time_stop (5) |
| vol_state_conditioned_symmetric_ma_cross | 0 | 15 | graveyard 15 | implicit ma_cross exit (15) |
| vol_state_filtered_breakout | 0 | 400 | graveyard 329 · quarantine 71 | exit/time_stop (400) |
| volregime_trend_fixedfrac_arm | 0 | 5 | graveyard 3 · quarantine 2 | exit/time_stop (5) |
| volregime_trend_voltarget_arm | 0 | 5 | graveyard 4 · quarantine 1 | exit/time_stop (5) |
| zscore_reversion_small_pt_wide_sl | 0 | 4 | graveyard 4 | exit/time_stop (4) |

Firing the re-trial (the Composer re-declaring each family's exit set and registering version-2 specs as D9 re-trials) is Coen-gated and is NOT part of this report. No chain write happened.
