import secrets

import pytest
import sqlalchemy as sa

from degreeplan.repositories import security_store
from degreeplan.repositories import users as users_repo
from degreeplan.security import lockout
from tests.helpers import PASSWORDS, ApiClient
from tests.integration.test_cli import production_app, run


def generated_credentials(result):
    assert result.exit_code == 0
    return dict(line.split() for line in result.output.splitlines() if line.startswith("  "))


def test_refresh_passwords_preserves_data_and_other_accounts(app, alice, teacher1, bob):
    plan = alice.post("/api/v1/plans", json={"name": "Keep me", "program_id": 1}).get_json()
    alice.put(f"/api/v1/plans/{plan['id']}/semesters", json={"term_index": 1, "name": "Spring 2027"})
    alice.put(f"/api/v1/plans/{plan['id']}/courses", json={"course_id": 1, "term_index": 1})
    alice.put("/api/v1/progress/1", json={"status": "Completed"})
    credentials = generated_credentials(run(app, "dev", "refresh-demo-passwords"))
    assert set(credentials) == {"alice", "teacher1"}
    assert len(set(credentials.values())) == 2
    assert all(len(password) >= 12 for password in credentials.values())
    assert alice.get("/api/v1/auth/me").status_code == 401
    assert teacher1.get("/api/v1/auth/me").status_code == 401
    assert bob.get("/api/v1/auth/me").status_code == 200
    student = ApiClient(app)
    assert student.login("alice", PASSWORDS["alice"]).status_code == 401
    assert student.login("alice", credentials["alice"]).status_code == 200
    saved = student.get(f"/api/v1/plans/{plan['id']}").get_json()
    assert saved["name"] == "Keep me"
    assert saved["semesters"] == [{"term_index": 1, "name": "Spring 2027"}]
    assert saved["courses"][0]["course_id"] == 1
    assert student.get("/api/v1/progress").get_json()["items"] == [{"course_id": 1, "status": "Completed"}]
    teacher = ApiClient(app)
    assert teacher.login("teacher1", credentials["teacher1"]).status_code == 200
    assert teacher.get("/api/v1/plans?student_id=1").status_code == 200
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        rows = security_store.recent_audit(conn, 20)
    resets = [row for row in rows if row["action"] == "user.set_password"]
    assert len(resets) == 2
    assert all(password not in str(rows) for password in credentials.values())


def test_passwords_rotate_on_each_refresh(app):
    first = generated_credentials(run(app, "dev", "refresh-demo-passwords"))
    second = generated_credentials(run(app, "dev", "refresh-demo-passwords"))
    assert all(first[name] != second[name] for name in first)
    for username in first:
        client = ApiClient(app)
        assert client.login(username, first[username]).status_code == 401
        assert client.login(username, second[username]).status_code == 200


def test_refresh_clears_only_the_two_demo_username_lockouts(app):
    svc = app.extensions["degreeplan"]
    with svc.databases.users.begin() as conn:
        for username in ("alice", "teacher1", "bob"):
            for _ in range(svc.settings.lockout_threshold):
                lockout.record_failure(conn, svc.settings, lockout.lockout_key(username))
    credentials = generated_credentials(run(app, "dev", "refresh-demo-passwords"))
    for username, password in credentials.items():
        assert ApiClient(app).login(username, password).status_code == 200
    assert ApiClient(app).login("bob", PASSWORDS["bob"]).status_code == 429


@pytest.mark.parametrize("problem", ["missing", "inactive", "wrong_role"])
def test_invalid_demo_accounts_do_not_partially_reset_passwords(app, problem):
    from degreeplan.db.tables import teachers, users

    with app.extensions["degreeplan"].databases.users.begin() as conn:
        teacher_id = conn.execute(sa.select(users.c.id).where(users.c.username == "teacher1")).scalar_one()
        if problem == "missing":
            conn.execute(sa.delete(teachers).where(teachers.c.user_id == teacher_id))
            conn.execute(sa.delete(users).where(users.c.id == teacher_id))
        elif problem == "inactive":
            users_repo.set_active(conn, teacher_id, False)
        else:
            conn.execute(sa.update(users).where(users.c.id == teacher_id).values(role="admin"))
    result = run(app, "dev", "refresh-demo-passwords")
    assert result.exit_code != 0
    assert "-KeepDemoPasswords" in result.output
    assert not any(line.startswith("  ") for line in result.output.splitlines())
    assert ApiClient(app).login("alice", PASSWORDS["alice"]).status_code == 200


def test_failed_second_update_rolls_back_both_accounts(app, monkeypatch, alice, teacher1):
    original = users_repo.set_password_hash
    count = 0

    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("Simulated password update failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(users_repo, "set_password_hash", fail_second)
    result = run(app, "dev", "refresh-demo-passwords")
    assert result.exit_code != 0
    assert "Fresh demo passwords" not in result.output
    assert alice.get("/api/v1/auth/me").status_code == 200
    assert teacher1.get("/api/v1/auth/me").status_code == 200
    for name in ("alice", "teacher1"):
        assert ApiClient(app).login(name, PASSWORDS[name]).status_code == 200


def test_generation_retries_passwords_that_contain_the_username(app, monkeypatch):
    first = "alice-" + secrets.token_urlsafe(20)
    valid = (secrets.token_urlsafe(24), secrets.token_urlsafe(24))
    generated = iter((first, *valid))
    monkeypatch.setattr("degreeplan.cli.secrets.token_urlsafe", lambda size: next(generated))
    credentials = generated_credentials(run(app, "dev", "refresh-demo-passwords"))
    assert credentials == dict(zip(("alice", "teacher1"), valid, strict=True))


def test_demo_password_refresh_is_absent_and_refuses_production(make_app, tmp_path):
    from degreeplan.cli import dev_cli

    app = production_app(make_app, tmp_path)
    assert run(app, "dev", "refresh-demo-passwords").exit_code == 2
    # Even accidentally registering the dev group must not allow a production reset.
    app.cli.add_command(dev_cli)
    result = run(app, "dev", "refresh-demo-passwords")
    assert result.exit_code != 0
    assert "cannot be refreshed in production" in result.output
