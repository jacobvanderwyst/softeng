#Requires -Version 5.1
<#
.SYNOPSIS
  Create many accounts from a CSV (teachers and admins first, students last), with random one-time passwords.

.DESCRIPTION
  CSV columns (header required; empty cells are allowed where noted):
    username,role,first_name,last_name,department,program_id,advisor
    role: student | teacher | admin       first/last name: required for students and teachers
    department: teachers only             program_id: students only (must exist in the plans database)
    advisor: students only, the username of a teacher (listed in the same CSV or already existing)

  Each created account gets a random password (policy-compliant). The usernames and passwords are written to
  -OutFile, a file only YOU can read: hand them out securely and delete the file. There is no "force change on first
  login" feature yet, so ask users to change their password after the first login (POST /api/v1/auth/change-password).

.EXAMPLE
  # Development (virtual environment in backend\.venv; APP_ENV and databases come from your shell/.env):
  $env:APP_ENV = "development"
  .\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile $env:USERPROFILE\new-accounts.csv

.EXAMPLE
  # Production (uses the installed operator command, which sets the production environment):
  .\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile D:\secure\new-accounts.csv `
      -Cli "C:\Program Files\DegreePlan\bin\degreeplan.ps1"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Csv,
    [Parameter(Mandatory)][string]$OutFile,
    [string]$Cli,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$backend = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $Csv)) { throw "CSV not found: $Csv" }
if ((Test-Path $OutFile) -and -not $DryRun) { throw "-OutFile already exists, refusing to overwrite: $OutFile" }
if ($Cli) { if (-not (Test-Path $Cli)) { throw "Operator command not found: $Cli" } }
elseif (-not (Test-Path $venvPython)) { throw "No virtual environment at $venvPython. Run scripts\demo.ps1 once, or pass -Cli." }

$rows = @(Import-Csv -Path $Csv)
$required = 'username', 'role'
foreach ($column in $required) {
    if (-not ($rows.Count -and $rows[0].PSObject.Properties.Name -contains $column)) { throw "CSV must have a '$column' column." }
}
$priority = @{ admin = 0; teacher = 1; student = 2 }
foreach ($row in $rows) {
    if (-not $priority.ContainsKey($row.role)) { throw "Row '$($row.username)': role must be student, teacher or admin (got '$($row.role)')." }
    if ($row.role -ne 'admin' -and -not ($row.first_name -and $row.last_name)) { throw "Row '$($row.username)': first_name and last_name are required." }
}
$ordered = $rows | Sort-Object { $priority[$_.role] }

function New-RandomPassword {
    $bytes = New-Object byte[] 18
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
}

if ($DryRun) {
    Write-Host "Dry run: $($ordered.Count) account(s) would be created:"
    $ordered | ForEach-Object { Write-Host ("  {0,-20} {1}" -f $_.username, $_.role) }
    return
}

New-Item -ItemType File -Path $OutFile | Out-Null
& icacls.exe $OutFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null   # only you can read the passwords
Set-Content -Path $OutFile -Value 'username,password' -Encoding ascii

$created = 0
$failed = @()
foreach ($row in $ordered) {
    $cliArgs = @('users', 'create', $row.username, '--role', $row.role)
    if ($row.first_name)  { $cliArgs += @('--first-name', $row.first_name) }
    if ($row.last_name)   { $cliArgs += @('--last-name', $row.last_name) }
    if ($row.department)  { $cliArgs += @('--department', $row.department) }
    if ($row.program_id)  { $cliArgs += @('--program-id', $row.program_id) }
    if ($row.advisor)     { $cliArgs += @('--advisor', $row.advisor) }
    $cliArgs += '--password-stdin'

    $password = New-RandomPassword
    if ($Cli) {
        $password | & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Cli @cliArgs | Out-Null
    } else {
        $password | & $venvPython -m flask --app degreeplan.wsgi @cliArgs | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
        Add-Content -Path $OutFile -Value "$($row.username),$password" -Encoding ascii
        $created++
        Write-Host "created  $($row.username) ($($row.role))"
    } else {
        $failed += $row.username
        Write-Host "FAILED   $($row.username) (see the message above)" -ForegroundColor Red
    }
}

Write-Host ''
Write-Host "$created created, $($failed.Count) failed. Credentials: $OutFile (readable only by you; deliver securely, then delete)."
if ($failed.Count) { Write-Host "Failed: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
