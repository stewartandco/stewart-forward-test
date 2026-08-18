@echo off
REM Quarantine forward test -- records ONE completed trading day, daily.
REM
REM Registered as Scheduled Task \StewartCo\23_QuarantineDaily, daily 08:20 local
REM (00:20 UTC). To (re)create:
REM   schtasks /Create /TN "StewartCo\23_QuarantineDaily" /TR "E:\Users\Coen\Claude\stewart-forward-test\research-layer\tasks\run_quarantine.bat" /SC DAILY /ST 08:20
REM
REM WHY 00:20 UTC: a decision for date D describes what the book did on D's bar,
REM and D's daily bar does not close until 00:00 UTC on D+1. Running at 00:20 UTC
REM records YESTERDAY, whose bar is complete, with 20 minutes of slack for the
REM exchange to publish it.
REM
REM This WRITES TO THE HASH CHAIN unattended. It is idempotent per
REM (strategy_id, date, asset), so a re-run cannot duplicate a row. A day missed
REM entirely (machine off) is NOT auto-backfilled -- run
REM `python -m pipeline.quarantine --review` to find gaps, then re-run with an
REM explicit --date. --review also reports how long after its bar each row was
REM chained, so a backfilled record and a faithfully-kept one stay
REM distinguishable.

cd /d "E:\Users\Coen\Claude\stewart-forward-test\research-layer"
if not exist logs mkdir logs
echo ==== %DATE% %TIME% ==== >> logs\quarantine-run.log

REM 1. Refresh the committed price CSVs. quarantine REFUSES a date with no bar,
REM    so a stale data dir silently stalls the forward record. The re-fetch is
REM    anticipated by design: each date's snapshot records bars_sha256 of the
REM    bars up to that date precisely so a later re-fetch cannot silently change
REM    what a reproduction yields.
python -m pipeline.data_fetch >> logs\quarantine-run.log 2>&1

REM 2. Resolve yesterday in UTC.
python -c "import datetime as d, pathlib; pathlib.Path('logs/qdate.txt').write_text((d.datetime.now(d.timezone.utc) - d.timedelta(days=1)).strftime('%%Y-%%m-%%d'))"
set /p QDATE=<logs\qdate.txt
echo recording %QDATE% >> logs\quarantine-run.log

REM 3. Record it.
python -m pipeline.quarantine --date %QDATE% >> logs\quarantine-run.log 2>&1

REM 4. Persist the witnessed record. Scoped pathspec ONLY -- a concurrent session
REM    shares this branch and working tree, and an unscoped add would sweep its
REM    work into this commit. Guarded so a no-change day makes no commit and
REM    leaves a clean tree. Never pushed; pushing stays a human action.
cd /d "E:\Users\Coen\Claude\stewart-forward-test"
git diff --quiet -- research-layer/registry_log.jsonl research-layer/data/BTCUSD_1d.csv research-layer/data/ETHUSD_1d.csv
if errorlevel 1 (
  git add research-layer/registry_log.jsonl research-layer/data/BTCUSD_1d.csv research-layer/data/ETHUSD_1d.csv
  git commit -q -m "quarantine: forward record for %QDATE%"
)
echo. >> logs\quarantine-run.log
