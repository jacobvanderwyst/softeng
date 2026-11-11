#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Help, [string]$InstallRoot = 'C:\Program Files\EduNav', [string]$Time = '02:00', [switch]$Replace)
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Help) {
    'schedule-backup.ps1 [-InstallRoot <owned root>] [-Time HH:mm] [-Replace] [-WhatIf]'
    'Explicit opt-in only: daily SYSTEM task runs paired quiesced Backup with maintenance downtime. No retention deletion; encrypt/copy offsite and monitor task failures separately.'
    return
}
$m = Read-Install $InstallRoot
$at = [DateTime]::ParseExact($Time, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$name = 'EduNavProductionBackup'
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    (Join-Path $m.InstallRoot 'bin\backup.ps1') + '" -InstallRoot "' + $m.InstallRoot + '"'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    if (-not $Replace -or @($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $shell -or
        $existing.Actions[0].Arguments -ne $arguments) { throw 'Existing schedule is not explicitly replaceable and owned.' }
}
if ($PSCmdlet.ShouldProcess($name, "Register daily quiesced production backup at $Time (local time)")) {
    Assert-Administrator
    $action = New-ScheduledTaskAction -Execute $shell -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force:$Replace | Out-Null
}
