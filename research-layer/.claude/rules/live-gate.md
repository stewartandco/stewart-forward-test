---
paths:
  - "pipeline/livegate.py"
  - "pipeline/test_livegate*.py"
  - "tasks/run_livegate.bat"
---
## Quarantine -> live gate runs unattended (26_LiveGateWeekly, 2026-09-03)
- `python -m pipeline.livegate` judges BOTH arms of the chained
  `quarantine-live-protocol-v1` and, when not `--dry-run`, chains a
  `live_gate` verdict plus a state change for every strategy it moves
  (quarantine -> live, or -> graveyard); a HOLD writes nothing. It takes
  `logs/chain.lock` only when there is a verdict to chain and defers politely
  (`deferred_lock`, exit 0) when it is held -- the quarantine daily's rule.
- `--report DIR` writes `<UTC date>-livegate-assessment.md` (every quarantined
  strategy, cohort size, verdicts) -- Coen's quarterly read. Written on a dry
  run too; it is not a chain write.
- `tasks/run_livegate.bat` = `\Morpheus\26_LiveGateWeekly` (in `\StewartCo\` until 2026-09-13), **Sunday 09:10**,
  exit code load-bearing. Weekly, not daily: the note charges
  Benjamini-Hochberg over "the eligible strategies at each assessment" and
  fixes no cadence; weekly keeps the kill arm prompt without re-asking a
  barely-changed record daily. Change the cadence here AND in
  `morpheus-hub/tasks/xml/26_LiveGateWeekly.xml` in the same pass.
- **LIVE is a lifecycle state, not capital** (the note's own words). Money at
  risk stays Coen's separate decision; nothing here can construct a
  `RouterConfig(mode="live")` in trading-systems, and nothing should;
  `pipeline/signal_service.py` (build 4a) only writes next-bar targets to
  logs/signals/ for the trading-systems executor (section below).
- Reachability, from the note: graduation is a MULTI-YEAR proposition
  (Sharpe 1.3 ~ 587 days best case). Do not read a slow record as a weak
  strategy; it may be a large cohort.

