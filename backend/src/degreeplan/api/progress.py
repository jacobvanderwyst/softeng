"""Authenticated student-reported course progress (not an official transcript)."""
from flask import Blueprint, jsonify, request

from .. import audit, clock
from ..db import plans_conn, users_conn
from ..errors import ForbiddenError, NotFoundError, ValidationError
from ..repositories import catalog, people
from ..repositories import progress as progress_repo
from ..security.auth import login_required, require_auth
from ..services import access
from ..validation import CourseProgressIn, parse_body
from .utils import int_arg

bp = Blueprint("progress", __name__, url_prefix="/progress")


@bp.get("")
@login_required
def list_progress():
    auth = require_auth()
    if auth.role == "student":
        student = people.student_by_user_id(users_conn(), auth.user_id)
        if not student:
            raise ForbiddenError("No student record is linked to this account.")
        student_id = student["id"]
    else:
        if not request.args.get("student_id"):
            raise ValidationError("Query parameter 'student_id' is required.")
        student_id = int_arg("student_id", 0, 1, 2**31)
        student = people.student_by_id(users_conn(), student_id)
        level = access.access_to_student(users_conn(), user_id=auth.user_id, role=auth.role, student=student)
        if level is None:
            raise NotFoundError("Student not found.")
    return jsonify({"items": progress_repo.list_for_student(plans_conn(), student_id)})


@bp.put("/<int:course_id>")
@login_required
def set_progress(course_id: int):
    auth = require_auth()
    if auth.role != "student":
        raise ForbiddenError("Only students can update their own progress.")
    student = people.student_by_user_id(users_conn(), auth.user_id)
    if not student:
        raise ForbiddenError("No student record is linked to this account.")
    body = parse_body(CourseProgressIn)
    if not catalog.get_course(plans_conn(), course_id):
        raise ValidationError("Unknown course_id.")
    progress_repo.set_status(plans_conn(), student["id"], course_id, body.status, clock.now())
    plans_conn().commit()
    audit.record(
        users_conn(), "progress.set", target_type="student", target_id=student["id"],
        detail={"course_id": course_id, "status": body.status},
    )
    users_conn().commit()
    return jsonify({"course_id": course_id, "status": body.status})
