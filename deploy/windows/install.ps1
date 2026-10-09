#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Installs (or upgrades) the degree plan backend as a Windows service, optionally with Caddy for TLS.

.DESCRIPTION
  * Creates the install tree (code + venv) and the data tree (secrets, data, logs, backups, www).
  * Installs ONLY hash-pinned dependencies (pip --require-hashes).
  * Generates the session signing key with a CSPRNG if it does not exist yet. It never leaves the
    secrets folder and is never printed.
  * Locks down ACLs: secrets are readable only by Administrators/SYSTEM and the service account.
  * Registers the services with WinSW, running as non-admin virtual accounts (NT SERVICE\<name>).
  * Applies database migrations (or stamps existing databases) and starts the services.

  Safe to re-run: existing secrets and data are preserved; code, dependencies and service
  definitions are refreshed.

.EXAMPLE
  .\install.ps1 -WinSWPath C:\tools\WinSW-x64.exe -WinSWSha256 <sha256> `
      -CaddyPath C:\tools\caddy.exe -Hostname plans.example.edu

.EXAMPLE
  # Adopt existing databases (schema already matches the baseline) instead of creating new ones:
  .\install.ps1 -WinSWPath ... -WinSWSha256 ... -UsersDatabasePath D:\db\users.sqlite3 `
      -PlansDatabasePath D:\db\plans.sqlite3 -AdoptExistingDatabases
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$WinSWPath,
    [Parameter(Mandatory)][string]$WinSWSha256,
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'DegreePlan'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'DegreePlan'),
    [string]$PythonExe = 'python',
    [string]$BackendDir = (Join-Path $PSScriptRoot '..\..\backend'),
    [string]$UsersDatabasePath,
    [string]$PlansDatabasePath,
    [switch]$AdoptExistingDatabases,
    [string]$CaddyPath,
    [string]$CaddySha256,
    [string]$Hostname,
    [switch]$SkipFirewall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

$ApiService = 'DegreePlanApi'
$ProxyService = 'DegreePlanProxy'
$BackendDir = (Resolve-Path $BackendDir).Path
$Templates = $PSScriptRoot
if (-not $UsersDatabasePath) { $UsersDatabasePath = Join-Path $DataRoot 'data\users.sqlite3' }
if (-not $PlansDatabasePath) { $PlansDatabasePath = Join-Path $DataRoot 'data\plans.sqlite3' }

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Assert-Sha256([string]$Path, [string]$Expected, [string]$What) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    if ($actual -ne $Expected.Trim().ToUpperInvariant()) {
        throw "$What failed the SHA-256 check (expected $Expected, got $actual). Refusing to continue."
    }
}

function Set-StrictAcl {
    <# Replace inherited permissions: Administrators + SYSTEM full control, optional extra principals. #>
    param([string]$Path, [string[]]$Extra = @())
    $grants = @('*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F') + $Extra   # SYSTEM, Administrators
    & icacls.exe $Path /inheritance:r | Out-Null
    foreach ($grant in $grants) { & icacls.exe $Path /grant:r $grant /T /C | Out-Null }
    if ($LASTEXITCODE -ne 0) { throw "icacls failed for $Path" }
}

