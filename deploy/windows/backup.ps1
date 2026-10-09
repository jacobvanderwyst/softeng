#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Online backup of both SQLite databases (consistent while the app is running), with integrity
  verification, a restricted-access destination and retention pruning.

.DESCRIPTION
  Schedule it daily (see docs\OPERATIONS.md). Exit code is non-zero on any failure so the scheduler
  or monitoring can alert.

  Backups contain student records and password hashes: keep the destination access-restricted
  (this script enforces Administrators + SYSTEM only) and copy them to encrypted offsite storage.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'DegreePlan'),
    [string]$BackupRoot = (Join-Path (Join-Path $env:ProgramData 'DegreePlan') 'backups'),
    [ValidateRange(1, 3650)][int]$RetentionDays = 30
)

$ErrorActionPreference = 'Stop'
$cli = Join-Path $InstallRoot 'bin\degreeplan.ps1'
if (-not (Test-Path $cli)) { throw "Operator command not found: $cli (is the application installed?)" }

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
& icacls.exe $BackupRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$target = Join-Path $BackupRoot $stamp
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $cli db backup --dest $target
if ($LASTEXITCODE -ne 0) { throw "Backup failed (exit code $LASTEXITCODE). Nothing was pruned." }

$files = Get-ChildItem -LiteralPath $target -Filter '*.sqlite3'
if ($files.Count -lt 2) { throw "Expected 2 database backups in $target, found $($files.Count)." }

# Prune only after a verified successful backup.
$cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem -LiteralPath $BackupRoot -Directory |
    Where-Object { $_.Name -match '^\d{8}T\d{6}Z$' -and $_.LastWriteTime -lt $cutoff } |
    ForEach-Object { Write-Host "Pruning old backup $($_.Name)"; Remove-Item -Recurse -Force $_.FullName }

Write-Host "Backup OK: $target ($($files.Count) databases)"
