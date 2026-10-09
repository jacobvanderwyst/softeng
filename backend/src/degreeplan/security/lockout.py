"""Persistent per-username login lockout (complements the per-IP rate limiter).

Attempts are tracked for ANY submitted username (existing or not), so a locked response never reveals
whether an account exists. Locks back off exponentially (capped at one hour).
"""
from __future__ import annotations

from sqlalchemy.engine import Connection

from .. import clock
from ..config import Settings
from ..repositories import security_store as store

WINDOW = 3600  # failures older than this no longer count
RETENTION = 86400


def lockout_key(username: str) -> str:
    return username.strip().lower()[:64]


def seconds_locked(conn: Connection, key: str) -> int:
    """Seconds until the key may try again (0 if not locked)."""
    row = store.get_attempt(conn, key)
    if not row:
        return 0
    return max(0, row["locked_until"] - clock.now())


def record_failure(conn: Connection, settings: Settings, key: str) -> int:
    now = clock.now()
    store.purge_attempts(conn, now - RETENTION)
    return store.record_failure(conn, key, now, WINDOW, settings.lockout_threshold, settings.lockout_seconds)


def clear(conn: Connection, key: str) -> None:
    store.clear_attempts(conn, key)
