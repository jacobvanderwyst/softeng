#Requires -Version 5.1
<#
.SYNOPSIS
  One-command local demo of the degree plan API (development mode only).

.DESCRIPTION
  * Creates backend\.venv and installs the hash-pinned dependencies if needed.
  * Creates (or resets) the local databases under backend\instance, loads sample data and prints the
    random demo passwords ONCE. Existing databases and saved plans are preserved by default.
  * On subsequent launches, generates and displays fresh passwords for admin, alice and teacher1 only
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
.PARAMETER AdoptExistingDemo
  Explicitly mark a validated legacy backend\instance database pair as local demo data once.
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
    [ValidateRange(1024, 65535)][int]$Port = 5000,
    [string]$FrontendOrigin,
    [switch]$Reset,
    [switch]$ClearSavedData,
    [switch]$AdoptExistingDemo,
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
  -KeepDemoPasswords         Keep passwords instead of refreshing admin/alice/teacher1.
  -NoRun                     Set up databases/passwords without starting the server.
  -AdoptExistingDemo          Once only: adopt an existing, reviewed backend\instance demo pair.
  -FrontendOrigin <origin>   Allow a separate frontend origin, e.g. http://localhost:5173.
  -ClearSavedData            DELETE ALL students' saved plans, semesters, planned courses
                             and course progress. Keep accounts and catalog.
  -Reset                     DELETE both demo databases and all saved plans/progress,
                             then recreate sample data and accounts. Not a password-recovery option.

QUICK START
  1. Run .\backend\scripts\demo.ps1
  2. Copy the passwords for admin (administrator), alice (student) and teacher1 (instructor).
  3. Open the printed HTTP Frontend URL (IPv4 loopback; default port 5000).
  4. Sign in as alice, create a plan, add semesters/courses, and update My Degree.
  5. Reload to check persistence. Sign out and use teacher1 for read-only student views.
     In the untouched sample database, Alice's student ID is 1.
  6. Choose Administrator sign in with admin to create instructors and edit degree-program requirements.
  Stop the server with Ctrl+C.

PASSWORDS AND DATA
  First setup: all six sample-account passwords are generated and displayed.
  Later launches: only admin, alice and teacher1 get new random passwords unless
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
  Targets are fixed at backend\instance. Database environment overrides are rejected,
  backend\.env is ignored, and linked/reparse targets cannot be adopted or wiped.
  Existing unmarked demo databases require -AdoptExistingDemo once, after reviewing them.

EXAMPLES
  .\backend\scripts\demo.ps1
  .\backend\scripts\demo.ps1 -KeepDemoPasswords
  .\backend\scripts\demo.ps1 -Port 8080
  .\backend\scripts\demo.ps1 -NoRun -KeepDemoPasswords
  .\backend\scripts\demo.ps1 -ClearSavedData
  .\backend\scripts\demo.ps1 -ClearSavedData -KeepDemoPasswords
  .\backend\scripts\demo.ps1 -AdoptExistingDemo -KeepDemoPasswords
  .\backend\scripts\demo.ps1 -FrontendOrigin http://localhost:5173

TROUBLESHOOTING
  "Bad request version" / strange \x16 characters:
    Use HTTP, not HTTPS. If your browser upgrades localhost, try http://127.0.0.1:5000/.
  Lost admin/alice/teacher1 password:
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
$demoHost = [Net.IPAddress]::Loopback.ToString()

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Assert-PlainDemoPath([string]$Path) {
    $itemPath = [IO.Path]::GetFullPath($Path)
    while ($itemPath) {
        if (Test-Path -LiteralPath $itemPath) {
            $item = Get-Item -Force -LiteralPath $itemPath
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Demo paths must not contain symlinks, junctions or reparse points.'
            }
        }
        $parent = Split-Path -Parent $itemPath
        if ($parent -eq $itemPath) { break }
        $itemPath = $parent
    }
}

# Refuse to run against anything that is not a plain local development setup.
foreach ($name in 'USERS_DATABASE_URL', 'PLANS_DATABASE_URL', 'USERS_DATABASE_URL_FILE', 'PLANS_DATABASE_URL_FILE') {
    if (Test-Path "Env:$name") { throw "$name is set in this shell. Unset it first: this script manages its own local databases." }
}
if ($env:APP_ENV -and $env:APP_ENV -ne 'development') {
    throw "APP_ENV is '$($env:APP_ENV)'. This demo script only runs in development mode."
}
if ($FrontendOrigin -and $FrontendOrigin -notmatch '^https?://[A-Za-z0-9.-]+(:\d{1,5})?$') {
    throw "-FrontendOrigin must be an exact origin such as http://localhost:5173 (no path, no wildcard)."
}

