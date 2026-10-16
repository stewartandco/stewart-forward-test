@echo off
rem \Morpheus\31_SignalService, daily 08:45 (build 4a). READ-ONLY: writes
rem logs\signals\ only; no chain write, no chain.lock, no git.
rem After the 08:20 quarantine daily refreshed data\BTCUSD_1d.csv and
rem ETHUSD_1d.csv. Output: logs\signals\targets-BAR_END.json, read by the
rem trading-systems executor. %PY% is the interpreter by ABSOLUTE path, never
rem bare python (WindowsApps alias; research-layer CLAUDE.md, Task guard).
rem Exit 4 = the run log could not be opened (another process holds it):
rem nothing ran.
rem Exit 1 = REFUSED: a served asset's CSV is missing, empty or ends before
rem the others (logs\signal-run.log names the dates); nothing written.
setlocal
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set PY=C:\Users\Coen\AppData\Local\Python\bin\python.exe
set LOG=%LAYER%\logs\signal-run.log
cd /d "%LAYER%"
if not exist "%LAYER%\logs" mkdir "%LAYER%\logs"
echo ==== %DATE% %TIME% signal ==== >> "%LOG%" 2>nul || exit /b 4
"%PY%" -m pipeline.signal >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo ==== %DATE% %TIME% exit %RC% ==== >> "%LOG%"
endlocal & exit /b %RC%
