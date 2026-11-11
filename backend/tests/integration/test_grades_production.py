import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from degreeplan.db import migrate
from degreeplan.db.tables import courses
from tests.helpers import logged_in
from tests.integration.test_migrations import make_dbs


def write(client, course=1, **body):
    return client.put(f"/api/v1/progress/{course}", json={"status": "Completed", **body})


def test_grade_omission_retention_clear_and_restart(alice, make_app):
    assert write(alice, grade="B").get_json()["grade"] == "B"
    assert write(alice, status="In Progress").get_json()["grade"] == "B"
    assert alice.get("/api/v1/progress/summary").get_json()["gpa"] is None
    assert write(alice).get_json()["grade"] == "B"
    fresh = logged_in(make_app(fresh=False), "alice")
    assert fresh.get("/api/v1/progress/summary").get_json()["gpa"] == 3
    assert write(fresh, grade=None).get_json()["grade"] is None
    assert fresh.get("/api/v1/progress/summary").get_json() == {
        "gpa": None, "graded_credits": 0, "quality_points": 0, "graded_courses": 0}


@pytest.mark.parametrize("grade", ["E", "a", "B+", "", 4, True, [], {}])
def test_invalid_grade_does_not_modify_existing(alice, grade):
    write(alice, grade="A")
    assert write(alice, grade=grade).status_code == 422
    assert alice.get("/api/v1/progress").get_json()["items"][0]["grade"] == "A"


def test_summary_weights_all_completed_positive_catalog_records(app, alice):
    with app.extensions["degreeplan"].databases.plans.begin() as conn:
        conn.execute(sa.update(courses).where(courses.c.id == 1).values(credits=4))
        conn.execute(sa.update(courses).where(courses.c.id == 2).values(credits=3))
        conn.execute(sa.update(courses).where(courses.c.id == 3).values(credits=0))
    write(alice, 1, grade="A")
    write(alice, 2, grade="F")
    write(alice, 3, grade="A")
    write(alice, 4, grade="B", status="In Progress")
    write(alice, 5)
    assert alice.get("/api/v1/progress/summary").get_json() == {
        "gpa": 2.29, "graded_credits": 7, "quality_points": 16, "graded_courses": 2}
    assert alice.get("/api/v1/plans").get_json() == {"items": []}


def test_summary_access_and_grade_csrf(api, alice, bob, teacher1, teacher2, admin):
    write(alice, grade="A")
    assert api.get("/api/v1/progress/summary").status_code == 401
    assert bob.get("/api/v1/progress/summary?student_id=1").get_json()["gpa"] is None
    assert teacher1.get("/api/v1/progress/summary?student_id=1").get_json()["gpa"] == 4
    assert teacher2.get("/api/v1/progress/summary?student_id=1").status_code == 404
    assert admin.get("/api/v1/progress/summary?student_id=1").status_code == 200
    assert teacher1.get("/api/v1/progress/summary").status_code == 422
    assert write(teacher1, grade="A").status_code == 403
    assert alice.put("/api/v1/progress/1", json={"status": "Completed", "grade": "F"},
                     headers={"X-CSRF-Token": "invalid"}).status_code == 403


def test_half_up_tie_and_grade_audit(app, alice):
    import json

    from tests.security.test_audit import audit_rows

    with app.extensions["degreeplan"].databases.plans.begin() as conn:
        for course_id, credits in ((1, 26), (2, 1), (3, 13)):
            conn.execute(sa.update(courses).where(courses.c.id == course_id).values(credits=credits))
    for course_id, grade in ((1, "A"), (2, "B"), (3, "F")):
        write(alice, course_id, grade=grade)
    assert alice.get("/api/v1/progress/summary").get_json()["gpa"] == 2.68
    events = [json.loads(row["detail"]) for row in audit_rows(app) if row["action"] == "progress.set"]
    assert events[-1] == {"course_id": 3, "status": "Completed", "grade": "F", "grade_changed": True}


def test_grade_migration_preserves_existing_status_and_plans(tmp_path):
    from tests.integration.test_semesters_progress_migration import seed_legacy

    dbs = make_dbs(tmp_path)
    try:
        migrate.upgrade(dbs.plans, "plans", "plans_0002")
        with dbs.plans.begin() as conn:
            seed_legacy(conn)
            conn.exec_driver_sql("INSERT INTO student_course_progress VALUES (1, 1, 'Completed', 123)")
        migrate.upgrade(dbs.plans, "plans")
        with dbs.plans.connect() as conn:
            assert conn.exec_driver_sql(
                "SELECT student_id,course_id,status,updated_at,grade FROM student_course_progress"
            ).one() == (1, 1, "Completed", 123, None)
            assert conn.exec_driver_sql("SELECT name FROM plans").scalar_one() == "Existing Plan"
        with pytest.raises(IntegrityError), dbs.plans.begin() as conn:
            conn.exec_driver_sql("UPDATE student_course_progress SET grade='E'")
    finally:
        dbs.dispose()
