#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Removes the degree plan services and firewall rules. Data, secrets and logs are KEPT unless
  -RemoveData is combined with -Force.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'DegreePlan'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'DegreePlan'),
    [switch]$RemoveData,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

foreach ($svc in @('DegreePlanProxy', 'DegreePlanApi')) {
    if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
        Write-Host "==> Removing service $svc"
        Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
        $exe = Join-Path $InstallRoot "service\$svc.exe"
        if (Test-Path $exe) { & $exe uninstall } else { & sc.exe delete $svc | Out-Null }
    }
}

foreach ($name in @('DegreePlan HTTP (redirect/ACME)', 'DegreePlan HTTPS')) {
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}

if (Test-Path $InstallRoot) {
    Write-Host "==> Removing $InstallRoot"
    Remove-Item -Recurse -Force $InstallRoot
}

if ($RemoveData) {
    if (-not $Force) {
        throw "Refusing to delete $DataRoot (databases, secrets, logs, backups) without -Force."
    }
    Write-Host "==> DELETING $DataRoot"
    Remove-Item -Recurse -Force $DataRoot
} else {
    Write-Host "Data kept in $DataRoot (databases, secrets, logs, backups)."
}
