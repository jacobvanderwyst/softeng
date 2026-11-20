"""Queries for administrator-only management; never expose credential/session columns."""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import courses, program_requirements, programs, teachers, users
from . import all_rows, new_id


def list_instructors(conn: Connection, search: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    joined = teachers.join(users, users.c.id == teachers.c.user_id)
    condition = users.c.role == "teacher"
    if search:
        condition = sa.and_(condition, sa.or_(
            users.c.username.icontains(search, autoescape=True),
            teachers.c.first_name.icontains(search, autoescape=True),
            teachers.c.last_name.icontains(search, autoescape=True),
        ))
    total = int(conn.execute(sa.select(sa.func.count()).select_from(joined).where(condition)).scalar_one())
    query = sa.select(
        teachers.c.id, users.c.id.label("user_id"), users.c.username, users.c.is_active,
        teachers.c.first_name, teachers.c.last_name, teachers.c.department,
    ).select_from(joined).where(condition).order_by(users.c.username).limit(limit).offset(offset)
    return all_rows(conn.execute(query)), total


def existing_course_ids(conn: Connection, ids: list[int]) -> set[int]:
    found: set[int] = set()
    for start in range(0, len(ids), 500):
        found.update(conn.execute(sa.select(courses.c.id).where(courses.c.id.in_(ids[start:start + 500]))).scalars())
    return found


def begin_program_edit(conn: Connection) -> None:
    """Take the SQLite writer lock BEFORE loading/checking the program revision."""
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def save_program(
    conn: Connection, *, program_id: int | None, code: str, name: str, total_credits: int, course_ids: list[int],
) -> int:
    values = {"code": code, "name": name, "total_credits": total_credits}
    if program_id is None:
        program_id = new_id(conn.execute(sa.insert(programs).values(**values)))
    else:
        conn.execute(sa.update(programs).where(programs.c.id == program_id).values(**values))
        conn.execute(sa.delete(program_requirements).where(program_requirements.c.program_id == program_id))
    conn.execute(sa.insert(program_requirements),
                 [{"program_id": program_id, "course_id": course_id} for course_id in course_ids])
    return program_id
