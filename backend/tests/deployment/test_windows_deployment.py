"""PowerShell 5.1 parser/render and mocked lifecycle tests, never host installation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as et
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WINDOWS = ROOT / "deploy" / "windows"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell 5.1 required")


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(code: str, *, ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(  # noqa: S603
        [
            POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference='Stop'; " + code + "; exit 0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if ok:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout
    return result


def test_preflight_and_install_whatif_do_not_execute_binaries(tmp_path):
    artifact = tmp_path / "checked.exe"
    wheel = tmp_path / "checked.whl"
    artifact.write_bytes(b"not an executable; checksum/preflight fixture")
    wheel.write_bytes(b"not a wheel; checksum/preflight fixture")
    run_ps(
        common()
        + f"""
function Get-Service($Name){{}}
function Get-NetTCPConnection($LocalPort,$State){{}}
function Assert-Administrator{{throw 'Unexpected elevation'}}
function New-PrivateDirectory($Path){{throw 'Unexpected filesystem mutation'}}
$exe={quote(artifact)}; $wheel={quote(wheel)}
$hash=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
$wheelHash=(Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash
$p=@{{WinSWPath=$exe;WinSWSha256=$hash;CaddyPath=$exe;CaddySha256=$hash;
 WheelPath=$wheel;WheelSha256=$wheelHash}}
& {quote(WINDOWS / 'install.ps1')} @p -Preflight
& {quote(WINDOWS / 'install.ps1')} @p -WhatIf
"""
    )


def test_get_safe_path_rejects_hardlinks(tmp_path):
    target = tmp_path / "target"
    target.write_text("local fixture", encoding="utf-8")
    run_ps(
        common()
        + f"$null=New-Item -ItemType HardLink -Path {quote(tmp_path / 'alias')} -Target {quote(target)}; "
        + f"Get-SafePath {quote(tmp_path / 'alias')}",
        ok=False,
    )


def test_bulk_creation_uses_private_acl_and_stdin_not_command_password(tmp_path):
    cli = tmp_path / "mock-operator.ps1"
    cli.write_text(
        "param([string[]]$CliArgs)\n"
        "if($CliArgs[0] -ne 'users' -or '--password-stdin' -notin $CliArgs){throw 'Bad args'}\n"
        "if($CliArgs[-2] -ne '--' -or $CliArgs[-1] -ne 'newadmin'){throw 'Unsafe positional args'}\n"
        "$pw=[Console]::ReadLine();if($pw.Length -lt 20){throw 'Missing stdin password'}\n",
        encoding="utf-8",
    )
    csv = tmp_path / "accounts.csv"
    csv.write_text("username,role\nnewadmin,admin\n", encoding="utf-8")
    output = tmp_path / "credentials.csv"
    run_ps(
        f"& {quote(ROOT / 'backend/scripts/bulk_create_users.ps1')} "
        f"-Csv {quote(csv)} -OutFile {quote(output)} -Cli {quote(cli)}; "
        f"$acl=Get-Acl -LiteralPath {quote(output)}; "
        "if(-not $acl.AreAccessRulesProtected -or @($acl.Access).Count -ne 1){throw 'Unsafe credentials ACL'}"
    )
    rows = output.read_text(encoding="ascii").splitlines()
    assert len(rows) == 2
    assert rows[1].startswith("newadmin,")


@pytest.mark.parametrize("rows", ["alice,admin\nALICE,admin\n", "'=bad',admin\n", "teacher1,teacher\n"])
def test_bulk_validation_fails_before_credentials_or_accounts(tmp_path, rows):
    csv = tmp_path / "accounts.csv"
    csv.write_text("username,role\n" + rows, encoding="utf-8")
    output = tmp_path / "never-created.csv"
    result = run_ps(
        f"& {quote(ROOT / 'backend/scripts/bulk_create_users.ps1')} "
        f"-Csv {quote(csv)} -OutFile {quote(output)} -Cli {quote(WINDOWS / 'operator.ps1')}",
        ok=False,
    )
    assert not output.exists()
    assert "created  " not in result.stdout


def test_hardening_audit_and_apply_whatif_are_read_only(tmp_path):
    run_ps(
        common()
        + f"""
$m=[pscustomobject]@{{InstallRoot={quote(tmp_path)};DataRoot={quote(tmp_path)};
 ApiService='EduNavApi';ProxyService='EduNavProxy';HttpsPort=8443;AccessMode='Local';CertificateMode='Internal'}}
function Read-Install($InstallRoot){{return $m}}
function Assert-Administrator{{throw 'Unexpected elevation'}}
function Get-CimInstance($ClassName,$Filter){{
 if($ClassName -eq 'Win32_OperatingSystem'){{
  return [pscustomobject]@{{BuildNumber='26100';ProductType=1;Caption='Mock Windows 11'}}
 }}
 [pscustomobject]@{{Name='EduNavApi';StartName='NT SERVICE\\EduNavApi';PathName='mock';State='Stopped'}}
}}
function Get-NetFirewallRule($Name){{}}
function Get-NetFirewallProfile{{[pscustomobject]@{{Name='Private';Enabled=$true}}}}
function Get-NetTCPConnection{{[pscustomobject]@{{LocalAddress='::1';LocalPort=8000;OwningProcess=1234}}}}
function Get-MpComputerStatus{{throw 'mock unavailable'}}
function Get-BitLockerVolume{{throw 'mock unavailable'}}
function Get-HotFix{{}}
function New-NetFirewallRule{{throw 'Unexpected host mutation'}}
function Remove-NetFirewallRule{{throw 'Unexpected host mutation'}}
$text=Get-Content -LiteralPath {quote(WINDOWS / 'harden.ps1')} -Raw
$text=$text.Replace(". (Join-Path `$PSScriptRoot 'common.ps1')",'')
$audit=[scriptblock]::Create($text)
$result=@(& $audit)
if(-not @($result|Where-Object Check -Like '*BitLocker*').Count){{throw 'Missing encryption audit'}}
& $audit -Apply -WhatIf
"""
    )


def common() -> str:
    return f". {quote(WINDOWS / 'common.ps1')}; "


def test_all_scripts_parse_and_help_without_admin():
    scripts = list(WINDOWS.glob("*.ps1")) + [ROOT / "backend/scripts/bulk_create_users.ps1"]
    for script in scripts:
        run_ps(
            "$t=$null; $e=$null; "
            f"$null=[Management.Automation.Language.Parser]::ParseFile({quote(script)},[ref]$t,[ref]$e);"
            "if($e.Count){throw ($e|Out-String)}"
        )
    for name in ("install", "manage", "operator", "backup", "uninstall", "harden", "trust", "schedule-backup"):
        assert run_ps(f"& {quote(WINDOWS / (name + '.ps1'))} -Help").stdout.strip()


def test_schedule_registration_whatif_is_explicit_and_nonmutating(tmp_path):
    run_ps(
        common()
        + f"""
$m=[pscustomobject]@{{InstallRoot={quote(tmp_path)}}}
function Read-Install($InstallRoot){{return $m}}
function Get-ScheduledTask($TaskName){{}}
function Assert-Administrator{{throw 'Unexpected elevation'}}
function Register-ScheduledTask{{throw 'Unexpected task registration'}}
$text=Get-Content -LiteralPath {quote(WINDOWS / 'schedule-backup.ps1')} -Raw
$text=$text.Replace(". (Join-Path `$PSScriptRoot 'common.ps1')",'')
& ([scriptblock]::Create($text)) -Time '03:15' -WhatIf
"""
    )


def test_local_render_has_no_automatic_trust_or_external_listener():
    value = json.loads(run_ps(f"& {quote(WINDOWS / 'install.ps1')} -RenderOnly | ConvertTo-Json -Depth 6").stdout)
    assert value["Manifest"]["ApiService"] == "EduNavApi"
    assert value["Manifest"]["DataRoot"] == r"C:\ProgramData\EduNav\production"
    assert "https://localhost:8443" in value["Caddyfile"]
    assert "bind localhost" in value["Caddyfile"]
    assert "skip_install_trust" in value["Caddyfile"]
    assert "tls internal" in value["Caddyfile"]
    assert "admin off" in value["Caddyfile"]
    assert "&quot;C:\\Program Files\\EduNav" in value["ApiXml"]
    assert "localhost:8000" in value["Caddyfile"]
    for key in ("ApiXml", "ProxyXml"):
        xml = et.fromstring(value[key])  # noqa: S314 - only this repository's generated renderer fixture
        assert xml.find("onfailure").attrib["action"] == "restart"
        assert xml.find("log").attrib["mode"] == "roll-by-size"


@pytest.mark.parametrize(
    "arguments",
    [
        "-AccessMode LAN",
        "-AccessMode LAN -Hostname plans.school.test",
        "-AccessMode Public -Hostname plans.school.test -RemoteAddress Any",
        "-CertificateMode ACME",
        "-Hostname 'bad{host'",
        "-InstallRoot C:/Windows/EduNav",
        "-InstallRoot C:/ProgramData/EduNav -DataRoot C:/ProgramData/EduNav/production",
    ],
)
def test_render_rejects_unsafe_configuration(arguments):
    run_ps(f"& {quote(WINDOWS / 'install.ps1')} -RenderOnly {arguments}", ok=False)


def test_lan_and_public_render_require_explicit_modes():
    value = json.loads(
        run_ps(
            f"& {quote(WINDOWS / 'install.ps1')} -RenderOnly -AccessMode LAN "
            "-Hostname plans.school.test -RemoteAddress LocalSubnet | ConvertTo-Json -Depth 6"
        ).stdout
    )
    assert "bind localhost" not in value["Caddyfile"]
    assert value["Manifest"]["RemoteAddress"] == ["LocalSubnet"]
    value = json.loads(
        run_ps(
            f"& {quote(WINDOWS / 'install.ps1')} -RenderOnly -AccessMode Public -CertificateMode ACME "
            "-Hostname plans.school.test -HttpsPort 443 -RemoteAddress 10.20.0.0/16 | ConvertTo-Json -Depth 6"
        ).stdout
    )
    assert "tls internal" not in value["Caddyfile"]


@pytest.mark.parametrize("path", ["C:/", r"\\server\share\db", "relative", "C:/temp/file:stream", "C:/temp/NUL.db"])
def test_unsafe_paths_rejected(path):
    run_ps(common() + f"Get-SafePath {quote(path)}", ok=False)


def test_checked_native_failures_are_not_silent():
    run_ps(common() + "Invoke-Checked $env:COMSPEC @('/c','exit 7')", ok=False)


@pytest.mark.parametrize("port", [8000, 8443])
def test_scm_stopped_requires_no_orphan_api_listener(port):
    result = run_ps(
        common()
        + f"""
$global:seen=New-Object Collections.Generic.List[string]
function Get-Service($Name){{
 $global:seen.Add($Name)
 [pscustomobject]@{{Status='Stopped'}}
}}
function Stop-Service{{throw 'Unexpected live service mutation'}}
function Get-NetTCPConnection{{[pscustomobject]@{{LocalPort={port};State='Listen';OwningProcess=1234}}}}
$m=[pscustomobject]@{{ApiService='EduNavApi';ProxyService='EduNavProxy'}}
Stop-OwnedServices $m
if($global:seen.Count -ne 2){{throw 'Did not inspect both services'}}
""",
        ok=port != 8000,
    )
    if port == 8000:
        assert "still listening after services stopped" in result.stderr


def test_quiescence_listener_enumeration_errors_fail_closed():
    result = run_ps(
        common()
        + """
function Get-Service($Name){[pscustomobject]@{Status='Stopped'}}
function Stop-Service{throw 'Unexpected live service mutation'}
function Get-NetTCPConnection{throw 'mock listener enumeration unavailable'}
Stop-OwnedServices ([pscustomobject]@{ApiService='EduNavApi';ProxyService='EduNavProxy'})
""",
        ok=False,
    )
    assert "mock listener enumeration unavailable" in result.stderr


def test_isolated_operator_environment_restored_even_on_failure(tmp_path):
    code = common() + f"""
$env:APP_ENV='development'; $env:USERS_DATABASE_URL_FILE='caller-file';
$env:SECRET_KEY='caller-secret'; $env:PYTHONPATH='caller-imports'; $env:EDUNAV_DEMO='1';
$m=[pscustomobject]@{{Python='not-executed';DataRoot={quote(tmp_path)};
 Secret='managed-key-file';Users='C:\\owned\\users.sqlite3';Plans='C:\\owned\\plans.sqlite3'}};
function Invoke-Checked($Executable,$Arguments) {{
 if($env:APP_ENV -ne 'production'){{throw 'Wrong managed env'}}
 if($env:USERS_DATABASE_URL -ne 'sqlite:///C:/owned/users.sqlite3'){{throw 'Wrong managed databases'}}
 if($env:PYTHONPATH -or $env:EDUNAV_DEMO -or $env:SECRET_KEY){{throw 'Inherited injection'}}
 if($env:USERS_DATABASE_URL_FILE){{throw 'Inherited file override'}}
 if($Arguments[0] -ne '-I'){{throw 'Python not isolated'}}
 throw 'expected-native-failure'
}}
try {{Invoke-Managed $m @('db','status');throw 'did not fail'}} catch {{
 if($_.Exception.Message -ne 'expected-native-failure'){{throw}}
}}
if($env:APP_ENV -ne 'development' -or $env:EDUNAV_DEMO -ne '1'){{throw 'Caller env not restored'}}
if($env:PYTHONPATH -ne 'caller-imports'){{throw 'Caller Python env not restored'}}
"""
    run_ps(code)


def test_exact_private_acl_builder_has_only_expected_principals():
    run_ps(
        common()
        + "$acl=New-PrivateAcl; "
        "if(-not $acl.AreAccessRulesProtected -or @($acl.Access).Count -ne 2){throw 'Not exact private ACL'};"
        "foreach($r in $acl.Access){if($r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value "
        "-notin @('S-1-5-18','S-1-5-32-544')){throw 'Broad principal'}}"
    )


def mocked_maintenance(tmp_path: Path, *, fail_revoke: bool = False) -> str:
    """Real file/hash/manifest logic; mock only host/admin/backend process boundaries."""
    return common() + f"""
$global:events=New-Object Collections.Generic.List[string]
$global:m=[pscustomobject]@{{InstallRoot={quote(tmp_path / 'code')};DataRoot={quote(tmp_path)};
 Users={quote(tmp_path / 'data/users.sqlite3')};Plans={quote(tmp_path / 'data/plans.sqlite3')};
 ApiService='EduNavApi';ProxyService='EduNavProxy'}}
$null=[IO.Directory]::CreateDirectory((Join-Path $m.DataRoot 'data'))
$null=[IO.Directory]::CreateDirectory((Join-Path $m.DataRoot 'backups'))
[IO.File]::WriteAllText($m.Users,'original-users');[IO.File]::WriteAllText($m.Plans,'original-plans')
function Read-Install($InstallRoot){{return $global:m}}
function Assert-Administrator {{}}
function Enter-Maintenance {{}}
function Exit-Maintenance($Mutex) {{}}
function Get-Service($Name){{[pscustomobject]@{{Status='Running'}}}}
function Stop-OwnedServices($Manifest){{$global:events.Add('stop')}}
function Start-Service($Name){{$global:events.Add('start:'+ $Name)}}
function New-PrivateDirectory($Path){{$null=[IO.Directory]::CreateDirectory($Path)}}
function Assert-PrivateOwnership($Path){{}}
function Set-PrivateFileAcl($Path,$Grants){{
 $global:events.Add('acl:' + [IO.Path]::GetFileName($Path))
 if($Path -like '*backups*' -and $Grants){{throw 'Backup ACL must be administrator/SYSTEM only'}}
}}
function Invoke-Managed($Manifest,$CliArgs,$Users,$Plans) {{
 $global:events.Add(($CliArgs[0..1] -join ':'))
 if($CliArgs[1] -eq 'backup'){{
  if($global:events[0] -ne 'stop'){{throw 'Backup before stop'}}
  if(-not $Users){{$Users=$Manifest.Users}}
  if(-not $Plans){{$Plans=$Manifest.Plans}}
  Copy-Item -LiteralPath $Users -Destination (Join-Path $CliArgs[3] 'users-20260101T000000Z.sqlite3')
  Copy-Item -LiteralPath $Plans -Destination (Join-Path $CliArgs[3] 'plans-20260101T000000Z.sqlite3')
 }}
 if($CliArgs[1] -eq 'revoke-all-sessions' -and ${str(fail_revoke).lower()}){{throw 'mock revoke failure'}}
}}
$text=Get-Content -LiteralPath {quote(WINDOWS / 'manage.ps1')} -Raw
$text=$text.Replace(". (Join-Path `$PSScriptRoot 'common.ps1')",'')
$maint=[scriptblock]::Create($text)
"""


def test_mocked_paired_backup_and_restore_order(tmp_path):
    run_ps(
        mocked_maintenance(tmp_path)
        + """
& $maint -Action Backup
$set=(Get-ChildItem (Join-Path $m.DataRoot 'backups') -Directory -Filter 'set-*')[0].FullName
$pair=Get-Content (Join-Path $set 'backup.json') -Raw|ConvertFrom-Json
if($pair.Files.Count -ne 2 -or -not $pair.Quiesced){throw 'Not paired'}
if(-not $global:events.Contains('acl:backup.json')){throw 'Manifest ACL not made explicit'}
if(-not $global:events.Contains('acl:users-20260101T000000Z.sqlite3')){throw 'Users snapshot ACL missing'}
if(-not $global:events.Contains('acl:plans-20260101T000000Z.sqlite3')){throw 'Plans snapshot ACL missing'}
[IO.File]::WriteAllText($m.Users,'later-users');[IO.File]::WriteAllText($m.Plans,'later-plans')
$global:events.Clear()
& $maint -Action Restore -BackupSet $set
if([IO.File]::ReadAllText($m.Users) -ne 'original-users'){throw 'Wrong restored users'}
if([IO.File]::ReadAllText($m.Plans) -ne 'original-plans'){throw 'Wrong restored plans'}
if($global:events[0] -ne 'stop'){throw 'Did not quiesce'}
if(-not $global:events.Contains('users:revoke-all-sessions')){throw 'Did not revoke sessions'}
if($global:events[$global:events.Count-1] -ne 'start:EduNavProxy'){
 throw 'Did not restart after verification'
}
"""
    )


def test_mocked_restore_hash_failure_leaves_live_data_stopped(tmp_path):
    run_ps(
        mocked_maintenance(tmp_path)
        + """
& $maint -Action Backup
$set=(Get-ChildItem (Join-Path $m.DataRoot 'backups') -Directory -Filter 'set-*')[0].FullName
[IO.File]::WriteAllText((Join-Path $set 'users-20260101T000000Z.sqlite3'),'tampered')
$global:events.Clear()
try {& $maint -Action Restore -BackupSet $set;throw 'unexpected-success'} catch {
 if($_.Exception.Message -notmatch 'Checksum mismatch'){throw}
}
if([IO.File]::ReadAllText($m.Users) -ne 'original-users'){throw 'Touched live data'}
if($global:events[0] -ne 'stop'){throw 'Did not stop'}
if(@($global:events|Where-Object {$_ -like 'start:*'}).Count){throw 'Restarted after failure'}
"""
    )


def test_mocked_failed_restore_recovers_pair_without_restart(tmp_path):
    run_ps(
        mocked_maintenance(tmp_path, fail_revoke=True)
        + """
& $maint -Action Backup
$set=(Get-ChildItem (Join-Path $m.DataRoot 'backups') -Directory -Filter 'set-*')[0].FullName
[IO.File]::WriteAllText($m.Users,'later-users');[IO.File]::WriteAllText($m.Plans,'later-plans')
$global:events.Clear()
try {& $maint -Action Restore -BackupSet $set;throw 'unexpected-success'} catch {
 if($_.Exception.Message -notmatch 'recovery snapshots restored'){throw}
}
if([IO.File]::ReadAllText($m.Users) -ne 'later-users'){throw 'Users not recovered'}
if([IO.File]::ReadAllText($m.Plans) -ne 'later-plans'){throw 'Plans not recovered'}
if(@($global:events|Where-Object {$_ -like 'start:*'}).Count){throw 'Restarted failed restore'}
"""
    )


def test_maintenance_whatif_does_not_stop_or_write(tmp_path):
    run_ps(
        mocked_maintenance(tmp_path)
        + """
& $maint -Action Restore -BackupSet 'not-read-in-whatif' -WhatIf
if($global:events.Count){throw 'WhatIf executed mutation'}
if(@(Get-ChildItem (Join-Path $m.DataRoot 'backups')).Count){throw 'WhatIf wrote backup'}
"""
    )
