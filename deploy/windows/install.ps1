#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Help, [switch]$Preflight, [switch]$RenderOnly,
    [string]$InstallRoot = 'C:\Program Files\EduNav',
    [string]$DataRoot = 'C:\ProgramData\EduNav\production',
    [string]$BackendDir = (Join-Path $PSScriptRoot '..\..\backend'),
    [string]$PythonExe = 'python',
    [string]$WinSWPath, [string]$WinSWSha256,
    [string]$CaddyPath, [string]$CaddySha256,
    [string]$WheelPath, [string]$WheelSha256,
    [switch]$EnableRegistration,
    [ValidateSet('Local', 'LAN', 'Public')][string]$AccessMode = 'Local',
    [ValidateSet('Internal', 'Files', 'ACME')][string]$CertificateMode = 'Internal',
    [string]$Hostname = 'localhost', [ValidateRange(1, 65535)][int]$HttpsPort = 8443,
    [string[]]$RemoteAddress = @(), [string]$CertificatePath, [string]$PrivateKeyPath
)
$registrationChoiceSpecified = $PSBoundParameters.ContainsKey('EnableRegistration')
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Help) {
    'install.ps1 [-Preflight|-RenderOnly|-WhatIf] -WinSWPath <exe> -WinSWSha256 <sha256> -CaddyPath <exe> -CaddySha256 <sha256> -WheelPath <whl> -WheelSha256 <sha256>'
    'Defaults: Local, Internal CA, https://localhost:8443; no automatic trust. LAN/Public require -Hostname and -RemoteAddress.'
    'Files requires -CertificatePath/-PrivateKeyPath. Public ACME requires -HttpsPort 443 and reachable DNS/challenge port 80.'
    'Existing DegreePlan services are never adopted. Existing owned EduNav installation is backed up before upgrading.'
    'Student self-registration is enabled by default. -EnableRegistration:$false opts out. Upgrades preserve explicit stored choices unless changed.'
    return
}
$InstallRoot = Get-SafePath $InstallRoot
$DataRoot = Get-SafePath $DataRoot
$BackendDir = Get-SafePath $BackendDir
Assert-Roots $InstallRoot $DataRoot (Split-Path -Parent $BackendDir)
if ($Hostname -notmatch '^(?=.{1,253}$)[A-Za-z0-9]+([.-][A-Za-z0-9]+)*$') { throw 'Invalid hostname.' }
if ($AccessMode -eq 'Local' -and ($Hostname -ne 'localhost' -or $CertificateMode -eq 'ACME')) { throw 'Local requires localhost and Internal/Files TLS.' }
if ($AccessMode -ne 'Local' -and ($Hostname -eq 'localhost' -or -not $RemoteAddress.Count)) { throw 'LAN/Public require hostname and explicit firewall RemoteAddress scope.' }
foreach ($scope in $RemoteAddress) {
    if ($scope -notmatch '^[A-Za-z0-9.:/-]+$' -or $scope -eq 'Any') { throw 'Use explicit IP/CIDR or LocalSubnet firewall scopes, not Any.' }
}
if ($CertificateMode -eq 'ACME' -and ($AccessMode -ne 'Public' -or $HttpsPort -ne 443)) { throw 'ACME requires Public on 443.' }
if ($CertificateMode -eq 'Files') {
    $CertificatePath = Get-SafePath $CertificatePath
    $PrivateKeyPath = Get-SafePath $PrivateKeyPath
    foreach ($file in @($CertificatePath, $PrivateKeyPath)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing TLS input: $file" }
    }
}
$m = [pscustomobject]@{
    Version = 1; Product = 'EduNavProduction'; InstallRoot = $InstallRoot; DataRoot = $DataRoot
    ApiService = 'EduNavApi'; ProxyService = 'EduNavProxy'
    Python = Join-Path $InstallRoot 'venv\Scripts\python.exe'
    Users = Join-Path $DataRoot 'data\users.sqlite3'; Plans = Join-Path $DataRoot 'data\plans.sqlite3'
    Secret = Join-Path $DataRoot 'secrets\secret_key'
    WinSWSha256 = $WinSWSha256; CaddySha256 = $CaddySha256
    WheelSha256 = $WheelSha256; InstalledUtc = [DateTime]::UtcNow.ToString('o')
    RegistrationEnabled = (Resolve-RegistrationPolicy -Previous $null -Requested ([bool]$EnableRegistration) -Explicit $registrationChoiceSpecified)
    Hostname = $Hostname; HttpsPort = $HttpsPort; AccessMode = $AccessMode
    CertificateMode = $CertificateMode; RemoteAddress = $RemoteAddress
}
function Xml([string]$s) { [Security.SecurityElement]::Escape($s) }
$bind = if ($AccessMode -eq 'Local') { 'bind localhost' } else { '' }
$tls = switch ($CertificateMode) {
    'Internal' { 'tls internal' }
    'Files' { 'tls "' + (Join-Path $DataRoot 'secrets\tls.crt').Replace('\', '/') + '" "' + (Join-Path $DataRoot 'secrets\tls.key').Replace('\', '/') + '"' }
    'ACME' { '' }
}
$globalTls = if ($CertificateMode -eq 'ACME') { '' } else { 'auto_https disable_redirects' }
$caddy = @"
{
    admin off
    skip_install_trust
    $globalTls
}
https://${Hostname}:$HttpsPort {
    $bind
    $tls
    encode zstd gzip
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
        -Server
    }
    request_body {
        max_size 1MB
    }
    reverse_proxy localhost:8000
}
"@
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$apiXml = @"
<service>
  <id>EduNavApi</id><name>EduNav API</name>
  <executable>$(Xml $shell)</executable>
  <arguments>-NoProfile -NonInteractive -ExecutionPolicy Bypass -File &quot;$(Xml (Join-Path $InstallRoot 'bin\serve.ps1'))&quot;</arguments>
  <workingdirectory>$(Xml $DataRoot)</workingdirectory>
  <startmode>Automatic</startmode><stoptimeout>60 sec</stoptimeout>
  <onfailure action="restart" delay="30 sec"/><resetfailure>1 hour</resetfailure>
  <logpath>$(Xml (Join-Path $DataRoot 'logs\api'))</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
