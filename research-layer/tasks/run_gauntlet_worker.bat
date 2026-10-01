@echo off
setlocal
rem 27_GauntletWorker - standalone gauntlet verdicts (Build 2a), every 30 min.
rem Exit code is load-bearing: the Ops Sentinel FAILs the digest on nonzero, so
rem the exit code is ALWAYS the python step's code and never a git result.
rem Registered by the owner (elevated) from morpheus-hub\tasks\xml\27_GauntletWorker.xml.
rem
rem COMMIT BLOCK (best-effort, never fails the run, never fails another job):
rem  * Scope = registry_log.jsonl plus every judged bundle the worker listed in
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
echo ==== %DATE% %TIME% gauntlet worker ==== >> "%LOG%"
python -m pipeline.gauntlet_worker --max-workers 6 >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
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
rem vs HEAD, not the index: a registry change another session staged is
rem still uncommitted and still ours to commit.
git diff --quiet HEAD -- research-layer/registry_log.jsonl
if errorlevel 1 set WANT=1
if "%WANT%"=="0" goto :done
> "%SCOPE%" echo research-layer/registry_log.jsonl
if exist "%TAKING%" type "%TAKING%" >> "%SCOPE%"
git add --pathspec-from-file="%SCOPE%" >> "%LOG%" 2>&1
if errorlevel 1 goto :keep
git commit -q -m "gauntlet worker: verdicts %DATE% %TIME%" --pathspec-from-file="%SCOPE%" >> "%LOG%" 2>&1
if errorlevel 1 goto :keep
if exist "%TAKING%" del "%TAKING%"
echo committed registry and listed bundles >> "%LOG%"
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
