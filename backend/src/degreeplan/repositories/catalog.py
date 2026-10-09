"""Course and program catalog (plans database)."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..db.tables import course_prerequisites, courses, program_requirements, programs
from . import all_rows, one

_COURSE_LIST = (courses.c.id, courses.c.code, courses.c.title, courses.c.credits)


def list_courses(conn: Connection, search: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
    condition = None
    if search:
        # autoescape=True escapes %, _ and the escape char so user input matches literally.
        condition = sa.or_(
            courses.c.code.icontains(search, autoescape=True),
            courses.c.title.icontains(search, autoescape=True),
        )
    count_stmt = sa.select(sa.func.count()).select_from(courses)
    rows_stmt = sa.select(*_COURSE_LIST).order_by(courses.c.code).limit(limit).offset(offset)
    if condition is not None:
        count_stmt, rows_stmt = count_stmt.where(condition), rows_stmt.where(condition)
    total = conn.execute(count_stmt).scalar_one()
    return all_rows(conn.execute(rows_stmt)), int(total)


def get_course(conn: Connection, course_id: int) -> dict[str, Any] | None:
    course = one(conn.execute(sa.select(courses).where(courses.c.id == course_id)))
    if course:
        course["prerequisites"] = prerequisites_of(conn, course_id)
    return course


def prerequisites_of(conn: Connection, course_id: int) -> list[dict[str, Any]]:
    stmt = (
        sa.select(*_COURSE_LIST)
        .select_from(
            course_prerequisites.join(courses, courses.c.id == course_prerequisites.c.prerequisite_id)
        )
        .where(course_prerequisites.c.course_id == course_id)
        .order_by(courses.c.code)
    )
    return all_rows(conn.execute(stmt))


def prerequisite_ids(conn: Connection, course_ids: Iterable[int]) -> dict[int, set[int]]:
    """Prerequisite course ids for each of ``course_ids`` (single query)."""
    ids = list(course_ids)
    result: dict[int, set[int]] = {i: set() for i in ids}
    if not ids:
        return result
    stmt = sa.select(course_prerequisites).where(course_prerequisites.c.course_id.in_(ids))
    for row in conn.execute(stmt).mappings():
        result[row["course_id"]].add(row["prerequisite_id"])
    return result


# ----- programs
def list_programs(conn: Connection) -> list[dict[str, Any]]:
    return all_rows(conn.execute(sa.select(programs).order_by(programs.c.code)))


def get_program(conn: Connection, program_id: int) -> dict[str, Any] | None:
    program = one(conn.execute(sa.select(programs).where(programs.c.id == program_id)))
    if program:
        stmt = (
            sa.select(*_COURSE_LIST)
            .select_from(program_requirements.join(courses, courses.c.id == program_requirements.c.course_id))
            .where(program_requirements.c.program_id == program_id)
            .order_by(courses.c.code)
        )
        program["requirements"] = all_rows(conn.execute(stmt))
    return program


def program_exists(conn: Connection, program_id: int) -> bool:
    stmt = sa.select(sa.literal(1)).select_from(programs).where(programs.c.id == program_id)
    return conn.execute(stmt).first() is not None
