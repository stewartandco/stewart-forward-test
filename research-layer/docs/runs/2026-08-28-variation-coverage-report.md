# Variation coverage map (D12)

READ ONLY: this script never writes to registry_log.jsonl or artifacts/. It writes exactly one new file, this report.

D12 (docs/2026-08-28-market-data-universe-design.md s7b): the declared per-block param grids are the "reasonable" fence -- params snap to grid values at fingerprint time, so an absurd combination cannot exist unless the grid declares it, and widening a grid is a reviewed, declared edit. This map shows what INSIDE the fence has never been asked: tested vs declared-untested grid points per family structure and cell. Steering the proposer toward these gaps is explicitly OUT of this spec's scope -- its own decision later, once this map exists to steer by.

Declared combo counts are the product of grid sizes over the structure's gridded params; params without declared grids are excluded from the declared-combo denominator (they have no fence to measure against). Tested combos are distinct snapped param tuples over ALL params of all blocks, so an off-grid param can still split combos.

Generated 2026-08-28 UTC. 34 structure(s), 2775 registration(s).

## Structure: entry/channel_breakout + exit/time_stop + filter/vol_percentile + regime/regime_ma + risk/vol_target + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 6 | 6 | 1296 | 0.5% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/channel_breakout.direction: tested = long; untested = both
- entry/channel_breakout.lookback: tested = 20, 55, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- filter/vol_percentile.lookback: tested = 180; untested = 90
- filter/vol_percentile.max_pctile: tested = 0.8, 1.0; untested = 0.9
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 3.0; untested = 1.5, 2.0

## Structure: entry/channel_breakout + exit/time_stop + filter/vol_percentile + risk/vol_target + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 6 | 6 | 648 | 0.9% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/channel_breakout.direction: tested = both; untested = long
- entry/channel_breakout.lookback: tested = 20, 55, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- filter/vol_percentile.lookback: tested = 90; untested = 180
- filter/vol_percentile.max_pctile: tested = 0.8, 1.0; untested = 0.9
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 3.0; untested = 1.5, 2.0

## Structure: entry/channel_breakout + exit/time_stop + risk/fixed_fraction + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 4 | 4 | 108 | 3.7% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/channel_breakout.direction: tested = both; untested = long
- entry/channel_breakout.lookback: tested = 55, 100; untested = 20
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0, 3.0; untested = 1.5

## Structure: entry/channel_breakout + exit/time_stop + risk/vol_target + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 4 | 4 | 108 | 3.7% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/channel_breakout.direction: tested = both; untested = long
- entry/channel_breakout.lookback: tested = 55, 100; untested = 20
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2, 0.4; untested = (none)
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0

## Structure: entry/channel_breakout_dense + exit/time_stop + filter/vol_percentile + risk/fixed_fraction + stop/atr_stop_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 15 | 15 | 1800 | 0.8% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/channel_breakout_dense.direction: tested = both; untested = long
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- filter/vol_percentile.lookback: tested = 180; untested = 90
- filter/vol_percentile.max_pctile: tested = 0.9; untested = 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.0, 2.5, 3.0; untested = 1.5, 3.5

## Structure: entry/channel_breakout_dense + exit/time_stop + regime/regime_ma + risk/vol_target + stop/atr_stop_dense + target/r_multiple

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| DIA_1d | 25 | 25 | 2400 | 1.0% |
| EEM_1d | 25 | 25 | 2400 | 1.0% |
| EFA_1d | 25 | 25 | 2400 | 1.0% |
| EWA_1d | 25 | 25 | 2400 | 1.0% |
| EWC_1d | 25 | 25 | 2400 | 1.0% |
| EWG_1d | 25 | 25 | 2400 | 1.0% |
| EWH_1d | 25 | 25 | 2400 | 1.0% |
| EWJ_1d | 25 | 25 | 2400 | 1.0% |
| EWU_1d | 25 | 25 | 2400 | 1.0% |
| EWY_1d | 25 | 25 | 2400 | 1.0% |
| EWZ_1d | 25 | 25 | 2400 | 1.0% |
| FXI_1d | 25 | 25 | 2400 | 1.0% |
| IWM_1d | 25 | 25 | 2400 | 1.0% |
| MDY_1d | 25 | 25 | 2400 | 1.0% |
| QQQ_1d | 25 | 25 | 2400 | 1.0% |
| SPY_1d | 25 | 25 | 2400 | 1.0% |

