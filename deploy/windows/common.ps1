#Requires -Version 5.1
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this apply operation as Administrator.' }
}

function Enter-Maintenance {
    $mutex = New-Object Threading.Mutex($false, 'Global\EduNavProductionMaintenance')
    try {
        try { $acquired = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw 'Another EduNav operator/maintenance process is active; retry after it finishes.' }
        return $mutex
    } catch { $mutex.Dispose(); throw }
}

function Exit-Maintenance($Mutex) {
    if ($Mutex) { $Mutex.ReleaseMutex(); $Mutex.Dispose() }
}
function Assert-PrivateOwnership([string]$Path) {
    $acl = Get-Acl -LiteralPath (Get-SafePath $Path)
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @('S-1-5-18', 'S-1-5-32-544')) { throw "Path not owned by Administrators/SYSTEM: $Path" }
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        # Services may read code or modify scoped data, but no unprivileged principal
        # may alter the manifest/code path used by an elevated operator.
        $write = [Security.AccessControl.FileSystemRights]::Write -bor
            [Security.AccessControl.FileSystemRights]::Delete -bor
            [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
            [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
            [Security.AccessControl.FileSystemRights]::TakeOwnership
        if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $write) -and
            $sid -notin @('S-1-5-18', 'S-1-5-32-544')) { throw "Untrusted write permissions: $Path" }
    }
}
function Set-PrivateFileAcl([string]$Path, [hashtable]$Grants = @{}) {
    $null = Get-SafePath $Path
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    $all = @{'S-1-5-18' = 'FullControl'; 'S-1-5-32-544' = 'FullControl'}
    foreach ($key in $Grants.Keys) { $all[$key] = $Grants[$key] }
    foreach ($key in $all.Keys) {
        $principal = if ($key -match '^S-1-') { New-Object Security.Principal.SecurityIdentifier($key) } else {
            New-Object Security.Principal.NTAccount($key)
        }
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($principal, $all[$key], 'Allow')))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Get-SafePath([string]$Path) {
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path -match '[\x00-\x1f"<>|?*]' -or
        $Path.Substring(2).Contains(':')) { throw "Expected a local absolute path: $Path" }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if ($full.Length -le 3) { throw 'A drive root is not an application path.' }
    $drive = New-Object IO.DriveInfo($full.Substring(0, 3))
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) { throw 'Production paths must use a local fixed disk, not a mapped/network/removable drive.' }
    $part = $full
    while ($part -and $part.Length -gt 3) {
        if (Test-Path -LiteralPath $part) {
            $item = Get-Item -LiteralPath $part -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point rejected: $part" }
            if ($item.PSObject.Properties.Name -contains 'LinkType' -and $item.LinkType -eq 'HardLink') {
                throw "Hardlinked file rejected: $part"
            }
        }
        $part = Split-Path -Parent $part
    }
    foreach ($segment in $full.Substring(3).Split('\')) {
        if ($segment -match '[. ]$|^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            throw "Ambiguous Windows path: $full"
        }
    }
    return $full
}

function Test-Within([string]$Path, [string]$Root) {
    return $Path.Equals($Root, [StringComparison]::OrdinalIgnoreCase) -or
        $Path.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Assert-Roots([string]$InstallRoot, [string]$DataRoot, [string]$SourceRoot) {
    if ((Test-Within $InstallRoot $DataRoot) -or (Test-Within $DataRoot $InstallRoot)) {
        throw 'InstallRoot and DataRoot must not overlap.'
    }
    foreach ($root in @($InstallRoot, $DataRoot)) {
        foreach ($forbidden in @($env:windir, $env:USERPROFILE, $SourceRoot)) {
            if ($forbidden -and ((Test-Within $root $forbidden) -or (Test-Within $forbidden $root))) {
                throw "Unsafe overlapping root: $root"
            }
        }
        foreach ($broad in @($env:ProgramFiles, $env:ProgramData)) {
            if ($broad -and $root.Equals($broad, [StringComparison]::OrdinalIgnoreCase)) { throw 'Use an application subdirectory.' }
        }
    }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Native process failed: $Executable (exit $LASTEXITCODE)" }
}

function Assert-Hash([string]$Path, [string]$Expected) {
    $null = Get-SafePath $Path
    if ($Expected -notmatch '^[a-fA-F0-9]{64}$') { throw 'A published SHA256 checksum is required.' }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Expected) { throw "Checksum mismatch: $Path" }
}

