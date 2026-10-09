#Requires -Version 5.1
<#
.SYNOPSIS
  One-command local demo of the degree plan API (development mode only).

.DESCRIPTION
  * Creates backend\.venv and installs the hash-pinned dependencies if needed.
  * Creates (or resets) the local databases under backend\instance, loads sample data and prints the
    random demo passwords ONCE.
  * Starts the API on http://localhost:<Port>.

  Nothing here touches production settings. The environment variables it sets are restored on exit.

.EXAMPLE
  .\scripts\demo.ps1                                   # first run, or reuse the existing demo database
  .\scripts\demo.ps1 -Reset                            # wipe the demo database, reseed, new passwords
  .\scripts\demo.ps1 -FrontendOrigin http://localhost:5173   # allow a separate frontend dev server (CORS)
#>
[CmdletBinding()]
param(
    [int]$Port = 5000,
    [string]$FrontendOrigin,
    [switch]$Reset,
    [switch]$NoRun
)

$ErrorActionPreference = 'Stop'
$backend = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
$instance = Join-Path $backend 'instance'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

# Refuse to run against anything that is not a plain local development setup.
foreach ($name in 'USERS_DATABASE_URL', 'PLANS_DATABASE_URL') {
    if (Test-Path "Env:$name") { throw "$name is set in this shell. Unset it first: this script manages its own local databases." }
}
if ($env:APP_ENV -and $env:APP_ENV -ne 'development') {
    throw "APP_ENV is '$($env:APP_ENV)'. This demo script only runs in development mode."
}
if ($FrontendOrigin -and $FrontendOrigin -notmatch '^https?://[A-Za-z0-9.-]+(:\d{1,5})?$') {
    throw "-FrontendOrigin must be an exact origin such as http://localhost:5173 (no path, no wildcard)."
}

$saved = @{ APP_ENV = $env:APP_ENV; CORS_ORIGINS = $env:CORS_ORIGINS }
Push-Location $backend
try {
    # ---- Python environment
    if (-not (Test-Path $venvPython)) {
        Write-Step 'Creating virtual environment (.venv)'
        $python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { $null }
        if (-not $python) { throw 'Python 3.11+ was not found on PATH. Install it from python.org and re-run.' }
        & $python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
    }
    & $venvPython -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }

    & $venvPython -c "import degreeplan" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Step 'Installing dependencies (hash-pinned)'
        & $venvPython -m pip install --quiet --require-hashes -r requirements\dev.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & $venvPython -m pip install --quiet --no-deps -e .
        if ($LASTEXITCODE -ne 0) { throw 'Installing the application failed.' }
    }

    $env:APP_ENV = 'development'
    if ($FrontendOrigin) { $env:CORS_ORIGINS = $FrontendOrigin }

    # ---- Databases
    if ($Reset) {
        Write-Step 'Resetting the demo databases'
        foreach ($db in 'users', 'plans') {
            foreach ($suffix in '.sqlite3', '.sqlite3-wal', '.sqlite3-shm') {
                Remove-Item -ErrorAction SilentlyContinue -Force (Join-Path $instance "$db$suffix")
            }
        }
    }
    $fresh = -not (Test-Path (Join-Path $instance 'users.sqlite3'))

    Write-Step 'Applying database migrations'
    & $venvPython -m flask --app degreeplan.wsgi db upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }

    if ($fresh) {
        Write-Step 'Loading sample data'
        $seed = & $venvPython -m flask --app degreeplan.wsgi dev seed
        if ($LASTEXITCODE -ne 0) { throw 'Loading sample data failed.' }
        Write-Host ''
        Write-Host 'DEMO ACCOUNTS (random passwords, shown ONCE: copy them now)' -ForegroundColor Yellow
        $seed | Where-Object { $_ -match '^\s+\w+\s+\S+$' } | ForEach-Object { Write-Host "  $_" }
        Write-Host '  roles: admin, teacher1/teacher2 (teachers), alice/bob (teacher1 advisees), carol (teacher2 advisee)'
    } else {
        Write-Host 'Existing demo database kept. Run with -Reset to recreate it and get new passwords.' -ForegroundColor Yellow
    }

    Write-Host ''
    Write-Host "API:        http://localhost:$Port/api/v1/health/ready"
    Write-Host "Frontend:   $(if ($FrontendOrigin) { "CORS allowed for $FrontendOrigin" } else { 'same-origin only (use -FrontendOrigin <origin> for a separate dev server)' })"
    Write-Host 'Guide:      docs\DEVELOPMENT.md'
    Write-Host ''

    if (-not $NoRun) {
        Write-Step "Starting the API on port $Port (Ctrl+C to stop)"
        & $venvPython -m flask --app degreeplan.wsgi run --port $Port
    }
} finally {
    Pop-Location
    foreach ($key in $saved.Keys) {
        if ($null -eq $saved[$key]) { Remove-Item "Env:$key" -ErrorAction SilentlyContinue } else { Set-Item "Env:$key" $saved[$key] }
    }
}
