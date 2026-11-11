import json
import secrets

import pytest
import sqlalchemy as sa

from degreeplan.db import migrate
from degreeplan.db.tables import courses, programs, students
from tests.integration.test_cli import run


def payload():
    return {
        "version": 1,
        "programs": [{"code": "NEW", "name": "New program", "total_credits": 120}],
        "courses": [
            {"code": "NEW101", "title": "New first", "credits": 3},
            {"code": "NEW201", "title": "New second", "credits": 4}],
        "prerequisites": [{"course": "NEW201", "prerequisite": "NEW101"}],
        "requirements": [{"program": "NEW", "course": "NEW201"}],
    }


def invoke_import(app, tmp_path, data, *options):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return run(app, "catalog", "import", str(path), *options)


def test_fresh_production_empty_import_and_verify(make_app, tmp_path):
    app = make_app(seeded=False, env_name="production", secret_key=secrets.token_urlsafe(48),
                   cookie_secure=True, ratelimit_enabled=True)
    assert run(app, "db", "upgrade").exit_code == 0
    assert run(app, "db", "verify").exit_code == 0
    for group in ("users", "catalog"):
        assert run(app, group, "--help").exit_code == 0
    assert invoke_import(app, tmp_path, payload(), "--dry-run").exit_code == 0
    dbs = app.extensions["degreeplan"].databases
    with dbs.plans.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(courses)).scalar_one() == 0
    assert invoke_import(app, tmp_path, payload()).exit_code == 0
    assert invoke_import(app, tmp_path, payload()).exit_code == 0
    assert run(app, "db", "verify").exit_code == 0
    assert run(app, "users", "list").output == ""


def test_catalog_explicit_update_and_rollback(app, tmp_path):
    assert invoke_import(app, tmp_path, payload()).exit_code == 0
    data = payload()
    data["programs"][0]["name"] = "Changed"
    data["courses"].append({"code": "FAIL", "title": "Should rollback", "credits": 3})
    data["courses"][0]["title"] = "Changed title"
    assert invoke_import(app, tmp_path, data).exit_code != 0
    with app.extensions["degreeplan"].databases.plans.connect() as conn:
        assert conn.execute(sa.select(programs.c.name).where(programs.c.code == "NEW")).scalar_one() == "New program"
        assert conn.execute(sa.select(courses.c.id).where(courses.c.code == "FAIL")).first() is None
    assert invoke_import(app, tmp_path, data, "--update", "--dry-run").exit_code == 0
    assert invoke_import(app, tmp_path, data, "--update").exit_code == 0


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=2),
    lambda d: d.update(extra=True),
    lambda d: d["courses"].append(d["courses"][0]),
    lambda d: d["courses"][0].update(credits=True),
    lambda d: d["courses"][0].update(credits=-1),
    lambda d: d["prerequisites"][0].update(prerequisite="MISSING"),
    lambda d: d["requirements"][0].update(program="MISSING"),
    lambda d: d["prerequisites"].append({"course": "NEW101", "prerequisite": "NEW201"}),
    lambda d: d["prerequisites"].append(d["prerequisites"][0]),
])
def test_import_invalid_is_all_or_nothing(app, tmp_path, mutation):
    data = payload()
    mutation(data)
    assert invoke_import(app, tmp_path, data).exit_code != 0
    with app.extensions["degreeplan"].databases.plans.connect() as conn:
        assert conn.execute(sa.select(programs.c.id).where(programs.c.code == "NEW")).first() is None


def test_import_duplicate_keys_and_bounded_file(app, tmp_path):
    path = tmp_path / "bad.json"
    for contents in ('{"version":1,"version":1}', " " * (5 * 1024 * 1024 + 1), "not json"):
        path.write_text(contents, encoding="utf-8")
        assert run(app, "catalog", "import", str(path)).exit_code != 0