$proxyXml = @"
<service>
  <id>EduNavProxy</id><name>EduNav HTTPS proxy</name>
  <executable>$(Xml (Join-Path $InstallRoot 'caddy\caddy.exe'))</executable>
  <arguments>run --config &quot;$(Xml (Join-Path $InstallRoot 'caddy\Caddyfile'))&quot; --adapter caddyfile</arguments>
  <workingdirectory>$(Xml (Join-Path $DataRoot 'caddy'))</workingdirectory>
  <env name="XDG_DATA_HOME" value="$(Xml (Join-Path $DataRoot 'caddy'))"/>
  <env name="XDG_CONFIG_HOME" value="$(Xml (Join-Path $DataRoot 'caddy'))"/>
  <startmode>Automatic</startmode><stoptimeout>60 sec</stoptimeout>
  <onfailure action="restart" delay="30 sec"/><resetfailure>1 hour</resetfailure>
  <logpath>$(Xml (Join-Path $DataRoot 'logs\proxy'))</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
if ($RenderOnly) {
    [pscustomobject]@{ Manifest = $m; Caddyfile = $caddy; ApiXml = $apiXml; ProxyXml = $proxyXml }
    return
}
Assert-Hash $WinSWPath $WinSWSha256
Assert-Hash $CaddyPath $CaddySha256
Assert-Hash $WheelPath $WheelSha256
if ([IO.Path]::GetExtension($WheelPath) -ne '.whl') { throw 'WheelPath must be a built .whl artifact.' }
$os = Get-CimInstance Win32_OperatingSystem
if (($os.ProductType -eq 1 -and [int]$os.BuildNumber -lt 22000) -or
    ($os.ProductType -ne 1 -and [int]$os.BuildNumber -lt 17763)) { throw 'Require Windows 11 or Server 2019/2022/2025.' }
