"""End-to-end tests: real sockets, real TLS, real SQLite (on high ports so no admin rights needed)."""
import http.client
import ssl
import threading
from urllib.parse import urlencode

import pytest

import gen_cert
import server

HOST = "127.0.0.1"  # avoid slow IPv6-first resolution of 'localhost' on Windows
CREDS: dict[str, str] = {}  # random demo passwords, filled by the assets fixture
UNVERIFIED = ssl.create_default_context()
UNVERIFIED.check_hostname = False
UNVERIFIED.verify_mode = ssl.CERT_NONE


@pytest.fixture(scope="session")
def assets(tmp_path_factory):
    """Demo DB + self-signed cert, built once (scrypt hashing is deliberately slow)."""
    d = tmp_path_factory.mktemp("assets")
    db = d / "poc.sqlite3"
    CREDS.update(server.init_demo_db(db))
    cert, key = d / "cert.pem", d / "key.pem"
    gen_cert.ensure_cert(cert, key)
    return db, cert, key


def start(assets, **overrides):
    db, cert, key = assets
    cfg = server.Config(bind="localhost", http_port=0, https_port=0, db_path=db, cert=cert, key=key, **overrides)
    http_srv, https_srv, app = server.create_servers(cfg)
    for s in (http_srv, https_srv):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return http_srv, https_srv, app


def stop(*servers):
    for s in servers:
        s.shutdown()
        s.server_close()


@pytest.fixture
def srv(assets):
    http_srv, https_srv, app = start(assets)
    yield http_srv, https_srv, app
    stop(http_srv, https_srv)


class Resp:
    def __init__(self, r):
        self.status = r.status
        self.headers = r
        self.body = r.read().decode("utf-8", "replace")

    def cookie(self):
        return self.headers.getheader("Set-Cookie")

    def sid(self):
        c = self.cookie()
        return c.split(";")[0] if c else None


