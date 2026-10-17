crypto-grid-record-v1: The record of which crypto cells are searched, fixed before any result from them exists. A cell is one asset at one timeframe. This note fixes the whole grid of crypto cells now, in three phases, so that nothing is added to it after results begin to arrive. It changes no existing verdict, moves no strategy, and re-judges nothing on the chain. Until this entry the crypto class was searched as one pooled two-asset book, BTCUSD and ETHUSD together at 1d, one spec per sweep combination. From this entry crypto is searched cell by cell, one single-asset spec per sweep combination, per active cell, as every other asset class already is.

THE ADMISSION LINE. A crypto asset is admitted only if its first daily bar falls on or before 2022-12-31, which is at least 365 days of history before the 2023-12-31 screen cutoff. The line was set by Coen on 2026-10-17, before any result from these cells exists, and applies to every asset alike. 76 declared assets meet it. 24 do not, and none of the 24 is a cell in any phase.

THE GRID.

Phase 1 cells (active from this entry): timeframe 1d on assets: BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT, SOLUSDT, TRXUSDT, ZECUSDT, DOGEUSDT, LINKUSDT, ADAUSDT, XLMUSDT, BCHUSDT, LTCUSDT, HBARUSDT, AVAXUSDT, SHIBUSDT, UNIUSDT, NEARUSDT, PAXGUSDT, AAVEUSDT, DOTUSDT, ICPUSDT, ETCUSDT, QNTUSDT, NEXOUSDT, ALGOUSDT, ATOMUSDT, JSTUSDT, VETUSDT, FILUSDT, CAKEUSDT, INJUSDT, STXUSDT, DASHUSDT, CRVUSDT, APTUSDT, FETUSDT, SUNUSDT, GNOUSDT, LDOUSDT, LUNCUSDT, XTZUSDT, ENSUSDT, CFXUSDT, DCRUSDT, JASMYUSDT, RAYUSDT, CVXUSDT, OPUSDT, IOTAUSDT, COMPUSDT, TWTUSDT, GRTUSDT, THETAUSDT, RUNEUSDT, AXSUSDT, NEOUSDT, CHZUSDT, MANAUSDT, APEUSDT, XECUSDT, ARUSDT, SFPUSDT, SNXUSDT, 1INCHUSDT, SANDUSDT, IMXUSDT, EGLDUSDT, BATUSDT, DYDXUSDT, ZENUSDT, RSRUSDT, GALAUSDT, QTUMUSDT, DGBUSDT, YFIUSDT

Phase 2 cells (inactive until their own Coen-gated commit): timeframes 1h, 4h, 12h on the same assets

Phase 3 cells (inactive until their own Coen-gated commit): timeframes 15m, 30m on the same assets

The assets are listed in manifest order, and the same 76 are used in every phase. Phase 1 is active from the registry entry of this note. Phase 2 and phase 3 are not active at this entry: each is activated only by its own commit, which Coen decides. The grid is 76 assets by 6 timeframes, 456 cells in all, and 76 of them are active now.

THE EXCLUDED ASSETS. Each is the ticker followed by USDT. The first bar is the date of its first daily bar, first_1d_utc in data/crypto_universe_manifest.json. Ten have a first bar in 2024 and fourteen have one between 2023-03-17 and 2023-12-15. All 24 fall after 2022-12-31.

Excluded assets (24, not swept): ENA first bar 2024-04-02, ETHFI first bar 2024-03-18, JUP first bar 2024-01-31, PYTH first bar 2024-02-02, RENDER first bar 2024-07-26, STRK first bar 2024-02-20, TAO first bar 2024-04-11, WIF first bar 2024-03-05, ZK first bar 2024-06-17, ZRO first bar 2024-06-20, GAS first bar 2023-03-17, GLM first bar 2023-03-17, PROM first bar 2023-03-17, ARB first bar 2023-03-23, SUI first bar 2023-05-03, FLOKI first bar 2023-05-05, PEPE first bar 2023-05-05, PENDLE first bar 2023-07-03, WLD first bar 2023-07-24, SEI first bar 2023-08-15, TIA first bar 2023-10-31, ORDI first bar 2023-11-07, JTO first bar 2023-12-07, BONK first bar 2023-12-15

WHAT IS FIXED AND WHAT IS OPEN. The set of cells is fixed by this note. Only the timing of phases 2 and 3 is open. Nothing outside this grid can be added: not an asset, not a timeframe, not one of the 24 excluded assets, and not a cell of an asset that listed later. A pair that is delisted or halted is RETIRED by a new note that names it. A retirement never adds a cell. Every strategy registered on the retired pair stays on the chain.

THE POOLED PATH IS RETIRED. From this entry on no new pooled BTCUSD+ETHUSD spec is proposed. Every pooled strategy registered before this entry stays on the chain and re-simulates. Nothing about them is re-judged.

WHAT COUNTS. Each cell is its own counted trial. Activating a phase adds its cells to what is searched. More registrations at a fixed bar is arithmetic about the denominator, never evidence of edge.
