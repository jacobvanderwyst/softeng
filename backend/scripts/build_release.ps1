#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Help,
    [string]$OutDir,
    [string]$PythonExe = (Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe')
)
if ($Help) {
    'build_release.ps1 [-OutDir <directory>] [-PythonExe <trusted development Python>] [-WhatIf]'
    'Builds an application wheel without downloading build dependencies, packages deployment scripts/quickstarts/production lock, and writes SHA256 checksums.'
    'Uses the development environment; no services, databases, firewall, certificate trust or machine policy changes.'
    'Output defaults to repository dist/. Supply separately verified WinSW/Caddy binaries to the target installer.'
    return
}
$ErrorActionPreference = 'Stop'
$backend = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $backend
if (-not $OutDir) { $OutDir = Join-Path $repo 'dist' }
$OutDir = [IO.Path]::GetFullPath($OutDir)
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Development Python not found. Prepare backend\.venv first.' }
$version = & $PythonExe -I -c "import sys,pathlib,tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())['project']['version'])" (Join-Path $backend 'pyproject.toml')
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw 'Could not read release version.' }
$name = "EduNav-$version-" + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$destination = Join-Path $OutDir $name
if (-not $PSCmdlet.ShouldProcess($destination, 'Build release wheel, deployment bundle and checksum manifests')) { return }
$null = New-Item -ItemType Directory -Force -Path $OutDir
$null = New-Item -ItemType Directory -Path $destination
$staging = Join-Path ([IO.Path]::GetTempPath()) ('edunav-build-' + [Guid]::NewGuid().ToString('N'))
$stageSource = @'
import pathlib, shutil, sys
source, target = map(pathlib.Path, sys.argv[1:])
target.mkdir()
shutil.copy2(source / "pyproject.toml", target / "pyproject.toml")
for path in (source / "src").rglob("*"):
    if not path.is_file() or path.suffix not in {".py", ".js", ".css", ".html", ".mako", ".ini"}:
        continue
    if "__pycache__" in path.parts or any(part.endswith(".egg-info") for part in path.parts):
        continue
    relative = path.relative_to(source)
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
'@
try {
    $stageSource | & $PythonExe -I - $backend $staging
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage clean application sources.' }
    & $PythonExe -I -m pip --isolated wheel --no-deps --no-build-isolation --wheel-dir $destination $staging
    if ($LASTEXITCODE -ne 0) { throw "Wheel build failed. Artifacts retained at $destination. Check development setuptools/wheel." }
} finally {
    # Only this newly generated build staging directory is removed, never checkout/data files.
    if (Test-Path -LiteralPath $staging) { [IO.Directory]::Delete($staging, $true) }
}
$wheels = @(Get-ChildItem -LiteralPath $destination -Filter '*.whl' -File)
if ($wheels.Count -ne 1) { throw 'Expected exactly one application wheel.' }
$verifyWheel = @'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as wheel:
    names = wheel.namelist()
    if any("/__pycache__/" in name or name.endswith((".pyc", ".sqlite3", ".db", ".key", ".pem", "/.env")) for name in names):
        raise RuntimeError("Unexpected generated/private files in production wheel")
    for required in ("frontend/app.js", "frontend/login.html", "frontend/register.html", "frontend/admin.html", "frontend/admin.js",
                     "migrations/plans/versions/plans_0003_progress_grade.py"):
        if not any(name.endswith(required) for name in names):
            raise RuntimeError("Required frontend/migration asset missing")
'@
$verifyWheel | & $PythonExe -I - $wheels[0].FullName
if ($LASTEXITCODE -ne 0) { throw 'Production wheel content verification failed; no release archive was created.' }
$lockDestination = Join-Path $destination 'backend\requirements'
$null = New-Item -ItemType Directory -Force -Path $lockDestination
Copy-Item -LiteralPath (Join-Path $backend 'requirements\prod.txt') -Destination (Join-Path $lockDestination 'prod.txt')
$helperDestination = Join-Path $destination 'backend\scripts'
$null = New-Item -ItemType Directory -Path $helperDestination
Copy-Item -LiteralPath (Join-Path $backend 'scripts\bulk_create_users.ps1') -Destination $helperDestination
foreach ($folder in @('windows', 'caddy')) {
    $source = Join-Path $repo "deploy\$folder"
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { continue }
    $target = Join-Path $destination "deploy\$folder"
    $null = New-Item -ItemType Directory -Force -Path $target
    foreach ($file in Get-ChildItem -LiteralPath $source -File) {
        if ($file.Extension -in @('.ps1', '.template')) { Copy-Item -LiteralPath $file.FullName -Destination $target }
    }
}
$docs = Join-Path $destination 'docs'
$null = New-Item -ItemType Directory -Path $docs
foreach ($file in @('WINDOWS_SERVER_QUICKSTART.md', 'WINDOWS11_QUICKSTART.md', 'WINDOWS11_DEMO.md',
                    'DEPLOYMENT.md', 'OPERATIONS.md', 'ARCHITECTURE.md', 'DEVELOPMENT.md')) {
    Copy-Item -LiteralPath (Join-Path $repo "docs\$file") -Destination $docs
}
$null = New-Item -ItemType Directory -Path (Join-Path $docs 'examples')
foreach ($file in @('catalog-v1.example.json', 'cj-coms-foundation.json', 'api-client.js', 'import-catalog.sql')) {
    Copy-Item -LiteralPath (Join-Path $repo "docs\examples\$file") -Destination (Join-Path $docs 'examples')
}
Copy-Item -LiteralPath (Join-Path $repo 'README.md') -Destination $destination
$metadata = [ordered]@{
    Product = 'EduNav'; Version = $version; CreatedUtc = [DateTime]::UtcNow.ToString('o')
    Wheel = $wheels[0].Name; WheelSha256 = (Get-FileHash -LiteralPath $wheels[0].FullName -Algorithm SHA256).Hash
    FrontendRevision = '7db3e17f7ef14469df4ba50b6a3090c87a99a395'
    DatabaseMode = 'Fresh separate production SQLite databases; no demo data bundled'
    WindowsServerAcceptance = 'Required on staging; build does not certify host deployment'
}
$utf8 = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $destination 'release.json'), ($metadata | ConvertTo-Json), $utf8)
$checksums = foreach ($file in Get-ChildItem -LiteralPath $destination -Recurse -File | Sort-Object FullName) {
    $relative = $file.FullName.Substring($destination.Length + 1).Replace('\', '/')
    '{0}  {1}' -f (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash, $relative
}
[IO.File]::WriteAllLines((Join-Path $destination 'SHA256SUMS'), [string[]]$checksums, $utf8)
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = Join-Path $OutDir ($name + '.zip')
[IO.Compression.ZipFile]::CreateFromDirectory($destination, $archive)
$archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
[IO.File]::WriteAllText(($archive + '.sha256'), $archiveHash + '  ' + [IO.Path]::GetFileName($archive) + "`n", $utf8)
Write-Output "Release directory: $destination"
Write-Output "Release archive: $archive"
Write-Output "Wheel SHA256: $($metadata.WheelSha256)"
Write-Output 'Checksums detect changes; distribute the checksum manifest through a trusted channel. They are not a digital signature.'
