@echo off
setlocal
rem 28_GauntletStats - gauntlet recorded statistics (Build 2a), daily 01:00.
rem --chain ON since 2026-10-04 (Coen's say-so, cutover step 7): appends one gauntlet_stats entry per v6.1 verdict.
rem The job itself refuses to chain until gauntlet-protocol-v6.1-amendment-1 is on the chain (it is, line 81246).
rem Exit code is load-bearing: the Ops Sentinel FAILs the digest on nonzero, so
rem the exit code is ALWAYS the python step's code and never a git result.
rem Registered by the owner (elevated) from morpheus-hub\tasks\xml\28_GauntletStats.xml.
rem
rem COMMIT BLOCK (best-effort, never fails the run, never fails another job):
rem  * `git commit -- PATH` (the --only form) commits ONLY that path and leaves
rem    anything another session or the loop has staged exactly as it found it. A
rem    bare commit after `git add` would sweep a concurrent writer's staged
rem    bundles into this commit.
rem  * Skipped while logs\chain.lock exists: a writer is mid-window and the next
rem    run's commit carries the rows. The chain is append-only and every commit
rem    is a snapshot, so a skipped or doubled commit loses nothing.
rem  * If .git\index.lock is held (the loop at 20:00-07:00, quarantine at 08:20)
rem    git fails fast, the failure goes to the log and is ignored. The other job's
rem    own commit is best-effort too (loop.commit_cycle WARNs and returns 0; the
rem    quarantine wrapper ignores the errorlevel), so a collision costs nobody an
rem    exit code. Never pushed.
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set REPO=E:\Users\Coen\Claude\stewart-forward-test
set LOG=%LAYER%\logs\gauntlet-stats-run.log
if not exist "%LAYER%\logs" mkdir "%LAYER%\logs"
cd /d "%LAYER%"
rem PY: the interpreter by ABSOLUTE path, never bare `python` (review
rem 2026-10-11). On this box bare `python` is the WindowsApps PythonManager
rem alias, a packaged app: it and the runtime it starts break out of the
rem task guard's job object, and the guard would watch the alias instead of
rem this cmd.exe. Python\bin\python.exe is the PyManager shim (its
rem python.exe.__target__ names pythoncore-3.14-64\python.exe): measured
rem 2026-10-11 as ONE process that stays in the job and passes the exit code.
set PY=C:\Users\Coen\AppData\Local\Python\bin\python.exe
rem TASK GUARD (incident 2026-10-10/11, pipeline\taskguard.py). At the task's
rem ExecutionTimeLimit Task Scheduler kills only THIS cmd.exe, never its
rem children: the python step ran on for up to 90 min, holding its instance
rem lock and this log (cmd opens a >> target without write sharing, so later
rem fires could not log, ran nothing and exited 0). So the wrapper re-runs
rem itself under the guard (RL_TASKGUARD=1 marks the inner copy): one job
rem object holds the inner cmd, python, git and all they start, and the guard
rem ends that tree at its wall (exit 124, which the Sentinel FAILs), when this
rem outer cmd is killed, or when the guard itself is killed. It never kills
rem mid-write: it freezes the tree and waits while chain.lock or the mirror's
rem lock (up to --grace-s) or the chain's append lock or git's index.lock (up
rem to --hard-grace-s) is held by it. The tree includes any descendant that
rem escaped the job. After a kill it removes the instance lock and chain.lock
rem only if a killed pid held them (chain.lock is KEPT if the killed tree also
rem held the append lock: the tail may be torn). Otherwise the exit
rem code is the inner wrapper's, unchanged. The guard sets RL_TASK_STARTED_UTC
rem so the python step's deadline counts from the task's start.
rem Wall 21300 s from the task's start (this cmd.exe's creation) + grace
rem 60 s ends inside PT6H (21600 s); a held append/index lock may stretch
rem the kill to 240 s, still before the 07:30 worker fire. The job's own
rem deadline (--deadline-hours 5.75) counts from the same start.
if "%RL_TASKGUARD%"=="1" goto :guarded
"%PY%" -m pipeline.taskguard --wall-s 21300 --grace-s 60 --hard-grace-s 240 --log "%LOG%" --lock "%LAYER%\logs\chain.lock" --lock "%LAYER%\registry_log.d.lock" --hard-lock "%LAYER%\registry_log.jsonl.lock" --release-lock "%LAYER%\logs\gauntlet_stats.lock" --git-dir "%REPO%\.git" -- cmd /d /c "%~f0"
exit /b %ERRORLEVEL%
:guarded
echo ==== %DATE% %TIME% gauntlet stats ==== >> "%LOG%" 2>nul || goto :log_locked
"%PY%" -m pipeline.gauntlet_stats --chain --report "%LAYER%\logs\gauntlet-stats-report.md" >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
rem Chain mirror (2026-10-09 segments design, pipeline\chain_mirror.py): git
rem tracks research-layer\registry_log.d (LF segments of the chain, none over
rem 40 MiB), never registry_log.jsonl. The mirror is staged ONLY on sync's exit
rem 0; exit 3 (refused) or any other code leaves it out of this commit, and a
rem refusal is an item in logs\degraded_commit.json for the Sentinel.
rem The mirror is this job's whole commit, so a "no" skips the commit.
"%PY%" -m pipeline.chain_mirror sync >> "%LOG%" 2>&1
rem Exactly 0, never `if errorlevel 1`: that test is ERRORLEVEL >= 1, so a
rem crash's NTSTATUS code (negative, e.g. -1073741819) would read as "yes".
if not "%ERRORLEVEL%"=="0" (
  echo chain mirror not synced, commit skipped >> "%LOG%"
  goto :done
)
cd /d "%REPO%"
if exist "%LAYER%\logs\chain.lock" (
  echo chain.lock held, commit skipped >> "%LOG%"
  goto :done
)
rem git status, not git diff: new segments are untracked, and a mirror change
rem another session staged is still uncommitted and still ours to commit.
git status --porcelain -- research-layer/registry_log.d | findstr /r /c:"." > nul
if not errorlevel 1 (
  git add -- research-layer/registry_log.d >> "%LOG%" 2>&1
  git commit -q -m "gauntlet stats: %DATE%" -- research-layer/registry_log.d >> "%LOG%" 2>&1
)
:done
echo ==== %DATE% %TIME% exit %RC% ==== >> "%LOG%"
endlocal & exit /b %RC%
:log_locked
rem The run log is held open by another process (before the task guard: an
rem orphaned python from a killed run; on 2026-10-10 three fires in a row
rem could not open it, ran nothing and exited 0). Nothing has run. Exit 4 so
rem the Sentinel FAILs rather than reading a silent exit 0.
echo %DATE% %TIME% run log locked by another process; nothing run, exit 4 >> "%LAYER%\logs\gauntlet-stats-run.locked.log" 2>nul
endlocal & exit /b 4
