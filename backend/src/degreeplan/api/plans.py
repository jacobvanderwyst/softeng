"""Degree plan endpoints.

Access (see services/access.py): students read/write their own plans; teachers read plans of students
assigned to them; admins read all plans. Anyone without access gets 404 (existence is not revealed).
"""
from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from .. import audit, clock, context
from ..db import plans_conn, users_conn
from ..errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ..repositories import catalog, people
from ..repositories import plans as plans_repo
from ..security.auth import login_required, require_auth
from ..services import access, plan_rules
from ..validation import PlanCourseIn, PlanCreateIn, PlanUpdateIn, parse_body
from .utils import int_arg, iso

log = logging.getLogger(__name__)
bp = Blueprint("plans", __name__, url_prefix="/plans")


def _commit_with_audit(action: str, plan_id: int, detail: dict[str, Any] | None = None) -> None:
    """Commit the plan change, then record it in the audit trail (separate database)."""
    plans_conn().commit()
    audit.record(users_conn(), action, target_type="plan", target_id=plan_id, detail=detail)
    users_conn().commit()


def _present(plan: dict[str, Any], courses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {**plan, "created_at": iso(plan["created_at"]), "updated_at": iso(plan["updated_at"])}
    if courses is not None:
        out["courses"] = courses
        out["total_credits"] = sum(c["credits"] for c in courses)
    return out


def _load_plan(plan_id: int, *, write: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    auth = require_auth()
    plan = plans_repo.get_plan(plans_conn(), plan_id)
    if not plan:
        raise NotFoundError("Plan not found.")
    student = people.student_by_id(users_conn(), plan["student_id"])
    level = access.access_to_student(users_conn(), user_id=auth.user_id, role=auth.role, student=student)
    if level is None:
        log.warning("User %s denied access to plan %s", auth.user_id, plan_id)
        raise NotFoundError("Plan not found.")
    if write and level != "write":
        raise ForbiddenError("You have read-only access to this plan.")
    return plan, plans_repo.list_plan_courses(plans_conn(), plan_id)


def _issues_for(plan: dict[str, Any], courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conn = plans_conn()
    program = catalog.get_program(conn, plan["program_id"]) or {"requirements": []}
    prereqs = catalog.prerequisite_ids(conn, [c["course_id"] for c in courses])
    return plan_rules.validate_plan(
        courses,
        prereqs,
        {r["id"] for r in program["requirements"]},
        context.settings().max_credits_per_term,
    )


@bp.get("")
@login_required
def list_plans():
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
    return jsonify({"items": [_present(p) for p in plans_repo.list_for_student(plans_conn(), student_id)]})


@bp.post("")
@login_required
def create_plan():
    auth = require_auth()
    if auth.role != "student":
        raise ForbiddenError("Only students can create plans.")
    body = parse_body(PlanCreateIn)
    student = people.student_by_user_id(users_conn(), auth.user_id)
    if not student:
        raise ForbiddenError("No student record is linked to this account.")
    if not catalog.program_exists(plans_conn(), body.program_id):
        raise ValidationError("Unknown program_id.")
    plan_id = plans_repo.create_plan(
        plans_conn(), student_id=student["id"], program_id=body.program_id, name=body.name, now=clock.now()
    )
    _commit_with_audit("plan.create", plan_id)
    plan, courses = _load_plan(plan_id)
    return jsonify(_present(plan, courses)), 201


@bp.get("/<int:plan_id>")
@login_required
def get_plan(plan_id: int):
    plan, courses = _load_plan(plan_id)
    return jsonify(_present(plan, courses))


@bp.patch("/<int:plan_id>")
@login_required
def update_plan(plan_id: int):
    _load_plan(plan_id, write=True)
    body = parse_body(PlanUpdateIn)
    plans_repo.rename_plan(plans_conn(), plan_id, body.name, clock.now())
    _commit_with_audit("plan.update", plan_id)
    plan, courses = _load_plan(plan_id)
    return jsonify(_present(plan, courses))


@bp.delete("/<int:plan_id>")
@login_required
def delete_plan(plan_id: int):
    _load_plan(plan_id, write=True)
    plans_repo.delete_plan(plans_conn(), plan_id)
    _commit_with_audit("plan.delete", plan_id)
    return "", 204


@bp.put("/<int:plan_id>/courses")
@login_required
def set_plan_course(plan_id: int):
    """Add a course to a term (or move it if already in the plan)."""
    plan, courses = _load_plan(plan_id, write=True)
    body = parse_body(PlanCourseIn)
    course = catalog.get_course(plans_conn(), body.course_id)
    if not course:
        raise ValidationError("Unknown course_id.")

    max_credits = context.settings().max_credits_per_term
    others = [c for c in courses if c["course_id"] != body.course_id]
    term_total = sum(c["credits"] for c in others if c["term_index"] == body.term_index) + course["credits"]
    if term_total > max_credits:
        raise ConflictError(f"Term {body.term_index} would have {term_total} credits (max {max_credits}).")

    plans_repo.set_course(plans_conn(), plan_id, body.course_id, body.term_index, clock.now())
    _commit_with_audit("plan.course.set", plan_id, {"course_id": body.course_id, "term_index": body.term_index})
    plan, courses = _load_plan(plan_id)
    result = _present(plan, courses)
    result["issues"] = _issues_for(plan, courses)
    return jsonify(result)


@bp.delete("/<int:plan_id>/courses/<int:course_id>")
@login_required
def remove_plan_course(plan_id: int, course_id: int):
    _load_plan(plan_id, write=True)
    if not plans_repo.remove_course(plans_conn(), plan_id, course_id, clock.now()):
        raise NotFoundError("Course is not in this plan.")
    _commit_with_audit("plan.course.remove", plan_id, {"course_id": course_id})
    return "", 204


@bp.get("/<int:plan_id>/validation")
@login_required
def validate_plan(plan_id: int):
    plan, courses = _load_plan(plan_id)
    issues = _issues_for(plan, courses)
    return jsonify({"valid": not issues, "issues": issues})
