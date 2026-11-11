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
  -OutFile, restricted to the user running the script: distribute the passwords securely, then delete the file.
  There is no "force change on first login" feature yet, so users should change their password after the first
  login (POST /api/v1/auth/change-password).

.EXAMPLE
  # Development (virtual environment in backend\.venv; APP_ENV and databases come from the shell or backend\.env):
  $env:APP_ENV = "development"
  .\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile $env:USERPROFILE\new-accounts.csv

.EXAMPLE
  # Production (uses the installed operator command, which sets the production environment):
  .\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile D:\secure\new-accounts.csv `
      -Cli "C:\Program Files\EduNav\bin\operator.ps1"
#>
[CmdletBinding()]
param(
    [string]$Csv,
    [string]$OutFile,
    [string]$Cli,
    [switch]$DryRun,
    [switch]$Help
)

if ($Help) {
    'bulk_create_users.ps1 -Csv <reviewed accounts.csv> -OutFile <new private local credential file> [-Cli <installed operator.ps1>] [-DryRun]'
    'CSV: username,role,first_name,last_name,department,program_id,advisor. Teachers/admins are created before students.'
    'Production: pass -Cli C:\Program Files\EduNav\bin\operator.ps1. Passwords travel through stdin, never command arguments.'
    'DryRun validates file structure only, not live program/advisor references. Account creation is per-row, not one atomic batch.'
    'Credential output is private to the current user. Distribute securely, then remove under your retention policy. Existing files are not overwritten.'
    return
}
if (-not $Csv -or -not $OutFile) { throw 'Both -Csv and -OutFile are required; use -Help for guidance.' }
$ErrorActionPreference = 'Stop'
$backend = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $Csv)) { throw "CSV not found: $Csv" }
if ((Get-Item -LiteralPath $Csv).Length -gt 10MB) { throw 'CSV exceeds 10 MB limit.' }
if ((Test-Path $OutFile) -and -not $DryRun) { throw "-OutFile already exists, refusing to overwrite: $OutFile" }
if ($Cli) { if (-not (Test-Path $Cli)) { throw "Operator command not found: $Cli" } }
elseif (-not (Test-Path $venvPython)) { throw "No virtual environment at $venvPython. Run scripts\demo.ps1 once, or pass -Cli." }

$rows = @(Import-Csv -Path $Csv)
if ($rows.Count -gt 10000) { throw 'CSV exceeds 10,000 account limit.' }
$required = 'username', 'role'
foreach ($column in $required) {
    if (-not ($rows.Count -and $rows[0].PSObject.Properties.Name -contains $column)) { throw "CSV must have a '$column' column." }
}
$priority = @{ admin = 0; teacher = 1; student = 2 }
$seen = @{}
foreach ($row in $rows) {
    if ($row.username -notmatch '^[A-Za-z0-9_.@-]{1,64}$') { throw 'Invalid username in CSV.' }
    if ($seen.ContainsKey($row.username)) { throw "Duplicate username: $($row.username)" }
    $seen[$row.username] = $row.role
    if (-not $priority.ContainsKey($row.role)) { throw "Row '$($row.username)': role must be student, teacher or admin (got '$($row.role)')." }
    if ($row.role -ne 'admin' -and -not ($row.first_name -and $row.last_name)) { throw "Row '$($row.username)': first_name and last_name are required." }
    foreach ($field in @('first_name', 'last_name', 'department', 'program_id', 'advisor')) {
        if (-not ($row.PSObject.Properties.Name -contains $field)) { $row | Add-Member -NotePropertyName $field -NotePropertyValue '' }
        if ($row.$field.Length -gt 200 -or $row.$field -match '[\x00-\x1f]') { throw "Invalid $field in CSV." }
    }
    if ($row.program_id -and ($row.role -ne 'student' -or $row.program_id -notmatch '^[1-9][0-9]{0,8}$')) { throw 'program_id requires student and a positive integer.' }
    if ($row.advisor -and ($row.role -ne 'student' -or $row.advisor -notmatch '^[A-Za-z0-9_.@-]{1,64}$')) { throw 'advisor requires student and a valid username.' }
    if ($row.department -and $row.role -ne 'teacher') { throw 'department applies to teachers only.' }
}
foreach ($row in $rows) {
    if ($row.advisor -and $seen.ContainsKey($row.advisor) -and $seen[$row.advisor] -ne 'teacher') { throw 'CSV advisor must be a teacher.' }
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

$OutFile = [IO.Path]::GetFullPath($OutFile)
if ($OutFile -notmatch '^[A-Za-z]:\\' -or $OutFile.Substring(2).Contains(':')) { throw 'Credential output must be a local file, not a network path or alternate stream.' }
$parent = Split-Path -Parent $OutFile
while ($parent) {
    if ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Credential output cannot traverse a reparse point.' }
    $parent = Split-Path -Parent $parent
}
# Atomically create the file with its final private ACL: never write a credential
# into a permissive file and then attempt to tighten permissions afterwards.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object Security.AccessControl.FileSecurity
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($sid)
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')))
$stream = New-Object IO.FileStream($OutFile, [IO.FileMode]::CreateNew,
    [Security.AccessControl.FileSystemRights]::Write, [IO.FileShare]::None, 4096, [IO.FileOptions]::None, $acl)
$stream.Dispose()
$actual = Get-Acl -LiteralPath $OutFile
if (-not $actual.AreAccessRulesProtected -or @($actual.Access).Count -ne 1 -or
    $actual.Access[0].IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { throw 'Could not verify private credential ACL.' }
Set-Content -LiteralPath $OutFile -Value 'username,password' -Encoding ascii

$created = 0
$failed = @()
foreach ($row in $ordered) {
    $cliArgs = @('users', 'create', '--role', $row.role)
    if ($row.first_name)  { $cliArgs += @('--first-name', $row.first_name) }
    if ($row.last_name)   { $cliArgs += @('--last-name', $row.last_name) }
    if ($row.department)  { $cliArgs += @('--department', $row.department) }
    if ($row.program_id)  { $cliArgs += @('--program-id', $row.program_id) }
    if ($row.advisor)     { $cliArgs += @('--advisor', $row.advisor) }
    $cliArgs += @('--password-stdin', '--', $row.username)

    $password = New-RandomPassword
    if ($Cli) {
        # Windows PowerShell -File cannot transport string[] parameters reliably.
        # Quote each non-secret argument as a literal; password travels only via stdin.
        $quoted = @($cliArgs | ForEach-Object { "'" + $_.Replace("'", "''") + "'" })
        $command = "& '" + ([IO.Path]::GetFullPath($Cli)).Replace("'", "''") + "' -CliArgs @(" + ($quoted -join ',') + ')'
        $password | & powershell.exe -NoProfile -Command $command | Out-Null
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
Write-Host "$created created, $($failed.Count) failed. Credentials: $OutFile (readable only by the current user; distribute securely, then delete it)."
if ($failed.Count) { Write-Host "Failed: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
