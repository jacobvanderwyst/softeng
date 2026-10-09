"""Server-side sessions.

The browser holds only an opaque random token. The database stores an HMAC of it (keyed with
SECRET_KEY), so a leaked database cannot be replayed as cookies. Sessions can be revoked
(logout, password change, account deactivation) and have idle + absolute timeouts.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from .. import clock
from ..config import Settings
from ..repositories import security_store as store

TOUCH_INTERVAL = 60  # seconds: avoid a database write on every request
PURGE_AFTER = 7 * 86400


@dataclass(frozen=True)
class AuthContext:
    session_id: int
    user_id: int
    username: str
    role: str
    csrf_token: str


class SessionManager:
    def __init__(self, settings: Settings):
        self._settings = settings

    def _digest(self, token: str, key: str) -> str:
        return hmac.new(key.encode(), token.encode(), hashlib.sha256).hexdigest()

    def create(self, conn: Connection, user_id: int, ip: str | None, user_agent: str | None) -> tuple[str, str]:
        """Create a session. Returns (cookie_token, csrf_token). Caller commits."""
        now = clock.now()
        store.purge_sessions(conn, now - PURGE_AFTER)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        store.insert_session(
            conn,
            token_hash=self._digest(token, self._settings.secret_key),
            user_id=user_id,
            csrf_token=csrf,
            now=now,
            expires_at=now + self._settings.session_absolute_timeout,
            ip=ip,
            user_agent=user_agent,
        )
        return token, csrf

    def load(self, conn: Connection, token: str) -> AuthContext | None:
        now = clock.now()
        row = None
        for key in (self._settings.secret_key, *self._settings.secret_key_fallbacks):
            row = store.find_session(conn, self._digest(token, key))
            if row:
                break
        if row is None:
            return None
        if (
            row["revoked_at"] is not None
            or not row["is_active"]
            or row["expires_at"] <= now
            or row["last_seen_at"] + self._settings.session_idle_timeout <= now
        ):
            return None
        if now - row["last_seen_at"] >= TOUCH_INTERVAL:
            store.touch_session(conn, row["session_id"], now)
            conn.commit()
        return AuthContext(
            session_id=row["session_id"],
            user_id=row["user_id"],
            username=row["username"],
            role=row["role"],
            csrf_token=row["csrf_token"],
        )

    def revoke(self, conn: Connection, session_id: int) -> None:
        store.revoke_session(conn, session_id, clock.now())

    def revoke_all(self, conn: Connection, user_id: int, except_session_id: int | None = None) -> int:
        return store.revoke_user_sessions(conn, user_id, clock.now(), except_session_id)
