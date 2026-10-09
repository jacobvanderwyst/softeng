import sqlalchemy as sa

from degreeplan.db.tables import sessions, users
from tests.helpers import COOKIE, PASSWORDS, ApiClient, logged_in


def _query(app, stmt):
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        return conn.execute(stmt).mappings().all()


def test_login_success_returns_user_and_hardened_cookie(app, api):
    resp = api.login("alice", PASSWORDS["alice"])
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["user"]["username"] == "alice"
    assert data["user"]["role"] == "student"
    assert data["user"]["profile"]["first_name"] == "Alice"
    assert data["csrf_token"]
    assert "password" not in str(data).lower()

    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
        assert attribute in cookie
    assert "Domain" not in cookie  # required by the __Host- prefix


def test_session_token_is_not_stored_in_clear(app, api):
    api.login("alice", PASSWORDS["alice"])
    token = api.session_cookie
    stored = [r["token_hash"] for r in _query(app, sa.select(sessions.c.token_hash))]
    assert token and token not in stored
    assert all(len(h) == 64 for h in stored)


def test_wrong_password_and_unknown_user_are_indistinguishable(api):
    wrong = api.login("alice", "definitely-wrong-password")
    unknown = api.login("nobody-here", "definitely-wrong-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json()["error"]["message"] == unknown.get_json()["error"]["message"]
    assert COOKIE not in wrong.headers.get("Set-Cookie", "")


def test_usernames_are_case_insensitive(api):
    assert api.login("ALICE", PASSWORDS["alice"]).status_code == 200


def test_login_request_validation(api):
    assert api.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 422
    assert api.post("/api/v1/auth/login", data="x=y").status_code == 415
    assert api.post("/api/v1/auth/login", data="{not json", content_type="application/json").status_code == 400
    extra = api.post("/api/v1/auth/login", json={"username": "a", "password": "b", "admin": True})
    assert extra.status_code == 422


def test_disabled_account_cannot_log_in(app, api):
    with app.extensions["degreeplan"].databases.users.begin() as conn:
        conn.execute(sa.update(users).where(users.c.username == "alice").values(is_active=False))
    assert api.login("alice", PASSWORDS["alice"]).status_code == 401


def test_protected_routes_require_login(api):
    for path in ("/api/v1/auth/me", "/api/v1/courses", "/api/v1/programs", "/api/v1/plans"):
        resp = api.get(path)
        assert resp.status_code == 401, path
        assert resp.get_json()["error"]["code"] == "unauthorized"


def test_me_returns_profile_and_csrf_token(alice):
    data = alice.get("/api/v1/auth/me").get_json()
    assert data["user"]["username"] == "alice"
    assert data["csrf_token"] == alice.csrf


def test_logout_revokes_the_session_server_side(app, alice):
    old_cookie = alice.session_cookie
    assert alice.post("/api/v1/auth/logout").status_code == 200
    assert alice.get("/api/v1/auth/me").status_code == 401
    # Replaying the stolen/old cookie must fail too: revocation is server-side.
    replay = ApiClient(app)
    replay.set_session_cookie(old_cookie)
    assert replay.get("/api/v1/auth/me").status_code == 401


def test_each_login_gets_a_new_session_and_csrf_token(app):
    first, second = ApiClient(app), ApiClient(app)
    first.login("alice", PASSWORDS["alice"])
    second.login("alice", PASSWORDS["alice"])
    assert first.session_cookie != second.session_cookie
    assert first.csrf != second.csrf


def test_change_password_flow(app, alice):
    other_device = logged_in(app, "alice")
    new_password = "a-brand-new-passphrase-42"

    wrong = alice.post("/api/v1/auth/change-password",
                       json={"current_password": "not-my-password", "new_password": new_password})
    assert wrong.status_code == 401

    weak = alice.post("/api/v1/auth/change-password",
                      json={"current_password": PASSWORDS["alice"], "new_password": "short"})
    assert weak.status_code == 422
    assert weak.get_json()["error"]["details"][0]["field"] == "new_password"

    ok = alice.post("/api/v1/auth/change-password",
                    json={"current_password": PASSWORDS["alice"], "new_password": new_password})
    assert ok.status_code == 200
    assert alice.get("/api/v1/auth/me").status_code == 200  # current session survives
    assert other_device.get("/api/v1/auth/me").status_code == 401  # other sessions are revoked

    fresh = ApiClient(app)
    assert fresh.login("alice", PASSWORDS["alice"]).status_code == 401
    assert fresh.login("alice", new_password).status_code == 200


def test_legacy_scrypt_hash_is_upgraded_to_argon2_on_login(app, api):
    import hashlib

    password = PASSWORDS["bob"]
    salt = "legacysalt0123"
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=1, maxmem=132 * 2**14 * 8).hex()
    legacy = f"scrypt:{2**14}:8:1${salt}${digest}"
    with app.extensions["degreeplan"].databases.users.begin() as conn:
        conn.execute(sa.update(users).where(users.c.username == "bob").values(password_hash=legacy))

    assert api.login("bob", password).status_code == 200
    stored = _query(app, sa.select(users.c.password_hash).where(users.c.username == "bob"))[0]["password_hash"]
    assert stored.startswith("$argon2id$")
    assert ApiClient(app).login("bob", password).status_code == 200  # still works after the upgrade


def test_login_is_rate_limited_per_ip(make_app):
    app = make_app(ratelimit_enabled=True, login_rate_limit="3 per minute", lockout_threshold=50)
    client = ApiClient(app)
    codes = [client.login("alice", "wrong-password-guess").status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]
    assert client.login("alice", "wrong-password-guess").get_json()["error"]["request_id"]
