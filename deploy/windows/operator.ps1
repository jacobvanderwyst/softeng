#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Help, [string]$InstallRoot = 'C:\Program Files\EduNav', [string[]]$CliArgs = @())
. (Join-Path $PSScriptRoot 'common.ps1')
if ($Help) {
    "operator.ps1 -InstallRoot <owned install> -CliArgs @('users','create','admin-name','--role','admin')"
    'Allowed: users create/list/update-profile/set-password/activate/deactivate/revoke-sessions/revoke-all-sessions; catalog import/list; db status/verify.'
    'Passwords are prompted secretly by the backend. No dev/reset/stamp or arbitrary Flask commands are exposed.'
    return
}
if ($CliArgs.Count -lt 2) { throw 'Supply a management group and command using -CliArgs.' }
$allowed = @{
    users = @('create', 'list', 'update-profile', 'set-password', 'activate', 'deactivate', 'revoke-sessions', 'revoke-all-sessions')
    catalog = @('import', 'list')
    db = @('status', 'verify')
}
if (-not $allowed.ContainsKey($CliArgs[0]) -or $CliArgs[1] -notin $allowed[$CliArgs[0]]) { throw 'Operator command not permitted.' }
$m = Read-Install $InstallRoot
if ($CliArgs[0] -eq 'catalog' -and $CliArgs[1] -eq 'import' -and $CliArgs.Count -ge 3 -and $CliArgs[2] -notmatch '^--') {
    $CliArgs[2] = [IO.Path]::GetFullPath($CliArgs[2])
}
if ($PSCmdlet.ShouldProcess($m.DataRoot, ($CliArgs[0..1] -join ' '))) {
    Assert-Administrator
    $lock = Enter-Maintenance
    try { Invoke-Managed $m $CliArgs } finally { Exit-Maintenance $lock }
}
