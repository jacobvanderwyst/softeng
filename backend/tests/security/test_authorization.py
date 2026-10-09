"""Least-privilege checks across both databases (users DB decides who may touch plans in the plans DB)."""
from tests.integration.test_plans import add, make_plan


def test_student_cannot_see_or_modify_another_students_plan(alice, bob):
    pid = make_plan(alice)
    assert bob.get(f"/api/v1/plans/{pid}").status_code == 404
    assert bob.patch(f"/api/v1/plans/{pid}", json={"name": "hax"}).status_code == 404
    assert bob.delete(f"/api/v1/plans/{pid}").status_code == 404
    assert add(bob, pid, 1, 1).status_code == 404
    assert bob.get(f"/api/v1/plans/{pid}/validation").status_code == 404
    assert bob.get("/api/v1/plans").get_json()["items"] == []
    assert alice.get(f"/api/v1/plans/{pid}").get_json()["name"] == "My Plan"  # untouched


def test_existing_and_missing_plans_look_identical_to_outsiders(alice, bob):
    pid = make_plan(alice)
    forbidden, missing = bob.get(f"/api/v1/plans/{pid}"), bob.get("/api/v1/plans/99999")
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.get_json()["error"]["message"] == missing.get_json()["error"]["message"]


def test_students_cannot_list_other_students_via_query_parameter(alice, bob):
    make_plan(bob)
    items = alice.get("/api/v1/plans?student_id=2").get_json()["items"]  # bob's student id
    assert items == []  # the parameter is ignored for students: they only ever see their own


def test_teacher_reads_only_assigned_students_and_never_writes(alice, carol, teacher1):
    alices_plan, carols_plan = make_plan(alice), make_plan(carol)

    assert teacher1.get(f"/api/v1/plans/{alices_plan}").status_code == 200  # advisee
    assert teacher1.get(f"/api/v1/plans/{carols_plan}").status_code == 404  # belongs to teacher2

    assert teacher1.get("/api/v1/plans?student_id=1").get_json()["items"][0]["id"] == alices_plan
    assert teacher1.get("/api/v1/plans?student_id=3").status_code == 404  # carol: not assigned
    assert teacher1.get("/api/v1/plans?student_id=999").status_code == 404
    assert teacher1.get("/api/v1/plans").status_code == 422  # student_id is required for staff

    assert teacher1.patch(f"/api/v1/plans/{alices_plan}", json={"name": "x"}).status_code == 403
    assert teacher1.delete(f"/api/v1/plans/{alices_plan}").status_code == 403
    assert add(teacher1, alices_plan, 1, 1).status_code == 403
    assert teacher1.post("/api/v1/plans", json={"name": "x", "program_id": 1}).status_code == 403


def test_other_teacher_sees_their_own_students(carol, teacher2):
    pid = make_plan(carol)
    assert teacher2.get(f"/api/v1/plans/{pid}").status_code == 200


def test_admin_can_read_everything_but_not_modify_plans(alice, admin):
    pid = make_plan(alice)
    assert admin.get(f"/api/v1/plans/{pid}").status_code == 200
    assert admin.get("/api/v1/plans?student_id=1").status_code == 200
    assert admin.patch(f"/api/v1/plans/{pid}", json={"name": "x"}).status_code == 403
    assert admin.delete(f"/api/v1/plans/{pid}").status_code == 403


def test_account_without_a_student_record_cannot_use_student_endpoints(app, make_app):
    # A student-role user with no profile row (e.g. created through direct DB access) gets a clear 403.
    import sqlalchemy as sa

    from degreeplan.db.tables import students
    from tests.helpers import logged_in

    with app.extensions["degreeplan"].databases.users.begin() as conn:
        conn.execute(sa.delete(students).where(students.c.user_id == 4))  # alice's profile
    client = logged_in(app, "alice")
    assert client.get("/api/v1/plans").status_code == 403
    assert client.post("/api/v1/plans", json={"name": "x", "program_id": 1}).status_code == 403
