@echo off
rem 25_PipelineLoop - trigger-check for the pipeline loop (spec 2026-08-27).
rem Exit code is load-bearing: Ops Sentinel FAILs the digest on nonzero.
set LAYER=E:\Users\Coen\Claude\stewart-forward-test\research-layer
set LOG=%LAYER%\logs\pipeline-loop-run.log

echo ==== %DATE% %TIME% pipeline loop fire ==== >> "%LOG%"
cd /d "%LAYER%"
python -m pipeline.loop --once >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

echo ==== %DATE% %TIME% ok ==== >> "%LOG%"
exit /b 0

:fail
echo ==== %DATE% %TIME% FAILED exit %ERRORLEVEL% ==== >> "%LOG%"
exit /b 2
