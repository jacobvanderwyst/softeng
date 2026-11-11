#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Help, [string]$InstallRoot = 'C:\Program Files\EduNav')
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Help) { 'uninstall.ps1 [-InstallRoot <owned root>] [-WhatIf]; removes owned services/rules only; all code/data/secrets/backups retained.'; return }
$m = Read-Install $InstallRoot
if (-not $PSCmdlet.ShouldProcess($m.InstallRoot, 'Unregister EduNav services; retain all files')) { return }
Assert-Administrator
$lock = Enter-Maintenance
try {
Stop-OwnedServices $m
$task = Get-ScheduledTask -TaskName 'EduNavProductionBackup' -ErrorAction SilentlyContinue
if ($task) {
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $expected = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        (Join-Path $m.InstallRoot 'bin\backup.ps1') + '" -InstallRoot "' + $m.InstallRoot + '"'
    if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $shell -or $task.Actions[0].Arguments -ne $expected) { throw 'Backup task ownership mismatch; not removed.' }
    Unregister-ScheduledTask -TaskName 'EduNavProductionBackup' -Confirm:$false
}
foreach ($svc in @($m.ProxyService, $m.ApiService)) {
    Invoke-Checked (Join-Path $m.InstallRoot "service\$svc.exe") @('uninstall')
}
foreach ($port in @($m.HttpsPort, 80)) {
    $name = "EduNavProduction-TCP-$port"
    $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
    if ($rule) {
        $filter = $rule | Get-NetFirewallApplicationFilter
        if ($filter.Program -ne (Join-Path $m.InstallRoot 'caddy\caddy.exe')) { throw "Rule ownership mismatch: $name" }
        $rule | Remove-NetFirewallRule
    }
}
'Services/rules removed. All files, secrets and backups retained; no recursive deletion performed.'
} finally { Exit-Maintenance $lock }
