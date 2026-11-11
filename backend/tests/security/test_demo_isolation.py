import hashlib
import json
import os
import shutil
import subprocess

import pytest

from degreeplan.config import ConfigError, Settings, load_dotenv_for_development
from degreeplan.devtools.demo_guard import (
    MARKER_NAME,
    DemoSafetyError,
    assert_owned_demo,
    assert_plain_path,
    prepare_demo,
)
from tests.integration.test_demo_help import POWERSHELL, SCRIPT


def managed_env(backend):
    return {
        "APP_ENV": "development",
        "EDUNAV_DEMO": "1",
        "USERS_DATABASE_URL": f"sqlite:///{(backend / 'instance' / 'users.sqlite3').as_posix()}",
        "PLANS_DATABASE_URL": f"sqlite:///{(backend / 'instance' / 'plans.sqlite3').as_posix()}",
    }


def test_new_demo_is_owned_without_creating_databases(tmp_path, monkeypatch):
    monkeypatch.setattr("degreeplan.config.BACKEND_DIR", tmp_path)
    root = prepare_demo(tmp_path)
    assert assert_owned_demo(tmp_path) == root
    assert not (root / "users.sqlite3").exists()
    assert not (root / "plans.sqlite3").exists()
    assert prepare_demo(tmp_path) == root
    settings = Settings.from_env(managed_env(tmp_path))
    assert settings.env_name == "development" and not settings.cookie_secure


def test_legacy_pair_requires_explicit_adoption_without_changing_its_data(tmp_path, db_templates):
    root = tmp_path / "instance"
    root.mkdir()
    before = {}
    for filename in ("users.sqlite3", "plans.sqlite3"):
        shutil.copyfile(db_templates / filename, root / filename)
        before[filename] = hashlib.sha256((root / filename).read_bytes()).digest()
    with pytest.raises(DemoSafetyError, match="AdoptExistingDemo"):
        prepare_demo(tmp_path)
    assert not (root / MARKER_NAME).exists()
    assert prepare_demo(tmp_path, adopt_existing=True) == root
    for filename in before:
        assert hashlib.sha256((root / filename).read_bytes()).digest() == before[filename]


@pytest.mark.parametrize("problem", ["incomplete", "not_sqlite", "unknown_schema"])
def test_bad_legacy_pair_is_never_adopted(tmp_path, problem):
    import sqlite3
    from contextlib import closing

    root = tmp_path / "instance"
    root.mkdir()
    (root / "users.sqlite3").write_bytes(b"not a database")
    if problem != "incomplete":
        (root / "plans.sqlite3").write_bytes(b"not a database")
    if problem == "unknown_schema":
        for filename in ("users.sqlite3", "plans.sqlite3"):
            (root / filename).unlink()
            with closing(sqlite3.connect(root / filename)) as conn:
                conn.execute("CREATE TABLE unrelated (id INTEGER)")
                conn.commit()
    with pytest.raises(DemoSafetyError):
        prepare_demo(tmp_path, adopt_existing=True)
    assert not (root / MARKER_NAME).exists()


@pytest.mark.parametrize("marker", [{"purpose": "production"}, {}, "not-json"])
def test_bad_ownership_marker_is_not_overwritten(tmp_path, marker):
    root = tmp_path / "instance"
    root.mkdir()
    value = marker if isinstance(marker, str) else json.dumps(marker)
    (root / MARKER_NAME).write_text(value, encoding="utf-8")
    with pytest.raises(DemoSafetyError):
        prepare_demo(tmp_path)
    assert (root / MARKER_NAME).read_text(encoding="utf-8") == value


def test_managed_demo_ignores_dotenv_entirely(tmp_path, monkeypatch):
    monkeypatch.setattr("degreeplan.config.BACKEND_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "USERS_DATABASE_URL=sqlite:///do-not-use.sqlite3\nSECRET_KEY_FILE=do-not-read\n", encoding="utf-8",
    )
    env = {"APP_ENV": "development", "EDUNAV_DEMO": "1"}
    load_dotenv_for_development(env)
    assert env == {"APP_ENV": "development", "EDUNAV_DEMO": "1"}


