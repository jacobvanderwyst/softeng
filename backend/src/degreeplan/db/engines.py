"""Database engines (one per database) and request-scoped connections.

The code only uses SQLAlchemy Core, so either database can move from SQLite to PostgreSQL / SQL Server
by changing its URL.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from flask import Flask, g
from sqlalchemy.engine import Connection, Engine, make_url

from ..config import Settings, sqlite_file_path
from ..context import services

log = logging.getLogger(__name__)


def build_engine(url: str) -> Engine:
    parsed = make_url(url)
    db_file = sqlite_file_path(url)
    if db_file is not None:
        db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(parsed, pool_pre_ping=True)
    if parsed.get_backend_name() == "sqlite":

        @sa.event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return engine


class Databases:
    """Holds the two engines. Never logs URLs (they may contain credentials)."""

    def __init__(self, settings: Settings):
        self.users: Engine = build_engine(settings.users_database_url)
        self.plans: Engine = build_engine(settings.plans_database_url)
        log.info(
            "Databases configured: users=%s plans=%s",
            self.users.dialect.name,
            self.plans.dialect.name,
        )

    def dispose(self) -> None:
        self.users.dispose()
        self.plans.dispose()


# ----- request-scoped connections (rolled back + closed on teardown unless committed)
def users_conn() -> Connection:
    if "users_conn" not in g:
        g.users_conn = services().databases.users.connect()
    return g.users_conn


def plans_conn() -> Connection:
    if "plans_conn" not in g:
        g.plans_conn = services().databases.plans.connect()
    return g.plans_conn


def _close_connections(_exc: BaseException | None = None) -> None:
    for key in ("users_conn", "plans_conn"):
        conn = g.pop(key, None)
        if conn is not None:
            conn.close()  # implicit rollback of anything uncommitted


def init_app(app: Flask) -> None:
    app.teardown_appcontext(_close_connections)
