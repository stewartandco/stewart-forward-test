---
paths:
  - "pipeline/budget.py"
  - "pipeline/pipeline_budget.py"
  - "pipeline/test_pipeline_budget.py"
  - "pipeline/test_composer_budget.py"
---
## Budget lines (D41, 2026-09-18): pipeline 200, Reader 20, one constant, NO batch-stop
- `pipeline/budget.py` `PIPELINE_CAP_USD` is THE pipeline cap;
  `pipeline_budget.MONTHLY_USD` imports it. The Reader's default meter cap and
  `scanner --cap` default are 20. Tests derive every threshold from the
  constants -- never pin a literal dollar figure.
- **D41 (Coen): the 80% batch-stop is GONE and the cap went 40 -> 200.** The
  batch-stop made the last fifth of every month unusable and skipped good
  candidates; work now runs right up to the hard cap. `may_start_batch` is
  kept as a named call site but is now the same line as `may_spend`.
- **⚠ 200 EXCEEDS the old D28 pool of 100 and no longer fits the Reader 20 /
  pipeline 40 band split. That band is superseded, not stretched.**
- **⚠⚠ AT 200 THE ALLOWANCE NO LONGER BINDS -- `TRIAGE_CEILING` (200 cards)
  does.** Measured: at 10/20/30 cycles a month the derived triage limit is 200
  every time, so each cycle costs a flat ~USD 4.24 and MONTHLY SPEND NOW SCALES
  WITH CYCLE COUNT (~42 / ~85 / ~127) instead of self-regulating to ~34 at any
  cadence. Two fires a day that all run full cycles would reach the 200 cap
  around day 23 and then park hard, with no glide path now the 80% stop is
  gone. **If that is not wanted, the lever is `TRIAGE_CEILING`, not the cap.**
- `BudgetMeter.state()` judges the CURRENT calendar month. A test that
  stamps rows in a fixed month goes silent when the month turns (that is what
  broke test_pipeline_budget on 2026-09-01); use a this-month timestamp.