def test_schema_validate_and_stamp_refuse_unknown_without_touching_data(make_app):
    app = make_app(seeded=False)
    dbs = app.extensions["degreeplan"].databases
    with dbs.users.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE valuable (id INTEGER PRIMARY KEY, note TEXT)")
        conn.exec_driver_sql("INSERT INTO valuable VALUES (1,'preserve')")
    for command in ("validate-schema", "stamp"):
        assert run(app, "db", command, "--target", "users").exit_code != 0
    with dbs.users.connect() as conn:
        assert conn.exec_driver_sql("SELECT note FROM valuable").scalar_one() == "preserve"
        assert migrate.current_revision(conn) is None


def test_validate_known_older_schema_before_upgrade(make_app):
    app = make_app(seeded=False)
    dbs = app.extensions["degreeplan"].databases
    migrate.upgrade(dbs.plans, "plans", "plans_0002")
    assert run(app, "db", "validate-schema", "--target", "plans", "--revision", "plans_0002").exit_code == 0
    assert run(app, "db", "validate-schema", "--target", "plans").exit_code != 0
    assert run(app, "db", "validate-schema", "--target", "plans", "--revision", "current").exit_code == 0
    with dbs.plans.connect() as conn:
        assert migrate.current_revision(conn) == "plans_0002"


def test_verify_soft_refs_and_schema_only(app):
    dbs = app.extensions["degreeplan"].databases
    assert run(app, "db", "verify").exit_code == 0
    with dbs.users.begin() as conn:
        conn.execute(sa.update(students).where(students.c.id == 1).values(program_id=9999))
    result = run(app, "db", "verify")
    assert result.exit_code != 0 and "unknown program" in result.output
    assert run(app, "db", "verify", "--schema-only").exit_code == 0


def test_profile_update_clear_and_guard(app):
    assert run(app, "users", "update-profile", "alice", "--first-name", "Alicia",
               "--clear-program", "--clear-advisor").exit_code == 0
    dbs = app.extensions["degreeplan"].databases
    with dbs.users.connect() as conn:
        row = conn.execute(sa.select(students).where(students.c.id == 1)).mappings().one()
        assert row["first_name"] == "Alicia" and row["program_id"] is None and row["advisor_id"] is None
    assert run(app, "users", "update-profile", "alice", "--program-id", "1", "--advisor", "teacher1").exit_code == 0
    for options in ([], ["--department", "x"], ["--first-name", ""], ["--program-id", "999"],
                    ["--advisor", "alice"], ["--advisor", "teacher1", "--clear-advisor"]):
        assert run(app, "users", "update-profile", "alice", *options).exit_code != 0
    assert run(app, "users", "update-profile", "teacher1", "--department", "Math").exit_code == 0
    assert run(app, "users", "update-profile", "teacher1", "--clear-department").exit_code == 0
    assert run(app, "users", "update-profile", "teacher1", "--clear-program").exit_code != 0
    assert run(app, "users", "update-profile", "admin", "--first-name", "X").exit_code != 0


def test_revoke_all_requires_confirmation_and_revokes_everyone(app, alice, bob):
    assert run(app, "users", "revoke-all-sessions").exit_code != 0
    assert alice.get("/api/v1/auth/me").status_code == 200
    assert run(app, "users", "revoke-all-sessions", "--yes").exit_code == 0
    assert alice.get("/api/v1/auth/me").status_code == 401
    assert bob.get("/api/v1/auth/me").status_code == 401


def test_import_failure_after_first_insert_rolls_back(app, tmp_path):
    data = payload()
    with app.extensions["degreeplan"].databases.plans.connect() as conn:
        existing = conn.execute(sa.select(courses.c.code)).scalar()
    data["courses"].append({"code": existing, "title": "Conflicting title", "credits": 2})
    assert invoke_import(app, tmp_path, data).exit_code != 0
    with app.extensions["degreeplan"].databases.plans.connect() as conn:
        assert conn.execute(sa.select(programs.c.id).where(programs.c.code == "NEW")).first() is None
        assert conn.execute(sa.select(courses.c.id).where(courses.c.code == "NEW101")).first() is None


