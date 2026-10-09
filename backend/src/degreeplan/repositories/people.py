"""Students and teachers (profile rows linked 1:1 to a user)."""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import students, teachers
from . import all_rows, new_id, one


# ----- students
def student_by_user_id(conn: Connection, user_id: int) -> dict[str, Any] | None:
    return one(conn.execute(sa.select(students).where(students.c.user_id == user_id)))


def student_by_id(conn: Connection, student_id: int) -> dict[str, Any] | None:
    return one(conn.execute(sa.select(students).where(students.c.id == student_id)))


def create_student(
    conn: Connection,
    *,
    user_id: int,
    first_name: str,
    last_name: str,
    program_id: int | None = None,
    advisor_id: int | None = None,
) -> int:
    result = conn.execute(
        sa.insert(students).values(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            program_id=program_id,
            advisor_id=advisor_id,
        )
    )
    return new_id(result)


def students_for_teacher(conn: Connection, teacher_id: int) -> list[dict[str, Any]]:
    stmt = sa.select(students).where(students.c.advisor_id == teacher_id).order_by(students.c.last_name)
    return all_rows(conn.execute(stmt))


# ----- teachers
def teacher_by_user_id(conn: Connection, user_id: int) -> dict[str, Any] | None:
    return one(conn.execute(sa.select(teachers).where(teachers.c.user_id == user_id)))


def teacher_by_id(conn: Connection, teacher_id: int) -> dict[str, Any] | None:
    return one(conn.execute(sa.select(teachers).where(teachers.c.id == teacher_id)))


def create_teacher(
    conn: Connection, *, user_id: int, first_name: str, last_name: str, department: str | None = None
) -> int:
    result = conn.execute(
        sa.insert(teachers).values(
            user_id=user_id, first_name=first_name, last_name=last_name, department=department
        )
    )
    return new_id(result)
