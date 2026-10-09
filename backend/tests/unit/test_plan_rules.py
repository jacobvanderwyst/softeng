from degreeplan.services.plan_rules import term_credit_totals, validate_plan


def pc(course_id, code, credits, term):
    return {"course_id": course_id, "code": code, "credits": credits, "term_index": term}


def test_term_credit_totals():
    courses = [pc(1, "A", 3, 1), pc(2, "B", 4, 1), pc(3, "C", 3, 2)]
    assert term_credit_totals(courses) == {1: 7, 2: 3}


def test_valid_plan_has_no_issues():
    courses = [pc(1, "A", 3, 1), pc(2, "B", 3, 2)]
    assert validate_plan(courses, {2: {1}}, {1, 2}, 18) == []


def test_missing_prerequisite_and_order():
    courses = [pc(2, "B", 3, 1), pc(3, "C", 3, 1)]
    issues = validate_plan(courses, {2: {1}, 3: {2}}, set(), 18)
    assert [i["type"] for i in issues] == ["missing_prerequisite", "prerequisite_order"]


def test_credit_limit_and_missing_requirement():
    courses = [pc(1, "A", 10, 1), pc(2, "B", 10, 1)]
    issues = validate_plan(courses, {}, {1, 2, 3}, 18)
    assert {i["type"] for i in issues} == {"term_credit_limit", "missing_requirement"}
