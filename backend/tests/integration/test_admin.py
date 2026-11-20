import json
import secrets

import pytest
import sqlalchemy as sa

from degreeplan.db import tables
from tests.helpers import ApiClient, logged_in
from tests.integration.test_plans import add, make_plan


def instructor(**changes):
    return {"username": "new-instructor", "password": secrets.token_urlsafe(24),
            "first_name": "New", "last_name": "Instructor", "department": "Computing", **changes}


def program(**changes):
    return {"code": "TEST", "name": "Test degree", "total_credits": 8, "course_ids": [1, 2], **changes}


def count(app, table, *, plans=False):
    engine = getattr(app.extensions["degreeplan"].databases, "plans" if plans else "users")
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


@pytest.mark.parametrize("client_name, status", [("api", 401), ("alice", 403), ("teacher1", 403)])
def test_admin_management_is_not_available_to_other_roles(request, client_name, status):
    client = request.getfixturevalue(client_name)
    for method, path, body in (
        ("get", "/api/v1/admin/instructors", None),
        ("post", "/api/v1/admin/instructors", instructor()),
        ("get", "/api/v1/admin/programs/1", None),
        ("post", "/api/v1/admin/programs", program()),
        ("put", "/api/v1/admin/programs/1", program(expected_revision="0" * 64)),
    ):
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == status


def test_admin_creates_instructor_without_returning_credentials(app, admin, audit_caplog):
    body = instructor(username="  New-Instructor  ", first_name=" New ")
    result = admin.post("/api/v1/admin/instructors", json=body)
    assert result.status_code == 201
    data = result.get_json()
    assert data["username"] == "new-instructor" and data["role"] == "teacher"
    assert data["first_name"] == "New"
    assert "password_hash" not in data and "password" not in data and "csrf_token" not in data
    assert body["password"] not in json.dumps(data) and body["password"] not in audit_caplog.text
    teacher = ApiClient(app)
    logged = teacher.login("NEW-INSTRUCTOR", body["password"])
    assert logged.status_code == 200 and logged.get_json()["user"]["role"] == "teacher"
    assert logged.get_json()["user"]["profile"]["department"] == "Computing"
    assert teacher.get("/api/v1/plans?student_id=1").status_code == 404
    assert teacher.post("/api/v1/admin/programs", json=program()).status_code == 403
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        event = conn.execute(sa.select(tables.audit_log)
                             .where(tables.audit_log.c.action == "admin.instructor.create")).mappings().one()
    assert event["actor_role"] == "admin" and event["actor_user_id"] == 1


def test_admin_instructor_creation_is_independent_of_public_signup(make_app):
    app = make_app(registration_enabled=False)
    admin = logged_in(app, "admin")
    assert admin.post("/api/v1/admin/instructors", json=instructor()).status_code == 201


def test_instructor_listing_is_paginated_searchable_and_has_no_secret_fields(admin):
    result = admin.get("/api/v1/admin/instructors?q=teacher&limit=1&offset=0").get_json()
    assert result["total"] == 2 and len(result["items"]) == 1
    assert result["password_min_length"] == 12
    assert set(result["items"][0]) == {
        "id", "user_id", "username", "is_active", "first_name", "last_name", "department",
    }
    assert admin.get("/api/v1/admin/instructors?q=%25").get_json()["total"] == 0
    assert admin.get("/api/v1/admin/instructors?limit=1000").status_code == 422


@pytest.mark.parametrize("changes", [
    {"role": "admin"}, {"role": "student"}, {"is_active": False}, {"program_id": 1},
    {"advisor_id": 1}, {"password": "short"}, {"username": "invalid name"}, {"first_name": ""},
    {"department": "x" * 121}, {"department": "bad\x00department"},
])
def test_instructor_input_never_grants_extra_privileges(app, admin, changes):
    before = count(app, tables.users)
    assert admin.post("/api/v1/admin/instructors", json=instructor(**changes)).status_code == 422
    assert count(app, tables.users) == before


def test_duplicate_instructor_username_cannot_overwrite_student(app, admin):
    before = count(app, tables.users)
    assert admin.post("/api/v1/admin/instructors", json=instructor(username="ALICE")).status_code == 409
    assert count(app, tables.users) == before
    assert logged_in(app, "alice").get("/api/v1/auth/me").get_json()["user"]["role"] == "student"


@pytest.mark.parametrize("failure", ["profile", "audit"])
def test_instructor_account_and_profile_roll_back_together(app, admin, monkeypatch, failure):
    before = count(app, tables.users), count(app, tables.teachers)

    def fail(*args, **kwargs):
        raise RuntimeError("Simulated instructor provisioning failure")

    target = "people.create_teacher" if failure == "profile" else "audit.record"
    monkeypatch.setattr("degreeplan.api.admin." + target, fail)
    assert admin.post("/api/v1/admin/instructors", json=instructor()).status_code == 500
    assert (count(app, tables.users), count(app, tables.teachers)) == before