function New-PrivateAcl([hashtable]$Grants = @{}) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    $all = @{'S-1-5-18' = 'FullControl'; 'S-1-5-32-544' = 'FullControl'}
    foreach ($key in $Grants.Keys) { $all[$key] = $Grants[$key] }
    foreach ($key in $all.Keys) {
        $principal = if ($key -match '^S-1-') { New-Object Security.Principal.SecurityIdentifier($key) } else {
            New-Object Security.Principal.NTAccount($key)
        }
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $principal, $all[$key], 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    return $acl
}

function New-PrivateDirectory([string]$Path) {
    $Path = Get-SafePath $Path
    if (Test-Path -LiteralPath $Path) { throw "Refusing existing directory: $Path" }
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $Path))) { throw "Parent must exist: $Path" }
    $null = [IO.Directory]::CreateDirectory($Path, (New-PrivateAcl))
    Assert-PrivateOwnership $Path
    $actual = Get-Acl -LiteralPath $Path
    if (-not $actual.AreAccessRulesProtected -or @($actual.Access).Count -ne 2) { throw "Private directory ACL verification failed: $Path" }
}

function Write-Json([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
}

function Read-Install([string]$InstallRoot, [switch]$ServiceMode) {
    $root = Get-SafePath $InstallRoot
    $file = Get-SafePath (Join-Path $root 'installation.json')
    Assert-PrivateOwnership $root
    Assert-PrivateOwnership $file
    $m = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
    if ($m.Version -ne 1 -or $m.Product -ne 'EduNavProduction' -or $m.InstallRoot -ne $root -or
        $m.ApiService -ne 'EduNavApi' -or $m.ProxyService -ne 'EduNavProxy') { throw 'Unrecognized installation manifest.' }
    $data = Get-SafePath $m.DataRoot
    Assert-Roots $root $data ''
    Assert-PrivateOwnership $data
    if (-not $ServiceMode) { Assert-PrivateOwnership (Join-Path $data 'backups') }
    Assert-PrivateOwnership (Join-Path $data 'secrets')
    if ($m.AccessMode -notin @('Local', 'LAN', 'Public') -or $m.CertificateMode -notin @('Internal', 'Files', 'ACME') -or
        $m.Hostname -notmatch '^(?=.{1,253}$)[A-Za-z0-9]+([.-][A-Za-z0-9]+)*$' -or
        $m.HttpsPort -lt 1 -or $m.HttpsPort -gt 65535) { throw 'Invalid manifest network settings.' }
    if ($m.AccessMode -eq 'Local' -and ($m.Hostname -ne 'localhost' -or $m.CertificateMode -eq 'ACME')) { throw 'Invalid local TLS settings.' }
    if ($m.AccessMode -ne 'Local' -and (-not @($m.RemoteAddress).Count -or 'Any' -in $m.RemoteAddress)) { throw 'Missing explicit firewall scope.' }
    foreach ($entry in @(@('Python', 'venv\Scripts\python.exe'), @('Users', 'data\users.sqlite3'),
        @('Plans', 'data\plans.sqlite3'), @('Secret', 'secrets\secret_key'))) {
        $base = if ($entry[0] -eq 'Python') { $root } else { $data }
        $expected = Get-SafePath (Join-Path $base $entry[1])
        if ($m.($entry[0]) -ne $expected) { throw "Manifest path mismatch: $($entry[0])" }
    }
    foreach ($exe in @('EduNavApi', 'EduNavProxy')) {
        Assert-Hash (Join-Path $root "service\$exe.exe") $m.WinSWSha256
        $service = Get-CimInstance Win32_Service -Filter "Name='$exe'"
        if (-not $service -or $service.PathName.Trim('"') -ne (Join-Path $root "service\$exe.exe") -or
            $service.StartName -ne "NT SERVICE\$exe") { throw "Service registration/identity does not match manifest: $exe" }
    }
    Assert-Hash (Join-Path $root 'caddy\caddy.exe') $m.CaddySha256
    return $m
}

function Invoke-Managed($Manifest, [string[]]$CliArgs, [string]$Users, [string]$Plans, [switch]$Serve, [switch]$ProxyValidate) {
    # Allowlist instead of a denylist: direct/_FILE config, dotenv, Python injection,
    # FLASK_APP and caller database targets cannot cross the subprocess boundary.
    $saved = @{}
    foreach ($e in Get-ChildItem Env:) { $saved[$e.Name] = $e.Value }
    $location = Get-Location
    try {
        foreach ($e in Get-ChildItem Env:) { Remove-Item "Env:$($e.Name)" -ErrorAction SilentlyContinue }
        foreach ($key in @('SystemRoot', 'windir', 'COMSPEC', 'TEMP', 'TMP', 'PATHEXT')) {
            if ($saved.ContainsKey($key)) { Set-Item "Env:$key" $saved[$key] }
        }
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        $env:APP_ENV = 'production'
        $env:FLASK_SKIP_DOTENV = '1'
        $env:SECRET_KEY_FILE = $Manifest.Secret
        if (-not $Users) { $Users = $Manifest.Users }
        if (-not $Plans) { $Plans = $Manifest.Plans }
        $env:USERS_DATABASE_URL = 'sqlite:///' + $Users.Replace('\', '/')
        $env:PLANS_DATABASE_URL = 'sqlite:///' + $Plans.Replace('\', '/')
        $env:LOG_DIR = Join-Path $Manifest.DataRoot 'logs\api'
        $env:LOG_FORMAT = 'json'
        $env:PROXY_HOPS = '1'
        $env:COOKIE_SECURE = 'true'
        $env:XDG_DATA_HOME = Join-Path $Manifest.DataRoot 'caddy'
        $env:XDG_CONFIG_HOME = Join-Path $Manifest.DataRoot 'caddy'
        Set-Location $Manifest.DataRoot
        if ($ProxyValidate) {
            Invoke-Checked (Join-Path $Manifest.InstallRoot 'caddy\caddy.exe') @('validate', '--config',
                (Join-Path $Manifest.InstallRoot 'caddy\Caddyfile'), '--adapter', 'caddyfile')
        } elseif ($Serve) {
            Invoke-Checked $Manifest.Python @('-I', '-m', 'waitress', '--listen=localhost:8000',
                '--threads=8', '--channel-timeout=30', '--max-request-body-size=1048576',
                '--no-clear-untrusted-proxy-headers', 'degreeplan.wsgi:app')
        } else {
            Invoke-Checked $Manifest.Python (@('-I', '-m', 'flask', '--app', 'degreeplan.wsgi') + $CliArgs)
        }
    } finally {
        Set-Location $location
        foreach ($e in Get-ChildItem Env:) { Remove-Item "Env:$($e.Name)" -ErrorAction SilentlyContinue }
        foreach ($key in $saved.Keys) { Set-Item "Env:$key" $saved[$key] }
    }
}

function Stop-OwnedServices($m) {
    foreach ($name in @($m.ProxyService, $m.ApiService)) {
        $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -ne 'Stopped') {
            Stop-Service -Name $name -ErrorAction Stop
            (Get-Service $name).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
        }
    }
    # SCM Stopped alone does not prove that WinSW terminated a spawned Python
    # process. Refuse paired snapshots/replacement while any API-port writer
    # could remain alive. Enumeration failures also fail closed.
    $listeners = @(Get-NetTCPConnection -ErrorAction Stop | Where-Object {
        $_.LocalPort -eq 8000 -and $_.State -eq 'Listen'
    })
    if ($listeners.Count) {
        throw 'API port 8000 is still listening after services stopped; possible orphan Waitress process. Inspect it manually before retrying maintenance.'
    }
}
