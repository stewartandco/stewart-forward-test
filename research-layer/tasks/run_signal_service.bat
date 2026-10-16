@echo off
rem \Morpheus\31_SignalService, daily 08:45 (build 4a). READ-ONLY: writes
rem logs\signals\ only; no chain write, no chain.lock, no git.
rem After the 08:20 quarantine daily refreshed data\BTCUSD_1d.csv and
rem ETHUSD_1d.csv. Output: logs\signals\targets-BAR_END.json, read by the
rem trading-systems executor. %PY% is the interpreter by ABSOLUTE path, never
rem bare python (WindowsApps alias; research-layer CLAUDE.md, Task guard).
rem Exit 4 = the run log could not be opened (another process holds it):
rem nothing ran.
rem Exit 0 = targets written (or nothing served on an existing chain).
rem Exit 2 = REFUSED, nothing written: a served asset's CSV is missing, empty
rem or ends before the others; bar_end is older than the bar that closed at
rem the start of the UTC day (stale data); or the registry file is missing.
rem logs\signal-run.log says which.
rem Exit 1 = an unhandled crash (Python's own exit code; traceback in the log).
setlocal
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set PY=C:\Users\Coen\AppData\Local\Python\bin\python.exe
set LOG=%LAYER%\logs\signal-run.log
cd /d "%LAYER%"
if not exist "%LAYER%\logs" mkdir "%LAYER%\logs"
echo ==== %DATE% %TIME% signal ==== >> "%LOG%" 2>nul || exit /b 4
"%PY%" -m pipeline.signal_service >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo ==== %DATE% %TIME% exit %RC% ==== >> "%LOG%"
endlocal & exit /b %RC%
