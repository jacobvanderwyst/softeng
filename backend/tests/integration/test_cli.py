import secrets
import sqlite3
from contextlib import closing

import pytest
import sqlalchemy as sa

from degreeplan.config import Settings
from degreeplan.db.tables import users
from degreeplan.devtools.sample_data import seed_sample_data
from tests.conftest import sqlite_url
from tests.helpers import PASSWORDS, ApiClient, logged_in


def run(app, *args, input_text=None):
    return app.test_cli_runner().invoke(args=list(args), input=input_text)


def password_input(password):
    return f"{password}\n{password}\n"


def test_create_student_with_advisor_then_login(app):
    password = secrets.token_urlsafe(16)
    result = run(app, "users", "create", "dave", "--role", "student", "--first-name", "Dave", "--last-name", "Doe",
                 "--program-id", "1", "--advisor", "teacher1", input_text=password_input(password))
    assert result.exit_code == 0, result.output
    assert "Created student 'dave'" in result.output
    assert password not in result.output  # the password is never echoed

    client = ApiClient(app)
    assert client.login("dave", password).status_code == 200
    assert client.get("/api/v1/auth/me").get_json()["user"]["profile"]["program_id"] == 1
    # advisor assignment is effective: teacher1 can read Dave's plans
    client.post("/api/v1/plans", json={"name": "d", "program_id": 1})
    teacher = logged_in(app, "teacher1")
    assert teacher.get("/api/v1/plans?student_id=4").status_code == 200


def test_create_admin_and_teacher(app):
    pw = secrets.token_urlsafe(16)
    assert run(app, "users", "create", "root2", "--role", "admin", input_text=password_input(pw)).exit_code == 0
    assert run(app, "users", "create", "prof", "--role", "teacher", "--first-name", "Pat", "--last-name", "Prof",
               "--department", "Math", input_text=password_input(pw)).exit_code == 0
    assert ApiClient(app).login("prof", pw).get_json()["user"]["profile"]["department"] == "Math"


def test_create_reads_password_from_stdin_for_automation(app):
    pw = secrets.token_urlsafe(16)
    result = run(app, "users", "create", "robot", "--role", "admin", "--password-stdin", input_text=pw + "\n")
    assert result.exit_code == 0, result.output
    assert ApiClient(app).login("robot", pw).status_code == 200


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["bad name!", "--role", "admin"], "Username must be"),
        (["x", "--role", "student"], "--first-name"),
        (["x", "--role", "admin", "--advisor", "teacher1"], "students only"),
        (["x", "--role", "student", "--first-name", "A", "--last-name", "B", "--advisor", "ghost"], "No such user"),
        (["x", "--role", "student", "--first-name", "A", "--last-name", "B", "--advisor", "alice"], "not a teacher"),
        (["x", "--role", "student", "--first-name", "A", "--last-name", "B", "--program-id", "99"], "Unknown program"),
    ],
)
def test_create_rejects_invalid_input(app, args, message):
    result = run(app, "users", "create", *args, input_text=password_input(secrets.token_urlsafe(16)))
    assert result.exit_code != 0 and message in result.output


def test_create_enforces_the_password_policy_and_uniqueness(app):
    weak = run(app, "users", "create", "weakling", "--role", "admin", input_text=password_input("short"))
    assert weak.exit_code != 0 and "at least 12" in weak.output
    duplicate = run(app, "users", "create", "ALICE", "--role", "admin",
                    input_text=password_input(secrets.token_urlsafe(16)))
    assert duplicate.exit_code != 0 and "already exists" in duplicate.output


def test_password_prompt_mismatch_is_rejected(app):
    mismatched = "one-long-password-1\ntwo-long-password-2\n"
    result = run(app, "users", "create", "mismatch", "--role", "admin", input_text=mismatched)
    assert result.exit_code != 0
    assert ApiClient(app).login("mismatch", "one-long-password-1").status_code == 401


def test_set_password_revokes_sessions_and_changes_the_credential(app, alice):
    new = secrets.token_urlsafe(16)
    result = run(app, "users", "set-password", "alice", input_text=password_input(new))
    assert result.exit_code == 0, result.output
    assert alice.get("/api/v1/auth/me").status_code == 401  # existing session revoked
    fresh = ApiClient(app)
    assert fresh.login("alice", PASSWORDS["alice"]).status_code == 401
    assert fresh.login("alice", new).status_code == 200


def test_deactivate_and_activate(app, alice):
    assert run(app, "users", "deactivate", "alice").exit_code == 0
    assert alice.get("/api/v1/auth/me").status_code == 401
    assert ApiClient(app).login("alice", PASSWORDS["alice"]).status_code == 401
    assert run(app, "users", "activate", "alice").exit_code == 0
    assert ApiClient(app).login("alice", PASSWORDS["alice"]).status_code == 200


def test_revoke_sessions(app, alice, bob):
    result = run(app, "users", "revoke-sessions", "alice")
    assert "Revoked 1 session" in result.output
    assert alice.get("/api/v1/auth/me").status_code == 401
    assert bob.get("/api/v1/auth/me").status_code == 200


def test_unknown_user_errors(app):
    for command in ("deactivate", "activate", "revoke-sessions", "set-password"):
        result = run(app, "users", command, "ghost", input_text=password_input("irrelevant-long-pw-1"))
        assert result.exit_code != 0 and "No such user" in result.output


