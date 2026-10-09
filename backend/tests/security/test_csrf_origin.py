from tests.helpers import PASSWORDS, ApiClient, logged_in

PLAN = {"name": "csrf-test", "program_id": 1}


def test_state_changing_requests_need_the_csrf_token(app):
    client = logged_in(app, "alice")
    token = client.csrf
    client.csrf = None  # stop the wrapper from sending the header automatically

    missing = client.post("/api/v1/plans", json=PLAN)
    assert missing.status_code == 403 and "CSRF" in missing.get_json()["error"]["message"]
    wrong = client.post("/api/v1/plans", json=PLAN, headers={"X-CSRF-Token": "wrong-token-value"})
    assert wrong.status_code == 403
    ok = client.post("/api/v1/plans", json=PLAN, headers={"X-CSRF-Token": token})
    assert ok.status_code == 201


def test_csrf_token_of_another_session_is_rejected(app):
    victim, attacker = logged_in(app, "alice"), logged_in(app, "bob")
    victim_client = ApiClient(app)
    victim_client.set_session_cookie(victim.session_cookie)
    resp = victim_client.post("/api/v1/plans", json=PLAN, headers={"X-CSRF-Token": attacker.csrf})
    assert resp.status_code == 403


def test_safe_methods_do_not_require_a_token(app):
    client = logged_in(app, "alice")
    client.csrf = None
    assert client.get("/api/v1/plans").status_code == 200


def test_unauthenticated_writes_get_401_not_403(api):
    assert api.post("/api/v1/plans", json=PLAN).status_code == 401


def test_foreign_origin_is_rejected_even_with_a_valid_token(alice):
    resp = alice.post("/api/v1/plans", json=PLAN, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert "Origin" in resp.get_json()["error"]["message"]


def test_same_origin_requests_are_allowed(alice):
    assert alice.post("/api/v1/plans", json=PLAN, headers={"Origin": "https://localhost"}).status_code == 201


def test_login_also_checks_the_origin(api):
    body = {"username": "alice", "password": PASSWORDS["alice"]}
    assert api.post("/api/v1/auth/login", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert api.post("/api/v1/auth/login", json=body).status_code == 200  # non-browser client: no Origin header


def test_configured_cors_origin_is_allowed_and_others_are_not(make_app):
    app = make_app(cors_origins=("https://app.example",))
    client = ApiClient(app)
    body = {"username": "alice", "password": PASSWORDS["alice"]}
    ok = client.post("/api/v1/auth/login", json=body, headers={"Origin": "https://app.example"})
    assert ok.status_code == 200
    assert ok.headers["Access-Control-Allow-Origin"] == "https://app.example"
    assert ok.headers["Access-Control-Allow-Credentials"] == "true"
    assert client.post("/api/v1/plans", json=PLAN, headers={"Origin": "https://evil.example"}).status_code == 403

    preflight = client.client.options(
        "/api/v1/plans",
        base_url="https://localhost",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "Access-Control-Allow-Origin" not in preflight.headers


def test_no_cors_headers_when_no_origins_are_configured(api):
    resp = api.get("/api/v1/health/live", headers={"Origin": "https://app.example"})
    assert "Access-Control-Allow-Origin" not in resp.headers
