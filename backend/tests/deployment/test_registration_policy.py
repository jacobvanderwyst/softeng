import json
import os

import pytest
from tests.deployment.test_windows_deployment import WINDOWS, common, quote, run_ps
from tests.integration.test_demo_help import POWERSHELL

pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell required")


def test_registration_upgrade_policy_preserves_or_explicitly_changes_choice():
    result = run_ps(
        common()
        + """
$legacy=[pscustomobject]@{}
$enabled=[pscustomobject]@{RegistrationEnabled=$true}
$disabled=[pscustomobject]@{RegistrationEnabled=$false}
@(
 (Resolve-RegistrationPolicy $null $false $false),
 (Resolve-RegistrationPolicy $null $true $true),
 (Resolve-RegistrationPolicy $legacy $true $false),
 (Resolve-RegistrationPolicy $enabled $false $false),
 (Resolve-RegistrationPolicy $enabled $false $true),
 (Resolve-RegistrationPolicy $disabled $true $true)
) | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == [True, True, True, True, False, True]


def test_invalid_manifest_registration_type_is_rejected():
    run_ps(common() + "Get-RegistrationEnabled ([pscustomobject]@{RegistrationEnabled='true'})", ok=False)


@pytest.mark.parametrize("argument, enabled", [
    ("", True), ("-EnableRegistration", True), ("-EnableRegistration:$false", False),
])
def test_installer_renders_explicit_signup_choice(argument, enabled):
    result = run_ps(
        f"& {quote(WINDOWS / 'install.ps1')} -RenderOnly {argument} | ConvertTo-Json -Depth 6"
    )
    assert json.loads(result.stdout)["Manifest"]["RegistrationEnabled"] is enabled


@pytest.mark.parametrize("manifest_setting, expected", [
    ("", "true"), (";RegistrationEnabled=$false", "false"), (";RegistrationEnabled=$true", "true"),
])
def test_service_registration_setting_comes_only_from_manifest(tmp_path, manifest_setting, expected):
    result = run_ps(
        common()
        + f"""
$env:REGISTRATION_ENABLED='true'
$m=[pscustomobject]@{{Python='not-executed';DataRoot={quote(tmp_path)};
 Secret='not-read';Users='C:\\owned\\users.sqlite3';Plans='C:\\owned\\plans.sqlite3'{manifest_setting}}}
function Invoke-Checked($Executable,$Arguments){{
 [pscustomobject]@{{Enabled=$env:REGISTRATION_ENABLED;Mode=$env:APP_ENV}} | ConvertTo-Json -Compress
}}
Invoke-Managed $m @('db','status')
if($env:REGISTRATION_ENABLED -ne 'true'){{throw 'Caller policy was not restored'}}
"""
    )
    assert json.loads(result.stdout) == {"Enabled": expected, "Mode": "production"}
