# taskguard_s4u_check.ps1 - one-off proof that pipeline\taskguard.py ends the
# process tree of an S4U (session 0) task that Task Scheduler stops at its
# ExecutionTimeLimit (incident 2026-10-10/11). Never scheduled; run by hand,
# ELEVATED (registering an S4U task needs admin).
#
# It registers two THROWAWAY tasks under \ClaudeScratch\ (S4U, PT1M limit, no
# trigger), starts them, waits for the scheduler's stop, reads heartbeat files
# and unregisters both tasks again:
#   unguarded  .bat runs python directly: the CONTROL. Its python and that
#              python's child must SURVIVE the stop (the bug).
#   guarded    .bat re-runs itself under the guard, exactly like the real
#              wrappers: both pythons must DIE with the stop.
# The heartbeat pythons end by themselves after 180 s, so a surviving control
# needs no cleanup. Interactive tasks do NOT show the bug (stopping one sends
# Ctrl+C to its console), which is why this must be S4U.
#
# Usage (elevated PowerShell):
#   powershell -ExecutionPolicy Bypass -File tools\taskguard_s4u_check.ps1
# Exit 0 = PASS (control survived, guarded died), 1 = FAIL, 2 = not elevated.
param([string]$Layer = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this elevated: registering an S4U task needs admin.'
    exit 2
}
$D = Join-Path $env:TEMP 'taskguard_s4u_check'
New-Item -ItemType Directory -Force $D | Out-Null
Remove-Item "$D\hb-*.txt", "$D\*.log" -ErrorAction SilentlyContinue

$hb = @"
import subprocess, sys, time
from pathlib import Path
d = Path(__file__).resolve().parent
name = sys.argv[1]
if not name.endswith('-child'):
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), name + '-child'])
end = time.time() + 180
while time.time() < end:
    (d / ('hb-' + name + '.txt')).write_text('%.1f' % time.time())
    time.sleep(1)
"@
Set-Content -Path "$D\hb.py" -Value $hb -Encoding ASCII
Set-Content -Path "$D\unguarded.bat" -Encoding ASCII -Value @(
    '@echo off',
    "python `"$D\hb.py`" unguarded >> `"$D\unguarded.log`" 2>&1")
# The control is the OLD wrapper shape (bare `python`, no guard); the guarded
# one is the NEW shape (absolute interpreter, re-run under the guard).
$py = Join-Path $env:LOCALAPPDATA 'Python\bin\python.exe'
if (-not (Test-Path $py)) { Write-Host "missing $py"; exit 1 }
Set-Content -Path "$D\guarded.bat" -Encoding ASCII -Value @(
    '@echo off',
    "cd /d `"$Layer`"",
    'if "%RL_TASKGUARD%"=="1" goto :guarded',
    "`"$py`" -m pipeline.taskguard --wall-s 300 --grace-s 10 --log `"$D\guarded.log`" -- cmd /d /c `"%~f0`"",
    'exit /b %ERRORLEVEL%',
    ':guarded',
    "`"$py`" `"$D\hb.py`" guarded >> `"$D\guarded.log`" 2>&1")

$user = "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$names = 'unguarded', 'guarded'
try {
    foreach ($n in $names) {
        $action = New-ScheduledTaskAction -Execute "$D\$n.bat"
        Register-ScheduledTask -TaskPath '\ClaudeScratch\' -TaskName "taskguard_$n" -Action $action `
            -Settings $settings -Principal $principal -Description 'THROWAWAY taskguard S4U check' | Out-Null
        Start-ScheduledTask -TaskPath '\ClaudeScratch\' -TaskName "taskguard_$n"
    }
    Write-Host 'Started both; waiting 100 s for the PT1M stop...'
    Start-Sleep -Seconds 100
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $age = @{}
    foreach ($n in 'unguarded', 'unguarded-child', 'guarded', 'guarded-child') {
        $f = "$D\hb-$n.txt"
        $age[$n] = if (Test-Path $f) { $now - [double](Get-Content $f) } else { [double]::NaN }
        Write-Host ('{0,-16} last heartbeat {1,6:N1} s ago' -f $n, $age[$n])
    }
    foreach ($n in $names) {
        $i = Get-ScheduledTask -TaskPath '\ClaudeScratch\' -TaskName "taskguard_$n" | Get-ScheduledTaskInfo
        Write-Host ('{0,-10} LastTaskResult {1}' -f $n, $i.LastTaskResult)
    }
    if (Test-Path "$D\guarded.log") { Write-Host '--- guarded.log'; Get-Content "$D\guarded.log" }
    # alive = beat within 5 s; dead = stale for over 20 s (the stop came ~40 s ago)
    $controlAlive = ($age['unguarded'] -lt 5) -and ($age['unguarded-child'] -lt 5)
    $guardedDead = ($age['guarded'] -gt 20) -and ($age['guarded-child'] -gt 20)
    Write-Host ('control survived the stop (the bug): {0}' -f $controlAlive)
    Write-Host ('guarded tree died with the stop:     {0}' -f $guardedDead)
    if ($controlAlive -and $guardedDead) { Write-Host 'PASS'; $rc = 0 } else { Write-Host 'FAIL'; $rc = 1 }
} finally {
    foreach ($n in $names) {
        Unregister-ScheduledTask -TaskPath '\ClaudeScratch\' -TaskName "taskguard_$n" -Confirm:$false `
            -ErrorAction SilentlyContinue
    }
    try {
        $svc = New-Object -ComObject Schedule.Service; $svc.Connect()
        $svc.GetFolder('\').DeleteFolder('ClaudeScratch', 0)
    } catch { }
}
exit $rc
