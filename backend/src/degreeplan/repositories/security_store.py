"""Persistence for sessions, login-attempt counters, and the audit trail (users database)."""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import audit_log, login_attempts, sessions, users
from . import all_rows, new_id, one


# ----- sessions
def insert_session(
    conn: Connection,
    *,
    token_hash: str,
    user_id: int,
    csrf_token: str,
    now: int,
    expires_at: int,
    ip: str | None,
    user_agent: str | None,
) -> int:
    result = conn.execute(
        sa.insert(sessions).values(
            token_hash=token_hash,
            user_id=user_id,
            csrf_token=csrf_token,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            ip=ip,
            user_agent=(user_agent or "")[:200] or None,
        )
    )
    return new_id(result)


def find_session(conn: Connection, token_hash: str) -> dict[str, Any] | None:
    """Session joined with its (active) user. Validity (timeouts, revocation) is decided by the caller."""
    stmt = (
        sa.select(
            sessions.c.id.label("session_id"),
            sessions.c.user_id,
            sessions.c.csrf_token,
            sessions.c.last_seen_at,
            sessions.c.expires_at,
            sessions.c.revoked_at,
            users.c.username,
            users.c.role,
            users.c.is_active,
        )
        .select_from(sessions.join(users, users.c.id == sessions.c.user_id))
        .where(sessions.c.token_hash == token_hash)
    )
    return one(conn.execute(stmt))


def touch_session(conn: Connection, session_id: int, now: int) -> None:
    conn.execute(sa.update(sessions).where(sessions.c.id == session_id).values(last_seen_at=now))


def revoke_session(conn: Connection, session_id: int, now: int) -> None:
    where = sa.and_(sessions.c.id == session_id, sessions.c.revoked_at.is_(None))
    conn.execute(sa.update(sessions).where(where).values(revoked_at=now))


def revoke_user_sessions(conn: Connection, user_id: int, now: int, except_session_id: int | None = None) -> int:
    where = sa.and_(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
    if except_session_id is not None:
        where = sa.and_(where, sessions.c.id != except_session_id)
    return conn.execute(sa.update(sessions).where(where).values(revoked_at=now)).rowcount


def purge_sessions(conn: Connection, older_than: int) -> int:
    stale = sa.or_(sessions.c.expires_at < older_than, sessions.c.revoked_at < older_than)
    return conn.execute(sa.delete(sessions).where(stale)).rowcount


# ----- login attempts (persistent per-username lockout)
def get_attempt(conn: Connection, key: str) -> dict[str, Any] | None:
    return one(conn.execute(sa.select(login_attempts).where(login_attempts.c.key == key)))


def record_failure(conn: Connection, key: str, now: int, window: int, threshold: int, lock_seconds: int) -> int:
    """Count a failed login; lock the key once ``threshold`` is reached. Returns the new failure count."""
    row = get_attempt(conn, key)
    if row is None or row["last_failed_at"] < now - window:
        count = 1
    else:
        count = row["failed_count"] + 1
    locked_until = 0
    if count >= threshold:
        # Exponential backoff: each extra failure past the threshold doubles the lock (capped at 1 hour).
        locked_until = now + min(lock_seconds * 2 ** (count - threshold), 3600)
    values = {"failed_count": count, "last_failed_at": now, "locked_until": locked_until}
    if row is None:
        conn.execute(sa.insert(login_attempts).values(key=key, **values))
    else:
        conn.execute(sa.update(login_attempts).where(login_attempts.c.key == key).values(**values))
    return count


def clear_attempts(conn: Connection, key: str) -> None:
    conn.execute(sa.delete(login_attempts).where(login_attempts.c.key == key))


def purge_attempts(conn: Connection, older_than: int) -> int:
    stale = sa.and_(login_attempts.c.last_failed_at < older_than, login_attempts.c.locked_until < older_than)
    return conn.execute(sa.delete(login_attempts).where(stale)).rowcount


# ----- audit
def insert_audit(conn: Connection, **fields: Any) -> None:
    conn.execute(sa.insert(audit_log).values(**fields))


def recent_audit(conn: Connection, limit: int = 100) -> list[dict[str, Any]]:
    stmt = sa.select(audit_log).order_by(audit_log.c.id.desc()).limit(limit)
    return all_rows(conn.execute(stmt))
