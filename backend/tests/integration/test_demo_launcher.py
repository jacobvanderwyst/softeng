import os
import shutil
import sqlite3
import subprocess
import sysconfig
import venv
from contextlib import closing

import pytest

from tests.integration.test_demo_help import POWERSHELL, SCRIPT

pytestmark = pytest.mark.skipif(POWERSHELL is None or os.name != "nt", reason="Windows PowerShell required")


@pytest.fixture
def copied_demo(tmp_path):
    backend = tmp_path / "backend"
    (backend / "scripts").mkdir(parents=True)
    shutil.copyfile(SCRIPT, backend / "scripts" / "demo.ps1")
    shutil.copytree(SCRIPT.parents[1] / "src", backend / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    # Reuse trusted test dependencies, not the editable application's source, in an isolated venv.
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(backend / ".venv")
    packages = backend / ".venv" / "Lib" / "site-packages"
    (packages / "test-dependencies.pth").write_text(sysconfig.get_paths()["purelib"] + "\n", encoding="utf-8")
    # Dotenv must not redirect the managed demo to any external database.
    (backend / ".env").write_text("USERS_DATABASE_URL_FILE=do-not-read\nPLANS_DATABASE_URL_FILE=do-not-read\n",
                                encoding="utf-8")
    return backend


def launch(backend, *options):
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    environment["APP_ENV"] = "development"
    result = subprocess.run(  # noqa: S603
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(backend / "scripts" / "demo.ps1"),
         "-NoRun", *options],
        cwd=backend.parent, env=environment, capture_output=True, text=True, timeout=90,
    )
    # Never expose stdout: first setup intentionally contains generated demonstration passwords.
    assert result.returncode == 0, result.stderr
    return result


def test_actual_demo_setup_clear_and_reset_stay_in_owned_copy(copied_demo):
    backend = copied_demo
    launch(backend)
    instance = backend / "instance"
    assert (instance / ".edunav-demo.json").is_file()
    with closing(sqlite3.connect(instance / "users.sqlite3")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 6
    with closing(sqlite3.connect(instance / "plans.sqlite3")) as conn:
        conn.execute("INSERT INTO plans (student_id,program_id,name,created_at,updated_at) VALUES (1,1,'Keep',0,0)")
        conn.execute("INSERT INTO plan_semesters (plan_id,term_index,name) VALUES (1,1,'Spring 2027')")
        conn.execute("INSERT INTO plan_courses (plan_id,course_id,term_index) VALUES (1,1,1)")
        conn.execute("INSERT INTO student_course_progress (student_id,course_id,status,updated_at,grade) "
                     "VALUES (1,1,'Completed',0,'A')")
        conn.commit()
    result = launch(backend, "-ClearSavedData", "-KeepDemoPasswords")
    assert "Existing passwords kept" in result.stdout
    with closing(sqlite3.connect(instance / "plans.sqlite3")) as conn:
        for table in ("plans", "plan_courses", "plan_semesters", "student_course_progress"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: S608
        assert conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 6
    launch(backend, "-Reset")
    with closing(sqlite3.connect(instance / "users.sqlite3")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 6
    assert (instance / ".edunav-demo.json").is_file()
    assert not (backend / "do-not-read").exists()
