import sqlalchemy as sa

from degreeplan.db import tables
from degreeplan.repositories import security_store
from tests.helpers import PASSWORDS, ApiClient
from tests.integration.test_cli import production_app, run

SAVED_TABLES = (tables.plans, tables.plan_semesters, tables.plan_courses, tables.student_course_progress)
CATALOG_TABLES = (tables.programs, tables.courses, tables.course_prerequisites, tables.program_requirements)


def add_saved_work(client):
    response = client.post("/api/v1/plans", json={"name": "Saved work", "program_id": 1})
    assert response.status_code == 201
    plan_id = response.get_json()["id"]
    assert client.put(f"/api/v1/plans/{plan_id}/semesters",
                      json={"term_index": 1, "name": "Spring 2027"}).status_code == 200
    assert client.put(f"/api/v1/plans/{plan_id}/courses",
                      json={"course_id": 1, "term_index": 1}).status_code == 200
    assert client.put("/api/v1/progress/1", json={"status": "Completed"}).status_code == 200
    return plan_id


def counts(app):
    with app.extensions["degreeplan"].databases.plans.connect() as conn:
        return {table.name: conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                for table in SAVED_TABLES}


def test_clear_all_students_saved_work_but_preserve_catalog_and_accounts(app, alice, bob, teacher1):
    alice_plan = add_saved_work(alice)
    bob_plan = add_saved_work(bob)
    dbs = app.extensions["degreeplan"].databases
    with dbs.plans.connect() as conn:
        catalog_before = {table.name: conn.execute(sa.select(table)).all() for table in CATALOG_TABLES}
    with dbs.users.connect() as conn:
        profiles_before = {table.name: conn.execute(sa.select(table)).all()
                           for table in (tables.students, tables.teachers)}
        audit_before = conn.execute(sa.select(sa.func.count()).select_from(tables.audit_log)).scalar_one()

    result = run(app, "dev", "clear-saved-data", "--yes")
    assert result.exit_code == 0, result.output
    assert "plans=2" in result.output and "student_course_progress=2" in result.output
    assert counts(app) == {table.name: 0 for table in SAVED_TABLES}
    for client, plan_id in ((alice, alice_plan), (bob, bob_plan)):
        assert client.get("/api/v1/plans").get_json() == {"items": []}
        assert client.get("/api/v1/progress").get_json() == {"items": []}
        assert client.get(f"/api/v1/plans/{plan_id}").status_code == 404
        assert client.get("/api/v1/auth/me").status_code == 200
    assert teacher1.get("/api/v1/plans?student_id=1").status_code == 200
    for username in ("alice", "teacher1"):
        assert ApiClient(app).login(username, PASSWORDS[username]).status_code == 200
    with dbs.plans.connect() as conn:
        assert {table.name: conn.execute(sa.select(table)).all() for table in CATALOG_TABLES} == catalog_before
    with dbs.users.connect() as conn:
        assert {table.name: conn.execute(sa.select(table)).all()
                for table in (tables.students, tables.teachers)} == profiles_before
        rows = security_store.recent_audit(conn, 30)
        assert conn.execute(sa.select(sa.func.count()).select_from(tables.audit_log)).scalar_one() > audit_before
    audit = next(row for row in rows if row["action"] == "dev.saved_data.clear")
    assert audit["actor_role"] == "cli"
    # Wiping never prevents starting another demo plan afterwards.
    add_saved_work(alice)
    assert counts(app)["plans"] == 1


def test_clear_requires_explicit_confirmation(app, alice):
    add_saved_work(alice)
    before = counts(app)
    result = run(app, "dev", "clear-saved-data")
    assert result.exit_code != 0 and "--yes" in result.output
    assert counts(app) == before


def test_clear_is_safe_to_repeat_when_already_empty(app):
    for _ in range(2):
        result = run(app, "dev", "clear-saved-data", "--yes")
        assert result.exit_code == 0
        assert counts(app) == {table.name: 0 for table in SAVED_TABLES}


def test_failed_clear_rolls_back_deleted_rows(app, alice):
    plan_id = add_saved_work(alice)
    before = counts(app)
    engine = app.extensions["degreeplan"].databases.plans

    def fail_after_first_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM plan_semesters"):
            raise RuntimeError("Simulated deletion failure")

    sa.event.listen(engine, "before_cursor_execute", fail_after_first_delete)
    try:
        result = run(app, "dev", "clear-saved-data", "--yes")
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail_after_first_delete)
    assert result.exit_code != 0
    assert "Cleared saved data:" not in result.output
    assert counts(app) == before
    assert alice.get(f"/api/v1/plans/{plan_id}").get_json()["courses"][0]["course_id"] == 1


def test_clear_is_unavailable_and_refuses_to_run_in_production(make_app, tmp_path):
    from degreeplan.cli import dev_cli

    app = production_app(make_app, tmp_path)
    assert run(app, "dev", "clear-saved-data", "--yes").exit_code == 2
    app.cli.add_command(dev_cli)
    result = run(app, "dev", "clear-saved-data", "--yes")
    assert result.exit_code != 0
    assert "cannot be cleared in production" in result.output
