---
paths:
  - "pipeline/test_switch2*.py"
  - "pipeline/cells.py"
  - "pipeline/composer.py"
  - "pipeline/crypto_refresh.py"
  - "pipeline/screen.py"
  - "tools/grid_switch.py"
  - "tools/test_grid_switch.py"
  - "docs/notes/gauntlet-protocol-v6.1-intraday-daily-marks.md"
  - "docs/notes/crypto-grid-phase2-v1.md"
---
## Switch 2 (2b-3 plan 5, 2026-11-01): crypto intraday goes live
- **Parameters are bars as written.** A lookback of N is N bars of the cell's
  own timeframe (200 = 200 days at 1d, 100 days at 12h, about 8 days at 1h), so
  one family is a different strategy on each timeframe and each is its own
  counted trial. The ONE window fixed in time is the vol-percentile regime:
  365 days of bars at every timeframe (`gate_mask`, protocol note). The
  composer's crypto brief says this in a bullet (`test_switch2.py` pins that the
  history block and every Rules bullet are byte for byte the Switch 1 text).
- **The composer HOLDS intraday cells until both notes are chained
  (`6f29e438d`).** `composer.INTRADAY_NOTE_PREFIXES` = the two note prefixes
  above; `intraday_held(asset_class, registry)` is True only when the class has
  an active non-1d timeframe AND at least one prefix has no chained `note`
  starting with it (ONE note present still holds). A 1d-only gate and every
  tradfi class never read the chain for it. While held, `run` sets
  `held_tfs = ("1d",)`, prints `HELD: intraday cells not swept until both
  notes are chained: ...`, and passes the SAME restriction to the per-combo
  cap (`sweep_cells(..., timeframes)`) and to the expansion
  (`expand_family_for_class(..., timeframes=)`); the cap and the expansion
  cannot disagree. The drift record in `logs/batch_drift.jsonl` carries
  `"held_intraday": true` when held (the key is ABSENT otherwise, so every
  other record is byte-identical). The hold lifts by itself on the first fire
  after the second note lands. Until then crypto sweeps its 76 1d cells only
  (a 12-asset window is 12 cells).
- **The refresh keeps every REGISTERED crypto cell fresh, not only active ones
  (`f052441f0`).** `crypto_refresh.refresh_cells(registry)` = `active_cells
  ("crypto")` in order, then every other `(asset, tf)` a registered
  single-asset crypto strategy runs on (asset in `cells.ASSETS`, tf in
  `cells.TIMEFRAMES`, not retired), sorted. The legacy pooled BTCUSD/ETHUSD
  specs are never added (`data_fetch` refreshes them at 08:20). New flag
  `--registry` (default the layer's `registry_log.jsonl`): the chain is read
  ONCE per run, read-only. A chain that cannot be read is NOT a failed run: the
  active cells are refreshed and the ledger carries ONE `registry_unreadable`
  item naming the error. Why it exists: a pause (below) drops intraday cells
  from `active_cells` while their strategies stay registered; unrefreshed they
  would park crypto at `stale_data` and make `gauntlet_stats` refuse
  `cells_not_comparable` registry-wide.
- **Fetch-owed uses the refresh's end cap (`03ed4d88c`, test fix `a4eb08277`).**
  `quarantine.fetch_owed` computes `end_ms = last_complete_utc_day_end_ms(now_ms)`
  once per call and passes `now_ms=` and `end_ms=` to every `fetch` call and
  to the feed-health scan (`_feed_health_items(..., end_ms)`), so an intraday
  cell ends on the same previous UTC day the refresh leaves it on and a
  capped afternoon run does not read as `feed_stale`. A fake `fetch` for
  `fetch_owed` (and the rehearsal's) must accept those two keywords.
- **The screen groups jobs by timeframe before chunking (`261a4a60c`).**
  `screen._group_by_tf(jobs)` is a STABLE reorder (timeframes in order of first
  appearance, original order within one) applied in `run` before `_tf_chunks`.
  Switch 2 expands per asset then per timeframe, so an alternating list would be
  cut toward one-spec chunks and one-spec chunks run in-process (the D-i
  rehearsal: 1.16 s per intraday spec serial against 0.43 / 0.15 / 0.085 s
  grouped). An all-one-timeframe run, so every all-1d run, keeps its exact order
  and chunking. Verdicts are unchanged; only the ORDER a mixed run's verdict
  batches are chained moves (the gauntlet worker re-sorts cheapest first).
- **The rehearsal skips twins already registered (`db046ecd6`, `f10a16cab`).**
  `tools/intraday_rehearsal.register` skips a twin whose composition fingerprint
  (`composer.composition_fingerprint`, universe + blocks) is already held by
  ANOTHER strategy on the chain (a 1d twin on an asset the composer already
  covered is a duplicate the verifier flags). Skipped twins stay out of the
  twins map (its flat `{sid: {original, asset, tf}}` shape is read by
  `pick_twins`, render, collect and quarantine) and go to the sidecar
  `<out stem>.skipped.json` (`{sid: {original, asset, tf, existing}}`). A twin
  already on the chain under its OWN id (a rerun) is listed in the map and never
  re-registered or skipped.
- **THE PAUSE (Coen 2026-11-01; trigger restated at the effective-trials
  per-cell change).** The trigger is a REPEATING `deferred_memory` on task 28
  (exit 3, night after night), not a date: the crossing date (planning date
  2026-12-03; 2026-12-23 was the original sign-off figure, history) is
  retired, because per-cell clustering grows with the largest
  cell's n, not the registry's (see "Effective trials per cell" under
  "Gauntlet worker"). Before the per-cell change is merged the same trigger
  applies to the registry-wide pass. Switch 2 pauses
  by a FORWARD reviewed commit that sets crypto's `ACTIVE_CELLS` timeframes back
  to `("1d",)`, plus a pause note if Coen wants one. The refresh's
  registered-cell union (above) keeps the registered intraday cells fresh, and
  quarantine keeps recording them. **NEVER a revert once an intraday strategy
  is registered:** those strategies stay on the chain, counted in N and
  re-simulated forever, and a revert deletes the code that reads them. A pause
  stops N growing; it does not shrink it.
- **ABORT / ROLLBACK.** A revert of the Switch 2 MERGE as a whole is safe ONLY
  BEFORE the first intraday registration (the first `strategy_registered` with a
  tf other than 1d; the composer hold keeps the composer from registering one
  before both notes are chained). After it, the only way back is the forward pause above. The D-i rule
  "a D-i revert must also delete `seriescache/`" (Engine series cache,
  ROLLBACK RULE) is UNCHANGED and separate: a Switch 2 revert neither implies
  it nor replaces it.

