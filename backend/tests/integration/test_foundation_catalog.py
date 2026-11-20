import json
from pathlib import Path

import sqlalchemy as sa

from degreeplan.db import tables
from tests.integration.test_cli import production_app, run

REFERENCE = Path(__file__).resolve().parents[3] / "docs" / "examples" / "cj-coms-foundation.json"
CJ = {
    "CRIJ 2361": "Introduction To The Criminal Justice System",
    "CRIJ 2362": "Criminology",
    "CRIJ 2364": "Fundamentals Of Criminal Law",
    "CRIJ 2365": "Correctional Systems and Practice",
    "CRIJ 2367": "Police Systems & Practices",
    "CRIJ 3378": "Introduction To Methods Of Research",
    "VCST 3350": "Victimology",
}
COMS = {
    "COMS 1361": "Public Speaking",
    "COMS 2331": "Introduction - Communication Theory & Research",
    "COMS 2386": "Interpersonal Communication",
    "COMS 4381": "Communication Theory",
}


def sync(app):
    result = run(app, "dev", "sync-foundation-catalog", str(REFERENCE))
    assert result.exit_code == 0, result.output
    return result


def test_reference_exactly_matches_supplied_foundation_images():
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert {course["code"]: course["title"] for course in data["courses"]} == {**CJ, **COMS}
    assert all(course["credits"] == 3 for course in data["courses"])
    assert data["prerequisites"] == []  # Superscript notes are not prerequisite-course IDs.
    assert all("unverified" in course["description"] for course in data["courses"])
    assert {program["code"]: program["total_credits"] for program in data["programs"]} == {"CJ": 21, "COMS": 12}
    assert all("Foundation only" in program["name"] and "partial" in program["name"] for program in data["programs"])


def test_sync_makes_both_majors_available_to_registration_and_planning(app, alice, api):
    sync(app)
    programs = {program["code"]: program for program in alice.get("/api/v1/programs").get_json()["items"]}
    assert set(programs) == {"BSCS", "CJ", "COMS"}
    options = api.get("/api/v1/auth/registration-options").get_json()
    assert {program["code"] for program in options["programs"]} == set(programs)
    for code, courses, credits in (("CJ", CJ, 21), ("COMS", COMS, 12)):
        program = alice.get(f"/api/v1/programs/{programs[code]['id']}").get_json()
        assert {course["code"]: course["title"] for course in program["requirements"]} == courses
        assert sum(course["credits"] for course in program["requirements"]) == credits
        created = alice.post("/api/v1/plans", json={"name": code + " foundation plan", "program_id": program["id"]})
        assert created.status_code == 201
        plan_id = created.get_json()["id"]
        for course in program["requirements"]:
            assert alice.put(f"/api/v1/plans/{plan_id}/courses",
                             json={"course_id": course["id"], "term_index": 1}).status_code == 200
        assert alice.get(f"/api/v1/plans/{plan_id}").get_json()["total_credits"] == credits
        assert alice.get(f"/api/v1/plans/{plan_id}/validation").get_json()["valid"] is True


def test_startup_sync_is_idempotent_and_keeps_edited_programs_courses_accounts_and_plans(app, alice, admin):
    sync(app)
    programs = {program["code"]: program for program in alice.get("/api/v1/programs").get_json()["items"]}
    cj_id = programs["CJ"]["id"]
    cj = admin.get(f"/api/v1/admin/programs/{cj_id}").get_json()
    course_id = cj["requirements"][0]["id"]
    plan_id = alice.post("/api/v1/plans", json={"name": "Keep my plan", "program_id": cj_id}).get_json()["id"]
    alice.put(f"/api/v1/plans/{plan_id}/courses", json={"course_id": course_id, "term_index": 1})
    alice.put(f"/api/v1/progress/{course_id}", json={"status": "Completed", "grade": "A"})
    edited = admin.put(f"/api/v1/admin/programs/{cj_id}", json={
        "code": "CJ", "name": "Reviewed Criminal Justice", "total_credits": 120, "course_ids": [course_id],
        "expected_revision": cj["revision"],
    })
    assert edited.status_code == 200
    dbs = app.extensions["degreeplan"].databases
    with dbs.plans.begin() as conn:
        conn.execute(sa.update(tables.courses).where(tables.courses.c.id == course_id)
                     .values(title="Reviewed course title"))
    result = sync(app)
    assert "'inserted': 0" in result.output and "'edges_added': 0" in result.output
    kept = admin.get(f"/api/v1/admin/programs/{cj_id}").get_json()
    assert kept["name"] == "Reviewed Criminal Justice" and kept["total_credits"] == 120
    assert [course["id"] for course in kept["requirements"]] == [course_id]
    assert kept["requirements"][0]["title"] == "Reviewed course title"
    assert alice.get(f"/api/v1/plans/{plan_id}").get_json()["courses"][0]["course_id"] == course_id
    assert alice.get("/api/v1/progress").get_json()["items"][0]["grade"] == "A"
    with dbs.users.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(tables.users)).scalar_one() == 6
    with dbs.plans.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(tables.courses)).scalar_one() == 17


def test_foundation_pack_supports_controlled_dry_run_and_production_import(make_app, tmp_path):
    app = production_app(make_app, tmp_path)
    assert run(app, "db", "upgrade").exit_code == 0
    assert run(app, "catalog", "import", str(REFERENCE), "--dry-run").exit_code == 0
    dbs = app.extensions["degreeplan"].databases
    with dbs.plans.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(tables.programs)).scalar_one() == 0
    assert run(app, "catalog", "import", str(REFERENCE)).exit_code == 0
    with dbs.plans.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(tables.programs)).scalar_one() == 2
    assert run(app, "db", "verify").exit_code == 0
    # Even accidental dev-group registration cannot auto-sync reference data in production.
    from degreeplan.cli import dev_cli

    app.cli.add_command(dev_cli)
    refused = run(app, "dev", "sync-foundation-catalog", str(REFERENCE))
    assert refused.exit_code != 0 and "disabled in production" in refused.output
