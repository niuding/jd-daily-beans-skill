<#
.SYNOPSIS
  Remove the jd-daily-beans OS-level scheduled task.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_task.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_task.ps1 -TaskName MyBeans
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'JD-Daily-Beans'
)

$ErrorActionPreference = 'Stop'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Scheduled task '$TaskName' not found; nothing to do."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'."
Write-Host 'Local config.json, .runtime.cmd, logs and state were left untouched.'
