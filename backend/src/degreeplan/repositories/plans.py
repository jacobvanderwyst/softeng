"""Degree plans (plans database). Portable SQL: no dialect-specific upserts."""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import courses, plan_courses, plans
from . import all_rows, new_id, one


def list_for_student(conn: Connection, student_id: int) -> list[dict[str, Any]]:
    stmt = sa.select(plans).where(plans.c.student_id == student_id).order_by(plans.c.id)
    return all_rows(conn.execute(stmt))


def get_plan(conn: Connection, plan_id: int) -> dict[str, Any] | None:
    return one(conn.execute(sa.select(plans).where(plans.c.id == plan_id)))


def list_plan_courses(conn: Connection, plan_id: int) -> list[dict[str, Any]]:
    stmt = (
        sa.select(
            courses.c.id.label("course_id"),
            courses.c.code,
            courses.c.title,
            courses.c.credits,
            plan_courses.c.term_index,
        )
        .select_from(plan_courses.join(courses, courses.c.id == plan_courses.c.course_id))
        .where(plan_courses.c.plan_id == plan_id)
        .order_by(plan_courses.c.term_index, courses.c.code)
    )
    return all_rows(conn.execute(stmt))


def create_plan(conn: Connection, *, student_id: int, program_id: int, name: str, now: int) -> int:
    result = conn.execute(
        sa.insert(plans).values(
            student_id=student_id, program_id=program_id, name=name, created_at=now, updated_at=now
        )
    )
    return new_id(result)


def rename_plan(conn: Connection, plan_id: int, name: str, now: int) -> None:
    conn.execute(sa.update(plans).where(plans.c.id == plan_id).values(name=name, updated_at=now))


def delete_plan(conn: Connection, plan_id: int) -> None:
    conn.execute(sa.delete(plan_courses).where(plan_courses.c.plan_id == plan_id))
    conn.execute(sa.delete(plans).where(plans.c.id == plan_id))


def set_course(conn: Connection, plan_id: int, course_id: int, term_index: int, now: int) -> None:
    """Add a course to the plan or move it to another term (update, then insert if absent)."""
    where = sa.and_(plan_courses.c.plan_id == plan_id, plan_courses.c.course_id == course_id)
    updated = conn.execute(sa.update(plan_courses).where(where).values(term_index=term_index))
    if updated.rowcount == 0:
        conn.execute(sa.insert(plan_courses).values(plan_id=plan_id, course_id=course_id, term_index=term_index))
    conn.execute(sa.update(plans).where(plans.c.id == plan_id).values(updated_at=now))


def remove_course(conn: Connection, plan_id: int, course_id: int, now: int) -> bool:
    where = sa.and_(plan_courses.c.plan_id == plan_id, plan_courses.c.course_id == course_id)
    removed = conn.execute(sa.delete(plan_courses).where(where)).rowcount > 0
    if removed:
        conn.execute(sa.update(plans).where(plans.c.id == plan_id).values(updated_at=now))
    return removed
