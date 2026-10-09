"""Shared test helpers. No credential is hardcoded: passwords are generated at runtime."""
from __future__ import annotations

import secrets

from degreeplan.devtools.sample_data import SAMPLE_USERNAMES

# One random password per sample account, regenerated for every test session.
PASSWORDS: dict[str, str] = {u: secrets.token_urlsafe(16) for u in SAMPLE_USERNAMES}

BASE_URL = "https://localhost"  # tests run production-like: Secure cookies, HSTS, __Host- prefix
COOKIE = "__Host-sid"


class ApiClient:
    """Flask test client wrapper: HTTPS base URL, and CSRF header on writes once logged in."""

    def __init__(self, app, csrf: str | None = None):
        self.app = app
        self.client = app.test_client()
        self.csrf = csrf

    def _kw(self, kw: dict, write: bool) -> dict:
        headers = dict(kw.pop("headers", None) or {})
        if write and self.csrf and "X-CSRF-Token" not in headers:
            headers["X-CSRF-Token"] = self.csrf
        kw["headers"] = headers
        kw.setdefault("base_url", BASE_URL)
        return kw

    def get(self, path, **kw):
        return self.client.get(path, **self._kw(kw, False))

    def post(self, path, **kw):
        return self.client.post(path, **self._kw(kw, True))

    def put(self, path, **kw):
        return self.client.put(path, **self._kw(kw, True))

    def patch(self, path, **kw):
        return self.client.patch(path, **self._kw(kw, True))

    def delete(self, path, **kw):
        return self.client.delete(path, **self._kw(kw, True))

    def login(self, username: str, password: str):
        resp = self.post("/api/v1/auth/login", json={"username": username, "password": password})
        if resp.status_code == 200:
            self.csrf = resp.get_json()["csrf_token"]
        return resp

    @property
    def session_cookie(self) -> str | None:
        cookie = self.client.get_cookie(COOKIE, domain="localhost")
        return cookie.value if cookie else None

    def set_session_cookie(self, value: str) -> None:
        self.client.set_cookie(COOKIE, value, domain="localhost", secure=True, httponly=True)


def logged_in(app, username: str) -> ApiClient:
    api = ApiClient(app)
    resp = api.login(username, PASSWORDS[username])
    assert resp.status_code == 200, resp.get_json()
    return api
