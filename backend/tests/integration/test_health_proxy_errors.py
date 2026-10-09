import logging

import pytest
from flask import request
from sqlalchemy.exc import OperationalError

from tests.helpers import PASSWORDS, ApiClient


# ----------------------------------------------------------------------------- health
def test_liveness_needs_no_database(api):
    resp = api.get("/api/v1/health/live")
    assert resp.status_code == 200 and resp.get_json() == {"status": "ok"}


def test_ready_when_both_databases_are_migrated(api):
    resp = api.get("/api/v1/health/ready")
    assert resp.status_code == 200 and resp.get_json() == {"status": "ready"}


def test_not_ready_when_databases_are_unmigrated(make_app, caplog):
    app = make_app(seeded=False)  # empty database files: no tables, no alembic_version
    with caplog.at_level(logging.ERROR):
        resp = ApiClient(app).get("/api/v1/health/ready")
    assert resp.status_code == 503 and resp.get_json() == {"status": "not_ready"}  # no details leak
    assert "migration revision" in caplog.text  # ...but the operator log explains why


# ----------------------------------------------------------------------------- reverse proxy
def whoami(app):
    @app.get("/whoami")
    def _whoami():
        return {"ip": request.remote_addr, "scheme": request.scheme, "host": request.host}

    return app


FORWARDED = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.example"}


def test_forwarded_headers_are_ignored_without_trusted_proxies(make_app):
    client = ApiClient(whoami(make_app()))
    data = client.get("/whoami", headers=FORWARDED).get_json()
    assert data["ip"] == "127.0.0.1" and data["host"] == "localhost"


def test_forwarded_headers_are_honoured_with_one_trusted_proxy(make_app):
    client = ApiClient(whoami(make_app(proxy_hops=1)))
    data = client.get("/whoami", headers=FORWARDED).get_json()
    assert data == {"ip": "203.0.113.7", "scheme": "https", "host": "app.example"}


def test_only_the_last_proxy_hop_is_trusted(make_app):
    """A client-supplied X-Forwarded-For entry must not be believed when one proxy is trusted."""
    client = ApiClient(whoami(make_app(proxy_hops=1)))
    spoofed = {**FORWARDED, "X-Forwarded-For": "6.6.6.6, 203.0.113.7"}
    assert client.get("/whoami", headers=spoofed).get_json()["ip"] == "203.0.113.7"


def test_origin_check_uses_the_forwarded_host_behind_a_proxy(make_app):
    app = make_app(proxy_hops=1)
    client = ApiClient(app)
    body = {"username": "alice", "password": PASSWORDS["alice"]}
    ok = client.post("/api/v1/auth/login", json=body, headers={**FORWARDED, "Origin": "https://app.example"})
    assert ok.status_code == 200
    bad = client.post("/api/v1/auth/login", json=body, headers={**FORWARDED, "Origin": "https://evil.example"})
    assert bad.status_code == 403


# ----------------------------------------------------------------------------- errors
def test_unknown_route_returns_json_404_with_request_id(api):
    resp = api.get("/api/v1/nope")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["error"]["code"] == "not_found" and body["error"]["request_id"]
    assert resp.headers["X-Request-ID"] == body["error"]["request_id"]


def test_method_not_allowed_is_json_and_keeps_the_allow_header(api):
    resp = api.get("/api/v1/auth/logout")
    assert resp.status_code == 405 and resp.is_json
    assert "POST" in resp.headers["Allow"]


def test_unexpected_exception_is_generic_for_clients_and_detailed_in_logs(make_app, caplog):
    app = make_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    with caplog.at_level(logging.ERROR):
        resp = ApiClient(app).get("/boom")
    assert resp.status_code == 500
    assert resp.get_json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in resp.get_data(as_text=True)
    assert any("Unhandled exception" in r.message and r.exc_info for r in caplog.records)


def test_database_outage_returns_503_without_details(make_app, monkeypatch, caplog):
    app = make_app()

    def broken(*_a, **_k):
        raise OperationalError("SELECT 1", {}, Exception("unable to open database file C:\\secret\\path.db"))

    monkeypatch.setattr("degreeplan.api.catalog.catalog.list_programs", broken)
    from tests.helpers import logged_in

    client = logged_in(app, "alice")
    with caplog.at_level(logging.ERROR):
        resp = client.get("/api/v1/programs")
    assert resp.status_code == 503
    assert "secret" not in resp.get_data(as_text=True)


def test_oversized_bodies_are_rejected(make_app):
    from tests.helpers import logged_in

    client = logged_in(make_app(max_content_length=2048), "alice")
    resp = client.post("/api/v1/plans", json={"name": "x" * 5000, "program_id": 1})
    assert resp.status_code == 413 and resp.get_json()["error"]["request_id"]


@pytest.mark.parametrize("incoming", ["abc-123.XYZ", "a" * 64])
def test_sane_request_ids_are_propagated(api, incoming):
    assert api.get("/api/v1/health/live", headers={"X-Request-ID": incoming}).headers["X-Request-ID"] == incoming


@pytest.mark.parametrize("incoming", ["x" * 200, "bad id with spaces", "../../etc/passwd", "<script>"])
def test_unsafe_request_ids_are_replaced(api, incoming):
    echoed = api.get("/api/v1/health/live", headers={"X-Request-ID": incoming}).headers["X-Request-ID"]
    assert echoed != incoming and len(echoed) == 12


def test_request_log_lines_have_no_query_string_or_secrets(api, caplog):
    with caplog.at_level(logging.INFO, logger="degreeplan.request"):
        api.get("/api/v1/health/live?token=SuperSecretQueryValue")
    assert "SuperSecretQueryValue" not in caplog.text
    assert "GET /api/v1/health/live -> 200" in caplog.text


def test_passwords_never_reach_the_logs(api, caplog):
    with caplog.at_level(logging.DEBUG):
        api.login("alice", "SuperSecretPw-Guess-77")
    assert "SuperSecretPw-Guess-77" not in caplog.text
