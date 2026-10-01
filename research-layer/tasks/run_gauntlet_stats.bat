@echo off
setlocal
rem 28_GauntletStats - gauntlet recorded statistics (Build 2a), daily 01:00.
rem No --chain until the plan's cutover step 7 (the --chain gate): this job records nothing on the chain yet.
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
echo ==== %DATE% %TIME% gauntlet stats ==== >> "%LOG%"
python -m pipeline.gauntlet_stats --report "%LAYER%\logs\gauntlet-stats-report.md" >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
cd /d "%REPO%"
if exist "%LAYER%\logs\chain.lock" (
  echo chain.lock held, commit skipped >> "%LOG%"
  goto :done
)
rem vs HEAD, not the index: a registry change another session staged is
rem still uncommitted and still ours to commit.
git diff --quiet HEAD -- research-layer/registry_log.jsonl
if errorlevel 1 (
  git commit -q -m "gauntlet stats: %DATE%" -- research-layer/registry_log.jsonl >> "%LOG%" 2>&1
)
:done
echo ==== %DATE% %TIME% exit %RC% ==== >> "%LOG%"
endlocal & exit /b %RC%
