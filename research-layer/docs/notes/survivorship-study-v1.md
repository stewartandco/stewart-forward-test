survivorship-study-v1: pre-registration of the off-chain crypto survivorship study, design doc research-layer/docs/2026-11-06-crypto-survivorship-study-design.md (Coen approved 2026-11-06). It is chained before any study outcome number exists (design s4 step 2) and fixes the decision rule below (design s5, verbatim), the frozen inputs by sha256 (design P5), the pinned registry entry index for the templates, the splice and death list, the P4 cuts, the declared halts and the declared exclusions. It arms nothing in the pipeline: only the study tool's gate (tools/survivorship.py) reads it, refusing every outcome computation until this note is on the chain.

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

**Verdict (exhaustive).** The rows are applied top to bottom; the first that matches is the verdict.

| verdict | condition |
|---|---|
| **MATERIAL** | G >= 0.10 and L > 0 |
| **NOT MATERIAL** | U < 0.10 (flagged **REVERSE** when also U < 0: our universe is HARDER than PIT, not easier) |
| **INCONCLUSIVE** | anything else |

Only the 1d arm decides (SV4). The 4h/12h/1h arms are computed and reported with the same formula and interval, and labelled "secondary, not decision-bearing". No secondary can rescue or overturn the 1d verdict. A precondition failure on an intraday arm stops that arm only.

**Secondaries (reported, never decision-bearing):**
- the screen pass-rate gap;
- the v6.2 gauntlet pass-rate gap over all template-cells;
- the gap in (strategy minus hold) Sharpe;
- the hold-only gap (mean OOS hold Sharpe, A minus B, no strategy at all);
- per-stratum means of m (S, R, F, N, D);
- a per-family table;
- None counts per stratum.

## Frozen inputs (design P5)

Each hash is the sha256 of the UTF-8 canonical JSON json.dumps(obj, sort_keys=True, separators=(",", ":")), except cmc, the sha256 of the raw CMC response bytes. templates = per timeframe, template key -> sorted registered strategy ids; cells_A / cells_B = the ordered pair lists; renames / delisted / cuts / halts / excluded = the declared lists below; first_bars = pair -> first bar date of each 1d cell; data_1d = pair -> sha256 of each 1d study CSV; design_doc_sha256 = the sha256 of the design doc's LF-normalised bytes (the file s5 above is quoted from); study_code = file name -> sha256 of the LF-normalised bytes of every tools/survivorship*.py (the study code and its decision constants). Every result row carries its cell spec's strategy id, its cell's study-CSV sha256 and the sha256 of the canonical JSON of these hashes; analysis refuses a row whose stamp differs.

- cells_A: d2a5c786c3c81f651b812da2da123034a5ac2c53777c31ebc9a5e191043bf5d6
- cells_B: 05a556a52e58f7d2312be0dbe3dacfce61eb50dc525976af2ee596526991e46b
- cmc: 0ef45adade50574307db6213b79f445fee43696ea635b4a8fd9f1cc37cac5d81
- cuts: c68d7f1fbe7a18317bee4464827d5ead9e68080544d2cfa8fb3f279c7880f03d
- data_1d: 5f4811f4e89667c2ecb4a3dd022b93ba29780b7aa676f55776115a01bde2772a
- delisted: 425ed2dbd6efc312c404cacd7390b44b755b1294656eb43ab03c9a6f6bdcb097
- design_doc_sha256: a85a158fa1443b9db21147fadacc02060e59e7f29214bc32b4b0f171cdf0c061
- excluded: 44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a
- first_bars: 65af147ae88c9d4910470b9a0f4f1e4563eccbf2a2d93c16ec70dc467eebf0a2
- halts: 1783a365d0ff916f80d63a14dcacbb2db9e8f66cb187d5db367bb1061e530605
- renames: 3b4fe1b312f84eb17a176b36ecc3865761ff9e7d3e6692c6b620758a7017a8c4
- study_code: 781fe7cbcddbf2e50648fbf25a4dc44387ce6f26e28b7b57582ca7e6997a4569
- templates: 597b13060458b154a472b641d933cd081ae103a5499728c0de679a0d4485e7f4

