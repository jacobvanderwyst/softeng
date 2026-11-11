import json
import subprocess
from pathlib import Path

import pytest
from tests.integration.test_demo_help import POWERSHELL

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not installed")
COMMON = Path(__file__).resolve().parents[3] / "deploy" / "windows" / "common.ps1"


def test_service_uses_isolated_module_entrypoint_and_production_environment(tmp_path):
    common = str(COMMON).replace("'", "''")
    data = str(tmp_path).replace("'", "''")
    script = tmp_path / "verify-service.ps1"
    script.write_text(
        f""". '{common}'
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {{
    [pscustomobject]@{{
        Arguments = $Arguments
        Mode = $env:APP_ENV
        SecureCookies = $env:COOKIE_SECURE
        HasDemoOverride = Test-Path Env:EDUNAV_DEMO
    }} | ConvertTo-Json -Compress
}}
$env:EDUNAV_DEMO = '1'
$m = [pscustomobject]@{{
    Python = 'not-executed'
    DataRoot = '{data}'
    Secret = Join-Path '{data}' 'not-read'
    Users = Join-Path '{data}' 'users.sqlite3'
    Plans = Join-Path '{data}' 'plans.sqlite3'
}}
Invoke-Managed $m @() -Serve
""",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    launched = json.loads(result.stdout)
    assert launched["Arguments"][:3] == ["-I", "-m", "waitress"]
    assert "--listen=localhost:8000" in launched["Arguments"]
    assert "--no-clear-untrusted-proxy-headers" in launched["Arguments"]
    assert "-c" not in launched["Arguments"]
    assert launched["Mode"] == "production"
    assert launched["SecureCookies"] == "true"
    assert launched["HasDemoOverride"] is False
