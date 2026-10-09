import sqlalchemy as sa

from degreeplan.db.tables import sessions, users
from tests.helpers import PASSWORDS, ApiClient, logged_in


def test_idle_timeout_expires_the_session(make_app, fake_clock):
    app = make_app(session_idle_timeout=120, session_absolute_timeout=3600)
    client = logged_in(app, "alice")
    fake_clock.set(119)
    assert client.get("/api/v1/auth/me").status_code == 200
    fake_clock.set(240)  # 121 s since the last activity at t=119
    assert client.get("/api/v1/auth/me").status_code == 401


def test_activity_extends_the_idle_window_but_not_the_absolute_limit(make_app, fake_clock):
    app = make_app(session_idle_timeout=120, session_absolute_timeout=400)
    client = logged_in(app, "alice")
    for elapsed in (100, 200, 300, 390):  # always within the idle window of the previous request
        fake_clock.set(elapsed)
        assert client.get("/api/v1/auth/me").status_code == 200, elapsed
    fake_clock.set(401)  # past the absolute lifetime, even though the user was active
    assert client.get("/api/v1/auth/me").status_code == 401


def test_revoking_all_sessions_logs_everyone_out(app):
    first, second = logged_in(app, "alice"), logged_in(app, "alice")
    other = logged_in(app, "bob")
    svc = app.extensions["degreeplan"]
    with svc.databases.users.begin() as conn:
        assert svc.sessions.revoke_all(conn, 4) == 2  # alice's user id
    assert first.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 401
    assert other.get("/api/v1/auth/me").status_code == 200


def test_deactivating_a_user_kills_existing_sessions_immediately(app):
    client = logged_in(app, "alice")
    with app.extensions["degreeplan"].databases.users.begin() as conn:
        conn.execute(sa.update(users).where(users.c.username == "alice").values(is_active=False))
    assert client.get("/api/v1/auth/me").status_code == 401


def test_forged_and_malformed_cookies_are_rejected(app):
    for value in ("", "short", "x" * 500, "A" * 43, "' OR 1=1 --" + "x" * 20):
        client = ApiClient(app)
        client.set_session_cookie(value)
        assert client.get("/api/v1/auth/me").status_code == 401, value


def test_old_secret_key_still_works_only_when_listed_as_a_fallback(make_app):
    old_key = "k1-" + "a" * 45
    first = make_app(secret_key=old_key)
    cookie = logged_in(first, "alice").session_cookie

    rotated = make_app(fresh=False, secret_key="k2-" + "b" * 45, secret_key_fallbacks=(old_key,))
    survivor = ApiClient(rotated)
    survivor.set_session_cookie(cookie)
    assert survivor.get("/api/v1/auth/me").status_code == 200  # rotation window: old key still accepted

    after_window = make_app(fresh=False, secret_key="k2-" + "b" * 45)
    expired = ApiClient(after_window)
    expired.set_session_cookie(cookie)
    assert expired.get("/api/v1/auth/me").status_code == 401  # fallback removed: old sessions are dead


def test_expired_sessions_are_purged_on_the_next_login(app, fake_clock):
    logged_in(app, "alice")
    fake_clock.set(30 * 86400)  # a month later, far beyond the retention window
    ApiClient(app).login("bob", PASSWORDS["bob"])
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        remaining = conn.execute(sa.select(sessions.c.user_id)).scalars().all()
    assert remaining == [5]  # only bob's fresh session remains