def test_admin_creates_and_edits_degree_requirements_with_revision_check(admin, alice):
    created = admin.post("/api/v1/admin/programs", json=program())
    assert created.status_code == 201
    original = created.get_json()
    assert {course["id"] for course in original["requirements"]} == {1, 2}
    assert len(original["revision"]) == 64
    result = admin.put(f"/api/v1/admin/programs/{original['id']}", json=program(
        name="Updated degree", total_credits=11, course_ids=[1, 2, 3], expected_revision=original["revision"],
    ))
    assert result.status_code == 200
    updated = result.get_json()
    assert updated["id"] == original["id"] and updated["revision"] != original["revision"]
    assert {course["id"] for course in updated["requirements"]} == {1, 2, 3}
    stale = admin.put(f"/api/v1/admin/programs/{original['id']}",
                      json=program(name="Stale overwrite", expected_revision=original["revision"]))
    assert stale.status_code == 409
    assert alice.get(f"/api/v1/programs/{original['id']}").get_json()["name"] == "Updated degree"


def test_program_requirement_changes_do_not_rewrite_personal_plans_or_grades(app, admin, alice):
    plan_id = make_plan(alice)
    assert add(alice, plan_id, 1, 1).status_code == 200
    assert alice.put("/api/v1/progress/1", json={"status": "Completed", "grade": "A"}).status_code == 200
    current = admin.get("/api/v1/admin/programs/1").get_json()
    result = admin.put("/api/v1/admin/programs/1", json=program(
        code=current["code"], name=current["name"], course_ids=[2], expected_revision=current["revision"],
    ))
    assert result.status_code == 200
    saved = alice.get(f"/api/v1/plans/{plan_id}").get_json()
    assert saved["courses"][0]["course_id"] == 1
    assert alice.get("/api/v1/progress").get_json()["items"] == [
        {"course_id": 1, "status": "Completed", "grade": "A"},
    ]
    issues = alice.get(f"/api/v1/plans/{plan_id}/validation").get_json()["issues"]
    assert any(issue["type"] == "missing_requirement" and issue["course_id"] == 2 for issue in issues)
    # Catalog management does not expand admin write access to students' own plans/grades.
    assert admin.patch(f"/api/v1/plans/{plan_id}", json={"name": "Not allowed"}).status_code == 403
    assert admin.put("/api/v1/progress/1", json={"status": "Not Started"}).status_code == 403
    assert count(app, tables.courses, plans=True) == 6


@pytest.mark.parametrize("changes", [
    {"code": ""}, {"code": "bad<script>"}, {"code": "x" * 33}, {"name": " "},
    {"name": "invalid\x00name"}, {"total_credits": True}, {"total_credits": 0},
    {"total_credits": 1001}, {"course_ids": []}, {"course_ids": [True]}, {"course_ids": [0]},
    {"course_ids": [1, 1]}, {"course_ids": [99999]}, {"role": "admin"},
])
def test_invalid_degree_programs_do_not_change_catalog(app, admin, changes):
    before = count(app, tables.programs, plans=True)
    assert admin.post("/api/v1/admin/programs", json=program(**changes)).status_code == 422
    assert count(app, tables.programs, plans=True) == before


def test_program_duplicates_missing_records_and_missing_revision_are_controlled(admin):
    original = admin.get("/api/v1/admin/programs/1").get_json()
    assert admin.post("/api/v1/admin/programs", json=program(code="BSCS")).status_code == 409
    assert admin.put("/api/v1/admin/programs/1", json=program()).status_code == 422
    assert admin.get("/api/v1/admin/programs/99999").status_code == 404
    assert admin.put("/api/v1/admin/programs/99999",
                     json=program(expected_revision="0" * 64)).status_code == 404
    assert admin.get("/api/v1/admin/programs/1").get_json() == original


def test_program_edit_failure_rolls_back_metadata_and_requirement_links(admin, monkeypatch):
    from degreeplan.repositories import administration

    original = admin.get("/api/v1/admin/programs/1").get_json()
    save = administration.save_program

    def fail_after_changes(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError("Simulated catalog edit failure")

    monkeypatch.setattr(administration, "save_program", fail_after_changes)
    response = admin.put("/api/v1/admin/programs/1", json=program(
        code="CHANGED", course_ids=[1], expected_revision=original["revision"],
    ))
    assert response.status_code == 500
    assert admin.get("/api/v1/admin/programs/1").get_json() == original


def test_all_admin_writes_require_csrf_and_trusted_origin(admin):
    for path, body in (("/api/v1/admin/instructors", instructor()), ("/api/v1/admin/programs", program())):
        assert admin.client.post(path, base_url="https://localhost", json=body).status_code == 403
        assert admin.post(path, json=body, headers={"Origin": "https://foreign.example"}).status_code == 403
    current = admin.get("/api/v1/admin/programs/1").get_json()
    assert admin.client.put("/api/v1/admin/programs/1", base_url="https://localhost",
                            json=program(expected_revision=current["revision"])).status_code == 403
