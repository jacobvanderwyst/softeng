"""Degree-plan business rules. Pure functions: easy to unit test without a database."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def term_credit_totals(plan_courses: list[dict[str, Any]]) -> dict[int, int]:
    totals: dict[int, int] = defaultdict(int)
    for pc in plan_courses:
        totals[pc["term_index"]] += pc["credits"]
    return dict(totals)


def validate_plan(
    plan_courses: list[dict[str, Any]],
    prereqs_by_course: dict[int, set[int]],
    required_course_ids: set[int],
    max_credits_per_term: int,
) -> list[dict[str, Any]]:
    """Return a list of issues (empty list == plan is valid).

    plan_courses: [{course_id, code, credits, term_index}, ...]
    prereqs_by_course: {course_id: {prerequisite course ids}}
    """
    issues: list[dict[str, Any]] = []
    term_of = {pc["course_id"]: pc["term_index"] for pc in plan_courses}
    code_of = {pc["course_id"]: pc["code"] for pc in plan_courses}

    for pc in plan_courses:
        for prereq_id in sorted(prereqs_by_course.get(pc["course_id"], set())):
            if prereq_id not in term_of:
                issues.append({
                    "type": "missing_prerequisite",
                    "course_id": pc["course_id"],
                    "prerequisite_id": prereq_id,
                    "message": f"{pc['code']} requires a prerequisite that is not in the plan.",
                })
            elif term_of[prereq_id] >= pc["term_index"]:
                issues.append({
                    "type": "prerequisite_order",
                    "course_id": pc["course_id"],
                    "prerequisite_id": prereq_id,
                    "message": f"{code_of[prereq_id]} must be taken before {pc['code']}.",
                })

    for term, total in sorted(term_credit_totals(plan_courses).items()):
        if total > max_credits_per_term:
            issues.append({
                "type": "term_credit_limit",
                "term_index": term,
                "message": f"Term {term} has {total} credits (max {max_credits_per_term}).",
            })

    for course_id in sorted(required_course_ids - set(term_of)):
        issues.append({
            "type": "missing_requirement",
            "course_id": course_id,
            "message": "A required program course is not in the plan.",
        })
    return issues