- upto: 186476 (0-based registry entry index; the templates are the single-asset crypto strategies registered at entries 0..186475)
- templates per timeframe: 12h 20 templates, 240 strategy ids, 1d 80 templates, 960 strategy ids, 1h 20 templates, 240 strategy ids, 4h 20 templates, 240 strategy ids
- cells: A 76, B 100; strata S 66, R 10, F 22, N 7, D 5
- study code: 7 files tools/survivorship*.py, sha256 of each file's LF-normalised bytes (study_code above hashes this map): survivorship.py c8d747ba4e5571b8c40daeca8aae66a4f93e0b7fba299d77832b4b4babf5e303, survivorship_archive.py 47c173a4b50e29a645acab6650f21939d7c1e089488dc1cb265cbb7520f3e60c, survivorship_checks.py 40fcc47ecf6a063dc6aaf1ccc16ea70b17452f4fe37ef04a0601a3ecfbd10088, survivorship_harness.py d9ead01123a9b1e5c2767f1a11f7dc6fd76da8c27ec40b80f12c62bf3936a6ee, survivorship_stats.py 4bc77a0f713b9c77a607863d5ac044e6f5731ccb5179cc57c14494a6e48e5993, survivorship_templates.py 84b7714bc51ee837b2b0f2a31fbc678080d1cd8a63d7c6e60847df3489364909, survivorship_universe.py a766739cb00b3ccdb49ebe2f5b4f76f0f563a49ac383990c6083d2a6165ea1c5
- git HEAD at freeze (informational, not checked): 2f7a936ed7950e3b283a00a04519c1731d14249e

## Splices (renamed pairs, stratum N)

- EOSUSDT -> AUSDT: swap date 2025-05-28, ratio 1.0 successor units per predecessor unit, https://www.binance.com/en/support/announcement/1e89a9ca957c4b0ca7502e60b993e201
- FTMUSDT -> SUSDT: swap date 2025-01-16, ratio 1.0 successor units per predecessor unit, https://www.binance.com/en/support/announcement/aec6fcbc84b749eeab6690e6bcac2f3d
- FXSUSDT -> FRAXUSDT: swap date 2026-01-15, ratio 1.0 successor units per predecessor unit, https://www.binance.com/en/support/announcement/detail/a17352ba70414c96882cd881591dd36d
- KLAYUSDT -> KAIAUSDT: swap date 2024-10-31, ratio 1.0 successor units per predecessor unit, https://www.binance.com/en/support/articles/f75f933759ee49d0af1dfbce7e32144c
- MATICUSDT -> POLUSDT: swap date 2024-09-13, ratio 1.0 successor units per predecessor unit, https://www.binance.com/en/support/articles/6a6de383727f4659a3050f7982e1620f
- MKRUSDT -> SKYUSDT: swap date 2025-09-17, ratio 24000.0 successor units per predecessor unit, https://www.binance.com/en/support/announcement/detail/bf29506a33a94c17a5d5a01e74e646d2
- RNDRUSDT -> RENDERUSDT: swap date 2024-07-26, ratio 1.0 successor units per predecessor unit, https://www.binance.com/en/support/announcement/d1f2ae8d99b24439a7a900caa9bb6b3b

## Deaths (delisted pairs, stratum D)

- ELFUSDT: https://www.binance.com/en/support/articles/fac9c3e401da4cc8b604566fd261d70c
- KDAUSDT: https://www.binance.com/en/support/announcement/detail/a6c810fc36424548917e146dbe21469a
- LRCUSDT: https://www.binance.com/en/support/announcement/detail/ddac99c0e5044c13a13c7c113e530e2b
- XEMUSDT: https://www.binance.com/en/support/announcement/binance-will-delist-omg-waves-wnxm-xem-on-2024-06-17-f65faeeefe07417a8b6dd2900ba8da7e
- XMRUSDT: https://www.binance.com/en/support/announcement/f73b083ba6834771b07dbe5319917ae5

## P4 cuts, halts and exclusions

- LUNAUSDT: cut to first usable date 2022-05-31
- declared halt FTTUSDT: last bar 2022-11-15, next bar 2023-09-22 (a trading halt the P4 gap check passes only for exactly these two dates)
- exclusions: none
