# Weekly source scout (StewartCo\22_SourceScout). One-shot; safe to run manually.
$layer = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $layer "logs\scout.log"
New-Item -ItemType Directory -Force (Join-Path $layer "logs") | Out-Null
Set-Location $layer
$env:PYTHONIOENCODING = "utf-8"
"=== scout run $(Get-Date -Format o) ===" | Add-Content $log
python -m pipeline.scout 2>&1 | Add-Content $log
"exit $LASTEXITCODE" | Add-Content $log
exit $LASTEXITCODE
