import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError

from degreeplan.db import migrate
from degreeplan.db.tables import (
    courses,
    plan_courses,
    plan_semesters,
    plans,
    programs,
    student_course_progress,
)
from degreeplan.repositories import plans as plans_repo
from tests.integration.test_migrations import make_dbs


def seed_legacy(conn):
    conn.execute(sa.insert(programs).values(id=1, code="LEGACY", name="Existing Program", total_credits=120))
    conn.execute(sa.insert(courses).values(id=1, code="OLD101", title="Existing Course", credits=4))
    conn.execute(sa.insert(plans).values(
        id=1, student_id=1, program_id=1, name="Existing Plan", created_at=100, updated_at=200,
    ))
    conn.execute(sa.insert(plan_courses).values(plan_id=1, course_id=1, term_index=2))


def test_forward_migration_preserves_legacy_data_without_seeding_or_backfill(tmp_path):
    dbs = make_dbs(tmp_path)
    try:
        migrate.upgrade(dbs.plans, "plans", "plans_0001")
        with dbs.plans.begin() as conn:
            seed_legacy(conn)
            before = {table.name: conn.execute(sa.select(table)).all()
                      for table in (programs, courses, plans, plan_courses)}
        migrate.upgrade(dbs.plans, "plans")
        migrate.upgrade(dbs.plans, "plans")
        with dbs.plans.connect() as conn:
            assert migrate.current_revision(conn) == "plans_0002"
            for table in (programs, courses, plans, plan_courses):
                assert conn.execute(sa.select(table)).all() == before[table.name]
            assert conn.execute(sa.select(plan_semesters)).all() == []
            assert conn.execute(sa.select(student_course_progress)).all() == []
            assert plans_repo.list_semesters(conn, 1) == [{"term_index": 2, "name": "Term 2"}]
            assert plans_repo.list_plan_courses(conn, 1)[0]["code"] == "OLD101"
        with dbs.plans.begin() as conn:
            plans_repo.set_semester(conn, 1, 1, "New empty term", 300)
        with dbs.plans.begin() as conn:
            command.downgrade(migrate._config("plans", conn), "plans_0001")
        with dbs.plans.connect() as conn:
            assert conn.execute(sa.select(plan_courses)).all() == before["plan_courses"]
            assert conn.execute(sa.select(courses)).all() == before["courses"]
        migrate.upgrade(dbs.plans, "plans")
    finally:
        dbs.dispose()


@pytest.mark.parametrize("table,values", [
    (plan_semesters, {"plan_id": 1, "term_index": 0, "name": "bad"}),
    (plan_semesters, {"plan_id": 1, "term_index": 17, "name": "bad"}),
    (student_course_progress, {"student_id": 1, "course_id": 1, "status": "Official", "updated_at": 1}),
])
def test_new_schema_enforces_term_and_status_constraints(tmp_path, table, values):
    dbs = make_dbs(tmp_path)
    try:
        migrate.upgrade(dbs.plans, "plans")
        with dbs.plans.begin() as conn:
            seed_legacy(conn)
        with pytest.raises(IntegrityError), dbs.plans.begin() as conn:
            conn.execute(sa.insert(table).values(**values))
    finally:
        dbs.dispose()
