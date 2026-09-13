@echo off
REM Quarantine -> live gate, per the chained quarantine-live-protocol-v1.
REM
REM Registered as Scheduled Task \StewartCo\26_LiveGateWeekly, WEEKLY Sunday
REM 09:10 local. To (re)create (elevated):
REM   schtasks /Create /TN "StewartCo\26_LiveGateWeekly" /TR "E:\Users\Coen\Claude\stewart-forward-test\research-layer\tasks\run_livegate.bat" /SC WEEKLY /D SUN /ST 09:10
REM   then: powershell -ExecutionPolicy Bypass -File E:\Users\Coen\Claude\quant\tasks\apply_retry_settings.ps1 -Task 26_LiveGateWeekly
REM
REM WHY WEEKLY, NOT DAILY: the note says "at each assessment the eligible
REM strategies form a cohort" and charges Benjamini-Hochberg over that cohort.
REM It does not fix a cadence. Assessing every day would re-ask the same
REM question of a barely-changed record daily; weekly keeps the kill arm
REM prompt (a catastrophic book is buried within a week of qualifying) while
REM not multiplying looks. Coen may change the cadence; record it here and
REM in setup_scheduler.bat in the same pass.
REM
REM WHY SUNDAY 09:10: after 18_ChainVerify (Sunday 08:30) and the 08:20
REM quarantine daily, before the loop's 10:30 fire, on a quiet chain.
REM
REM THIS WRITES TO THE HASH CHAIN unattended -- a live_gate verdict and a
REM state change (quarantine -> live, or -> graveyard) for every strategy the
REM gate moves; nothing for a HOLD. It honours logs\chain.lock like the
REM quarantine daily: held lock = deferred_lock, exit 0, nothing written, the
REM next week's run picks it up. It also writes a dated assessment report to
REM docs\runs\ for Coen's quarterly read regardless of whether anything moved.
REM
REM EXIT CODE IS LOAD-BEARING: Ops Sentinel alarms on a nonzero last result.
REM ABSOLUTE log path throughout (see run_quarantine.bat for why).
setlocal
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set LOG=%LAYER%\logs\livegate-run.log
cd /d "%LAYER%"
if not exist "%LAYER%\logs" mkdir "%LAYER%\logs"
echo ==== %DATE% %TIME% live gate ==== >> "%LOG%"
python -m pipeline.livegate --report "%LAYER%\docs\runs" >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo ==== %DATE% %TIME% FAILED exit %RC% ==== >> "%LOG%"
  endlocal & exit /b %RC%
)
echo ==== %DATE% %TIME% ok ==== >> "%LOG%"
endlocal & exit /b 0
