from tests.helpers import ApiClient


def test_security_headers_on_every_response(api, alice):
    for response in (api.get("/api/v1/health/live"), api.get("/nope"), alice.get("/api/v1/courses")):
        h = response.headers
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["X-Frame-Options"] == "DENY"
        assert h["Cache-Control"] == "no-store"
        assert "default-src 'none'" in h["Content-Security-Policy"]
        assert h["Referrer-Policy"] == "no-referrer"
        assert h["Cross-Origin-Opener-Policy"] == "same-origin"
        assert "max-age=" in h["Strict-Transport-Security"]
        assert "geolocation=()" in h["Permissions-Policy"]


def test_sql_injection_in_search_is_harmless(alice):
    for payload in ("' OR 1=1; DROP TABLE courses;--", "\" OR \"\"=\"", "%' UNION SELECT password_hash FROM users--"):
        resp = alice.get("/api/v1/courses", query_string={"q": payload})
        assert resp.status_code == 200 and resp.get_json()["total"] == 0
    assert alice.get("/api/v1/courses").get_json()["total"] == 6  # table intact


def test_sql_injection_in_login_is_harmless(api):
    resp = api.login("' OR '1'='1' --", "x")
    assert resp.status_code == 401 and "Set-Cookie" not in resp.headers


def test_unknown_methods_and_paths_return_json_errors(api):
    for method, path in (("delete", "/api/v1/health/live"), ("put", "/api/v1/courses"), ("get", "/admin")):
        resp = getattr(api, method)(path)
        assert resp.status_code in (401, 403, 404, 405)
        assert resp.is_json and "error" in resp.get_json()


def test_server_does_not_expose_flask_debugger_or_stack_traces(make_app):
    app = make_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("internal detail that must not leak")

    resp = ApiClient(app).get("/boom")
    assert resp.status_code == 500
    assert "internal detail" not in resp.get_data(as_text=True)
    assert "Traceback" not in resp.get_data(as_text=True)
    assert app.debug is False
