#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Help,
    [ValidateSet('Status', 'Start', 'Stop', 'Migrate', 'Backup', 'Restore')][string]$Action = 'Status',
    [string]$InstallRoot = 'C:\Program Files\EduNav', [string]$BackupSet, [switch]$LeaveStopped
)
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Help) {
    'manage.ps1 -Action Status|Start|Stop|Migrate|Backup|Restore [-InstallRoot <root>] [-BackupSet <owned backup directory>] [-LeaveStopped] [-WhatIf]'
    'Backup quiesces both services, creates paired SHA256 manifest and verifies schema/integrity; no retention deletion or automatic scheduling.'
    'Restore accepts only paired backups under the manifest-owned backups directory; retains recovery copies, stops services, verifies before replacement, revokes sessions.'
    return
}
$m = Read-Install $InstallRoot
if ($Action -eq 'Status') {
    $lock = Enter-Maintenance
    try {
        Get-Service -Name $m.ApiService, $m.ProxyService
        Invoke-Managed $m @('db', 'status')
    } finally { Exit-Maintenance $lock }
    return
}
if (-not $PSCmdlet.ShouldProcess($m.DataRoot, $Action)) { return }
Assert-Administrator
$lock = Enter-Maintenance
try {
$running = @{}
foreach ($name in @($m.ApiService, $m.ProxyService)) {
    $running[$name] = (Get-Service -Name $name).Status -eq 'Running'
}

function New-PairedBackup {
    $root = Get-SafePath (Join-Path $m.DataRoot 'backups')
    $dest = Join-Path $root ('set-' + [Guid]::NewGuid().ToString('N'))
    New-PrivateDirectory $dest
    foreach ($kind in @('users', 'plans')) {
        Invoke-Managed $m @('db', 'validate-schema', '--target', $kind, '--revision', 'current') | Out-Host
    }
    Invoke-Managed $m @('db', 'backup', '--dest', $dest) | Out-Host
    $files = @()
    foreach ($kind in @('users', 'plans')) {
        $found = @(Get-ChildItem -LiteralPath $dest -Filter "$kind-*.sqlite3" -File)
        if ($found.Count -ne 1) { throw "Expected exactly one $kind snapshot." }
        Set-PrivateFileAcl $found[0].FullName
        $files += [ordered]@{ Kind = $kind; Name = $found[0].Name; Sha256 = (Get-FileHash -LiteralPath $found[0].FullName -Algorithm SHA256).Hash }
    }
    $pair = [ordered]@{
        Version = 1; Product = 'EduNavProductionBackup'; InstallRoot = $m.InstallRoot; DataRoot = $m.DataRoot
        CreatedUtc = [DateTime]::UtcNow.ToString('o'); Quiesced = $true; Files = $files
    }
    Write-Json (Join-Path $dest 'backup.json') $pair
    Set-PrivateFileAcl (Join-Path $dest 'backup.json')
    return $dest
}

function Read-PairedBackup([string]$Directory) {
    $dir = Get-SafePath $Directory
    $root = Get-SafePath (Join-Path $m.DataRoot 'backups')
    if (-not (Test-Within $dir $root) -or $dir -eq $root) { throw 'Restore input must be an owned backup set directory.' }
    Assert-PrivateOwnership $dir
    $manifest = Get-SafePath (Join-Path $dir 'backup.json')
    Assert-PrivateOwnership $manifest
    $pair = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    if ($pair.Version -ne 1 -or $pair.Product -ne 'EduNavProductionBackup' -or
        $pair.InstallRoot -ne $m.InstallRoot -or $pair.DataRoot -ne $m.DataRoot -or
        $pair.Quiesced -ne $true -or @($pair.Files).Count -ne 2) { throw 'Invalid paired backup manifest.' }
    $paths = @{}
    foreach ($file in $pair.Files) {
        if ($file.Kind -notin @('users', 'plans') -or $paths.ContainsKey($file.Kind) -or
            $file.Name -notmatch ('^' + $file.Kind + '-[0-9]{8}T[0-9]{6}Z\.sqlite3$')) { throw 'Invalid backup entry.' }
        $path = Get-SafePath (Join-Path $dir $file.Name)
        Assert-PrivateOwnership $path
        Assert-Hash $path $file.Sha256
        $paths[$file.Kind] = $path
    }
    return $paths
}

    if ($Action -eq 'Start') {
        Invoke-Managed $m @('db', 'verify')
        Start-Service $m.ApiService
        Start-Service $m.ProxyService
        return
    }
    Stop-OwnedServices $m
    switch ($Action) {
        'Stop' { return }
        'Backup' {
            $result = New-PairedBackup
            "Paired quiesced backup: $result"
        }
        'Migrate' {
            $result = New-PairedBackup
            "Pre-migration backup: $result"
            Invoke-Managed $m @('db', 'upgrade')
            Invoke-Managed $m @('db', 'verify')
        }
        'Restore' {
            if (-not $BackupSet) { throw 'Restore requires -BackupSet.' }
            $paths = Read-PairedBackup $BackupSet
            # Validate isolated copies, not the only backup; verification may create SQLite sidecars.
            $stage = Join-Path (Join-Path $m.DataRoot 'backups') ('restore-' + [Guid]::NewGuid().ToString('N'))
            New-PrivateDirectory $stage
            foreach ($kind in @('users', 'plans')) { Copy-Item -LiteralPath $paths[$kind] -Destination (Join-Path $stage "$kind.sqlite3") }
            foreach ($kind in @('users', 'plans')) {
                Invoke-Managed $m @('db', 'validate-schema', '--target', $kind, '--revision', 'current') -Users (Join-Path $stage 'users.sqlite3') -Plans (Join-Path $stage 'plans.sqlite3')
            }
            Invoke-Managed $m @('db', 'upgrade') -Users (Join-Path $stage 'users.sqlite3') -Plans (Join-Path $stage 'plans.sqlite3')
            Invoke-Managed $m @('db', 'verify') -Users (Join-Path $stage 'users.sqlite3') -Plans (Join-Path $stage 'plans.sqlite3')
            # Never copy only a main SQLite file after staged migrations: its latest
            # changes may still be in WAL. Use SQLite's backup API for the staged pair.
            $migrated = Join-Path $stage 'migrated'
            New-PrivateDirectory $migrated
            Invoke-Managed $m @('db', 'backup', '--dest', $migrated) -Users (Join-Path $stage 'users.sqlite3') -Plans (Join-Path $stage 'plans.sqlite3')
            $ready = @{}
            foreach ($kind in @('users', 'plans')) {
                $found = @(Get-ChildItem -LiteralPath $migrated -Filter "$kind-*.sqlite3" -File)
                if ($found.Count -ne 1) { throw 'Staged snapshot pair missing.' }
                Set-PrivateFileAcl $found[0].FullName
                $ready[$kind] = Get-SafePath $found[0].FullName
            }
            $recovery = New-PairedBackup
            try {
                # Preserve all live files/sidecars before replacing only these fixed owned paths.
                foreach ($path in @($m.Users, $m.Plans)) {
                    foreach ($suffix in @('', '-wal', '-shm', '-journal')) {
                        $live = Get-SafePath ($path + $suffix)
                        if (Test-Path -LiteralPath $live) {
                            Move-Item -LiteralPath $live -Destination (Join-Path $recovery ('live-' + [IO.Path]::GetFileName($live)))
                        }
                    }
                }
                Copy-Item -LiteralPath $ready.users -Destination $m.Users
                Copy-Item -LiteralPath $ready.plans -Destination $m.Plans
                foreach ($path in @($m.Users, $m.Plans)) { Set-PrivateFileAcl $path @{'NT SERVICE\EduNavApi' = 'Modify'} }
                Invoke-Managed $m @('db', 'verify')
                Invoke-Managed $m @('users', 'revoke-all-sessions', '--yes')
            } catch {
                $restoreError = $_
                # Failed restore artifacts are retained. Roll back the complete paired pre-restore
                # snapshots; never try to combine a pre-restore database with restored WAL files.
                foreach ($path in @($m.Users, $m.Plans)) {
                    foreach ($suffix in @('', '-wal', '-shm', '-journal')) {
                        $live = Get-SafePath ($path + $suffix)
                        if (Test-Path -LiteralPath $live) {
                            Move-Item -LiteralPath $live -Destination (Join-Path $stage ('failed-' + [IO.Path]::GetFileName($live))) -Force
                        }
                    }
                }
                $before = Read-PairedBackup $recovery
                Copy-Item -LiteralPath $before.users -Destination $m.Users -Force
                Copy-Item -LiteralPath $before.plans -Destination $m.Plans -Force
                foreach ($path in @($m.Users, $m.Plans)) { Set-PrivateFileAcl $path @{'NT SERVICE\EduNavApi' = 'Modify'} }
                throw "Restore failed; recovery snapshots restored, services remain stopped. Recovery: $recovery. $restoreError"
            }
            "Restore verified; all restored sessions revoked. Recovery retained: $recovery"
        }
    }
    if (-not $LeaveStopped) {
        foreach ($name in @($m.ApiService, $m.ProxyService)) { if ($running[$name]) { Start-Service $name } }
    }
} catch {
    # Deliberately fail closed: never restart writers after failed maintenance.
    $failure = $_
    try { Stop-OwnedServices $m } catch { Write-Warning 'Could not stop services after failed maintenance; inspect service state immediately.' }
    throw $failure
} finally { Exit-Maintenance $lock }
