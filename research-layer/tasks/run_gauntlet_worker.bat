@echo off
setlocal
rem 27_GauntletWorker - standalone gauntlet verdicts (Build 2a), every 30 min.
rem Exit code is load-bearing: the Ops Sentinel FAILs the digest on nonzero, so
rem the exit code is ALWAYS the python step's code and never a git result.
rem Registered by the owner (elevated) from morpheus-hub\tasks\xml\27_GauntletWorker.xml.
rem
rem COMMIT BLOCK (best-effort, never fails the run, never fails another job):
rem  * Scope = the chain mirror (registry_log.d) plus every judged bundle the worker listed in
rem    logs\gauntlet_worker_commit_paths.txt (one repo-relative path per line;
rem    the artifacts_hash of each v6.1 verdict refers to that bundle). Both
rem    `git add` and `git commit` read the scope from ONE pathspec file, never
rem    argv: loop.commit_cycle hit Windows' 32,767-character command-line cap
rem    on 2026-09-01 with one path per bundle on argv.
rem  * `git commit --pathspec-from-file` is the --only form: it commits ONLY
rem    those paths and leaves anything another session or the loop has staged
rem    exactly as it found it. A bare commit would sweep their staged work.
rem  * The list is taken (renamed to .taking) before the commit and deleted
rem    only after the commit succeeded. A skipped commit (chain.lock held) or a
rem    failed one (index.lock held, any git error) puts it back, so the next
rem    run's commit carries those bundles. A commit with nothing new in scope
rem    also fails and keeps the list; the next verdict's commit clears it.
rem  * Skipped while logs\chain.lock exists: a writer is mid-window. The chain
rem    is append-only and every commit is a snapshot, so a skipped or doubled
rem    commit loses nothing.
rem  * If .git\index.lock is held (the loop at 20:00-07:00, quarantine at 08:20)
rem    git fails fast, the failure goes to the log and is ignored. The other job's
rem    own commit is best-effort too (loop.commit_cycle WARNs and returns 0; the
rem    quarantine wrapper ignores the errorlevel), so a collision costs nobody an
rem    exit code. Never pushed.
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set REPO=E:\Users\Coen\Claude\stewart-forward-test
set LOG=%LAYER%\logs\gauntlet-worker-run.log
set LIST=%LAYER%\logs\gauntlet_worker_commit_paths.txt
set TAKING=%LAYER%\logs\gauntlet_worker_commit_paths.taking
set SCOPE=%LAYER%\logs\gauntlet_worker_commit_pathspec.txt
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
rem Wall 1600 s from the task's start (this cmd.exe's creation) + grace 45 s
rem ends inside PT28M (1680 s). A held append/index lock may stretch the
rem kill by up to 150 s, PAST PT28M: Task Scheduler then stops this cmd and
rem the task result is its 267014, not 124, but the guard still ends the tree
rem before the next :00/:30 fire. The worker's own
rem deadline is 25 min from the same start.
if "%RL_TASKGUARD%"=="1" goto :guarded
"%PY%" -m pipeline.taskguard --wall-s 1600 --grace-s 45 --hard-grace-s 150 --log "%LOG%" --lock "%LAYER%\logs\chain.lock" --lock "%LAYER%\registry_log.d.lock" --hard-lock "%LAYER%\registry_log.jsonl.lock" --release-lock "%LAYER%\logs\gauntlet_worker.lock" --git-dir "%REPO%\.git" -- cmd /d /c "%~f0"
exit /b %ERRORLEVEL%
:guarded
echo ==== %DATE% %TIME% gauntlet worker ==== >> "%LOG%" 2>nul || goto :log_locked
"%PY%" -m pipeline.gauntlet_worker --max-workers 6 >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
rem Chain mirror (2026-10-09 segments design, pipeline\chain_mirror.py): git
rem tracks research-layer\registry_log.d (LF segments of the chain, none over
rem 40 MiB), never registry_log.jsonl. The mirror is staged ONLY on sync's exit
rem 0; exit 3 (refused) or any other code leaves it out of this commit, and a
rem refusal is an item in logs\degraded_commit.json for the Sentinel.
rem The listed bundles still commit without it.
set REG=0
"%PY%" -m pipeline.chain_mirror sync >> "%LOG%" 2>&1
rem Exactly 0, never `if errorlevel 1`: that test is ERRORLEVEL >= 1, so a
rem crash's NTSTATUS code (negative, e.g. -1073741819) would read as "yes".
if "%ERRORLEVEL%"=="0" set REG=1
cd /d "%REPO%"
rem A .taking file left by a run killed mid-commit goes back on the list.
if exist "%TAKING%" (
  type "%TAKING%" >> "%LIST%"
  del "%TAKING%"
)
if exist "%LAYER%\logs\chain.lock" (
  echo chain.lock held, commit skipped; bundle list kept >> "%LOG%"
  goto :done
)
if exist "%LIST%" move /y "%LIST%" "%TAKING%" > nul
set WANT=0
if exist "%TAKING%" set WANT=1
rem New segments are UNTRACKED, which `git diff` never reports: ask git status
rem (it also shows a mirror change another session staged, still ours to commit).
if "%REG%"=="1" (
  git status --porcelain -- research-layer/registry_log.d | findstr /r /c:"." > nul
  if not errorlevel 1 set WANT=1
)
if "%WANT%"=="0" goto :done
type nul > "%SCOPE%"
if "%REG%"=="1" echo research-layer/registry_log.d>> "%SCOPE%"
rem Only bundle paths ride the list (the worker writes nothing else), so a
rem hand edit naming the registry or a parent directory never reaches git.
rem A line with ".." or a backslash is dropped too: git resolves
rem research-layer/artifacts/../registry_log.jsonl to the live file. findstr
rem /l /c: is literal; "\\" is findstr's escape for one backslash.
if exist "%TAKING%" findstr /b /c:"research-layer/artifacts/" "%TAKING%" | findstr /v /l /c:".." | findstr /v /l /c:"\\" >> "%SCOPE%"
rem An empty scope must never reach git: `git commit --pathspec-from-file`
rem with an empty file commits the WHOLE index, another session's staged work
rem and an unsynced mirror included (review 2026-10-09). findstr exits 1 on
rem no match and 2 on an error; both skip the commit.
findstr /r /c:"[a-z0-9]" "%SCOPE%" > nul
if errorlevel 1 goto :empty
rem `|| goto :keep`, not `if errorlevel 1`: a >> that fails to open the log
rem skips the command and leaves ERRORLEVEL unchanged (0 from findstr), so
rem the old test fell through and deleted the list with nothing committed.
git add --pathspec-from-file="%SCOPE%" >> "%LOG%" 2>&1 || goto :keep
git commit -q -m "gauntlet worker: verdicts %DATE% %TIME%" --pathspec-from-file="%SCOPE%" >> "%LOG%" 2>&1 || goto :keep
if exist "%TAKING%" del "%TAKING%"
echo committed chain mirror and listed bundles >> "%LOG%"
goto :done
:empty
echo nothing in scope (mirror not synced, no listed bundle); no commit >> "%LOG%"
if exist "%TAKING%" del "%TAKING%"
goto :done
:keep
echo commit skipped (git failed or nothing new in scope); bundle list kept >> "%LOG%"
if exist "%TAKING%" (
  type "%TAKING%" >> "%LIST%"
  del "%TAKING%"
)
:done
echo ==== %DATE% %TIME% exit %RC% ==== >> "%LOG%"
endlocal & exit /b %RC%
:log_locked
rem The run log is held open by another process (before the task guard: an
rem orphaned python from a killed run; on 2026-10-10 three fires in a row
rem could not open it, ran nothing and exited 0). Nothing has run. Exit 4 so
rem the Sentinel FAILs rather than reading a silent exit 0.
echo %DATE% %TIME% run log locked by another process; nothing run, exit 4 >> "%LAYER%\logs\gauntlet-worker-run.locked.log" 2>nul
endlocal & exit /b 4