@pytest.mark.parametrize("key", [
    "USERS_DATABASE_URL_FILE", "PLANS_DATABASE_URL_FILE", "SECRET_KEY_FILE", "SECRET_KEY_FALLBACKS_FILE",
])
def test_managed_demo_refuses_file_redirection_before_reading_files(tmp_path, monkeypatch, key):
    monkeypatch.setattr("degreeplan.config.BACKEND_DIR", tmp_path)
    prepare_demo(tmp_path)
    env = managed_env(tmp_path)
    env[key] = "do-not-open-this-file"
    with pytest.raises(ConfigError, match="cannot redirect"):
        Settings.from_env(env)


@pytest.mark.parametrize("key", ["USERS_DATABASE_URL", "PLANS_DATABASE_URL"])
def test_managed_demo_refuses_external_or_missing_target(tmp_path, monkeypatch, key):
    monkeypatch.setattr("degreeplan.config.BACKEND_DIR", tmp_path)
    prepare_demo(tmp_path)
    env = managed_env(tmp_path)
    env[key] = f"sqlite:///{(tmp_path / 'production.sqlite3').as_posix()}"
    with pytest.raises(ConfigError, match="repository-local"):
        Settings.from_env(env)
    del env[key]
    with pytest.raises(ConfigError, match="explicitly"):
        Settings.from_env(env)
    assert not (tmp_path / "production.sqlite3").exists()


def test_managed_demo_cannot_be_production(tmp_path, monkeypatch):
    monkeypatch.setattr("degreeplan.config.BACKEND_DIR", tmp_path)
    env = managed_env(tmp_path)
    env["APP_ENV"] = "production"
    with pytest.raises(ConfigError, match="only permitted in development"):
        Settings.from_env(env)


def test_production_cannot_open_demo_owned_directory(tmp_path):
    root = prepare_demo(tmp_path)
    with pytest.raises(ConfigError, match="demo-owned"):
        Settings.for_testing(
            f"sqlite:///{(root / 'users.sqlite3').as_posix()}",
            f"sqlite:///{(root / 'plans.sqlite3').as_posix()}",
            env_name="production", ratelimit_enabled=True,
        )


def test_equivalent_sqlite_paths_cannot_select_the_same_database(tmp_path):
    first = f"sqlite:///{(tmp_path / 'users.sqlite3').as_posix()}"
    alias = f"sqlite:///{tmp_path.as_posix()}/unused/../users.sqlite3"
    with pytest.raises(ConfigError, match="different"):
        Settings.for_testing(first, alias)


def test_database_hardlinks_cannot_redirect_demo_writes(tmp_path):
    root = prepare_demo(tmp_path)
    target = tmp_path / "not-demo.sqlite3"
    target.write_bytes(b"must remain untouched")
    try:
        os.link(target, root / "users.sqlite3")
    except OSError:
        pytest.skip("Filesystem does not support hard links")
    with pytest.raises(DemoSafetyError, match="hard-linked"):
        assert_owned_demo(tmp_path)
    assert target.read_bytes() == b"must remain untouched"


def test_reparse_or_symlink_ancestor_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("No directory link support")
        command = shutil.which("cmd.exe")
        if command is None:
            pytest.skip("Windows command processor is not installed")
        result = subprocess.run(  # noqa: S603
            [command, "/c", "mklink", "/J", str(link), str(real)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode:
            pytest.skip("Cannot create a test junction")
    with pytest.raises(DemoSafetyError, match="links or reparse"):
        assert_plain_path(link / "users.sqlite3")


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not installed")
@pytest.mark.parametrize("key", ["USERS_DATABASE_URL_FILE", "PLANS_DATABASE_URL_FILE"])
def test_launcher_rejects_inherited_redirect_before_any_setup(tmp_path, key):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "demo.ps1"
    shutil.copyfile(SCRIPT, script)
    environment = {name: value for name, value in os.environ.items()
                   if name not in {"USERS_DATABASE_URL", "PLANS_DATABASE_URL",
                                   "USERS_DATABASE_URL_FILE", "PLANS_DATABASE_URL_FILE"}}
    environment.update({"APP_ENV": "development", key: str(tmp_path / "not-read")})
    result = subprocess.run(  # noqa: S603
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(script), "-Reset", "-NoRun"],
        env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert key in result.stderr
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "instance").exists()
