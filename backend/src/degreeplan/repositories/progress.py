"""Student-reported course statuses, independent of all saved plans."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import courses, student_course_progress
from . import all_rows


def list_for_student(conn: Connection, student_id: int) -> list[dict[str, Any]]:
    return all_rows(conn.execute(
        sa.select(student_course_progress.c.course_id, student_course_progress.c.status,
                  student_course_progress.c.grade)
        .where(student_course_progress.c.student_id == student_id)
        .order_by(student_course_progress.c.course_id)
    ))


def set_status(conn: Connection, student_id: int, course_id: int, status: str, now: int,
               *, grade: str | None = None, set_grade: bool = False) -> None:
    """Portable update-then-insert, matching the existing plan course repository."""
    where = sa.and_(
        student_course_progress.c.student_id == student_id, student_course_progress.c.course_id == course_id
    )
    values: dict[str, Any] = {"status": status, "updated_at": now}
    if set_grade:
        values["grade"] = grade
    updated = conn.execute(sa.update(student_course_progress).where(where).values(**values))
    if updated.rowcount == 0:
        conn.execute(sa.insert(student_course_progress).values(
            student_id=student_id, course_id=course_id, **values
        ))


def summary(conn: Connection, student_id: int) -> dict[str, Any]:
    """All completed catalog records, not only courses appearing in saved plans."""
    points = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    rows = conn.execute(sa.select(courses.c.credits, student_course_progress.c.grade)
                        .join(student_course_progress, courses.c.id == student_course_progress.c.course_id)
                        .where(student_course_progress.c.student_id == student_id,
                               student_course_progress.c.status == "Completed",
                               student_course_progress.c.grade.in_(points),
                               courses.c.credits > 0)).all()
    credits = sum(row.credits for row in rows)
    quality = sum(row.credits * points[row.grade] for row in rows)
    gpa = float((Decimal(quality) / Decimal(credits)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)) if credits else None
    return {"gpa": gpa, "graded_credits": credits, "quality_points": quality, "graded_courses": len(rows)}
