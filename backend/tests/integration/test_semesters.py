import pytest
import sqlalchemy as sa

from degreeplan.db.tables import plan_courses, plan_semesters, plans
from degreeplan.repositories import plans as plans_repo
from tests.helpers import logged_in
from tests.integration.test_plans import add, make_plan


def set_semester(client, plan_id, term=1, name="Fall 2026"):
    return client.put(f"/api/v1/plans/{plan_id}/semesters", json={"term_index": term, "name": name})


def test_named_empty_semesters_and_legacy_terms_are_sorted_and_upserted(alice):
    pid = make_plan(alice)
    assert alice.get(f"/api/v1/plans/{pid}").get_json()["semesters"] == []
    assert add(alice, pid, 1, 3).get_json()["semesters"] == [{"term_index": 3, "name": "Term 3"}]
    result = set_semester(alice, pid, 1, "  Fall 2026  ")
    assert result.status_code == 200
    assert result.get_json()["semesters"] == [
        {"term_index": 1, "name": "Fall 2026"}, {"term_index": 3, "name": "Term 3"},
    ]
    assert result.get_json()["total_credits"] == 4
    assert len(result.get_json()["courses"]) == 1
    set_semester(alice, pid, 3, "Spring 2027")
    result = set_semester(alice, pid, 3, "Summer 2027").get_json()
    assert result["semesters"][-1] == {"term_index": 3, "name": "Summer 2027"}
    assert len(result["semesters"]) == 2
    renamed = alice.patch(f"/api/v1/plans/{pid}", json={"name": "Renamed"}).get_json()
    assert renamed["semesters"] == result["semesters"]


@pytest.mark.parametrize("body", [
    {}, {"term_index": 1}, {"term_index": 0, "name": "x"}, {"term_index": 17, "name": "x"},
    {"term_index": 1, "name": ""}, {"term_index": 1, "name": "  "},
    {"term_index": 1, "name": "x" * 121}, {"term_index": True, "name": "x"},
    {"term_index": 1.5, "name": "x"}, {"term_index": "1", "name": "x"},
    {"term_index": 1, "name": None}, {"term_index": 1, "name": "x", "student_id": 2},
])
def test_semester_validation(alice, body):
    pid = make_plan(alice)
    assert alice.put(f"/api/v1/plans/{pid}/semesters", json=body).status_code == 422
    assert alice.get(f"/api/v1/plans/{pid}").get_json()["semesters"] == []


def test_semester_json_and_boundary_validation(alice):
    pid = make_plan(alice)
    path = f"/api/v1/plans/{pid}/semesters"
    assert alice.put(path, data="term_index=1&name=x").status_code == 415
    assert alice.put(path, json=[]).status_code == 400
    assert set_semester(alice, pid, 16, "x" * 120).status_code == 200
    assert alice.delete(f"{path}/0").status_code == 422
    assert alice.delete(f"{path}/17").status_code == 422
    assert alice.delete(f"{path}/15").status_code == 404


def test_remove_named_and_synthesized_semesters_and_courses(alice):
    pid = make_plan(alice)
    add(alice, pid, 1, 1)
    add(alice, pid, 2, 1)
    add(alice, pid, 4, 2)
    set_semester(alice, pid, 1)
    set_semester(alice, pid, 3, "Empty")
    assert alice.delete(f"/api/v1/plans/{pid}/semesters/1").status_code == 204
    result = alice.get(f"/api/v1/plans/{pid}").get_json()
    assert [c["course_id"] for c in result["courses"]] == [4]
    assert result["total_credits"] == 4
    assert result["semesters"] == [{"term_index": 2, "name": "Term 2"}, {"term_index": 3, "name": "Empty"}]
    assert alice.delete(f"/api/v1/plans/{pid}/semesters/2").status_code == 204
    assert alice.delete(f"/api/v1/plans/{pid}/semesters/3").status_code == 204
    assert alice.delete(f"/api/v1/plans/{pid}/semesters/3").status_code == 404
    result = alice.get(f"/api/v1/plans/{pid}").get_json()
    assert result["semesters"] == result["courses"] == []
    assert result["total_credits"] == 0


def test_courses_can_move_without_named_terms_and_empty_named_terms_remain(alice):
    pid = make_plan(alice)
    set_semester(alice, pid)
    add(alice, pid, 1, 1)
    result = add(alice, pid, 1, 2).get_json()
    assert result["semesters"] == [{"term_index": 1, "name": "Fall 2026"}, {"term_index": 2, "name": "Term 2"}]
    alice.delete(f"/api/v1/plans/{pid}/courses/1")
    assert alice.get(f"/api/v1/plans/{pid}").get_json()["semesters"] == [{"term_index": 1, "name": "Fall 2026"}]


def test_plan_delete_explicitly_cleans_dependents_without_fk_cascade(app, alice):
    pid = make_plan(alice)
    add(alice, pid, 1, 1)
    set_semester(alice, pid)
    engine = app.extensions["degreeplan"].databases.plans
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            plans_repo.delete_plan(conn, pid)
            conn.commit()
            for table in (plan_courses, plan_semesters):
                assert conn.execute(sa.select(table).where(table.c.plan_id == pid)).all() == []
            assert conn.execute(sa.select(plans).where(plans.c.id == pid)).all() == []
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def test_semester_delete_rolls_back_course_delete_on_failure(app, alice):
    pid = make_plan(alice)
    add(alice, pid, 1, 1)
    set_semester(alice, pid)
    engine = app.extensions["degreeplan"].databases.plans

    def fail_semester_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM plan_semesters"):
            raise RuntimeError("simulated storage failure")

    sa.event.listen(engine, "before_cursor_execute", fail_semester_delete)
    try:
        assert alice.delete(f"/api/v1/plans/{pid}/semesters/1").status_code == 500
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail_semester_delete)
    result = alice.get(f"/api/v1/plans/{pid}").get_json()
    assert result["semesters"] == [{"term_index": 1, "name": "Fall 2026"}]
    assert [c["course_id"] for c in result["courses"]] == [1]


def test_semesters_persist_across_app_restart(app, make_app, alice):
    pid = make_plan(alice)
    set_semester(alice, pid, 2, "Saved empty term")
    restarted = logged_in(make_app(fresh=False), "alice")
    assert restarted.get(f"/api/v1/plans/{pid}").get_json()["semesters"] == [
        {"term_index": 2, "name": "Saved empty term"},
    ]