Per-param coverage (cell DIA_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EEM_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EFA_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWA_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWC_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWG_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWH_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWJ_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWU_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWY_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell EWZ_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell FXI_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell IWM_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell MDY_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell QQQ_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

Per-param coverage (cell SPY_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

## Structure: entry/channel_breakout_dense + filter/vol_percentile_dense + risk/fixed_fraction + stop/atr_stop_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 5 | 5 | 2000 | 0.2% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/channel_breakout_dense.direction: tested = both; untested = long
- entry/channel_breakout_dense.lookback: tested = 55; untested = 20, 35, 75, 100
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

## Structure: entry/channel_breakout_dense + filter/vol_percentile_dense + risk/vol_target + stop/atr_stop_dense + target/r_multiple_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BND_1d | 25 | 25 | 10000 | 0.2% |
| EMB_1d | 25 | 25 | 10000 | 0.2% |
| HYG_1d | 25 | 25 | 10000 | 0.2% |
| IEF_1d | 25 | 25 | 10000 | 0.2% |
| LQD_1d | 25 | 25 | 10000 | 0.2% |
| SHY_1d | 25 | 25 | 10000 | 0.2% |
| TIP_1d | 25 | 25 | 10000 | 0.2% |
| TLT_1d | 25 | 25 | 10000 | 0.2% |

Per-param coverage (cell BND_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

Per-param coverage (cell EMB_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

Per-param coverage (cell HYG_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

Per-param coverage (cell IEF_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

Per-param coverage (cell LQD_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

Per-param coverage (cell SHY_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

Per-param coverage (cell TIP_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

Per-param coverage (cell TLT_1d):
- entry/channel_breakout_dense.direction: tested = long; untested = both
- entry/channel_breakout_dense.lookback: tested = 20, 35, 55, 75, 100; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

## Structure: entry/ma_cross_dense + exit/time_stop + regime/regime_ma_short_dense + risk/fixed_fraction + stop/atr_stop + target/r_multiple

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 15 | 15 | 21600 | 0.1% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/ma_cross_dense.direction: tested = short; untested = long, both
- entry/ma_cross_dense.fast: tested = 8, 13, 20; untested = 5, 34
- entry/ma_cross_dense.slow: tested = 50; untested = 80, 130, 200
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0
- target/r_multiple.r: tested = 2.0; untested = 1.0, 1.5, 3.0

## Structure: entry/ma_cross_dense + exit/time_stop + risk/fixed_fraction + stop/atr_stop_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 5 | 5 | 1800 | 0.3% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/ma_cross_dense.direction: tested = short; untested = long, both
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 50; untested = 80, 130, 200
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

## Structure: entry/ma_cross_dense + filter/vol_percentile_dense + risk/vol_target + stop/atr_stop_dense + target/r_multiple_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BND_1d | 20 | 20 | 60000 | 0.0% |
| DIA_1d | 15 | 15 | 60000 | 0.0% |
| EEM_1d | 15 | 15 | 60000 | 0.0% |
| EFA_1d | 15 | 15 | 60000 | 0.0% |
| EMB_1d | 20 | 20 | 60000 | 0.0% |
| EWA_1d | 15 | 15 | 60000 | 0.0% |
| EWC_1d | 15 | 15 | 60000 | 0.0% |
| EWG_1d | 15 | 15 | 60000 | 0.0% |
| EWH_1d | 15 | 15 | 60000 | 0.0% |
| EWJ_1d | 15 | 15 | 60000 | 0.0% |
| EWU_1d | 15 | 15 | 60000 | 0.0% |
| EWY_1d | 15 | 15 | 60000 | 0.0% |
| EWZ_1d | 15 | 15 | 60000 | 0.0% |
| FXI_1d | 15 | 15 | 60000 | 0.0% |
| HYG_1d | 20 | 20 | 60000 | 0.0% |
| IEF_1d | 20 | 20 | 60000 | 0.0% |
| IWM_1d | 15 | 15 | 60000 | 0.0% |
| LQD_1d | 20 | 20 | 60000 | 0.0% |
| MDY_1d | 15 | 15 | 60000 | 0.0% |
| QQQ_1d | 15 | 15 | 60000 | 0.0% |
| SHY_1d | 20 | 20 | 60000 | 0.0% |
| SPY_1d | 15 | 15 | 60000 | 0.0% |
| TIP_1d | 20 | 20 | 60000 | 0.0% |
| TLT_1d | 20 | 20 | 60000 | 0.0% |

Per-param coverage (cell BND_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell DIA_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EEM_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EFA_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EMB_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWA_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWC_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWG_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWH_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWJ_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWU_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWY_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWZ_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell FXI_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell HYG_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell IEF_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell IWM_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell LQD_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell MDY_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell QQQ_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell SHY_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell SPY_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.7, 0.8, 0.9; untested = 0.6, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell TIP_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell TLT_1d):
- entry/ma_cross_dense.direction: tested = long; untested = short, both
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 50, 80, 130, 200; untested = (none)
- filter/vol_percentile_dense.lookback: tested = 180; untested = 90, 120, 150
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

## Structure: entry/ma_cross_dense + filter/vol_percentile_dense + risk/vol_target + stop/pct_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| AUD_1d | 5 | 5 | 7200 | 0.1% |
| CAD_1d | 5 | 5 | 7200 | 0.1% |
| CHF_1d | 5 | 5 | 7200 | 0.1% |
| EUR_1d | 5 | 5 | 7200 | 0.1% |
| GBP_1d | 5 | 5 | 7200 | 0.1% |
| JPY_1d | 5 | 5 | 7200 | 0.1% |
| MXN_1d | 5 | 5 | 7200 | 0.1% |
| NOK_1d | 5 | 5 | 7200 | 0.1% |
| NZD_1d | 5 | 5 | 7200 | 0.1% |
| SEK_1d | 5 | 5 | 7200 | 0.1% |
| SGD_1d | 5 | 5 | 7200 | 0.1% |
| ZAR_1d | 5 | 5 | 7200 | 0.1% |

Per-param coverage (cell AUD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CAD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CHF_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell EUR_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell GBP_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell JPY_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell MXN_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NOK_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NZD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SEK_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SGD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell ZAR_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

## Structure: entry/ma_cross_dense + risk/fixed_fraction + stop/atr_stop_dense + target/r_multiple_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 5 | 5 | 3000 | 0.2% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 13; untested = 5, 8, 20, 34
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)
- target/r_multiple_dense.r: tested = 2.0; untested = 1.0, 1.5, 2.5, 3.0

## Structure: entry/ma_cross_dense + risk/vol_target + stop/pct_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| AUD_1d | 5 | 5 | 360 | 1.4% |
| CAD_1d | 5 | 5 | 360 | 1.4% |
| CHF_1d | 5 | 5 | 360 | 1.4% |
| EUR_1d | 5 | 5 | 360 | 1.4% |
| GBP_1d | 5 | 5 | 360 | 1.4% |
| JPY_1d | 5 | 5 | 360 | 1.4% |
| MXN_1d | 5 | 5 | 360 | 1.4% |
| NOK_1d | 5 | 5 | 360 | 1.4% |
| NZD_1d | 5 | 5 | 360 | 1.4% |
| SEK_1d | 5 | 5 | 360 | 1.4% |
| SGD_1d | 5 | 5 | 360 | 1.4% |
| ZAR_1d | 5 | 5 | 360 | 1.4% |

Per-param coverage (cell AUD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CAD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CHF_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell EUR_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell GBP_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell JPY_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell MXN_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NOK_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NZD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SEK_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SGD_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell ZAR_1d):
- entry/ma_cross_dense.direction: tested = both; untested = long, short
- entry/ma_cross_dense.fast: tested = 5, 8, 13, 20, 34; untested = (none)
- entry/ma_cross_dense.slow: tested = 130; untested = 50, 80, 200
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

## Structure: entry/ma_cross_ds + exit/time_stop + regime/regime_ma_short + risk/fixed_fraction + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 4 | 4 | 972 | 0.4% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/ma_cross_ds.direction: tested = short; untested = long, both
- entry/ma_cross_ds.fast: tested = 5, 10; untested = 20
- entry/ma_cross_ds.slow: tested = 50; untested = 100, 200
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short.ma_len: tested = 100, 200; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0

## Structure: entry/ma_cross_ds + exit/time_stop + regime/regime_ma_short + risk/vol_target + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 8 | 8 | 972 | 0.8% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/ma_cross_ds.direction: tested = short; untested = long, both
- entry/ma_cross_ds.fast: tested = 20; untested = 5, 10
- entry/ma_cross_ds.slow: tested = 100, 200; untested = 50
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short.ma_len: tested = 100, 200; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0, 3.0; untested = 1.5

## Structure: entry/ma_cross_ds + filter/vol_percentile + risk/fixed_fraction + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 8 | 8 | 972 | 0.8% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/ma_cross_ds.direction: tested = both; untested = long, short
- entry/ma_cross_ds.fast: tested = 10, 20; untested = 5
- entry/ma_cross_ds.slow: tested = 100, 200; untested = 50
- filter/vol_percentile.lookback: tested = 180; untested = 90
- filter/vol_percentile.max_pctile: tested = 0.9; untested = 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.01, 0.02; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0

## Structure: entry/trend_scan + exit/time_stop + regime/regime_ma + risk/vol_target + stop/atr_stop + target/r_multiple

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 12 | 12 | 864 | 1.4% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan.max_lookback: tested = 60, 90, 120; untested = (none)
- entry/trend_scan.t_min: tested = 2.0, 3.0; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0
- target/r_multiple.r: tested = 2.0, 3.0; untested = 1.0, 1.5

## Structure: entry/trend_scan_dense + exit/time_stop + filter/vol_percentile_dense + risk/fixed_fraction + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 5 | 5 | 16200 | 0.0% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 90; untested = 60, 75, 105, 120
- entry/trend_scan_dense.t_min: tested = 2.0; untested = 2.5, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0

## Structure: entry/trend_scan_dense + exit/time_stop + filter/vol_percentile_dense + risk/vol_target + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 5 | 5 | 16200 | 0.0% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 90; untested = 60, 75, 105, 120
- entry/trend_scan_dense.t_min: tested = 2.0; untested = 2.5, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.6, 0.7, 0.8, 0.9, 1.0; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0

## Structure: entry/trend_scan_dense + exit/time_stop + regime/regime_ma_short_dense + risk/fixed_fraction + stop/atr_stop_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BND_1d | 25 | 25 | 6750 | 0.4% |
| BTCUSD+ETHUSD_1d | 5 | 5 | 6750 | 0.1% |
| EMB_1d | 25 | 25 | 6750 | 0.4% |
| HYG_1d | 25 | 25 | 6750 | 0.4% |
| IEF_1d | 25 | 25 | 6750 | 0.4% |
| LQD_1d | 25 | 25 | 6750 | 0.4% |
| SHY_1d | 25 | 25 | 6750 | 0.4% |
| TIP_1d | 25 | 25 | 6750 | 0.4% |
| TLT_1d | 25 | 25 | 6750 | 0.4% |

Per-param coverage (cell BND_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75; untested = 60, 90, 105, 120
- entry/trend_scan_dense.t_min: tested = 2.0; untested = 2.5, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell EMB_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell HYG_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell IEF_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell LQD_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell SHY_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell TIP_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell TLT_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

## Structure: entry/trend_scan_dense + exit/time_stop + regime/regime_ma_short_dense + risk/vol_target + stop/atr_stop_dense + target/r_multiple

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| DIA_1d | 15 | 15 | 27000 | 0.1% |
| EEM_1d | 15 | 15 | 27000 | 0.1% |
| EFA_1d | 15 | 15 | 27000 | 0.1% |
| EWA_1d | 15 | 15 | 27000 | 0.1% |
| EWC_1d | 15 | 15 | 27000 | 0.1% |
| EWG_1d | 15 | 15 | 27000 | 0.1% |
| EWH_1d | 15 | 15 | 27000 | 0.1% |
| EWJ_1d | 15 | 15 | 27000 | 0.1% |
| EWU_1d | 15 | 15 | 27000 | 0.1% |
| EWY_1d | 15 | 15 | 27000 | 0.1% |
| EWZ_1d | 15 | 15 | 27000 | 0.1% |
| FXI_1d | 15 | 15 | 27000 | 0.1% |
| IWM_1d | 15 | 15 | 27000 | 0.1% |
| MDY_1d | 15 | 15 | 27000 | 0.1% |
| QQQ_1d | 15 | 15 | 27000 | 0.1% |
| SPY_1d | 15 | 15 | 27000 | 0.1% |

Per-param coverage (cell DIA_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EEM_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EFA_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWA_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWC_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWG_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWH_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWJ_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWU_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWY_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell EWZ_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell FXI_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell IWM_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell MDY_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell QQQ_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

Per-param coverage (cell SPY_1d):
- entry/trend_scan_dense.direction: tested = short; untested = long, both
- entry/trend_scan_dense.max_lookback: tested = 75, 90, 105; untested = 60, 120
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5
- target/r_multiple.r: tested = 1.5; untested = 1.0, 2.0, 3.0

## Structure: entry/trend_scan_dense + exit/time_stop + risk/fixed_fraction + stop/atr_stop_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 10 | 10 | 1350 | 0.7% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5; untested = 3.0
- exit/time_stop.max_bars: tested = 20, 40; untested = 10
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

## Structure: entry/trend_scan_dense + exit/time_stop + risk/vol_target + stop/atr_stop_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BND_1d | 25 | 25 | 1350 | 1.9% |
| BTCUSD+ETHUSD_1d | 5 | 5 | 1350 | 0.4% |
| EMB_1d | 25 | 25 | 1350 | 1.9% |
| HYG_1d | 25 | 25 | 1350 | 1.9% |
| IEF_1d | 25 | 25 | 1350 | 1.9% |
| LQD_1d | 25 | 25 | 1350 | 1.9% |
| SHY_1d | 25 | 25 | 1350 | 1.9% |
| TIP_1d | 25 | 25 | 1350 | 1.9% |
| TLT_1d | 25 | 25 | 1350 | 1.9% |

Per-param coverage (cell BND_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 2.5; untested = 1.5, 2.0, 3.0, 3.5

Per-param coverage (cell EMB_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

Per-param coverage (cell HYG_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

Per-param coverage (cell IEF_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

Per-param coverage (cell LQD_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

Per-param coverage (cell SHY_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

Per-param coverage (cell TIP_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

Per-param coverage (cell TLT_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 1.5, 2.0, 2.5, 3.0, 3.5; untested = (none)

## Structure: entry/trend_scan_dense + exit/time_stop + risk/vol_target + stop/pct_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| AUD_1d | 5 | 5 | 810 | 0.6% |
| CAD_1d | 5 | 5 | 810 | 0.6% |
| CHF_1d | 5 | 5 | 810 | 0.6% |
| EUR_1d | 5 | 5 | 810 | 0.6% |
| GBP_1d | 5 | 5 | 810 | 0.6% |
| JPY_1d | 5 | 5 | 810 | 0.6% |
| MXN_1d | 5 | 5 | 810 | 0.6% |
| NOK_1d | 5 | 5 | 810 | 0.6% |
| NZD_1d | 5 | 5 | 810 | 0.6% |
| SEK_1d | 5 | 5 | 810 | 0.6% |
| SGD_1d | 5 | 5 | 810 | 0.6% |
| ZAR_1d | 5 | 5 | 810 | 0.6% |

Per-param coverage (cell AUD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CAD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CHF_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell EUR_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell GBP_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell JPY_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell MXN_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NOK_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NZD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SEK_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SGD_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell ZAR_1d):
- entry/trend_scan_dense.direction: tested = both; untested = long, short
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.5; untested = 2.0, 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

## Structure: entry/trend_scan_dense + regime/regime_ma + risk/vol_target + stop/atr_stop_dense + target/r_multiple_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| DIA_1d | 15 | 15 | 4500 | 0.3% |
| EEM_1d | 15 | 15 | 4500 | 0.3% |
| EFA_1d | 15 | 15 | 4500 | 0.3% |
| EWA_1d | 15 | 15 | 4500 | 0.3% |
| EWC_1d | 15 | 15 | 4500 | 0.3% |
| EWG_1d | 15 | 15 | 4500 | 0.3% |
| EWH_1d | 15 | 15 | 4500 | 0.3% |
| EWJ_1d | 15 | 15 | 4500 | 0.3% |
| EWU_1d | 15 | 15 | 4500 | 0.3% |
| EWY_1d | 15 | 15 | 4500 | 0.3% |
| EWZ_1d | 15 | 15 | 4500 | 0.3% |
| FXI_1d | 15 | 15 | 4500 | 0.3% |
| IWM_1d | 15 | 15 | 4500 | 0.3% |
| MDY_1d | 15 | 15 | 4500 | 0.3% |
| QQQ_1d | 15 | 15 | 4500 | 0.3% |
| SPY_1d | 15 | 15 | 4500 | 0.3% |

Per-param coverage (cell DIA_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EEM_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EFA_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWA_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWC_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWG_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWH_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWJ_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWU_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWY_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell EWZ_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell FXI_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell IWM_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell MDY_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell QQQ_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

Per-param coverage (cell SPY_1d):
- entry/trend_scan_dense.direction: tested = long; untested = short, both
- entry/trend_scan_dense.max_lookback: tested = 60, 75, 90, 105, 120; untested = (none)
- entry/trend_scan_dense.t_min: tested = 2.0, 2.5, 3.0; untested = (none)
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.0; untested = 1.5, 2.0, 2.5, 3.5
- target/r_multiple_dense.r: tested = 3.0; untested = 1.0, 1.5, 2.0, 2.5

## Structure: entry/trend_scan_ds + exit/time_stop + regime/regime_ma_short_dense + risk/vol_target + stop/pct_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| AUD_1d | 5 | 5 | 1620 | 0.3% |
| CAD_1d | 5 | 5 | 1620 | 0.3% |
| CHF_1d | 5 | 5 | 1620 | 0.3% |
| EUR_1d | 5 | 5 | 1620 | 0.3% |
| GBP_1d | 5 | 5 | 1620 | 0.3% |
| JPY_1d | 5 | 5 | 1620 | 0.3% |
| MXN_1d | 5 | 5 | 1620 | 0.3% |
| NOK_1d | 5 | 5 | 1620 | 0.3% |
| NZD_1d | 5 | 5 | 1620 | 0.3% |
| SEK_1d | 5 | 5 | 1620 | 0.3% |
| SGD_1d | 5 | 5 | 1620 | 0.3% |
| ZAR_1d | 5 | 5 | 1620 | 0.3% |

Per-param coverage (cell AUD_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CAD_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell CHF_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell EUR_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell GBP_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell JPY_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell MXN_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NOK_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell NZD_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SEK_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell SGD_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

Per-param coverage (cell ZAR_1d):
- entry/trend_scan_ds.direction: tested = short; untested = long, both
- entry/trend_scan_ds.max_lookback: tested = 90; untested = 60, 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- regime/regime_ma_short_dense.ma_len: tested = 50, 100, 150, 200, 250; untested = (none)
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.1; untested = 0.05, 0.15

## Structure: entry/trend_scan_ds + exit/time_stop + risk/vol_target + stop/atr_stop

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 12 | 12 | 324 | 3.7% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan_ds.direction: tested = both; untested = long, short
- entry/trend_scan_ds.max_lookback: tested = 60, 90, 120; untested = (none)
- entry/trend_scan_ds.t_min: tested = 2.0, 3.0; untested = (none)
- exit/time_stop.max_bars: tested = 40; untested = 10, 20
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0, 3.0; untested = 1.5

## Structure: entry/trend_scan_ds + filter/vol_percentile + risk/fixed_fraction + stop/atr_stop + target/r_multiple

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 4 | 4 | 2592 | 0.2% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/trend_scan_ds.direction: tested = both; untested = long, short
- entry/trend_scan_ds.max_lookback: tested = 60, 90; untested = 120
- entry/trend_scan_ds.t_min: tested = 2.0; untested = 3.0
- filter/vol_percentile.lookback: tested = 90; untested = 180
- filter/vol_percentile.max_pctile: tested = 0.9; untested = 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0; untested = 1.5, 3.0
- target/r_multiple.r: tested = 2.0, 3.0; untested = 1.0, 1.5

## Structure: entry/zscore_reversion + exit/time_stop + filter/vol_percentile + risk/fixed_fraction + stop/atr_stop + target/r_multiple

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 4 | 4 | 7776 | 0.1% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/zscore_reversion.direction: tested = long; untested = both
- entry/zscore_reversion.lookback: tested = 20; untested = 60, 90
- entry/zscore_reversion.z_entry: tested = 2.0; untested = 1.5, 2.5
- exit/time_stop.max_bars: tested = 10; untested = 20, 40
- filter/vol_percentile.lookback: tested = 90; untested = 180
- filter/vol_percentile.max_pctile: tested = 0.8; untested = 0.9, 1.0
- risk/fixed_fraction.f: tested = 0.01; untested = 0.02
- stop/atr_stop.atr_len: tested = 14; untested = (none)
- stop/atr_stop.mult: tested = 2.0, 3.0; untested = 1.5
- target/r_multiple.r: tested = 1.0, 1.5; untested = 2.0, 3.0

## Structure: entry/zscore_reversion + exit/time_stop + risk/vol_target + stop/pct_stop + target/r_multiple

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BTCUSD+ETHUSD_1d | 8 | 8 | 1296 | 0.6% |

Per-param coverage (cell BTCUSD+ETHUSD_1d):
- entry/zscore_reversion.direction: tested = both; untested = long
- entry/zscore_reversion.lookback: tested = 20, 60; untested = 90
- entry/zscore_reversion.z_entry: tested = 2.0, 2.5; untested = 1.5
- exit/time_stop.max_bars: tested = 10; untested = 20, 40
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple.r: tested = 1.0, 1.5; untested = 2.0, 3.0

## Structure: entry/zscore_reversion_dense + exit/time_stop + filter/vol_percentile_dense + risk/fixed_fraction + stop/atr_stop_dense + target/r_multiple_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| BND_1d | 25 | 25 | 150000 | 0.0% |
| EMB_1d | 25 | 25 | 150000 | 0.0% |
| HYG_1d | 25 | 25 | 150000 | 0.0% |
| IEF_1d | 25 | 25 | 150000 | 0.0% |
| LQD_1d | 25 | 25 | 150000 | 0.0% |
| SHY_1d | 25 | 25 | 150000 | 0.0% |
| TIP_1d | 25 | 25 | 150000 | 0.0% |
| TLT_1d | 25 | 25 | 150000 | 0.0% |

Per-param coverage (cell BND_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell EMB_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell HYG_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell IEF_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell LQD_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell SHY_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell TIP_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell TLT_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 20, 40, 60, 75, 90; untested = (none)
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- filter/vol_percentile_dense.lookback: tested = 120; untested = 90, 150, 180
- filter/vol_percentile_dense.max_pctile: tested = 0.9; untested = 0.6, 0.7, 0.8, 1.0
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

## Structure: entry/zscore_reversion_dense + exit/time_stop + regime/regime_ma + risk/vol_target + stop/atr_stop_dense + target/r_multiple_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| DIA_1d | 15 | 15 | 15000 | 0.1% |
| EEM_1d | 15 | 15 | 15000 | 0.1% |
| EFA_1d | 15 | 15 | 15000 | 0.1% |
| EWA_1d | 15 | 15 | 15000 | 0.1% |
| EWC_1d | 15 | 15 | 15000 | 0.1% |
| EWG_1d | 15 | 15 | 15000 | 0.1% |
| EWH_1d | 15 | 15 | 15000 | 0.1% |
| EWJ_1d | 15 | 15 | 15000 | 0.1% |
| EWU_1d | 15 | 15 | 15000 | 0.1% |
| EWY_1d | 15 | 15 | 15000 | 0.1% |
| EWZ_1d | 15 | 15 | 15000 | 0.1% |
| FXI_1d | 15 | 15 | 15000 | 0.1% |
| IWM_1d | 15 | 15 | 15000 | 0.1% |
| MDY_1d | 15 | 15 | 15000 | 0.1% |
| QQQ_1d | 15 | 15 | 15000 | 0.1% |
| SPY_1d | 15 | 15 | 15000 | 0.1% |

Per-param coverage (cell DIA_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EEM_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EFA_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWA_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWC_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWG_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWH_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWJ_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWU_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWY_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell EWZ_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell FXI_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell IWM_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell MDY_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell QQQ_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

Per-param coverage (cell SPY_1d):
- entry/zscore_reversion_dense.direction: tested = long; untested = both
- entry/zscore_reversion_dense.lookback: tested = 40; untested = 20, 60, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- regime/regime_ma.ma_len: tested = 200; untested = 100
- risk/vol_target.ann_vol: tested = 0.2; untested = 0.4
- risk/vol_target.lookback: tested = 30; untested = (none)
- stop/atr_stop_dense.atr_len: tested = 14; untested = (none)
- stop/atr_stop_dense.mult: tested = 3.5; untested = 1.5, 2.0, 2.5, 3.0
- target/r_multiple_dense.r: tested = 1.0, 1.5, 2.0; untested = 2.5, 3.0

## Structure: entry/zscore_reversion_dense + exit/time_stop + risk/fixed_fraction + stop/pct_stop + target/r_multiple_dense

| cell | registrations | tested combos | declared combos | coverage % |
|---|---|---|---|---|
| AUD_1d | 5 | 5 | 4500 | 0.1% |
| CAD_1d | 5 | 5 | 4500 | 0.1% |
| CHF_1d | 5 | 5 | 4500 | 0.1% |
| EUR_1d | 5 | 5 | 4500 | 0.1% |
| GBP_1d | 5 | 5 | 4500 | 0.1% |
| JPY_1d | 5 | 5 | 4500 | 0.1% |
| MXN_1d | 5 | 5 | 4500 | 0.1% |
| NOK_1d | 5 | 5 | 4500 | 0.1% |
| NZD_1d | 5 | 5 | 4500 | 0.1% |
| SEK_1d | 5 | 5 | 4500 | 0.1% |
| SGD_1d | 5 | 5 | 4500 | 0.1% |
| ZAR_1d | 5 | 5 | 4500 | 0.1% |

Per-param coverage (cell AUD_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell CAD_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell CHF_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell EUR_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell GBP_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell JPY_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell MXN_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell NOK_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell NZD_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell SEK_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell SGD_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

Per-param coverage (cell ZAR_1d):
- entry/zscore_reversion_dense.direction: tested = both; untested = long
- entry/zscore_reversion_dense.lookback: tested = 60; untested = 20, 40, 75, 90
- entry/zscore_reversion_dense.z_entry: tested = 1.5, 1.75, 2.0, 2.25, 2.5; untested = (none)
- exit/time_stop.max_bars: tested = 20; untested = 10, 40
- risk/fixed_fraction.f: tested = 0.02; untested = 0.01
- stop/pct_stop.pct: tested = 0.15; untested = 0.05, 0.1
- target/r_multiple_dense.r: tested = 1.0; untested = 1.5, 2.0, 2.5, 3.0

## Global summary

- structures seen: 34
- total registrations: 2775
- declared combo points (summed over structures): 373368
- tested combo points (distinct snapped combos across all cells, summed over structures): 385 (0.1%)
- params without declared grids are excluded from the declared-combo denominator.

Top-5 most-covered structures (distinct combos across all cells vs declared):
- entry/trend_scan_ds + exit/time_stop + risk/vol_target + stop/atr_stop: 12 / 324 (3.7%)
- entry/channel_breakout + exit/time_stop + risk/vol_target + stop/atr_stop: 4 / 108 (3.7%)
- entry/channel_breakout + exit/time_stop + risk/fixed_fraction + stop/atr_stop: 4 / 108 (3.7%)
- entry/trend_scan_dense + exit/time_stop + risk/vol_target + stop/atr_stop_dense: 30 / 1350 (2.2%)
- entry/trend_scan + exit/time_stop + regime/regime_ma + risk/vol_target + stop/atr_stop + target/r_multiple: 12 / 864 (1.4%)

Top-5 least-covered structures:
- entry/zscore_reversion_dense + exit/time_stop + filter/vol_percentile_dense + risk/fixed_fraction + stop/atr_stop_dense + target/r_multiple_dense: 25 / 150000 (0.0%)
- entry/trend_scan_dense + exit/time_stop + filter/vol_percentile_dense + risk/fixed_fraction + stop/atr_stop: 5 / 16200 (0.0%)
- entry/trend_scan_dense + exit/time_stop + filter/vol_percentile_dense + risk/vol_target + stop/atr_stop: 5 / 16200 (0.0%)
- entry/zscore_reversion + exit/time_stop + filter/vol_percentile + risk/fixed_fraction + stop/atr_stop + target/r_multiple: 4 / 7776 (0.1%)
- entry/trend_scan_dense + exit/time_stop + regime/regime_ma_short_dense + risk/vol_target + stop/atr_stop_dense + target/r_multiple: 15 / 27000 (0.1%)

RECORDED, NOT GATED: nothing here changes any strategy's state or steers any proposer run. This is a map, not chain data.
