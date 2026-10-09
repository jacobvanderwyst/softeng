"""Security audit trail: who did what, to what, from where, with what outcome.

Each event is written to the dedicated audit logger (JSON-friendly) AND to the ``audit_log`` table.
Never put passwords, tokens or request bodies in ``detail``.
The database row is added to the caller's transaction: the caller commits.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from flask import g, has_request_context, request
from sqlalchemy.engine import Connection

from . import clock
from .logging_setup import AUDIT_LOGGER
from .repositories import security_store as store

_log = logging.getLogger(AUDIT_LOGGER)


def record(
    conn: Connection,
    action: str,
    *,
    outcome: str = "success",
    actor_user_id: int | None = None,
    actor_role: str | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    request_id = ip = None
    if has_request_context():
        request_id, ip = g.get("request_id"), request.remote_addr
        auth = g.get("auth")
        if auth and actor_user_id is None:
            actor_user_id, actor_role = auth.user_id, auth.role
    event = {
        "action": action,
        "outcome": outcome,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "target_type": target_type,
        "target_id": None if target_id is None else str(target_id),
        "ip": ip,
        "detail": detail or None,
    }
    store.insert_audit(
        conn,
        occurred_at=clock.now(),
        request_id=request_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=action,
        target_type=target_type,
        target_id=event["target_id"],
        outcome=outcome,
        ip=ip,
        detail=json.dumps(detail, sort_keys=True) if detail else None,
    )
    _log.info("%s %s", action, outcome, extra={"audit": event})
