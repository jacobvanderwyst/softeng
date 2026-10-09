"""Programmatic Alembic helpers for the two databases ("users" and "plans")."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
TARGETS = ("users", "plans")


def _config(target: str, connection: Connection | None = None) -> Config:
    if target not in TARGETS:
        raise ValueError(f"Unknown migration target: {target}")
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR / target))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision(target: str) -> str | None:
    return ScriptDirectory.from_config(_config(target)).get_current_head()


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine: Engine, target: str, revision: str = "head") -> None:
    with engine.begin() as conn:
        command.upgrade(_config(target, conn), revision)


def stamp(engine: Engine, target: str, revision: str = "head") -> None:
    """Mark an existing database as being at ``revision`` without running migrations."""
    with engine.begin() as conn:
        command.stamp(_config(target, conn), revision)


def is_at_head(connection: Connection, target: str) -> bool:
    return current_revision(connection) == head_revision(target)
