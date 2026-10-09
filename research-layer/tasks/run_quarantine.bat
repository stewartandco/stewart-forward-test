@echo off
REM Quarantine forward test -- records ONE completed trading day, daily.
REM
REM Registered as Scheduled Task \Morpheus\23_QuarantineDaily, daily 08:20 local
REM (00:20 UTC). Moved from \StewartCo\ on 2026-09-13. To (re)create (elevated):
REM   E:\Users\Coen\Claude\morpheus-hub\tasks\setup_morpheus_scheduler.bat
REM NOT a lone schtasks /Create: that registers with NO retry. 23 is in
REM $NO_RETRY_BY_DESIGN in that folder's applier, so it takes no retry pass.
REM
REM WHY 00:20 UTC: a decision for date D describes what the book did on D's bar,
REM and D's daily bar does not close until 00:00 UTC on D+1. Running at 00:20 UTC
REM records YESTERDAY, whose bar is complete, with 20 minutes of slack for the
REM exchange to publish it.
REM
REM This WRITES TO THE HASH CHAIN unattended. It is idempotent per
REM (strategy_id, date, asset), so a re-run cannot duplicate a row. Since
REM 2026-09-28 every run ends with `--catch-up`, which records every OWED date
REM (a deferred class, a missed day, a deferred_lock skip) through the same
REM --date path, oldest first, up to MAX_CATCHUP_DATES per run. Before that a
REM deferred date waited for a hand --date run that never came: equity_etf and
REM fx sat at zero forward days. `--review` still audits gaps. --review also reports how long after its bar each row was
REM chained, so a backfilled record and a faithfully-kept one stay
REM distinguishable.
REM
REM PER-CLASS CALENDARS (2026-08-27 addendum, see docs/): a spec whose class
REM has not published the date's bar (FRED-fed FX lags ~a week) is DEFERRED
REM for the day while on-time classes still record -- that is exit 0, by
REM design, with "deferred:" lines in the log. Deferred dates are recorded by
REM the `--catch-up` step below once their bars publish; `--review` lists what
REM is owed. Since the 2026-10-08 quarantine-isolation addendum a missing price
REM FILE or a bar restatement defers only the affected strategies the same way
REM (exit 0, recorded in logs\degraded_quarantine.json for the Sentinel). Exit 1
REM remains for a total stall (every owing strategy missing a bar or price file,
REM with no row already chained) and for real failures.
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

REM 1. Refresh the committed price CSVs. A stale crypto data dir defers every
REM    crypto spec, which the all-deferred guard turns into exit 1 -- so this
REM    refresh staying healthy is what keeps the daily record alive. The
REM    fetcher drops the exchange's currently-open kline, so this never
REM    writes a partial bar.
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

REM 3b. Record every owed date the day's run could not (deferred classes, missed
REM     days). A catch-up failure must NOT skip step 4: the day's rows and every
REM     date catch-up did record still get committed, and the failure is carried
REM     to the task's exit code afterwards.
set CATCHUP_RC=0
python -m pipeline.quarantine --catch-up >> "%LOG%" 2>&1
if errorlevel 1 set CATCHUP_RC=1

REM 4. Persist the witnessed record. Scoped pathspec ONLY -- a concurrent session
REM    shares this branch and working tree, and an unscoped add would sweep its
REM    work into this commit. Guarded so a no-change day makes no commit and
REM    leaves a clean tree. Never pushed; pushing stays a human action.
REM    `git diff --quiet` exits 1 when there ARE changes, which is the signal to
REM    commit, NOT an error -- hence the explicit exit 0 below rather than
REM    letting that errorlevel leak out as the task's result.
REM Commit guard (2026-10-09 stopgap, pipeline\commit_guard.py): GitHub blocks
REM any file over 100 MiB in pushed history, so the registry is committed ONLY
REM on the guard's exit 0. Exit 3 (at or over the guard), or any other code,
REM keeps it out of this commit; the chain still grows on disk, and the pause
REM is an item in logs\degraded_commit.json for the Sentinel.
REM The price CSVs still commit without it. The commit is the --only form
REM (`-- paths`), so nothing another session staged, a registry included,
REM can ride along in this commit.
set QPATHS=research-layer/data/BTCUSD_1d.csv research-layer/data/ETHUSD_1d.csv
python -m pipeline.commit_guard >> "%LOG%" 2>&1
REM Exactly 0, never `if errorlevel 1`: that test is ERRORLEVEL >= 1, so a
REM crash's NTSTATUS code (negative, e.g. -1073741819) would read as "yes".
if "%ERRORLEVEL%"=="0" set QPATHS=research-layer/registry_log.jsonl %QPATHS%
cd /d "%REPO%"
git diff --quiet -- %QPATHS%
if errorlevel 1 (
  git add %QPATHS%
  git commit -q -m "quarantine: forward record for %QDATE% (+ catch-up)" -- %QPATHS% >> "%LOG%" 2>&1
)

if "%CATCHUP_RC%"=="1" goto :fail
echo done, exit 0 >> "%LOG%"
echo. >> "%LOG%"
endlocal
exit /b 0

:fail
echo FAILED - see errors above; task exits 1 so Ops Sentinel alarms >> "%LOG%"
echo. >> "%LOG%"
endlocal
exit /b 1
