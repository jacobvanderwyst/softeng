"""Exercise a built wheel from a separate installation target and fresh production databases.

Uses the current interpreter's dependencies, but imports application code/assets/migrations only
from the installed wheel. No service installation, external databases or existing accounts.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

CHECKS = r'''
import os, pathlib, secrets, sys
target = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(target))
import degreeplan
if not pathlib.Path(degreeplan.__file__).resolve().is_relative_to(target.resolve()):
    raise RuntimeError("Installed-wheel test imported source checkout instead")
from degreeplan import create_app
from degreeplan.db import tables
import sqlalchemy as sa
app = create_app()
svc = app.extensions["degreeplan"]
def check(ok, label):
    if not ok:
        raise RuntimeError(label)
    print("PASS " + label)
def cli(*args, input_text=None):
    result = app.test_cli_runner().invoke(args=list(args), input=input_text)
    check(result.exit_code == 0, "installed CLI: " + " ".join(args[:2]))
    return result
cli("db", "upgrade")
cli("db", "verify")
with svc.databases.users.connect() as conn:
    check(conn.execute(sa.select(sa.func.count()).select_from(tables.users)).scalar_one() == 0,
          "fresh production has no demo accounts")
with svc.databases.plans.connect() as conn:
    check(conn.execute(sa.select(sa.func.count()).select_from(tables.courses)).scalar_one() == 0,
          "fresh production has no demonstration catalog")
catalog = sys.argv[2]
cli("catalog", "import", catalog, "--dry-run")
with svc.databases.plans.connect() as conn:
    check(conn.execute(sa.select(sa.func.count()).select_from(tables.courses)).scalar_one() == 0,
          "catalog dry run rolls back")
cli("catalog", "import", catalog)
password = secrets.token_urlsafe(24)
cli("users", "create", "artifact-student", "--role", "student", "--first-name", "Artifact",
    "--last-name", "Student", "--program-id", "1", "--password-stdin", input_text=password + "\n")
client = app.test_client()
login = client.post("/api/v1/auth/login", base_url="https://localhost",
                    json={"username": "artifact-student", "password": password})
check(login.status_code == 200, "installed application login")
headers = {"X-CSRF-Token": login.json["csrf_token"], "Origin": "https://localhost"}
saved = client.put("/api/v1/progress/1", base_url="https://localhost", headers=headers,
                   json={"status": "Completed", "grade": "A"})
check(saved.status_code == 200 and saved.json["grade"] == "A", "installed grade persistence")
summary = client.get("/api/v1/progress/summary", base_url="https://localhost")
check(summary.status_code == 200 and summary.json["gpa"] == 4.0, "installed authoritative GPA")
home = client.get("/", base_url="https://localhost")
check(home.status_code == 200 and b"dashboardGPA" in home.data, "wheel bundles updated frontend")
check("script-src 'self'" in home.headers["Content-Security-Policy"], "installed frontend CSP")
check(app.test_cli_runner().invoke(args=["dev", "seed"]).exit_code == 2,
      "production has no demo/reset commands")
cookie = client.get_cookie("__Host-sid")
svc.databases.dispose()
restarted = create_app()
again = restarted.test_client()
again.set_cookie("__Host-sid", cookie.value, secure=True, httponly=True)
restored = again.get("/api/v1/progress/summary", base_url="https://localhost")
check(restored.status_code == 200 and restored.json["gpa"] == 4.0,
      "installed data and session survive application restart")
restarted.extensions["degreeplan"].databases.dispose()
print("Installed wheel verified; application version " + degreeplan.__version__)
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--catalog", type=Path, default=Path(__file__).resolve().parents[2]
                        / "docs" / "examples" / "catalog-v1.example.json")
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    with TemporaryDirectory(prefix="edunav-installed-") as temporary:
        root = Path(temporary)
        target = root / "application" / "site"
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-I", "-m", "pip", "--isolated", "install",
             "--no-deps", "--no-compile", "--target", str(target), str(wheel)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode:
            print("Wheel installation failed: " + result.stderr, file=sys.stderr)
            return result.returncode
        secret = root / "secret"
        import secrets

        secret.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
        environment.update({
            "APP_ENV": "production", "SECRET_KEY_FILE": str(secret), "COOKIE_SECURE": "true",
            "USERS_DATABASE_URL": f"sqlite:///{(root / 'users.sqlite3').as_posix()}",
            "PLANS_DATABASE_URL": f"sqlite:///{(root / 'plans.sqlite3').as_posix()}",
            "PROXY_HOPS": "0", "LOG_LEVEL": "ERROR", "FLASK_SKIP_DOTENV": "1",
            "LOG_DIR": str(root / "logs"),
        })
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-I", "-c", CHECKS, str(target), str(args.catalog.resolve(strict=True))],
            cwd=root, env=environment, capture_output=True, text=True, timeout=120,
        )
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
