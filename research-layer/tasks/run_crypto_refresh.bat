@echo off
rem \Morpheus\33_CryptoGridRefresh, daily 08:02 Perth (2b-3 switch 1).
rem Tops up data\<SYMBOL>USDT_1d.csv for every ACTIVE crypto cell
rem (pipeline.crypto_refresh) so the 08:20 quarantine daily and the 08:45
rem signal service read fresh daily bars (Binance's 1d bar closes 00:00 UTC =
rem 08:00 Perth). Number 33: 30/31 are build 4a's, 32 is build 5's.
rem READS: the cell list (cells.py) and each cell's CSV in data\.
rem WRITES: the cell CSVs (append only, capped at the last complete UTC day),
rem data\crypto_snapshot_manifest.json, logs\degraded_crypto_refresh.json and
rem this run log logs\crypto-refresh-run.log. NO git, NO chain write, NO
rem chain.lock, NO instance lock: so the guard below is given no --lock,
rem --hard-lock, --release-lock or --git-dir.
rem %PY% is the interpreter by ABSOLUTE path, never bare python (WindowsApps
rem alias; research-layer CLAUDE.md, Task guard).
rem Exit codes (the exit code is ALWAYS the python step's, never a later step):
rem  0   = the run completed (or stopped on a Binance ban / rate limit, or at
rem        the 08:12 hard stop):
rem        failed or skipped cells are items in logs\degraded_crypto_refresh.json
rem        for the Sentinel, not a non-zero exit (a retry would hit Binance again).
rem  1   = an unhandled crash (Python's own exit code; traceback in the log).
rem  2   = REFUSED, nothing written: the data directory does not exist.
rem  4   = the run log could not be opened (another process holds it): nothing ran.
rem  124 = the task guard's wall ended the run and killed its process tree.
rem  125 = the guard saw this wrapper's outer cmd die (Task Scheduler stopped it).
setlocal
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set LOG=%LAYER%\logs\crypto-refresh-run.log
set PY=C:\Users\Coen\AppData\Local\Python\bin\python.exe
if not exist "%LAYER%\logs" mkdir "%LAYER%\logs"
cd /d "%LAYER%"
rem TASK GUARD (pipeline\taskguard.py; research-layer CLAUDE.md, Task guard).
rem At the task's ExecutionTimeLimit Task Scheduler kills only THIS cmd.exe,
rem never its children, and an orphaned python would keep the run log open
rem (cmd opens a >> target without write sharing), so later fires could not
rem log and would exit 0 having done nothing. The wrapper therefore re-runs
rem itself under the guard (RL_TASKGUARD=1 marks the inner copy); the guard
rem ends the whole tree at its wall (exit 124) and otherwise returns the inner
rem wrapper's exit code unchanged. Wall 780 s from the task's start + grace
rem 30 s = 810 s, inside the XML's ExecutionTimeLimit PT15M (900 s): change
rem the two together.
rem DEADLINE AND HARD STOP (pipeline\crypto_refresh.py docstring). The guard
rem holds no lock, so no grace applies: it kills the run at 08:15:00, and a
rem killed run writes neither the ledger nor the manifest (yesterday's ledger,
rem under the Sentinel's 30 h, would read all clear on the morning every cell
rem timed out, ~96 s per cell). So: no retry starts at or after 08:11
rem (--deadline-local) and NO attempt, pass 1 included, at or after 08:12
rem (--stop-local); unfetched cells become "skipped: past the stop time" items
rem and the ledger is written well before the 08:15 wall. These flags are
rem for the 08:02 fire only: a hand run of THIS wrapper after 08:12 fetches
rem nothing and marks every cell skipped; hand-run `python -m
rem pipeline.crypto_refresh` (no --stop-local) instead.
if "%RL_TASKGUARD%"=="1" goto :guarded
"%PY%" -m pipeline.taskguard --wall-s 780 --grace-s 30 --log "%LOG%" -- cmd /d /c "%~f0"
exit /b %ERRORLEVEL%
:guarded
echo ==== %DATE% %TIME% crypto_refresh ==== >> "%LOG%" 2>nul || goto :log_locked
"%PY%" -m pipeline.crypto_refresh --deadline-local 08:11 --stop-local 08:12 >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo ==== %DATE% %TIME% exit %RC% ==== >> "%LOG%"
endlocal & exit /b %RC%
:log_locked
rem The run log is held open by another process (an orphaned python from a
rem killed run). Nothing has run. Exit 4 so the Sentinel FAILs rather than
rem reading a silent exit 0.
echo %DATE% %TIME% run log locked by another process; nothing run, exit 4 >> "%LAYER%\logs\crypto-refresh-run.locked.log" 2>nul
endlocal & exit /b 4
