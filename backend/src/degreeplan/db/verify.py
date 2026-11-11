"""Read-only checks against isolated reference schemas; never infer or stamp unknown schemas."""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

from . import migrate
from . import tables as t


def _sql(value) -> str:
    return re.sub(r"[\s()\"`\[\]]", "", str(value or "")).lower()


def _schema(conn: Connection) -> dict:
    inspector = sa.inspect(conn)
    result = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        result[table] = {
            "columns": sorted((c["name"], str(c["type"]), c["nullable"], _sql(c.get("default")))
                              for c in inspector.get_columns(table)),
            "pk": tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            "unique": sorted(tuple(c["column_names"]) for c in inspector.get_unique_constraints(table)),
            "indexes": sorted((tuple(c["column_names"]), bool(c["unique"])) for c in inspector.get_indexes(table)),
            "checks": sorted(re.sub(r"[\s\"`\[\]]", "", c["sqltext"])
                             for c in inspector.get_check_constraints(table)),
            "fks": sorted((tuple(c["constrained_columns"]), c["referred_table"], tuple(c["referred_columns"]),
                           c.get("options", {}).get("ondelete", "").upper())
                          for c in inspector.get_foreign_keys(table)),
        }
    return result


def validate_schema(conn: Connection, target: str, revision: str = "head") -> None:
    if conn.dialect.name != "sqlite":
        raise ValueError("Schema adoption validation currently supports SQLite only.")
    scripts = ScriptDirectory.from_config(migrate._config(target))
    try:
        current = migrate.current_revision(conn)
        if revision == "current":
            if current is None:
                raise ValueError("Database has no recorded revision.")
            revision = current
        resolved = scripts.get_revision(revision)
        if current is not None:
            scripts.get_revision(current)
    except Exception as exc:
        raise ValueError("Unknown migration revision; adoption refused.") from exc
    if resolved is None:
        raise ValueError("An explicit known schema revision is required.")
    if sa.inspect(conn).get_view_names() or conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).first():
        raise ValueError("Unexpected views or triggers; adoption refused.")
    reference = sa.create_engine("sqlite://")
    try:
        migrate.upgrade(reference, target, resolved.revision)
        with reference.connect() as expected:
            if _schema(conn) != _schema(expected):
                raise ValueError(f"{target}: schema does not match known revision {resolved.revision}.")
    finally:
        reference.dispose()
    sqlite_checks(conn, target)


def sqlite_checks(conn: Connection, target: str) -> None:
    if conn.dialect.name != "sqlite":
        raise ValueError("Database verification currently supports SQLite only.")
    if conn.exec_driver_sql("PRAGMA integrity_check").scalars().all() != ["ok"]:
        raise ValueError(f"{target}: SQLite integrity check failed.")
    if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise ValueError(f"{target}: SQLite foreign key check failed.")


def verify(users: Connection, plans: Connection, *, schema_only: bool = False) -> None:
    for name, conn in (("users", users), ("plans", plans)):
        if not migrate.is_at_head(conn, name):
            raise ValueError(f"{name}: migrations are not at head.")
        validate_schema(conn, name)
    if schema_only:
        return
    student_ids = set(users.execute(sa.select(t.students.c.id)).scalars())
    program_ids = set(plans.execute(sa.select(t.programs.c.id)).scalars())
    for table in (t.plans, t.student_course_progress):
        refs = set(plans.execute(sa.select(table.c.student_id).distinct()).scalars())
        if refs - student_ids:
            raise ValueError(f"{table.name}: unknown student reference.")
    refs = set(users.execute(sa.select(t.students.c.program_id)
                             .where(t.students.c.program_id.is_not(None))).scalars())
    if refs - program_ids:
        raise ValueError("students: unknown program reference.")
    for profile, role in ((t.students, "student"), (t.teachers, "teacher")):
        if users.execute(sa.select(profile.c.id).join(t.users)
                         .where(t.users.c.role != role)).first():
            raise ValueError(f"{profile.name}: profile/account role mismatch.")
        if users.execute(sa.select(t.users.c.id).outerjoin(profile)
                         .where(t.users.c.role == role, profile.c.id.is_(None))).first():
            raise ValueError(f"{profile.name}: missing account profile.")
