@echo off
rem \Morpheus\31_SignalService, daily 08:45 (build 4a). READ-ONLY: writes
rem logs\signals\ only; no chain write, no chain.lock, no git.
rem After the 08:20 quarantine daily refreshed data\BTCUSD_1d.csv and
rem ETHUSD_1d.csv. Output: logs\signals\targets-BAR_END.json, read by the
rem trading-systems executor; files beyond the newest 30 are pruned after a
rem publish. %PY% is the interpreter by ABSOLUTE path, never bare python
rem (WindowsApps alias; research-layer CLAUDE.md, Task guard).
rem TASK GUARD (pipeline\taskguard.py, as run_gauntlet_worker.bat): the wrapper
rem re-runs itself under the guard (RL_TASKGUARD=1 marks the inner copy), so an
rem overrun is ended WITH its python (Task Scheduler kills only this cmd.exe)
rem and the run log handle is released for the next fire. Wall 1500 s from the
rem task's start; the service takes no lock (read-only), so nothing delays the
rem kill and it lands well inside the task's PT30M (1800 s).
rem Exit 4 = the run log could not be opened (another process holds it):
rem nothing ran.
rem Exit 0 = targets written (or nothing served on an existing chain). A
rem strategy whose asset is missing, empty or behind bar_end is listed under
rem "deferred" in the file and not published; the rest still are.
rem Exit 2 = REFUSED, nothing written: no strategy can be published (every one
rem deferred); bar_end is not exactly the bar that closed at the start of the
rem UTC day (stale, or a partial bar dated today or later); a malformed price
rem CSV row; or the registry file is missing. logs\signal-run.log says which.
rem Exit 1 = an unhandled crash (Python's own exit code; traceback in the log).
rem Exit 124 = the guard's wall ended the run; 125 = this cmd.exe was stopped
rem (Task Scheduler) and the guard ended the run. Any other code is the
rem service's own, passed through the guard unchanged.
setlocal
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set PY=C:\Users\Coen\AppData\Local\Python\bin\python.exe
set LOG=%LAYER%\logs\signal-run.log
cd /d "%LAYER%"
if not exist "%LAYER%\logs" mkdir "%LAYER%\logs"
if "%RL_TASKGUARD%"=="1" goto :guarded
"%PY%" -m pipeline.taskguard --wall-s 1500 --log "%LOG%" -- cmd /d /c "%~f0"
exit /b %ERRORLEVEL%
:guarded
echo ==== %DATE% %TIME% signal ==== >> "%LOG%" 2>nul || exit /b 4
"%PY%" -m pipeline.signal_service >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo ==== %DATE% %TIME% exit %RC% ==== >> "%LOG%"
endlocal & exit /b %RC%
