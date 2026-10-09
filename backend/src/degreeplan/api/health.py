"""Liveness and readiness probes. Responses are deliberately minimal (no versions, paths, or errors)."""
from __future__ import annotations

import logging

import sqlalchemy as sa
from flask import Blueprint, jsonify

from ..db import plans_conn, users_conn
from ..db.migrate import is_at_head

log = logging.getLogger(__name__)
bp = Blueprint("health", __name__, url_prefix="/health")


@bp.get("/live")
def live():
    return jsonify({"status": "ok"})


@bp.get("/ready")
def ready():
    """Ready only if both databases answer and both are migrated to the expected revision."""
    try:
        for name, conn in (("users", users_conn()), ("plans", plans_conn())):
            conn.execute(sa.text("SELECT 1"))
            if not is_at_head(conn, name):
                log.error("Readiness: %s database is not at the expected migration revision", name)
                return jsonify({"status": "not_ready"}), 503
    except Exception:
        log.exception("Readiness check failed")
        return jsonify({"status": "not_ready"}), 503
    return jsonify({"status": "ready"})
