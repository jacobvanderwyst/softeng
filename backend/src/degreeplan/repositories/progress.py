"""Student-reported course statuses, independent of all saved plans."""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import student_course_progress
from . import all_rows


def list_for_student(conn: Connection, student_id: int) -> list[dict[str, Any]]:
    return all_rows(conn.execute(
        sa.select(student_course_progress.c.course_id, student_course_progress.c.status)
        .where(student_course_progress.c.student_id == student_id)
        .order_by(student_course_progress.c.course_id)
    ))


def set_status(conn: Connection, student_id: int, course_id: int, status: str, now: int) -> None:
    """Portable update-then-insert, matching the existing plan course repository."""
    where = sa.and_(
        student_course_progress.c.student_id == student_id, student_course_progress.c.course_id == course_id
    )
    updated = conn.execute(sa.update(student_course_progress).where(where).values(status=status, updated_at=now))
    if updated.rowcount == 0:
        conn.execute(sa.insert(student_course_progress).values(
            student_id=student_id, course_id=course_id, status=status, updated_at=now
        ))
