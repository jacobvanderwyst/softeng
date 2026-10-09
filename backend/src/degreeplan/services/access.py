"""Authorization decisions (least privilege), kept separate from HTTP code so they are easy to test.

* student: read + write their OWN plans only
* teacher: read-only access to plans of students assigned to them (students.advisor_id)
* admin:   read-only access to plans (account management happens through the CLI, not the API)
"""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.engine import Connection

from ..repositories import people

AccessLevel = Literal["write", "read"]


def access_to_student(
    users: Connection, *, user_id: int, role: str, student: dict[str, Any] | None
) -> AccessLevel | None:
    """What may this user do with this student's plans? None means no access at all."""
    if student is None:
        return None
    if role == "admin":
        return "read"
    if role == "student":
        return "write" if student["user_id"] == user_id else None
    if role == "teacher":
        teacher = people.teacher_by_user_id(users, user_id)
        if teacher and student["advisor_id"] == teacher["id"]:
            return "read"
    return None
