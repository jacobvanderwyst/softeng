#Requires -Version 5.1
<#
.SYNOPSIS
  One-command local demo of the degree plan API (development mode only).

.DESCRIPTION
  * Creates backend\.venv and installs the hash-pinned dependencies if needed.
  * Creates (or resets) the local databases under backend\instance, loads sample data and prints the
    random demo passwords ONCE. Existing databases and saved plans are preserved by default.
  * On subsequent launches, generates and displays fresh passwords for alice and teacher1 only
    (unless -KeepDemoPasswords is supplied), revoking their old sessions and clearing their login lockouts.
  * Starts the API on http://localhost:<Port>.

  Nothing here touches production settings. The environment variables it sets are restored on exit.
.PARAMETER Help
  Show the help menu and return without installing packages, changing passwords/data or starting the server.
.PARAMETER Port
  Local HTTP port (default: 5000).
.PARAMETER FrontendOrigin
  Exact origin of an optional separate frontend server, such as http://localhost:5173.
.PARAMETER Reset
  Delete BOTH local demo databases, including saved plans/progress, then recreate sample data and accounts.
.PARAMETER ClearSavedData
  Delete ALL students' saved plans, semesters, planned courses and course progress on startup.
  Keep accounts and the catalog. Use -KeepDemoPasswords too if existing passwords should stay unchanged.
.PARAMETER KeepDemoPasswords
  Keep existing passwords on subsequent launches. Fresh/reset databases still receive generated passwords.
.PARAMETER NoRun
  Prepare the environment/databases/passwords but do not start the server.

.EXAMPLE
  .\scripts\demo.ps1 -Help                            # help only; changes nothing
  .\scripts\demo.ps1 -h                               # short help alias
  .\scripts\demo.ps1                                   # first run, or reuse the existing demo database
  .\scripts\demo.ps1 -Reset                            # wipe the demo database, reseed, new passwords
  .\scripts\demo.ps1 -ClearSavedData                   # clear saved work, keep accounts and catalog
  .\scripts\demo.ps1 -KeepDemoPasswords                # keep manually chosen/existing passwords
  .\scripts\demo.ps1 -FrontendOrigin http://localhost:5173   # allow a separate frontend dev server (CORS)
#>
[CmdletBinding()]
param(
    [int]$Port = 5000,
    [string]$FrontendOrigin,
    [switch]$Reset,
    [switch]$ClearSavedData,
    [switch]$KeepDemoPasswords,
    [switch]$NoRun,
    [Alias('h')]
    [switch]$Help
)

# Help must return before environment checks or ANY setup/password/database side effects.
if ($Help) {
    Write-Output @'
EDUNAV LOCAL DEMO

USAGE (from the repository root)
  .\backend\scripts\demo.ps1 [options]

OPTIONS
  -Help, -h                  Show this menu only. No changes are made.
  -Port <number>             HTTP port (default: 5000).
  -KeepDemoPasswords         Keep existing passwords instead of refreshing alice/teacher1.
  -NoRun                     Set up databases/passwords without starting the server.
  -FrontendOrigin <origin>   Allow a separate frontend origin, e.g. http://localhost:5173.
  -ClearSavedData            DELETE ALL students' saved plans, semesters, planned courses
                             and course progress. Keep accounts and catalog.
  -Reset                     DELETE both demo databases and all saved plans/progress,
                             then recreate sample data and accounts. Not a password-recovery option.

QUICK START
  1. Run .\backend\scripts\demo.ps1
  2. Copy the displayed passwords for alice (student) and teacher1 (teacher).
  3. Open http://localhost:5000/ (use your chosen port if different).
  4. Sign in as alice, create a plan, add semesters/courses, and update My Degree.
  5. Reload to check persistence. Sign out and use teacher1 for read-only student views.
     In the untouched sample database, Alice's student ID is 1.
  Stop the server with Ctrl+C.

PASSWORDS AND DATA
  First setup: all six sample-account passwords are generated and displayed.
  Later launches: only alice and teacher1 get new random passwords unless
  -KeepDemoPasswords is supplied. Their old sessions are revoked and lockouts cleared.
  Saved work is preserved unless -ClearSavedData or -Reset is supplied.
  -ClearSavedData affects ALL students, not just alice. It also runs with -NoRun.
  Add -KeepDemoPasswords to keep passwords too; otherwise normal password refresh still runs.
  Accounts/catalog are preserved by -ClearSavedData. -Reset replaces BOTH databases instead
  and takes precedence when both flags are supplied. Neither option creates a backup.
  Passwords are displayed once per setup/refresh, not saved as plaintext or logged.
  -NoRun still performs password setup; add -KeepDemoPasswords to preserve existing passwords.
  -KeepDemoPasswords does not prevent account/password generation on fresh/reset databases.
  This launcher is for local development only, not production or external databases.

EXAMPLES
  .\backend\scripts\demo.ps1
  .\backend\scripts\demo.ps1 -KeepDemoPasswords
  .\backend\scripts\demo.ps1 -Port 8080
  .\backend\scripts\demo.ps1 -NoRun -KeepDemoPasswords
  .\backend\scripts\demo.ps1 -ClearSavedData
  .\backend\scripts\demo.ps1 -ClearSavedData -KeepDemoPasswords
  .\backend\scripts\demo.ps1 -FrontendOrigin http://localhost:5173

TROUBLESHOOTING
  "Bad request version" / strange \x16 characters:
    Use HTTP, not HTTPS. If your browser upgrades localhost, try http://127.0.0.1:5000/.
  Lost alice/teacher1 password:
    Stop the server, run normally again, and copy the newly displayed passwords.
  Port already in use:
    Stop the previous server or select another port with -Port 8080.
  Custom development database without the demo accounts:
    Use -KeepDemoPasswords; do not use -Reset.

MORE HELP
  Get-Help .\backend\scripts\demo.ps1 -Detailed
  docs\DEVELOPMENT.md

When running from backend, use .\scripts\demo.ps1 instead of .\backend\scripts\demo.ps1.
'@
    return
}
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
        Write-Host 'Existing demo databases kept.' -ForegroundColor Yellow
        if ($KeepDemoPasswords) {
            Write-Host 'Existing passwords kept (-KeepDemoPasswords). No passwords are displayed.' -ForegroundColor Yellow
        } else {
            Write-Step 'Generating fresh login passwords for alice and teacher1'
            & $venvPython -m flask --app degreeplan.wsgi dev refresh-demo-passwords
            if ($LASTEXITCODE -ne 0) { throw 'Could not refresh demo passwords. No database reset was performed.' }
            Write-Host 'Copy the two passwords above. They change on the next launch unless you use -KeepDemoPasswords.' -ForegroundColor Yellow
        }
    }

    if ($ClearSavedData -and -not $Reset) {
        Write-Step 'Clearing ALL saved plans, semesters, planned courses and course progress'
        & $venvPython -m flask --app degreeplan.wsgi dev clear-saved-data --yes
        if ($LASTEXITCODE -ne 0) { throw 'Could not clear saved demo information.' }
    }

    Write-Host ''
    Write-Host "API:        http://localhost:$Port/api/v1/health/ready"
    Write-Host "Frontend:   http://localhost:$Port/"
    if ($FrontendOrigin) { Write-Host "CORS:       allowed for $FrontendOrigin" }
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