foreach ($legacy in @('DegreePlanApi', 'DegreePlanProxy')) {
    if (Get-Service $legacy -ErrorAction SilentlyContinue) { throw "Legacy service $legacy detected; no automatic adoption." }
}
$existing = Test-Path -LiteralPath (Join-Path $InstallRoot 'installation.json')
if ($existing) {
    $old = Read-Install $InstallRoot
    $m.RegistrationEnabled = Resolve-RegistrationPolicy -Previous $old -Requested ([bool]$EnableRegistration) `
        -Explicit $registrationChoiceSpecified
    foreach ($key in @('DataRoot', 'Hostname', 'HttpsPort', 'AccessMode', 'CertificateMode')) {
        if ($old.$key -ne $m.$key) { throw "Upgrade cannot silently change $key." }
    }
} else {
    foreach ($root in @($InstallRoot, $DataRoot)) {
        if (Test-Path -LiteralPath $root) { throw "Unowned root already exists: $root" }
        if (-not (Test-Path -LiteralPath (Split-Path -Parent $root))) {
            # Default production parent is also application-owned and created privately.
            if ($root -ne $DataRoot -or (Split-Path -Leaf (Split-Path -Parent $root)) -ne 'EduNav' -or
                -not (Test-Path -LiteralPath (Split-Path -Parent (Split-Path -Parent $root)))) { throw "Parent missing: $root" }
        }
    }
    foreach ($svc in @('EduNavApi', 'EduNavProxy')) {
        if (Get-Service $svc -ErrorAction SilentlyContinue) { throw "Unowned service collision: $svc" }
    }
}
foreach ($port in @($HttpsPort, 8000) + $(if ($CertificateMode -eq 'ACME') { @(80) } else { @() })) {
    if (-not $existing -and (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { throw "Port occupied: $port" }
}
if (-not (Test-Path (Join-Path $BackendDir 'requirements\prod.txt'))) { throw 'Missing hash-pinned dependency lock.' }
if ($Preflight) { 'Preflight passed (no changes made).'; return }
if (-not $PSCmdlet.ShouldProcess("$InstallRoot / $DataRoot", 'Install or upgrade production services and scoped firewall rules')) { return }
Assert-Administrator
$lock = Enter-Maintenance
try {
if ($existing) {
    Stop-OwnedServices $old
    & (Join-Path $PSScriptRoot 'manage.ps1') -InstallRoot $InstallRoot -Action Backup -LeaveStopped
    if (-not $?) { throw 'Pre-upgrade backup failed; services remain stopped.' }
    $recovery = Join-Path $DataRoot ('backups\code-' + [Guid]::NewGuid().ToString('N'))
    New-PrivateDirectory $recovery
    Copy-Item -LiteralPath $InstallRoot -Destination (Join-Path $recovery 'installation') -Recurse
} else {
    New-PrivateDirectory $InstallRoot
    $parent = Split-Path -Parent $DataRoot
    if (-not (Test-Path -LiteralPath $parent)) { New-PrivateDirectory $parent }
    New-PrivateDirectory $DataRoot
    foreach ($dir in @('data', 'secrets', 'logs', 'backups', 'caddy')) { New-PrivateDirectory (Join-Path $DataRoot $dir) }
    foreach ($dir in @('bin', 'service', 'caddy')) { New-PrivateDirectory (Join-Path $InstallRoot $dir) }
    foreach ($dir in @('api', 'proxy')) { New-PrivateDirectory (Join-Path $DataRoot "logs\$dir") }
}
# Every mutable/code location is private before package/config/secret writes.
$release = Join-Path $InstallRoot 'release'
if (-not (Test-Path -LiteralPath $release)) { New-PrivateDirectory $release }
$releaseWheel = Join-Path $release ([IO.Path]::GetFileName($WheelPath))
Copy-Item -LiteralPath $WheelPath -Destination $releaseWheel -Force
Assert-Hash $releaseWheel $WheelSha256
Copy-Item -LiteralPath (Join-Path $BackendDir 'requirements\prod.txt') -Destination (Join-Path $release 'requirements.txt') -Force
Invoke-Checked $PythonExe @('-I', '-m', 'venv', (Join-Path $InstallRoot 'venv'))
Invoke-Checked $m.Python @('-I', '-m', 'pip', '--isolated', 'install', '--only-binary=:all:', '--require-hashes', '-r', (Join-Path $release 'requirements.txt'))
Invoke-Checked $m.Python @('-I', '-m', 'pip', '--isolated', 'install', '--no-deps', '--force-reinstall', $releaseWheel)
foreach ($file in @('common.ps1', 'operator.ps1', 'serve.ps1', 'manage.ps1', 'harden.ps1', 'backup.ps1', 'trust.ps1', 'uninstall.ps1', 'schedule-backup.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $InstallRoot "bin\$file") -Force
}
Copy-Item -LiteralPath $WinSWPath -Destination (Join-Path $InstallRoot 'service\EduNavApi.exe') -Force
Copy-Item -LiteralPath $WinSWPath -Destination (Join-Path $InstallRoot 'service\EduNavProxy.exe') -Force
Copy-Item -LiteralPath $CaddyPath -Destination (Join-Path $InstallRoot 'caddy\caddy.exe') -Force
Assert-Hash (Join-Path $InstallRoot 'caddy\caddy.exe') $CaddySha256
if (-not (Test-Path -LiteralPath $m.Secret)) {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($m.Secret, [Convert]::ToBase64String($bytes))
}
if ($CertificateMode -eq 'Files') {
    Copy-Item -LiteralPath $CertificatePath -Destination (Join-Path $DataRoot 'secrets\tls.crt') -Force
    Copy-Item -LiteralPath $PrivateKeyPath -Destination (Join-Path $DataRoot 'secrets\tls.key') -Force
}
[IO.File]::WriteAllText((Join-Path $InstallRoot 'caddy\Caddyfile'), $caddy)
[IO.File]::WriteAllText((Join-Path $InstallRoot 'service\EduNavApi.xml'), $apiXml)
[IO.File]::WriteAllText((Join-Path $InstallRoot 'service\EduNavProxy.xml'), $proxyXml)
Write-Json (Join-Path $InstallRoot 'installation.json') $m
Set-PrivateFileAcl (Join-Path $InstallRoot 'installation.json')
Invoke-Managed $m @() -ProxyValidate
foreach ($svc in @('EduNavApi', 'EduNavProxy')) {
    $exe = Join-Path $InstallRoot "service\$svc.exe"
    Assert-Hash $exe $WinSWSha256
    if (-not $existing) { Invoke-Checked $exe @('install') }
    Invoke-Checked 'sc.exe' @('config', $svc, 'obj=', "NT SERVICE\$svc")
    Invoke-Checked 'sc.exe' @('sidtype', $svc, 'unrestricted')
}
$api = 'NT SERVICE\EduNavApi'; $proxy = 'NT SERVICE\EduNavProxy'
Set-PrivateFileAcl (Join-Path $InstallRoot 'installation.json') @{$api = 'Read'}
Set-Acl -LiteralPath $InstallRoot -AclObject (New-PrivateAcl @{$api = 'ReadAndExecute'; $proxy = 'ReadAndExecute'})
Set-Acl -LiteralPath $DataRoot -AclObject (New-PrivateAcl @{$api = 'ReadAndExecute'; $proxy = 'ReadAndExecute'})
Set-Acl -LiteralPath (Join-Path $DataRoot 'logs') -AclObject (New-PrivateAcl @{$api = 'ReadAndExecute'; $proxy = 'ReadAndExecute'})
foreach ($dir in @('data', 'logs\api')) { Set-Acl -LiteralPath (Join-Path $DataRoot $dir) -AclObject (New-PrivateAcl @{$api = 'Modify'}) }
foreach ($dir in @('caddy', 'logs\proxy')) { Set-Acl -LiteralPath (Join-Path $DataRoot $dir) -AclObject (New-PrivateAcl @{$proxy = 'Modify'}) }
Set-Acl -LiteralPath (Join-Path $DataRoot 'secrets') -AclObject (New-PrivateAcl @{$api = 'ReadAndExecute'; $proxy = 'ReadAndExecute'})
Set-PrivateFileAcl $m.Secret @{$api = 'Read'}
if ($CertificateMode -eq 'Files') {
    foreach ($file in @('tls.crt', 'tls.key')) { Set-PrivateFileAcl (Join-Path $DataRoot "secrets\$file") @{$proxy = 'Read'} }
}
# Protected child directories need explicit grants; no arbitrary recursive host ACL changes.
foreach ($dir in @('bin', 'service', 'caddy', 'venv')) {
    Set-Acl -LiteralPath (Join-Path $InstallRoot $dir) -AclObject (New-PrivateAcl @{$api = 'ReadAndExecute'; $proxy = 'ReadAndExecute'})
}
Invoke-Managed $m @('db', 'upgrade')
Invoke-Managed $m @('db', 'verify')
if ($AccessMode -ne 'Local') {
    foreach ($port in @($HttpsPort) + $(if ($CertificateMode -eq 'ACME') { @(80) } else { @() })) {
        $name = "EduNavProduction-TCP-$port"
        $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if ($rule) {
            if (-not $existing -or ($rule | Get-NetFirewallApplicationFilter).Program -ne (Join-Path $InstallRoot 'caddy\caddy.exe')) { throw "Firewall rule collision: $name" }
            # Manifest settings cannot change during upgrade; preserve the audited owned rule.
        } else {
            New-NetFirewallRule -Name $name -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP `
                -LocalPort $port -RemoteAddress $RemoteAddress -Profile Domain,Private -Program (Join-Path $InstallRoot 'caddy\caddy.exe') | Out-Null
        }
    }
}
Start-Service EduNavApi
Start-Service EduNavProxy
'Services started; installation requires trusted HTTPS login/host acceptance verification. No CA trust was changed.'
"Create first admin with: & '$InstallRoot\bin\operator.ps1' -CliArgs @('users','create','your-admin','--role','admin')"
} catch {
    $failure = $_
    try { Stop-OwnedServices $m } catch { Write-Warning 'Could not stop services after failed installation; inspect service state immediately.' }
    throw $failure
} finally { Exit-Maintenance $lock }
