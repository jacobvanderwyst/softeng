"""Administrator-only instructor provisioning and catalog degree-program requirements."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from .. import audit, clock, context
from ..db import plans_conn, users_conn
from ..errors import ConflictError, NotFoundError, ValidationError
from ..repositories import administration, catalog, people
from ..repositories import users as users_repo
from ..security.auth import roles_required
from ..security.passwords import PasswordPolicyError
from ..security.protection import limiter
from ..validation import InstructorCreateIn, ProgramAdminIn, ProgramAdminUpdateIn, parse_body
from .utils import int_arg

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _program(program_id: int) -> dict[str, Any]:
    program = catalog.get_program(plans_conn(), program_id)
    if program is None:
        raise NotFoundError("Degree program not found.")
    state = {key: program[key] for key in ("id", "code", "name", "total_credits")}
    state["course_ids"] = sorted(course["id"] for course in program["requirements"])
    revision = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**program, "revision": revision}


@bp.get("/instructors")
@roles_required("admin")
def list_instructors():
    limit = int_arg("limit", 25, 1, 100)
    offset = int_arg("offset", 0, 0, 1_000_000)
    search = (request.args.get("q") or "").strip()[:64]
    items, total = administration.list_instructors(users_conn(), search, limit, offset)
    return jsonify({"items": items, "total": total, "limit": limit, "offset": offset,
                    "password_min_length": context.settings().password_min_length})


@bp.post("/instructors")
@roles_required("admin")
@limiter.limit("10 per minute")
def create_instructor():
    body = parse_body(InstructorCreateIn)
    svc = context.services()
    username = users_repo.normalize_username(body.username)
    try:
        svc.passwords.validate_new(body.password, username)
    except PasswordPolicyError as exc:
        raise ValidationError(
            "Password does not meet the policy.",
            details=[{"field": "password", "message": problem} for problem in exc.problems],
        ) from None
    conn = users_conn()
    try:
        user_id = users_repo.create(conn, username=username, role="teacher",
                                    password_hash=svc.passwords.hash(body.password), now=clock.now())
        teacher_id = people.create_teacher(
            conn, user_id=user_id, first_name=body.first_name, last_name=body.last_name, department=body.department,
        )
        audit.record(conn, "admin.instructor.create", target_type="user", target_id=user_id)
        conn.commit()
    except IntegrityError:
        conn.rollback()
        raise ConflictError("This username cannot be used for a new instructor.") from None
    return jsonify({
        "id": teacher_id, "user_id": user_id, "username": username, "role": "teacher", "is_active": True,
        "first_name": body.first_name, "last_name": body.last_name, "department": body.department,
    }), 201


@bp.get("/programs/<int:program_id>")
@roles_required("admin")
def get_program(program_id: int):
    return jsonify(_program(program_id))


def _save_program(body: ProgramAdminIn, program_id: int | None = None) -> dict[str, Any]:
    conn = plans_conn()
    administration.begin_program_edit(conn)
    if program_id is not None:
        current = _program(program_id)
        if not isinstance(body, ProgramAdminUpdateIn) or body.expected_revision != current["revision"]:
            raise ConflictError("This degree program changed. Reload it before saving your edits.")
    if administration.existing_course_ids(conn, body.course_ids) != set(body.course_ids):
        raise ValidationError("One or more required courses do not exist.")
    try:
        program_id = administration.save_program(
            conn, program_id=program_id, code=body.code, name=body.name,
            total_credits=body.total_credits, course_ids=body.course_ids,
        )
        conn.commit()
    except IntegrityError:
        conn.rollback()
        raise ConflictError("That degree-program code is already in use or conflicts with catalog data.") from None
    # As with other catalog/plan changes, audit uses a separate database, not a distributed transaction.
    audit.record(users_conn(), "admin.program.save", target_type="program", target_id=program_id,
                 detail={"required_courses": len(body.course_ids)})
    users_conn().commit()
    return _program(program_id)


@bp.post("/programs")
@roles_required("admin")
@limiter.limit("30 per minute")
def create_program():
    return jsonify(_save_program(parse_body(ProgramAdminIn))), 201


@bp.put("/programs/<int:program_id>")
@roles_required("admin")
@limiter.limit("30 per minute")
def update_program(program_id: int):
    return jsonify(_save_program(parse_body(ProgramAdminUpdateIn), program_id))
