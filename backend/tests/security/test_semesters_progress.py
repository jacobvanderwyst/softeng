import json

import pytest
import sqlalchemy as sa

from degreeplan.db.tables import students
from tests.helpers import logged_in
from tests.integration.test_plans import make_plan
from tests.integration.test_progress import set_progress
from tests.integration.test_semesters import set_semester
from tests.security.test_audit import audit_rows


def test_semesters_use_plan_ownership_and_staff_read_only(alice, bob, carol, teacher1, teacher2, admin):
    pid = make_plan(alice)
    set_semester(alice, pid)
    for client in (bob, carol, teacher2):
        assert set_semester(client, pid).status_code == 404
        assert client.delete(f"/api/v1/plans/{pid}/semesters/1").status_code == 404
    for client in (teacher1, admin):
        assert client.get(f"/api/v1/plans/{pid}").get_json()["semesters"] == [
            {"term_index": 1, "name": "Fall 2026"},
        ]
        assert set_semester(client, pid).status_code == 403
        assert client.delete(f"/api/v1/plans/{pid}/semesters/1").status_code == 403
    assert alice.get(f"/api/v1/plans/{pid}").get_json()["semesters"] == [
        {"term_index": 1, "name": "Fall 2026"},
    ]


def test_progress_is_scoped_to_logged_in_student_even_with_query_override(alice, bob):
    set_progress(alice)
    set_progress(bob, 1, "In Progress", query_string={"student_id": 1})
    assert bob.get("/api/v1/progress?student_id=1").get_json() == {
        "items": [{"course_id": 1, "status": "In Progress", "grade": None}],
    }
    assert alice.get("/api/v1/progress?student_id=2").get_json() == {
        "items": [{"course_id": 1, "status": "Completed", "grade": None}],
    }


def test_staff_progress_reads_require_target_and_existing_access(alice, carol, teacher1, teacher2, admin):
    set_progress(alice)
    set_progress(carol, 2, "In Progress")
    for client in (teacher1, teacher2, admin):
        assert client.get("/api/v1/progress").status_code == 422
        for invalid in ("x", "0", "-1", "2147483649"):
            assert client.get(f"/api/v1/progress?student_id={invalid}").status_code == 422
        assert client.get("/api/v1/progress?student_id=999").status_code == 404
        assert set_progress(client).status_code == 403
        assert set_progress(client, query_string={"student_id": 1}).status_code == 403
    assert teacher1.get("/api/v1/progress?student_id=1").get_json() == {
        "items": [{"course_id": 1, "status": "Completed", "grade": None}],
    }
    assert teacher1.get("/api/v1/progress?student_id=3").status_code == 404
    assert teacher2.get("/api/v1/progress?student_id=1").status_code == 404
    assert teacher2.get("/api/v1/progress?student_id=3").get_json() == {
        "items": [{"course_id": 2, "status": "In Progress", "grade": None}],
    }
    for student_id in (1, 2, 3):
        assert admin.get(f"/api/v1/progress?student_id={student_id}").status_code == 200


def test_progress_student_without_profile_cannot_read_or_write(app):
    with app.extensions["degreeplan"].databases.users.begin() as conn:
        conn.execute(sa.delete(students).where(students.c.user_id == 4))
    client = logged_in(app, "alice")
    assert client.get("/api/v1/progress").status_code == 403
    assert set_progress(client).status_code == 403


def test_new_endpoints_require_authentication(api):
    assert api.get("/api/v1/progress").status_code == 401
    assert set_progress(api).status_code == 401
    assert set_semester(api, 1).status_code == 401
    assert api.delete("/api/v1/plans/1/semesters/1").status_code == 401


@pytest.mark.parametrize("protection", ["missing", "wrong", "foreign_origin"])
def test_new_writes_enforce_csrf_and_origin(alice, protection):
    pid = make_plan(alice)
    set_semester(alice, pid)
    headers = {}
    if protection == "missing":
        alice.csrf = None
    elif protection == "wrong":
        headers = {"X-CSRF-Token": "invalid"}
    else:
        headers = {"Origin": "https://evil.example"}
    assert set_progress(alice, headers=headers).status_code == 403
    assert alice.put(
        f"/api/v1/plans/{pid}/semesters", json={"term_index": 2, "name": "Blocked"}, headers=headers
    ).status_code == 403
    assert alice.delete(f"/api/v1/plans/{pid}/semesters/1", headers=headers).status_code == 403
    assert alice.get("/api/v1/progress").get_json() == {"items": []}
    assert alice.get(f"/api/v1/plans/{pid}").get_json()["semesters"] == [
        {"term_index": 1, "name": "Fall 2026"},
    ]


def test_semester_and_progress_audit_actions_actor_target_and_details(app, alice):
    pid = make_plan(alice)
    set_semester(alice, pid)
    set_semester(alice, pid, 1, "Renamed")
    alice.delete(f"/api/v1/plans/{pid}/semesters/1")
    set_progress(alice, 1, "Completed")
    set_progress(alice, 1, "Not Started")
    events = [r for r in audit_rows(app) if r["action"] in {
        "plan.semester.set", "plan.semester.remove", "progress.set",
    }]
    assert [r["action"] for r in events] == [
        "plan.semester.set", "plan.semester.set", "plan.semester.remove", "progress.set", "progress.set",
    ]
    assert all(r["actor_user_id"] == 4 and r["actor_role"] == "student" for r in events)
    assert all(r["outcome"] == "success" and r["request_id"] for r in events)
    assert all(r["target_type"] == "plan" and r["target_id"] == str(pid) for r in events[:3])
    assert json.loads(events[0]["detail"]) == {"term_index": 1, "name": "Fall 2026"}
    assert json.loads(events[2]["detail"]) == {"term_index": 1}
    assert events[-1]["target_type"] == "student" and events[-1]["target_id"] == "1"
    assert json.loads(events[-1]["detail"]) == {"course_id": 1, "status": "Not Started"}


def test_rejected_mutations_do_not_record_success_audits(app, alice, bob):
    pid = make_plan(alice)
    assert set_semester(bob, pid).status_code == 404
    assert set_progress(alice, 999).status_code == 422
    assert alice.put("/api/v1/progress/1", json={"status": "invalid"}).status_code == 422
    assert not any(r["action"] in {"plan.semester.set", "progress.set"} for r in audit_rows(app))
