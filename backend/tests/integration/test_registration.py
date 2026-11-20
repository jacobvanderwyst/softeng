import secrets

import pytest
import sqlalchemy as sa

from degreeplan.config import ConfigError, Settings
from degreeplan.db.tables import audit_log, students, users
from tests.helpers import ApiClient, logged_in


@pytest.fixture
def signup(make_app):
    app = make_app(registration_enabled=True)
    return app, ApiClient(app)


def payload(**overrides):
    return {
        "username": "new-student", "first_name": "New", "last_name": "Student",
        "password": secrets.token_urlsafe(24), "program_id": 1, **overrides,
    }


def user_count(app):
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(users)).scalar_one()


def test_registration_creates_student_profile_then_normal_login(signup, audit_caplog):
    app, client = signup
    body = payload(username="  New-Student  ", first_name="  New  ", last_name=" Student ")
    response = client.post("/api/v1/auth/register", json=body, headers={"Origin": "https://localhost"})
    assert response.status_code == 201
    assert response.get_json() == {"ok": True}
    assert "Set-Cookie" not in response.headers
    assert client.get("/api/v1/auth/me").status_code == 401
    login = client.login("NEW-STUDENT", body["password"])
    assert login.status_code == 200
    public_user = login.get_json()["user"]
    assert public_user["role"] == "student"
    assert public_user["profile"]["first_name"] == "New"
    assert public_user["profile"]["last_name"] == "Student"
    assert public_user["profile"]["program_id"] == 1
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        user = conn.execute(sa.select(users).where(users.c.username == "new-student")).mappings().one()
        profile = conn.execute(sa.select(students).where(students.c.user_id == user["id"])).mappings().one()
        audit = conn.execute(sa.select(audit_log).where(audit_log.c.action == "auth.register")).mappings().one()
    assert user["is_active"] is True
    assert user["password_hash"].startswith("$argon2id$")
    assert body["password"] != user["password_hash"]
    assert profile["advisor_id"] is None
    assert audit["actor_user_id"] == user["id"] and audit["actor_role"] == "student"
    assert body["password"] not in audit_caplog.text
    assert body["password"] not in response.get_data(as_text=True)


def test_program_is_optional_and_new_student_can_create_own_plan(signup):
    _, client = signup
    body = payload()
    del body["program_id"]
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    user = client.login(body["username"], body["password"]).get_json()["user"]
    assert user["profile"]["program_id"] is None
    assert client.post("/api/v1/plans", json={"name": "My first plan", "program_id": 1}).status_code == 201
    assert client.get("/api/v1/plans?student_id=1").get_json()["items"][0]["student_id"] == user["profile"]["id"]


def test_registration_and_login_preserve_password_whitespace(signup):
    _, client = signup
    body = payload(password="  " + secrets.token_urlsafe(24) + "  ")
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    assert client.login(body["username"], body["password"].strip()).status_code == 401
    assert client.login(body["username"], body["password"]).status_code == 200


@pytest.mark.parametrize("extra", [
    {"role": "admin"}, {"role": "teacher"}, {"is_active": True}, {"advisor_id": 1},
    {"user_id": 1}, {"student_id": 1}, {"password_hash": "not-accepted"},
])
def test_registration_cannot_assign_privileges_or_existing_profiles(signup, extra):
    app, client = signup
    before = user_count(app)
    response = client.post("/api/v1/auth/register", json=payload(**extra))
    assert response.status_code == 422
    assert user_count(app) == before


@pytest.mark.parametrize("change", [
    {"username": ""}, {"username": "   "}, {"username": "bad name"}, {"username": "x" * 65},
    {"username": "<script>"}, {"username": 123}, {"first_name": ""}, {"first_name": "\x00Name"},
    {"last_name": " "}, {"last_name": "x" * 101}, {"program_id": 99999}, {"program_id": 0},
    {"program_id": True}, {"program_id": "1"}, {"password": "short"}, {"password": "a" * 257},
    {"password": "new-student-strong-passphrase"}, {"password": "password123"},
])
def test_invalid_registration_does_not_create_an_account(signup, change):
    app, client = signup
    before = user_count(app)
    response = client.post("/api/v1/auth/register", json=payload(**change))
    assert response.status_code == 422
    assert user_count(app) == before


def test_duplicate_usernames_do_not_change_the_existing_account(signup):
    app, client = signup
    before = user_count(app)
    response = client.post("/api/v1/auth/register", json=payload(username=" ALICE "))
    assert response.status_code == 409
    assert "cannot be registered" in response.get_json()["error"]["message"]
    assert user_count(app) == before
    assert logged_in(app, "alice").get("/api/v1/auth/me").status_code == 200
    body = payload()
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    assert client.post("/api/v1/auth/register", json=body).status_code == 409
    assert user_count(app) == before + 1


