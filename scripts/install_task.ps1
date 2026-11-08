<#
.SYNOPSIS
  Register an OS-level Windows Task Scheduler job for jd-daily-beans.

.DESCRIPTION
  Fires at every whole hour (:00). The Python script's once-per-day guard makes
  all subsequent triggers on the same day no-ops, so missed runs are harmlessly
  retried. The task runs for the current interactive user, because Chrome and
  the agent-browser-cli extension live in that logon session.

  Nothing here contains machine-specific paths: they are resolved at run time
  and written into gitignored local files (config.json, .runtime.cmd).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_task.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_task.ps1 -TaskName MyBeans -PythonExe C:\Python313\pythonw.exe
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'JD-Daily-Beans',
    [string] $PythonExe = '',
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

$scriptsDir = $PSScriptRoot
$root       = Split-Path -Parent $scriptsDir
$entry      = Join-Path $scriptsDir 'jd_daily_beans.py'
$configPath = Join-Path $root 'config.json'
$runtimeCmd = Join-Path $scriptsDir '.runtime.cmd'

if (-not (Test-Path $entry)) { throw "Entry script not found: $entry" }

# ---------------------------------------------------------------------------
# Resolve a console-less Python interpreter (pythonw.exe) when available.
# ---------------------------------------------------------------------------
function Resolve-PythonExe {
    param([string] $Preferred)

    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    if ($env:JD_BEANS_PYTHON) { $candidates += $env:JD_BEANS_PYTHON }

    $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }

    $versions = Join-Path $env:USERPROFILE '.workbuddy\binaries\python\versions\*\pythonw.exe'
    $found = Get-ChildItem -Path $versions -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty FullName
    if ($found) { $candidates += $found }

    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        $path = $candidate
        if ($path -like '*python.exe') {
            $windowless = Join-Path (Split-Path -Parent $path) 'pythonw.exe'
            if (Test-Path $windowless) { $path = $windowless }
        }
        if ((Test-Path $path) -and ($path -match 'python(w)?\.exe$')) { return $path }
        if ((Test-Path $path) -and ($path -match 'python(3)?(\.\d+)?$')) { return $path }
    }
    throw 'Python 3.9+ not found. Pass -PythonExe C:\path\to\pythonw.exe'
}

$python = Resolve-PythonExe -Preferred $PythonExe
$visiblePython = Join-Path (Split-Path -Parent $python) 'python.exe'
if (-not (Test-Path $visiblePython)) { $visiblePython = $python }

# ---------------------------------------------------------------------------
# Local, gitignored configuration (resolved paths only, no credentials).
# ---------------------------------------------------------------------------
$dataDir = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA 'jd-daily-beans'
} else {
    Join-Path $env:USERPROFILE 'jd-daily-beans'
}
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $dataDir 'logs') -Force | Out-Null

$config = @{
    cli_path        = ''
    log_dir         = (Join-Path $dataDir 'logs')
    state_dir       = $dataDir
    close_temp_tabs = $true
    # JD refreshes its task list around midnight; ignore 00:00-07:59 triggers
    # so the first real run of the day sees the fresh task list.
    earliest_hour   = 8
}
if (Test-Path $configPath) {
    Write-Host "Keeping existing config.json: $configPath"
} else {
    $config | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8
    Write-Host "Wrote config.json: $configPath"
}

@(
    "@echo off",
    "set `"PYTHON_EXE=$visiblePython`"",
    "set `"SKILL_ROOT=$root`""
) | Set-Content -Path $runtimeCmd -Encoding ASCII
Write-Host "Wrote .runtime.cmd: $runtimeCmd"

# ---------------------------------------------------------------------------
# Scheduled task: hourly at :00, start-when-available, no overlapping runs.
# ---------------------------------------------------------------------------
$action = New-ScheduledTaskAction -Execute $python `
    -Argument ('"' + $entry + '" --config "' + $configPath + '"') `
    -WorkingDirectory $root

$startBoundary = (Get-Date).Date
$trigger = New-ScheduledTaskTrigger -Once -At $startBoundary `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$description = 'JD daily 京豆 sign-in + PC browse tasks (background, once per day via in-script guard).'

$registerArgs = @{
    TaskName    = $TaskName
    Action      = $action
    Trigger     = $trigger
    Settings    = $settings
    Principal   = $principal
    Description = $description
}
if ($Force) { $registerArgs['Force'] = $true }
Register-ScheduledTask @registerArgs | Out-Null

Write-Host ''
Write-Host "Registered scheduled task '$TaskName'"
Write-Host "  Python    : $python"
Write-Host "  Script    : $entry"
Write-Host "  Config    : $configPath"
Write-Host "  Trigger   : every hour at :00 (first boundary $startBoundary), missed runs start when available"
Write-Host "  Guard     : $dataDir\last_run.json (written only on success)"
Write-Host ''
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-Table -AutoSize | Out-String | Write-Host
Get-ScheduledTaskInfo -TaskName $TaskName |
    Select-Object LastRunTime, LastTaskResult, NextRunTime |
    Format-List | Out-String | Write-Host
