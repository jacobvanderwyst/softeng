import pytest

from tests.helpers import logged_in
from tests.integration.test_plans import add, make_plan


def set_progress(client, course_id=1, status="Completed", **kwargs):
    return client.put(f"/api/v1/progress/{course_id}", json={"status": status}, **kwargs)


def test_progress_statuses_upsert_and_order_without_plans(alice):
    assert alice.get("/api/v1/progress").get_json() == {"items": []}
    assert set_progress(alice, 4, "In Progress").get_json() == {"course_id": 4, "status": "In Progress"}
    for status in ("Completed", "Not Started", "In Progress"):
        resp = set_progress(alice, 1, status)
        assert resp.status_code == 200
        assert resp.get_json() == {"course_id": 1, "status": status}
    assert alice.get("/api/v1/progress").get_json() == {"items": [
        {"course_id": 1, "status": "In Progress"}, {"course_id": 4, "status": "In Progress"},
    ]}
    assert alice.get("/api/v1/plans").get_json() == {"items": []}


def test_progress_independent_of_plan_courses_deletion_and_validation(alice):
    first, second = make_plan(alice), make_plan(alice, "Alternative")
    add(alice, first, 3, 1)
    before = alice.get(f"/api/v1/plans/{first}/validation").get_json()
    for course_id in (1, 2, 5):
        set_progress(alice, course_id)
    assert alice.get(f"/api/v1/plans/{first}/validation").get_json() == before
    assert alice.get(f"/api/v1/plans/{second}").get_json()["courses"] == []
    alice.delete(f"/api/v1/plans/{first}/semesters/1")
    alice.delete(f"/api/v1/plans/{first}")
    alice.delete(f"/api/v1/plans/{second}")
    assert len(alice.get("/api/v1/progress").get_json()["items"]) == 3


@pytest.mark.parametrize("body", [
    {}, {"status": None}, {"status": ""}, {"status": "complete"}, {"status": "completed"},
    {"status": "Official"}, {"status": 1}, {"status": "Completed", "student_id": 2},
    {"status": "Completed", "plan_id": 1},
])
def test_progress_rejects_invalid_and_extra_fields(alice, body):
    assert alice.put("/api/v1/progress/1", json=body).status_code == 422
    assert alice.get("/api/v1/progress").get_json() == {"items": []}


def test_progress_unknown_courses_and_non_json(alice):
    assert set_progress(alice, 999).status_code == 422
    assert set_progress(alice, 0).status_code == 422
    assert alice.put("/api/v1/progress/1", data="status=Completed").status_code == 415
    assert alice.put("/api/v1/progress/1", json=[]).status_code == 400


def test_progress_persists_across_app_restart(app, make_app, alice):
    set_progress(alice, 1)
    restarted = logged_in(make_app(fresh=False), "alice")
    assert restarted.get("/api/v1/progress").get_json() == {"items": [{"course_id": 1, "status": "Completed"}]}
