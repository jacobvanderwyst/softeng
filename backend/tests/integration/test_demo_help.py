"""Exercise PowerShell help in isolation: it must never reach setup or credential rotation."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "demo.ps1"


@pytest.mark.parametrize("flag", ["-Help", "-h"])
def test_demo_help_returns_before_setup_even_with_other_flags(tmp_path, flag):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "demo.ps1"
    shutil.copyfile(SCRIPT, script)
    instance = tmp_path / "instance"
    instance.mkdir()
    for database in ("users.sqlite3", "plans.sqlite3"):
        (instance / database).write_bytes(b"test sentinel - must not be changed")
    environment = {
        **os.environ,
        "APP_ENV": "production",
        "USERS_DATABASE_URL": "sqlite:///help-test-users.sqlite3",
        "PLANS_DATABASE_URL": "sqlite:///help-test-plans.sqlite3",
    }
    result = subprocess.run(  # noqa: S603
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(script),
         flag, "-Reset", "-NoRun", "-FrontendOrigin", "invalid-origin"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "EDUNAV LOCAL DEMO" in result.stdout
    assert "QUICK START" in result.stdout
    assert "TROUBLESHOOTING" in result.stdout
    assert "-KeepDemoPasswords" in result.stdout
    assert "HTTP, not HTTPS" in result.stdout
    assert "==>" not in result.stdout
    assert not (tmp_path / ".venv").exists()
    for database in ("users.sqlite3", "plans.sqlite3"):
        assert (instance / database).read_bytes() == b"test sentinel - must not be changed"
