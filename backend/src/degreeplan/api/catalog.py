"""Read-only catalog endpoints: courses and programs (any authenticated user)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import plans_conn
from ..errors import NotFoundError
from ..repositories import catalog
from ..security.auth import login_required
from .utils import int_arg

bp = Blueprint("catalog", __name__)


@bp.get("/courses")
@login_required
def list_courses():
    limit = int_arg("limit", 25, 1, 100)
    offset = int_arg("offset", 0, 0, 1_000_000)
    q = (request.args.get("q") or "").strip()[:64] or None
    items, total = catalog.list_courses(plans_conn(), q, limit, offset)
    return jsonify({"items": items, "total": total, "limit": limit, "offset": offset})


@bp.get("/courses/<int:course_id>")
@login_required
def get_course(course_id: int):
    course = catalog.get_course(plans_conn(), course_id)
    if not course:
        raise NotFoundError("Course not found.")
    return jsonify(course)


@bp.get("/programs")
@login_required
def list_programs():
    return jsonify({"items": catalog.list_programs(plans_conn())})


@bp.get("/programs/<int:program_id>")
@login_required
def get_program(program_id: int):
    program = catalog.get_program(plans_conn(), program_id)
    if not program:
        raise NotFoundError("Program not found.")
    return jsonify(program)
