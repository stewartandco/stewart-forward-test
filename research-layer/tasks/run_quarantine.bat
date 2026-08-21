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
REM
REM EXIT CODE IS LOAD-BEARING: Ops Sentinel alarms on a nonzero last result, so
REM this script exits 0 only when both Python steps succeeded. Every path uses
REM the ABSOLUTE log path -- the git block changes directory, and a relative
REM redirect after that resolves against the repo root, where logs\ does not
REM exist. That failing redirect is what made the first run report exit 1 while
REM having actually done its work correctly.

setlocal
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set REPO=E:\Users\Coen\Claude\stewart-forward-test
set LOG=%LAYER%\logs\quarantine-run.log

cd /d "%LAYER%"
if not exist "%LAYER%\logs" mkdir "%LAYER%\logs"
echo ==== %DATE% %TIME% ==== >> "%LOG%"

REM 1. Refresh the committed price CSVs. quarantine REFUSES a date with no bar,
REM    so a stale data dir silently stalls the forward record. The fetcher drops
REM    the exchange's currently-open kline, so this never writes a partial bar.
python -m pipeline.data_fetch >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

REM 2. Resolve yesterday in UTC.
python -c "import datetime as d, pathlib; pathlib.Path(r'%LAYER%\logs\qdate.txt').write_text((d.datetime.now(d.timezone.utc) - d.timedelta(days=1)).strftime('%%Y-%%m-%%d'))"
if errorlevel 1 goto :fail
set /p QDATE=<"%LAYER%\logs\qdate.txt"
echo recording %QDATE% >> "%LOG%"

REM 3. Record it.
python -m pipeline.quarantine --date %QDATE% >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

REM 4. Persist the witnessed record. Scoped pathspec ONLY -- a concurrent session
REM    shares this branch and working tree, and an unscoped add would sweep its
REM    work into this commit. Guarded so a no-change day makes no commit and
REM    leaves a clean tree. Never pushed; pushing stays a human action.
REM    `git diff --quiet` exits 1 when there ARE changes, which is the signal to
REM    commit, NOT an error -- hence the explicit exit 0 below rather than
REM    letting that errorlevel leak out as the task's result.
cd /d "%REPO%"
git diff --quiet -- research-layer/registry_log.jsonl research-layer/data/BTCUSD_1d.csv research-layer/data/ETHUSD_1d.csv
if errorlevel 1 (
  git add research-layer/registry_log.jsonl research-layer/data/BTCUSD_1d.csv research-layer/data/ETHUSD_1d.csv
  git commit -q -m "quarantine: forward record for %QDATE%" >> "%LOG%" 2>&1
)

echo done, exit 0 >> "%LOG%"
echo. >> "%LOG%"
endlocal
exit /b 0

:fail
echo FAILED - see errors above; task exits 1 so Ops Sentinel alarms >> "%LOG%"
echo. >> "%LOG%"
endlocal
exit /b 1
