# Launch the Reader v2 scanner OS-detached (survives session/harness exit).
# The session harness kills ~60-65min background children (vault gotcha), so
# NEVER run the resident loop as a session child - use this, or Task Scheduler.
#
#   powershell -ExecutionPolicy Bypass -File run_scanner.ps1
#
# Stop it (scoped - NEVER taskkill python.exe globally):
#   Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
#     Where-Object { $_.CommandLine -match 'pipeline\.scanner' } |
#     ForEach-Object { Stop-Process -Id $_.ProcessId }

$layer = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $layer "logs\scanner_console.log"
New-Item -ItemType Directory -Force (Join-Path $layer "logs") | Out-Null

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'pipeline\.scanner' }
if ($existing) {
    Write-Host "scanner already running (pid $($existing.ProcessId)); not starting a second."
    exit 0
}

Start-Process -FilePath "python" `
    -ArgumentList "-m", "pipeline.scanner" `
    -WorkingDirectory $layer `
    -WindowStyle Hidden `
    -RedirectStandardOutput $log `
    -RedirectStandardError (Join-Path $layer "logs\scanner_console.err.log")
Write-Host "scanner launched detached; console -> $log"
