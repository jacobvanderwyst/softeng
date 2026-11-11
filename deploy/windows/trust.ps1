#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Help, [string]$CertificatePath, [string]$ExpectedSha256)
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Help) {
    'trust.ps1 -CertificatePath <reviewed CA certificate> -ExpectedSha256 <64-hex DER certificate fingerprint> [-WhatIf]'
    'Explicit local-machine root trust only. Verify issuer and SHA256 through a trusted independent channel before running. Never accepts a TLS bypass.'
    return
}
$path = Get-SafePath $CertificatePath
if ($ExpectedSha256 -notmatch '^[A-Fa-f0-9]{64}$') { throw 'Supply the independently verified DER SHA256 fingerprint (no separators).' }
$cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($path)
$sha = [Security.Cryptography.SHA256]::Create()
try { $fingerprint = [BitConverter]::ToString($sha.ComputeHash($cert.RawData)).Replace('-', '') } finally { $sha.Dispose() }
if ($fingerprint -ne $ExpectedSha256) { throw 'CA certificate fingerprint mismatch.' }
$basic = @($cert.Extensions | Where-Object { $_ -is [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension] })
if ($basic.Count -ne 1 -or -not $basic[0].CertificateAuthority -or $cert.Subject -ne $cert.Issuer -or $cert.HasPrivateKey -or
    $cert.NotAfter -lt (Get-Date) -or $cert.NotBefore -gt (Get-Date)) { throw 'Expected a current self-issued CA certificate without a private key.' }
"Issuer: $($cert.Issuer); SHA256: $fingerprint"
if ($PSCmdlet.ShouldProcess("$($cert.Subject) ($fingerprint)", 'Add explicitly verified CA to LocalMachine Root trust')) {
    Assert-Administrator
    $store = New-Object Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
    try { $store.Open('ReadWrite'); $store.Add($cert) } finally { $store.Close(); $cert.Dispose() }
}