Assert-PlainDemoPath $backend
Assert-PlainDemoPath $instance
$managedNames = @(
    'APP_ENV', 'EDUNAV_DEMO', 'CORS_ORIGINS', 'USERS_DATABASE_URL', 'PLANS_DATABASE_URL',
    'USERS_DATABASE_URL_FILE', 'PLANS_DATABASE_URL_FILE', 'SECRET_KEY', 'SECRET_KEY_FILE',
    'SECRET_KEY_FALLBACKS', 'SECRET_KEY_FALLBACKS_FILE', 'COOKIE_SECURE', 'PROXY_HOPS', 'LOG_DIR',
    'FLASK_SKIP_DOTENV', 'FLASK_RUN_HOST', 'FLASK_RUN_PORT', 'FLASK_RUN_CERT', 'FLASK_RUN_KEY',
    'FLASK_DEBUG', 'PYTHONPATH', 'PYTHONHOME'
)
$saved = @{}
foreach ($name in $managedNames) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
Push-Location $backend
try {
    $env:APP_ENV = 'development'
    $env:EDUNAV_DEMO = '1'
    $env:FLASK_SKIP_DOTENV = '1'
    $env:PYTHONPATH = Join-Path $backend 'src'
    $env:COOKIE_SECURE = 'false'
    $env:PROXY_HOPS = '0'
    $env:USERS_DATABASE_URL = 'sqlite:///' + (Join-Path $instance 'users.sqlite3').Replace('\', '/')
    $env:PLANS_DATABASE_URL = 'sqlite:///' + (Join-Path $instance 'plans.sqlite3').Replace('\', '/')
    if ($FrontendOrigin) { $env:CORS_ORIGINS = $FrontendOrigin }
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

    $guardArgs = @('-m', 'degreeplan.devtools.demo_guard')
    if ($AdoptExistingDemo) { $guardArgs += '--adopt-existing' }
    & $venvPython @guardArgs
    if ($LASTEXITCODE -ne 0) { throw 'Demo ownership validation failed; no database wipe or password change was performed.' }

    # ---- Databases
    if ($Reset) {
        & $venvPython -m degreeplan.devtools.demo_guard --check
        if ($LASTEXITCODE -ne 0) { throw 'Refusing to wipe databases without verified demo ownership.' }
        Write-Step 'Resetting the demo databases'
        foreach ($db in 'users', 'plans') {
            foreach ($suffix in '.sqlite3', '.sqlite3-wal', '.sqlite3-shm') {
                $target = Join-Path $instance "$db$suffix"
                Assert-PlainDemoPath $target
                if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
            }
        }
    }
    $usersExist = Test-Path -LiteralPath (Join-Path $instance 'users.sqlite3')
    $plansExist = Test-Path -LiteralPath (Join-Path $instance 'plans.sqlite3')
    if ($usersExist -ne $plansExist) { throw 'Incomplete demo database pair. Back it up before an explicit -Reset.' }
    $fresh = -not $usersExist

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
            Write-Step 'Generating fresh login passwords for admin, alice and teacher1'
            & $venvPython -m flask --app degreeplan.wsgi dev refresh-demo-passwords
            if ($LASTEXITCODE -ne 0) { throw 'Could not refresh demo passwords. No database reset was performed.' }
            Write-Host 'Copy the three passwords above. They change on the next launch unless you use -KeepDemoPasswords.' -ForegroundColor Yellow
        }
    }

    if ($ClearSavedData -and -not $Reset) {
        & $venvPython -m degreeplan.devtools.demo_guard --check
        if ($LASTEXITCODE -ne 0) { throw 'Refusing to clear data without verified demo ownership.' }
        Write-Step 'Clearing ALL saved plans, semesters, planned courses and course progress'
        & $venvPython -m flask --app degreeplan.wsgi dev clear-saved-data --yes
        if ($LASTEXITCODE -ne 0) { throw 'Could not clear saved demo information.' }
    }

    Write-Host ''
    Write-Host "API:        http://${demoHost}:$Port/api/v1/health/ready"
    Write-Host "Frontend:   http://${demoHost}:$Port/"
    if ($FrontendOrigin) { Write-Host "CORS:       allowed for $FrontendOrigin" }
    Write-Host 'Guide:      docs\DEVELOPMENT.md'
    Write-Host ''

    if (-not $NoRun) {
        Write-Step "Starting the API on port $Port (Ctrl+C to stop)"
        & $venvPython -m flask --app degreeplan.wsgi run --host $demoHost --port $Port --no-debugger --no-reload
        if ($LASTEXITCODE -ne 0) { throw 'The demo server stopped with an error. Check the port and preceding message.' }
    }
} finally {
    Pop-Location
    foreach ($key in $saved.Keys) {
        if ($null -eq $saved[$key]) { Remove-Item "Env:$key" -ErrorAction SilentlyContinue } else { Set-Item "Env:$key" $saved[$key] }
    }
}
