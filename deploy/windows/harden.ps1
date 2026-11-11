#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Help, [switch]$Apply, [string]$InstallRoot = 'C:\Program Files\EduNav')
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Help) {
    'harden.ps1 [-InstallRoot <owned root>] [-Apply [-WhatIf]]'
    'Default: read-only audit to console. Apply only repairs owned application firewall rules; it never changes OS policy, remote access, encryption, antivirus, update settings or trust.'
    'Use the matching Microsoft Security Compliance Toolkit baseline for Windows 11/Server 2019/2022; OSConfig baseline only applies to Server 2025.'
    return
}
function Audit([string]$Name, [scriptblock]$Read) {
    try { [pscustomobject]@{ Check = $Name; State = 'Observed'; Detail = (& $Read | Out-String).Trim() } }
    catch { [pscustomobject]@{ Check = $Name; State = 'Unavailable'; Detail = $_.Exception.Message } }
}
$m = Read-Install $InstallRoot
Audit 'Supported operating system' {
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    if (($os.ProductType -eq 1 -and $build -lt 22000) -or ($os.ProductType -ne 1 -and $build -lt 17763)) { throw 'Unsupported: require Windows 11 or Server 2019/2022/2025.' }
    $os | Select-Object Caption, Version, BuildNumber, ProductType
}
Audit 'Firewall profiles (all should be enabled)' { Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction }
Audit 'Defender/AV visibility (domain AV may replace Defender)' {
    Get-MpComputerStatus | Select-Object AntivirusEnabled, RealTimeProtectionEnabled, AntivirusSignatureLastUpdated
}
Audit 'Recent updates (review patch policy separately)' { Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5 HotFixID, InstalledOn }
Audit 'Pending reboot indicators' {
    [pscustomobject]@{
        Servicing = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
        WindowsUpdate = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
        PendingFileRename = $null -ne (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue)
    }
}
Audit 'BitLocker visibility (manual institutional policy)' { Get-BitLockerVolume | Select-Object MountPoint, VolumeStatus, ProtectionStatus, EncryptionPercentage }
Audit 'Service identity and executable (must match owned virtual account)' {
    foreach ($name in @($m.ApiService, $m.ProxyService)) {
        $s = Get-CimInstance Win32_Service -Filter "Name='$name'"
        if (-not $s -or $s.StartName -ne "NT SERVICE\$name") { throw "Unexpected service identity: $name" }
        $s | Select-Object Name, StartName, State, PathName
    }
}
Audit 'Listener exposure (API must be loopback only)' {
    $connections = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -In @(8000, $m.HttpsPort))
    $connections | Select-Object LocalAddress, LocalPort, OwningProcess
    foreach ($c in $connections) {
        $loopback = [Net.IPAddress]::IsLoopback([Net.IPAddress]::Parse($c.LocalAddress))
        if ($c.LocalPort -eq 8000 -and -not $loopback) { throw 'API exposed beyond loopback.' }
        if ($c.LocalPort -eq $m.HttpsPort -and $m.AccessMode -eq 'Local' -and -not $loopback) { throw 'Local HTTPS exposed beyond loopback.' }
    }
}
Audit 'Owned ACLs (review exact principals; no broad write)' {
    foreach ($path in @($m.InstallRoot, $m.DataRoot, (Join-Path $m.DataRoot 'data'), (Join-Path $m.DataRoot 'secrets'), (Join-Path $m.DataRoot 'backups'))) {
        $null = Get-SafePath $path
        $acl = Get-Acl -LiteralPath $path
        [pscustomobject]@{ Path = $path; Protected = $acl.AreAccessRulesProtected; Owner = $acl.Owner; Rules = $acl.AccessToString }
        if (-not $acl.AreAccessRulesProtected) { throw "Inherited application root ACL: $path" }
    }
    Assert-PrivateOwnership $m.InstallRoot
    Assert-PrivateOwnership $m.DataRoot
    Assert-PrivateOwnership (Join-Path $m.DataRoot 'backups')
    foreach ($file in @($m.Secret) + $(if ($m.CertificateMode -eq 'Files') { @(Join-Path $m.DataRoot 'secrets\tls.key') } else { @() })) {
        $acl = Get-Acl -LiteralPath (Get-SafePath $file)
        $service = if ($file -eq $m.Secret) { 'NT SERVICE\EduNavApi' } else { 'NT SERVICE\EduNavProxy' }
        $expected = (New-Object Security.Principal.NTAccount($service)).Translate([Security.Principal.SecurityIdentifier]).Value
        if (-not $acl.AreAccessRulesProtected -or @($acl.Access).Count -ne 3) { throw "Secret ACL is not exact/private: $file" }
        foreach ($rule in $acl.Access) {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            if ($rule.AccessControlType -ne 'Allow' -or $sid -notin @('S-1-5-18', 'S-1-5-32-544', $expected)) { throw "Unexpected secret access: $file" }
            if ($sid -eq $expected -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write)) { throw "Service can write secret: $file" }
        }
        [pscustomobject]@{ Path = $file; Protected = $acl.AreAccessRulesProtected; Rules = $acl.AccessToString }
    }
}
Audit 'TLS configuration (client trust/issuance requires separate verification)' {
    $config = Get-Content -LiteralPath (Get-SafePath (Join-Path $m.InstallRoot 'caddy\Caddyfile')) -Raw
    if ($config -notmatch 'https://' -or $config -notmatch 'skip_install_trust' -or $config -notmatch 'admin off') { throw 'Unexpected TLS/admin configuration.' }
    "Mode=$($m.CertificateMode), URL=https://$($m.Hostname):$($m.HttpsPort). No TLS bypass or automatic CA trust."
}
Audit 'Paired backup readiness (offsite/encryption/schedule require operator verification)' {
    $sets = @(Get-ChildItem -LiteralPath (Get-SafePath (Join-Path $m.DataRoot 'backups')) -Directory -Filter 'set-*')
    $sets | Select-Object Name, LastWriteTimeUtc
    if (-not $sets.Count) { throw 'No paired backup sets found. Run managed Backup and test Restore on staging.' }
}
Audit 'Application firewall ownership/scope' {
    Get-NetFirewallRule -Name 'EduNavProduction-*' -ErrorAction Stop | ForEach-Object {
        $_ | Select-Object Name, Enabled, Profile
        $_ | Get-NetFirewallPortFilter | Select-Object Protocol, LocalPort
        $_ | Get-NetFirewallAddressFilter | Select-Object RemoteAddress
        $_ | Get-NetFirewallApplicationFilter | Select-Object Program
    }
}
if ($Apply) {
    foreach ($port in @($m.HttpsPort) + $(if ($m.CertificateMode -eq 'ACME') { @(80) } else { @() })) {
        $name = "EduNavProduction-TCP-$port"
        $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if ($rule -and ($rule | Get-NetFirewallApplicationFilter).Program -ne (Join-Path $m.InstallRoot 'caddy\caddy.exe')) { throw "Unowned firewall rule: $name" }
        if ($PSCmdlet.ShouldProcess($name, 'Reconcile application-scoped inbound firewall rule')) {
            Assert-Administrator
            if ($rule) { $rule | Remove-NetFirewallRule }
            if ($m.AccessMode -ne 'Local') {
                if (-not @($m.RemoteAddress).Count -or 'Any' -in $m.RemoteAddress) { throw 'Explicit non-Any firewall scope required.' }
                New-NetFirewallRule -Name $name -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port `
                    -RemoteAddress $m.RemoteAddress -Profile Domain,Private -Program (Join-Path $m.InstallRoot 'caddy\caddy.exe') | Out-Null
            }
        }
    }
}