def https(srv, method, path, body=None, headers=None):
    conn = http.client.HTTPSConnection(HOST, srv[1].server_address[1], context=UNVERIFIED, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        return Resp(conn.getresponse())
    finally:
        conn.close()


def login(srv, username, password):
    return https(
        srv, "POST", "/login", urlencode({"username": username, "password": password}),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )


# ----------------------------------------------------------------- pages
def test_first_page_is_login(srv):
    r = https(srv, "GET", "/")
    assert r.status == 200
    assert "Sign in" in r.body and 'action="/login"' in r.body
    assert "{{" not in r.body  # template placeholders all filled


def test_login_aliases_serve_login_page(srv):
    for path in ("/login", "/index.html", "/?utm=1"):
        assert "Sign in" in https(srv, "GET", path).body


def test_unknown_page_is_404(srv):
    r = https(srv, "GET", "/nope")
    assert r.status == 404 and "doesn't exist" in r.body


def test_static_css_and_no_path_traversal(srv):
    css = https(srv, "GET", "/static/style.css")
    assert css.status == 200 and "text/css" in css.headers.getheader("Content-Type")
    for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/", "/pages/login.html", "/server.py"):
        assert https(srv, "GET", path).status == 404, path


def test_head_has_no_body(srv):
    r = https(srv, "HEAD", "/")
    assert r.status == 200 and r.body == ""


def test_landing_requires_session(srv):
    r = https(srv, "GET", "/landing")
    assert r.status == 303 and r.headers.getheader("Location") == "/"
    bogus = https(srv, "GET", "/landing", headers={"Cookie": "sid=not-a-real-session"})
    assert bogus.status == 303


# ----------------------------------------------------------------- login
def test_valid_login_sets_cookie_and_serves_landing(srv):
    r = login(srv, "alice", CREDS["alice"])
    assert r.status == 303 and r.headers.getheader("Location") == "/landing"
    cookie = r.cookie()
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Strict" in cookie

    landing = https(srv, "GET", "/landing", headers={"Cookie": r.sid()})
    assert landing.status == 200
    assert "Welcome, alice" in landing.body and "student" in landing.body

    # logged-in users visiting / go straight to landing
    assert https(srv, "GET", "/", headers={"Cookie": r.sid()}).status == 303


def test_bad_credentials_rejected_with_same_message(srv):
    wrong_pw = login(srv, "alice", "nope")
    unknown = login(srv, "ghost", "nope")
    assert wrong_pw.status == unknown.status == 401
    assert "Invalid username or password." in wrong_pw.body
    assert wrong_pw.body == unknown.body
    assert wrong_pw.cookie() is None


def test_empty_fields_rejected(srv):
    assert login(srv, "", "").status == 401
    assert login(srv, "alice", "").status == 401


def test_disabled_account_cannot_login(assets, tmp_path):
    import shutil
    import sqlite3

    db = tmp_path / "copy.sqlite3"
    shutil.copy(assets[0], db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET is_active = 0 WHERE username = 'bob'")
    conn.commit()
    conn.close()
    http_srv, https_srv, _ = start((db, assets[1], assets[2]))
    try:
        assert login((http_srv, https_srv), "bob", CREDS["bob"]).status == 401
    finally:
        stop(http_srv, https_srv)


def test_sql_injection_in_username(srv):
    r = login(srv, "' OR '1'='1' --", "x")
    assert r.status == 401 and r.cookie() is None


def test_username_is_html_escaped_on_landing(assets, tmp_path):
    import shutil
    import sqlite3

    db = tmp_path / "xss.sqlite3"
    shutil.copy(assets[0], db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'student')",
        ("<script>alert(1)</script>", server.hash_password("pw12345")),
    )
    conn.commit()
    conn.close()
    s = start((db, assets[1], assets[2]))
    try:
        r = login(s, "<script>alert(1)</script>", "pw12345")
        landing = https(s, "GET", "/landing", headers={"Cookie": r.sid()})
        assert "<script>alert(1)</script>" not in landing.body
        assert "&lt;script&gt;" in landing.body
    finally:
        stop(s[0], s[1])


def test_logout_ends_session(srv):
    sid = login(srv, "alice", CREDS["alice"]).sid()
    out = https(srv, "POST", "/logout", "", {"Cookie": sid, "Content-Type": "application/x-www-form-urlencoded"})
    assert out.status == 303 and "Max-Age=0" in out.cookie()
    assert https(srv, "GET", "/landing", headers={"Cookie": sid}).status == 303


def test_login_rate_limit(assets):
    http_srv, https_srv, _ = start(assets, max_failures=3)
    s = (http_srv, https_srv)
    try:
        assert [login(s, "alice", "bad").status for _ in range(3)] == [401, 401, 401]
        blocked = login(s, "alice", CREDS["alice"])  # even the right password is blocked now
        assert blocked.status == 429 and blocked.headers.getheader("Retry-After")
    finally:
        stop(*s)


def test_successful_login_resets_failure_count(assets):
    http_srv, https_srv, _ = start(assets, max_failures=3)
    s = (http_srv, https_srv)
    try:
        login(s, "alice", "bad")
        login(s, "alice", "bad")
        assert login(s, "alice", CREDS["alice"]).status == 303
        assert [login(s, "alice", "bad").status for _ in range(2)] == [401, 401]
    finally:
        stop(*s)


# ----------------------------------------------------------------- hardening / errors
def test_request_validation(srv):
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert https(srv, "POST", "/login", "x" * 5000, form).status == 413
    assert https(srv, "POST", "/login", "{}", {"Content-Type": "application/json"}).status == 415
    assert https(srv, "POST", "/login", "a=b", {"Content-Type": "application/x-www-form-urlencoded",
                                                "Content-Length": "abc"}).status == 411
    assert https(srv, "POST", "/login", b"\xff\xfe=1", form).status == 400
    assert https(srv, "POST", "/elsewhere", "a=b", form).status == 404


def test_security_headers(srv):
    h = https(srv, "GET", "/").headers
    assert h.getheader("X-Content-Type-Options") == "nosniff"
    assert h.getheader("X-Frame-Options") == "DENY"
    assert h.getheader("Strict-Transport-Security")
    assert "Python" not in (h.getheader("Server") or "")


def test_unexpected_error_returns_generic_500(srv, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(server, "authenticate", boom)
    r = login(srv, "alice", CREDS["alice"])
    assert r.status == 500
    assert "secret internal detail" not in r.body
    assert https(srv, "GET", "/").status == 200  # server keeps serving


def test_passwords_not_logged(srv, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        login(srv, "alice", "SuperSecretPw!")
    assert "SuperSecretPw!" not in caplog.text
    assert "Failed login for username='alice'" in caplog.text


def test_plain_http_on_tls_port_does_not_crash_server(srv):
    conn = http.client.HTTPConnection(HOST, srv[1].server_address[1], timeout=5)
    try:
        conn.request("GET", "/")
        conn.getresponse()
    except Exception:
        pass  # a TLS port rejecting plain HTTP is the expected outcome
    finally:
        conn.close()
    assert https(srv, "GET", "/").status == 200


# ----------------------------------------------------------------- port 80
def test_http_redirects_to_https(srv):
    conn = http.client.HTTPConnection(HOST, srv[0].server_address[1], timeout=5)
    conn.request("GET", "/landing?x=1", headers={"Host": "example.test:80"})
    r = conn.getresponse()
    assert r.status == 301
    assert r.getheader("Location") == f"https://example.test:{srv[1].server_address[1]}/landing?x=1"
    conn.request("POST", "/login", body="a=b", headers={"Host": "example.test"})
    assert conn.getresponse().status == 308  # method-preserving
    conn.close()


def test_redirect_ignores_malicious_host_header(srv):
    conn = http.client.HTTPConnection(HOST, srv[0].server_address[1], timeout=5)
    conn.request("GET", "/", headers={"Host": "evil.com/@attacker.net"})
    loc = conn.getresponse().getheader("Location")
    assert loc.startswith("https://localhost:")
    conn.close()


def test_http_serve_mode_serves_pages_without_secure_cookie(assets):
    http_srv, https_srv, _ = start(assets, http_mode="serve")
    try:
        port = http_srv.server_address[1]
        conn = http.client.HTTPConnection(HOST, port, timeout=5)
        conn.request("GET", "/")
        r = conn.getresponse()
        assert r.status == 200 and "Sign in" in r.read().decode()
        body = urlencode({"username": "alice", "password": CREDS["alice"]})
        conn.request("POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        assert r.status == 303 and "Secure" not in r.getheader("Set-Cookie")
        conn.close()
    finally:
        stop(http_srv, https_srv)


# ----------------------------------------------------------------- units
def test_password_hash_roundtrip_and_rejects_garbage():
    h = server.hash_password("pw")
    assert server.verify_password(h, "pw") and not server.verify_password(h, "other")
    for bad in (None, "", "plaintext", "scrypt:x:y:z$a$b", "pbkdf2:sha256:1$a$b", "scrypt:99999999:8:1$a$b"):
        assert not server.verify_password(bad, "pw")


def test_demo_accounts_get_distinct_random_passwords(tmp_path):
    created = server.init_demo_db(tmp_path / "demo.sqlite3")
    assert set(created) == {name for name, _ in server.DEMO_ACCOUNTS}
    assert len(set(created.values())) == len(created) and all(len(p) >= 16 for p in created.values())
    assert server.init_demo_db(tmp_path / "demo.sqlite3") == {}  # existing accounts are left untouched


def test_session_expiry():
    store = server.SessionStore(ttl=-1)
    token = store.create({"username": "x"})
    assert store.get(token) is None


def test_missing_db_gives_helpful_exit(tmp_path):
    with pytest.raises(SystemExit, match="--init-demo"):
        server.create_servers(server.Config(db_path=tmp_path / "missing.sqlite3"))