function ConvertTo-FwdSlash([string]$Path) { return $Path.Replace('\', '/') }

function Expand-Template([string]$Template, [hashtable]$Values) {
    $text = Get-Content -Raw -LiteralPath $Template
    foreach ($key in $Values.Keys) { $text = $text.Replace("@$key@", [string]$Values[$key]) }
    if ($text -match '@[A-Z_]+@') { throw "Unreplaced placeholder in $Template : $($Matches[0])" }
    return $text
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}

# --------------------------------------------------------------------------- preflight
Write-Step 'Preflight checks'
if (-not (Test-Path $WinSWPath)) { throw "WinSW not found: $WinSWPath" }
Assert-Sha256 $WinSWPath $WinSWSha256 'WinSW executable'
if ($CaddyPath) {
    if (-not (Test-Path $CaddyPath)) { throw "Caddy not found: $CaddyPath" }
    if (-not $CaddySha256) { throw '-CaddySha256 is required with -CaddyPath' }
    if (-not $Hostname) { throw '-Hostname is required with -CaddyPath' }
    Assert-Sha256 $CaddyPath $CaddySha256 'Caddy executable'
}
if ($Hostname -and $Hostname -notmatch '^[A-Za-z0-9.-]{1,253}$') { throw "Invalid -Hostname: $Hostname" }
foreach ($dbPath in @($UsersDatabasePath, $PlansDatabasePath)) {
    if (-not [IO.Path]::IsPathRooted($dbPath)) { throw "Database paths must be absolute: $dbPath" }
    if ($dbPath.StartsWith($BackendDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Database files must live outside the source tree: $dbPath"
    }
}
if ($AdoptExistingDatabases -and -not ((Test-Path $UsersDatabasePath) -and (Test-Path $PlansDatabasePath))) {
    throw '-AdoptExistingDatabases requires both database files to exist.'
}

# --------------------------------------------------------------------------- directories
Write-Step 'Creating directories'
$dirs = @{
    Service = Join-Path $InstallRoot 'service'
    Venv    = Join-Path $InstallRoot 'venv'
    Bin     = Join-Path $InstallRoot 'bin'
    Secrets = Join-Path $DataRoot 'secrets'
    Data    = Join-Path $DataRoot 'data'
    Logs    = Join-Path $DataRoot 'logs'
    Backups = Join-Path $DataRoot 'backups'
    Www     = Join-Path $DataRoot 'www'
    Caddy   = Join-Path $DataRoot 'caddy'
}
foreach ($d in $dirs.Values) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
foreach ($p in @($UsersDatabasePath, $PlansDatabasePath)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent) | Out-Null
}

# --------------------------------------------------------------------------- application
Write-Step 'Creating virtual environment and installing hash-pinned dependencies'
& $PythonExe -m venv $dirs.Venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment' }
$venvPython = Join-Path $dirs.Venv 'Scripts\python.exe'
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet --require-hashes -r (Join-Path $BackendDir 'requirements\prod.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed (hash verification is enforced)' }
& $venvPython -m pip install --quiet --no-deps --force-reinstall $BackendDir
if ($LASTEXITCODE -ne 0) { throw 'Application installation failed' }

# --------------------------------------------------------------------------- secrets
Write-Step 'Preparing secrets'
$secretKeyFile = Join-Path $dirs.Secrets 'secret_key'
if (-not (Test-Path $secretKeyFile)) {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $key = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    Write-Utf8NoBom $secretKeyFile $key
    Write-Host '    generated a new session signing key (not displayed)'
} else {
    Write-Host '    existing session signing key kept'
}

# Shared by the service definition and the operator wrapper so they can never drift apart.
$appEnv = [ordered]@{
    APP_ENV            = 'production'
    SECRET_KEY_FILE    = $secretKeyFile
    USERS_DATABASE_URL = 'sqlite:///' + (ConvertTo-FwdSlash $UsersDatabasePath)
    PLANS_DATABASE_URL = 'sqlite:///' + (ConvertTo-FwdSlash $PlansDatabasePath)
    LOG_DIR            = $dirs.Logs
    LOG_FORMAT         = 'json'
    LOG_LEVEL          = 'INFO'
    PROXY_HOPS         = $(if ($CaddyPath) { '1' } else { '0' })
}
# Key rotation window: a retired key stays valid for verifying existing sessions until you delete the file.
$previousKeyFile = Join-Path $dirs.Secrets 'secret_key_previous'
if (Test-Path $previousKeyFile) { $appEnv['SECRET_KEY_FALLBACKS_FILE'] = $previousKeyFile }

# --------------------------------------------------------------------------- services
Write-Step 'Registering the API service'
foreach ($svc in @($ApiService, $ProxyService)) {
    if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
        Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
    }
}
$envBlock = ($appEnv.GetEnumerator() | ForEach-Object {
    '  <env name="{0}" value="{1}"/>' -f $_.Key, [Security.SecurityElement]::Escape([string]$_.Value)
}) -join "`n"
$apiExe = Join-Path $dirs.Service "$ApiService.exe"
Copy-Item -Force $WinSWPath $apiExe
Write-Utf8NoBom (Join-Path $dirs.Service "$ApiService.xml") (Expand-Template `
    (Join-Path $Templates 'degreeplan-api.xml.template') `
    @{ INSTALL_ROOT = $InstallRoot; DATA_ROOT = $DataRoot; ENV_BLOCK = $envBlock })
if (-not (Get-Service -Name $ApiService -ErrorAction SilentlyContinue)) { & $apiExe install }
& sc.exe config $ApiService obj= "NT SERVICE\$ApiService" | Out-Null
& sc.exe sidtype $ApiService unrestricted | Out-Null

if ($CaddyPath) {
    Write-Step 'Registering the reverse proxy (Caddy) service'
    $caddyDir = Join-Path $InstallRoot 'caddy'
    New-Item -ItemType Directory -Force -Path $caddyDir | Out-Null
    Copy-Item -Force $CaddyPath (Join-Path $caddyDir 'caddy.exe')
    Write-Utf8NoBom (Join-Path $caddyDir 'Caddyfile') (Expand-Template `
        (Join-Path $PSScriptRoot '..\caddy\Caddyfile.template') `
        @{ HOSTNAME = $Hostname; DATA_ROOT_FWD = (ConvertTo-FwdSlash $DataRoot) })
    $proxyExe = Join-Path $dirs.Service "$ProxyService.exe"
    Copy-Item -Force $WinSWPath $proxyExe
    Write-Utf8NoBom (Join-Path $dirs.Service "$ProxyService.xml") (Expand-Template `
        (Join-Path $Templates 'degreeplan-proxy.xml.template') `
        @{ INSTALL_ROOT = $InstallRoot; DATA_ROOT = $DataRoot })
    if (-not (Get-Service -Name $ProxyService -ErrorAction SilentlyContinue)) { & $proxyExe install }
    & sc.exe config $ProxyService obj= "NT SERVICE\$ProxyService" | Out-Null
    & sc.exe sidtype $ProxyService unrestricted | Out-Null
}

# --------------------------------------------------------------------------- operator wrapper
Write-Step 'Writing the operator command (bin\degreeplan.ps1)'
$wrapper = @"
#Requires -RunAsAdministrator
# Operator CLI: runs the application's commands with the production environment, e.g.
#   .\degreeplan.ps1 users create alice --role admin
#   .\degreeplan.ps1 db status
`$ErrorActionPreference = 'Stop'
"@
foreach ($entry in $appEnv.GetEnumerator()) {
    $wrapper += "`n`$env:$($entry.Key) = '$($entry.Value.ToString().Replace("'", "''"))'"
}
$wrapper += "`n& '$venvPython' -m flask --app degreeplan.wsgi @args`nexit `$LASTEXITCODE`n"
Write-Utf8NoBom (Join-Path $dirs.Bin 'degreeplan.ps1') $wrapper
$cli = Join-Path $dirs.Bin 'degreeplan.ps1'

# --------------------------------------------------------------------------- permissions
Write-Step 'Applying permissions'
$apiAccount = "NT SERVICE\$ApiService"

# Databases and migrations (run as the installing administrator, before ACLs are tightened).
Write-Step 'Applying database migrations'
if ($AdoptExistingDatabases) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $cli db stamp --target users
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $cli db stamp --target plans
} else {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $cli db upgrade
}
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }

Set-StrictAcl $dirs.Secrets @("${apiAccount}:(OI)(CI)R")                # read-only for the service
Set-StrictAcl $dirs.Data    @("${apiAccount}:(OI)(CI)M")
Set-StrictAcl $dirs.Logs    @("${apiAccount}:(OI)(CI)M")
Set-StrictAcl $dirs.Backups                                               # administrators only
foreach ($dbPath in @($UsersDatabasePath, $PlansDatabasePath)) {
    $dbDir = Split-Path $dbPath -Parent
    if ($dbDir -ne $dirs.Data) { Set-StrictAcl $dbDir @("${apiAccount}:(OI)(CI)M") }
}
if ($CaddyPath) {
    $proxyAccount = "NT SERVICE\$ProxyService"
    Set-StrictAcl $dirs.Caddy @("${proxyAccount}:(OI)(CI)M")
    & icacls.exe $dirs.Logs /grant "${proxyAccount}:(OI)(CI)M" /C | Out-Null   # Caddy access log
    & icacls.exe $dirs.Www /grant "${proxyAccount}:(OI)(CI)RX" /C | Out-Null
}

# --------------------------------------------------------------------------- firewall
if (-not $SkipFirewall -and $CaddyPath) {
    Write-Step 'Opening inbound 80/443 (the API itself listens only on loopback)'
    foreach ($rule in @(@{ N = 'DegreePlan HTTP (redirect/ACME)'; P = 80 }, @{ N = 'DegreePlan HTTPS'; P = 443 })) {
        if (-not (Get-NetFirewallRule -DisplayName $rule.N -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName $rule.N -Direction Inbound -Protocol TCP -LocalPort $rule.P `
                -Action Allow -Profile Domain, Private | Out-Null
        }
    }
}

# --------------------------------------------------------------------------- start + verify
Write-Step 'Starting services'
Start-Service -Name $ApiService
if ($CaddyPath) { Start-Service -Name $ProxyService }

$ready = $false
foreach ($attempt in 1..30) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8000/api/v1/health/ready' -TimeoutSec 3
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) {
    throw "The API did not become ready. Check $($dirs.Logs) and the service log in $($dirs.Logs)."
}

Write-Host ''
Write-Host 'Installation complete. The API is healthy.' -ForegroundColor Green
Write-Host "Next: create the first administrator (prompts for a password):"
Write-Host "    & '$cli' users create <name> --role admin"
Write-Host "Frontend files go in: $($dirs.Www)"
Write-Host 'See docs\DEPLOYMENT.md for verification, backups and upgrades.'
