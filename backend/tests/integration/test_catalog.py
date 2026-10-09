def test_list_courses_paginated(alice):
    data = alice.get("/api/v1/courses?limit=2&offset=0").get_json()
    assert data["total"] == 6
    assert len(data["items"]) == 2
    assert data["items"][0]["code"] == "CS101"


def test_search_courses(alice):
    data = alice.get("/api/v1/courses?q=math").get_json()
    assert {c["code"] for c in data["items"]} == {"MATH101", "MATH201"}


def test_search_treats_wildcards_literally(alice):
    assert alice.get("/api/v1/courses?q=%25").get_json()["total"] == 0
    assert alice.get("/api/v1/courses?q=_").get_json()["total"] == 0


def test_bad_pagination_params(alice):
    assert alice.get("/api/v1/courses?limit=abc").status_code == 422
    assert alice.get("/api/v1/courses?limit=1000").status_code == 422
    assert alice.get("/api/v1/courses?offset=-1").status_code == 422


def test_course_detail_includes_prerequisites(alice):
    data = alice.get("/api/v1/courses/3").get_json()
    assert data["code"] == "CS301"
    assert {p["code"] for p in data["prerequisites"]} == {"CS201", "MATH201"}


def test_course_not_found(alice):
    resp = alice.get("/api/v1/courses/9999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_programs(alice):
    items = alice.get("/api/v1/programs").get_json()["items"]
    assert items[0]["code"] == "BSCS"
    detail = alice.get("/api/v1/programs/1").get_json()
    assert len(detail["requirements"]) == 6
    assert alice.get("/api/v1/programs/99").status_code == 404


def test_catalog_is_readable_by_every_role(teacher1, admin):
    assert teacher1.get("/api/v1/courses").status_code == 200
    assert admin.get("/api/v1/programs").status_code == 200
