def make_plan(client, name="My Plan"):
    resp = client.post("/api/v1/plans", json={"name": name, "program_id": 1})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["id"]


def add(client, plan_id, course_id, term):
    return client.put(f"/api/v1/plans/{plan_id}/courses", json={"course_id": course_id, "term_index": term})


def test_plan_crud(alice):
    pid = make_plan(alice)
    created = alice.get(f"/api/v1/plans/{pid}").get_json()
    assert created["name"] == "My Plan"
    assert created["created_at"].endswith("+00:00")  # ISO-8601, UTC
    assert [p["id"] for p in alice.get("/api/v1/plans").get_json()["items"]] == [pid]

    renamed = alice.patch(f"/api/v1/plans/{pid}", json={"name": "Renamed"})
    assert renamed.get_json()["name"] == "Renamed"

    assert alice.delete(f"/api/v1/plans/{pid}").status_code == 204
    assert alice.get(f"/api/v1/plans/{pid}").status_code == 404


def test_create_plan_validation(alice):
    assert alice.post("/api/v1/plans", json={"name": "", "program_id": 1}).status_code == 422
    assert alice.post("/api/v1/plans", json={"name": "ok", "program_id": "x"}).status_code == 422
    assert alice.post("/api/v1/plans", json={"name": "ok", "program_id": 999}).status_code == 422
    resp = alice.post("/api/v1/plans", json={"name": "ok"})
    assert resp.status_code == 422
    assert resp.get_json()["error"]["details"][0]["field"] == "program_id"


def test_add_move_remove_course(alice):
    pid = make_plan(alice)
    resp = add(alice, pid, 1, 1)
    assert resp.status_code == 200
    assert resp.get_json()["total_credits"] == 4

    moved = add(alice, pid, 1, 2).get_json()
    assert len(moved["courses"]) == 1
    assert moved["courses"][0]["term_index"] == 2

    assert alice.delete(f"/api/v1/plans/{pid}/courses/1").status_code == 204
    assert alice.delete(f"/api/v1/plans/{pid}/courses/1").status_code == 404


def test_add_unknown_course_or_bad_term(alice):
    pid = make_plan(alice)
    assert add(alice, pid, 999, 1).status_code == 422
    assert add(alice, pid, 1, 0).status_code == 422
    assert add(alice, pid, 1, 99).status_code == 422


def test_term_credit_cap_enforced(make_app):
    from tests.helpers import logged_in

    client = logged_in(make_app(max_credits_per_term=8), "alice")
    pid = make_plan(client)
    assert add(client, pid, 1, 1).status_code == 200  # 4 credits
    assert add(client, pid, 2, 1).status_code == 200  # 8 credits
    over = add(client, pid, 4, 1)  # would be 12
    assert over.status_code == 409
    assert "credits" in over.get_json()["error"]["message"]


def test_validation_reports_prerequisite_problems(alice):
    pid = make_plan(alice)
    add(alice, pid, 3, 1)  # CS301 needs CS201 and MATH201, neither is in the plan
    result = alice.get(f"/api/v1/plans/{pid}/validation").get_json()
    types = {i["type"] for i in result["issues"]}
    assert not result["valid"]
    assert {"missing_prerequisite", "missing_requirement"} <= types

    add(alice, pid, 2, 1)  # CS201 in the same term as CS301: wrong order
    types = {i["type"] for i in alice.get(f"/api/v1/plans/{pid}/validation").get_json()["issues"]}
    assert "prerequisite_order" in types


def test_fully_valid_plan(alice):
    pid = make_plan(alice)
    for course_id, term in {1: 1, 4: 1, 2: 2, 5: 2, 3: 3, 6: 3}.items():
        assert add(alice, pid, course_id, term).status_code == 200
    assert alice.get(f"/api/v1/plans/{pid}/validation").get_json() == {"valid": True, "issues": []}


def test_write_requests_must_be_json(alice):
    assert alice.post("/api/v1/plans", data="name=x&program_id=1").status_code == 415