def test_current_revision_validation_refuses_unstamped_and_unknown(app, make_app):
    empty = make_app(seeded=False)
    assert run(empty, "db", "validate-schema", "--target", "users", "--revision", "current").exit_code != 0
    with app.extensions["degreeplan"].databases.users.begin() as conn:
        conn.exec_driver_sql("UPDATE alembic_version SET version_num='unknown'")
    assert run(app, "db", "validate-schema", "--target", "users", "--revision", "current").exit_code != 0


def test_adoption_rejects_extra_triggers_without_removing_them(app):
    with app.extensions["degreeplan"].databases.plans.begin() as conn:
        conn.exec_driver_sql("CREATE TRIGGER extra AFTER INSERT ON courses BEGIN SELECT 1; END")
    assert run(app, "db", "validate-schema", "--target", "plans").exit_code != 0
    assert run(app, "db", "stamp", "--target", "plans").exit_code != 0
    with app.extensions["degreeplan"].databases.plans.connect() as conn:
        assert conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'").scalar_one() == "extra"


def test_catalog_list_exposes_program_assignment_ids_without_mutation(app, tmp_path):
    assert invoke_import(app, tmp_path, payload()).exit_code == 0
    dbs = app.extensions["degreeplan"].databases
    with dbs.plans.connect() as conn:
        before = conn.execute(sa.select(programs).order_by(programs.c.code)).mappings().all()
    result = run(app, "catalog", "list")
    assert result.exit_code == 0
    for row in before:
        line = next(line for line in result.output.splitlines() if line.split()[1] == row["code"])
        assert int(line.split()[0]) == row["id"]
        assert row["name"] in line and f"credits={row['total_credits']}" in line
    with dbs.plans.connect() as conn:
        assert conn.execute(sa.select(programs).order_by(programs.c.code)).mappings().all() == before


def test_user_list_details_exposes_profile_ids_without_secrets(app, alice):
    from degreeplan.db.tables import audit_log, sessions, teachers, users

    default = run(app, "users", "list")
    detailed = run(app, "users", "list", "--details")
    assert default.exit_code == detailed.exit_code == 0
    assert "student_id=" not in default.output and "user_id=" not in default.output
    dbs = app.extensions["degreeplan"].databases
    with dbs.users.connect() as conn:
        account_rows = conn.execute(sa.select(users)).mappings().all()
        student_rows = conn.execute(sa.select(students)).mappings().all()
        teacher_rows = conn.execute(sa.select(teachers)).mappings().all()
        session_rows = conn.execute(sa.select(sessions)).mappings().all()
        audits_before = conn.execute(sa.select(sa.func.count()).select_from(audit_log)).scalar_one()
    for row in account_rows:
        line = next(line for line in detailed.output.splitlines() if line.split()[0] == row["username"])
        assert f"user_id={row['id']}" in line
        assert row["password_hash"] not in detailed.output
        for profile in student_rows:
            if profile["user_id"] == row["id"]:
                assert f"student_id={profile['id']}" in line
                assert f"program_id={profile['program_id']}" in line
                assert f"advisor_id={profile['advisor_id']}" in line
        for profile in teacher_rows:
            if profile["user_id"] == row["id"]:
                assert f"teacher_id={profile['id']}" in line
    for row in session_rows:
        assert row["token_hash"] not in detailed.output and row["csrf_token"] not in detailed.output
    with dbs.users.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(audit_log)).scalar_one() == audits_before


def test_id_listing_on_fresh_empty_production(make_app):
    app = make_app(seeded=False, env_name="production", secret_key=secrets.token_urlsafe(48),
                   cookie_secure=True, ratelimit_enabled=True)
    assert run(app, "db", "upgrade").exit_code == 0
    for args in (("catalog", "list"), ("users", "list", "--details")):
        result = run(app, *args)
        assert result.exit_code == 0 and result.output == ""
