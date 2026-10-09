from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from degreeplan import clock, create_app
from degreeplan.config import Settings
from degreeplan.db import Databases, migrate
from degreeplan.devtools.sample_data import seed_sample_data
from degreeplan.logging_setup import AUDIT_LOGGER
from degreeplan.security.passwords import PasswordService

from .helpers import PASSWORDS, ApiClient, logged_in


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture(scope="session")
def db_templates(tmp_path_factory) -> Path:
    """Migrated + seeded databases, built once (via the real Alembic migrations) and copied per test."""
    directory = tmp_path_factory.mktemp("db-templates")
    settings = Settings.for_testing(sqlite_url(directory / "users.sqlite3"), sqlite_url(directory / "plans.sqlite3"))
    dbs = Databases(settings)
    migrate.upgrade(dbs.users, "users")
    migrate.upgrade(dbs.plans, "plans")
    with dbs.users.connect() as users, dbs.plans.connect() as plans:
        seed_sample_data(
            users, plans, settings=settings, passwords=PasswordService(settings), credentials=PASSWORDS
        )
    dbs.dispose()
    return directory


class AppFactory:
    """Builds apps against per-test copies of the seeded databases."""

    def __init__(self, templates: Path, workdir: Path):
        self.templates, self.workdir = templates, workdir
        self.apps: list = []
        self.current = workdir
        self._count = 0

    def urls(self) -> tuple[str, str]:
        return sqlite_url(self.current / "users.sqlite3"), sqlite_url(self.current / "plans.sqlite3")

    def __call__(self, *, fresh: bool = True, seeded: bool = True, **overrides):
        """fresh=True: new database files in their own directory (seeded copies unless seeded=False).
        fresh=False: reuse the files of the previous app (e.g. to simulate a restart or key rotation)."""
        if fresh:
            self._count += 1
            self.current = self.workdir / f"app{self._count}"
            self.current.mkdir()
            if seeded:
                for name in ("users.sqlite3", "plans.sqlite3"):
                    shutil.copy(self.templates / name, self.current / name)
        users_url, plans_url = self.urls()
        app = create_app(Settings.for_testing(users_url, plans_url, **overrides))
        self.apps.append(app)
        return app

    def close(self) -> None:
        for app in self.apps:
            app.extensions["degreeplan"].databases.dispose()


@pytest.fixture
def make_app(db_templates, tmp_path):
    factory = AppFactory(db_templates, tmp_path)
    yield factory
    factory.close()


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def api(app) -> ApiClient:
    """Anonymous client."""
    return ApiClient(app)


@pytest.fixture
def alice(app) -> ApiClient:
    return logged_in(app, "alice")


@pytest.fixture
def bob(app) -> ApiClient:
    return logged_in(app, "bob")


@pytest.fixture
def carol(app) -> ApiClient:
    return logged_in(app, "carol")


@pytest.fixture
def teacher1(app) -> ApiClient:
    return logged_in(app, "teacher1")


@pytest.fixture
def teacher2(app) -> ApiClient:
    return logged_in(app, "teacher2")


@pytest.fixture
def admin(app) -> ApiClient:
    return logged_in(app, "admin")


class FakeClock:
    """Absolute offsets (seconds) from the moment the test started."""

    def __init__(self, monkeypatch):
        self._start = clock.now()
        self._offset = 0
        monkeypatch.setattr(clock, "now", lambda: self._start + self._offset)

    def set(self, offset_seconds: int) -> None:
        self._offset = offset_seconds


@pytest.fixture
def fake_clock(monkeypatch) -> FakeClock:
    return FakeClock(monkeypatch)


@pytest.fixture
def audit_caplog(caplog):
    """caplog also listening to the audit logger (which does not propagate to the root logger)."""
    audit_logger = logging.getLogger(AUDIT_LOGGER)
    audit_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    yield caplog
    audit_logger.removeHandler(caplog.handler)
