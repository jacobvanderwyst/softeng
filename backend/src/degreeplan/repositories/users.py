from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import users
from . import all_rows, new_id, one

_PUBLIC = (users.c.id, users.c.username, users.c.role, users.c.is_active, users.c.created_at)


def normalize_username(username: str) -> str:
    """Usernames are case-insensitive; they are stored and looked up in lower case."""
    return username.strip().lower()


def get_by_username(conn: Connection, username: str) -> dict[str, Any] | None:
    """Includes password_hash: only for authentication code."""
    stmt = sa.select(users).where(users.c.username == normalize_username(username))
    return one(conn.execute(stmt))


def get_by_id(conn: Connection, user_id: int) -> dict[str, Any] | None:
    return one(conn.execute(sa.select(*_PUBLIC).where(users.c.id == user_id)))


def list_users(conn: Connection) -> list[dict[str, Any]]:
    return all_rows(conn.execute(sa.select(*_PUBLIC).order_by(users.c.username)))


def create(conn: Connection, *, username: str, password_hash: str, role: str, now: int) -> int:
    result = conn.execute(
        sa.insert(users).values(
            username=normalize_username(username),
            password_hash=password_hash,
            role=role,
            is_active=True,
            created_at=now,
            password_changed_at=now,
        )
    )
    return new_id(result)


def set_password_hash(conn: Connection, user_id: int, password_hash: str, now: int | None = None) -> None:
    values: dict[str, Any] = {"password_hash": password_hash}
    if now is not None:
        values["password_changed_at"] = now
    conn.execute(sa.update(users).where(users.c.id == user_id).values(**values))


def set_active(conn: Connection, user_id: int, active: bool) -> bool:
    result = conn.execute(sa.update(users).where(users.c.id == user_id).values(is_active=active))
    return result.rowcount > 0