@pytest.mark.parametrize("account", ["alice", "teacher1", "admin"])
def test_signed_in_users_cannot_register_or_switch_identity(signup, account):
    app, _ = signup
    client = logged_in(app, account)
    before = user_count(app)
    assert client.post("/api/v1/auth/register", json=payload()).status_code == 409
    assert client.get("/api/v1/auth/me").get_json()["user"]["username"] == account
    assert user_count(app) == before


def test_registration_options_expose_only_public_program_choices(signup):
    _, client = signup
    options = client.get("/api/v1/auth/registration-options")
    assert options.status_code == 200
    data = options.get_json()
    assert data["enabled"] is True and data["password_min_length"] == 12
    assert set(data["programs"][0]) == {"id", "code", "name"}
    assert "csrf_token" not in data and "users" not in data


def test_disabled_registration_exposes_no_programs_and_cannot_create_users(make_app):
    app = make_app(registration_enabled=False)
    api = ApiClient(app)
    before = user_count(app)
    assert api.get("/api/v1/auth/registration-options").get_json() == {"enabled": False}
    response = api.post("/api/v1/auth/register", json=payload())
    assert response.status_code == 403
    assert user_count(app) == before


def test_registration_origin_json_and_other_write_csrf_protection(signup):
    app, client = signup
    before = user_count(app)
    assert client.post("/api/v1/auth/register", json=payload(),
                       headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.post("/api/v1/auth/register", json=payload(), headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/v1/auth/register", data="username=someone").status_code == 415
    assert client.post("/api/v1/auth/register", data="{broken", content_type="application/json").status_code == 400
    assert user_count(app) == before
    student = logged_in(app, "alice")
    assert student.client.post("/api/v1/plans", base_url="https://localhost",
                               json={"name": "Missing CSRF", "program_id": 1}).status_code == 403


@pytest.mark.parametrize("failure", ["profile", "audit"])
def test_registration_rolls_back_account_and_profile_on_failure(signup, monkeypatch, failure):
    app, client = signup
    before = user_count(app)
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        profiles_before = conn.execute(sa.select(sa.func.count()).select_from(students)).scalar_one()

    def fail(*args, **kwargs):
        raise RuntimeError("Simulated registration transaction failure")

    target = "people.create_student" if failure == "profile" else "audit.record"
    monkeypatch.setattr("degreeplan.api.auth." + target, fail)
    assert client.post("/api/v1/auth/register", json=payload()).status_code == 500
    assert user_count(app) == before
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(students)).scalar_one() == profiles_before


def test_registration_rate_limit_is_separate_from_login(make_app):
    app = make_app(registration_enabled=True, ratelimit_enabled=True, registration_rate_limit="2 per minute")
    client = ApiClient(app)
    assert client.post("/api/v1/auth/register", json=payload(password="short")).status_code == 422
    assert client.post("/api/v1/auth/register", json=payload(password="short")).status_code == 422
    blocked = client.post("/api/v1/auth/register", json=payload())
    assert blocked.status_code == 429 and blocked.headers.get("Retry-After")
    assert logged_in(app, "alice").get("/api/v1/auth/me").status_code == 200


def test_production_registration_defaults_enabled_and_allows_explicit_opt_out(tmp_path):
    environment = {
        "APP_ENV": "production", "SECRET_KEY": secrets.token_urlsafe(48),
        "USERS_DATABASE_URL": f"sqlite:///{(tmp_path / 'users.sqlite3').as_posix()}",
        "PLANS_DATABASE_URL": f"sqlite:///{(tmp_path / 'plans.sqlite3').as_posix()}",
    }
    assert Settings.from_env(environment).registration_enabled is True
    environment["REGISTRATION_ENABLED"] = "false"
    assert Settings.from_env(environment).registration_enabled is False
    environment["REGISTRATION_ENABLED"] = "invalid"
    with pytest.raises(ConfigError, match="REGISTRATION_ENABLED"):
        Settings.from_env(environment)
    assert Settings.from_env({"APP_ENV": "development"}).registration_enabled is True


def test_explicitly_enabled_production_registration_uses_normal_secure_login(make_app):
    app = make_app(env_name="production", registration_enabled=True, ratelimit_enabled=True)
    client = ApiClient(app)
    body = payload()
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    response = client.login(body["username"], body["password"])
    assert response.status_code == 200
    assert "__Host-sid=" in response.headers["Set-Cookie"]
    assert "Secure" in response.headers["Set-Cookie"]