def test_list_never_shows_password_hashes(app):
    result = run(app, "users", "list")
    assert "alice" in result.output and "student" in result.output
    assert "$argon2" not in result.output


def test_cli_actions_are_audited_without_secrets(app):
    from degreeplan.repositories import security_store

    pw = secrets.token_urlsafe(16)
    run(app, "users", "create", "audited", "--role", "admin", input_text=password_input(pw))
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        rows = security_store.recent_audit(conn, 5)
    created = [r for r in rows if r["action"] == "user.create"][0]
    assert created["actor_role"] == "cli" and pw not in str(created)


# ----------------------------------------------------------------------------- db commands
def test_db_status_reports_ok_and_needs_migration(app, make_app):
    assert run(app, "db", "status").exit_code == 0
    empty = make_app(seeded=False)
    result = run(empty, "db", "status")
    assert result.exit_code == 1 and "NEEDS MIGRATION" in result.output


def test_db_upgrade_then_status_ok_on_empty_databases(make_app):
    app = make_app(seeded=False)
    assert run(app, "db", "upgrade").exit_code == 0
    assert run(app, "db", "status").exit_code == 0
    assert run(app, "db", "upgrade", "--target", "users").exit_code == 0  # idempotent


def test_db_stamp_adopts_an_existing_schema(make_app):
    from degreeplan.db.tables import plans_md, users_md

    app = make_app(seeded=False)
    dbs = app.extensions["degreeplan"].databases
    users_md.create_all(dbs.users)
    plans_md.create_all(dbs.plans)
    assert run(app, "db", "status").exit_code == 1
    assert run(app, "db", "stamp", "--target", "users").exit_code == 0
    assert run(app, "db", "stamp", "--target", "plans").exit_code == 0
    assert run(app, "db", "status").exit_code == 0


def test_db_backup_creates_verified_copies_that_contain_the_data(app, tmp_path):
    destination = tmp_path / "backups"
    result = run(app, "db", "backup", "--dest", str(destination))
    assert result.exit_code == 0, result.output
    files = sorted(p.name.split("-")[0] for p in destination.glob("*.sqlite3"))
    assert files == ["plans", "users"]
    users_copy = next(destination.glob("users-*.sqlite3"))
    with closing(sqlite3.connect(users_copy)) as conn:  # `with connect()` alone does not close it
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 6


def test_db_backup_refuses_non_sqlite_databases(make_app, tmp_path):
    app = make_app(seeded=False)
    dbs = app.extensions["degreeplan"]
    # Pretend the users database is a server database: backups must point operators to native tooling.
    from dataclasses import replace

    dbs.settings = replace(dbs.settings, users_database_url="postgresql://app@db.internal/users")
    result = run(app, "db", "backup", "--dest", str(tmp_path / "b"))
    assert result.exit_code != 0 and "native backup tooling" in result.output
    assert "app@db.internal" not in result.output  # connection details are not echoed


# ----------------------------------------------------------------------------- dev tooling guard
def test_dev_seed_loads_data_with_random_printed_passwords(make_app):
    app = make_app(seeded=False)
    run(app, "db", "upgrade")
    result = run(app, "dev", "seed")
    assert result.exit_code == 0, result.output
    lines = [ln.split() for ln in result.output.splitlines() if ln.startswith("  ")]
    credentials = {name: pw for name, pw in lines}
    assert set(credentials) >= {"admin", "teacher1", "alice"}
    assert len(set(credentials.values())) == len(credentials)  # all distinct
    assert ApiClient(app).login("alice", credentials["alice"]).status_code == 200


def production_app(make_app, tmp_path):
    return make_app(
        seeded=False,
        env_name="production",
        secret_key=secrets.token_urlsafe(48),
        cookie_secure=True,
        ratelimit_enabled=True,
    )


def test_dev_commands_do_not_exist_in_production(make_app, tmp_path):
    app = production_app(make_app, tmp_path)
    result = run(app, "dev", "seed")
    assert result.exit_code == 2 and "No such command" in result.output
    assert run(app, "db", "upgrade").exit_code == 0
    assert run(app, "users", "list").exit_code == 0  # real operator commands remain


def test_sample_data_function_refuses_to_run_in_production(make_app, tmp_path):
    app = production_app(make_app, tmp_path)
    svc = app.extensions["degreeplan"]
    with svc.databases.users.connect() as users_conn, svc.databases.plans.connect() as plans_conn:
        with pytest.raises(RuntimeError, match="production"):
            seed_sample_data(users_conn, plans_conn, settings=svc.settings, passwords=svc.passwords,
                             credentials=PASSWORDS)


def test_sample_data_requires_a_password_for_every_account(app, tmp_path):
    settings = Settings.for_testing(sqlite_url(tmp_path / "a.sqlite3"), sqlite_url(tmp_path / "b.sqlite3"))
    svc = app.extensions["degreeplan"]
    with svc.databases.users.connect() as u, svc.databases.plans.connect() as p:
        with pytest.raises(ValueError, match="No password supplied"):
            seed_sample_data(u, p, settings=settings, passwords=svc.passwords, credentials={"admin": "x"})


def test_hash_column_never_contains_plaintext(app):
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        hashes = conn.execute(sa.select(users.c.password_hash)).scalars().all()
    assert hashes and all(h.startswith("$argon2id$") for h in hashes)
    assert not any(pw in h for h in hashes for pw in PASSWORDS.values())
