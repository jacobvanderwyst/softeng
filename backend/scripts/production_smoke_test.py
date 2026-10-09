"""Production-path smoke test: runs the real CLI + waitress server with production settings.

Uses a throw-away temp directory (secret file, databases, logs), the same environment variables and waitress
arguments as the Windows service, and simulates the headers a TLS-terminating proxy sends.

    python scripts/production_smoke_test.py
"""
import http.client
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

tmp = pathlib.Path(tempfile.mkdtemp(prefix="dp-smoke-"))
(tmp / "secrets").mkdir()
(tmp / "data").mkdir()
(tmp / "secrets" / "secret_key").write_text(secrets.token_urlsafe(48))
PORT = 8765
env = dict(
    os.environ,
    APP_ENV="production",
    SECRET_KEY_FILE=str(tmp / "secrets" / "secret_key"),
    USERS_DATABASE_URL=f"sqlite:///{(tmp / 'data' / 'users.sqlite3').as_posix()}",
    PLANS_DATABASE_URL=f"sqlite:///{(tmp / 'data' / 'plans.sqlite3').as_posix()}",
    LOG_DIR=str(tmp / "logs"),
    LOG_FORMAT="json",
    PROXY_HOPS="1",
)
# Keep in sync with the arguments in deploy/windows/degreeplan-api.xml.template
WAITRESS_ARGS = sys.argv[1:] or [
    f"--listen=localhost:{PORT}", "--threads=8", "--channel-timeout=30", "--max-request-body-size=1048576",
    "--no-clear-untrusted-proxy-headers", "degreeplan.wsgi:app",
]
results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))


def flask(*args, stdin=None):
    return subprocess.run(  # noqa: S603 - fixed argument list, no untrusted input
        [sys.executable, "-m", "flask", "--app", "degreeplan.wsgi", *args],
        env=env, input=stdin, capture_output=True, text=True, timeout=120,
    )


admin_pw, student_pw = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
server = None
try:
    r = flask("db", "upgrade")
    check("db upgrade (production env, secret file, absolute sqlite paths)", r.returncode == 0, r.stderr[-300:])
    check("db status reports both databases ok", flask("db", "status").returncode == 0)
    r = flask("users", "create", "smoke_admin", "--role", "admin", "--password-stdin", stdin=admin_pw + "\n")
    check("users create admin via stdin", r.returncode == 0, r.stderr[-300:])
    r = flask("users", "create", "smoke_stu", "--role", "student", "--first-name", "S", "--last-name", "T",
              "--password-stdin", stdin=student_pw + "\n")
    check("users create student", r.returncode == 0, r.stderr[-300:])
    r = flask("dev", "seed")
    check("dev commands are absent in production", r.returncode == 2 and "No such command" in r.stderr)

    server = subprocess.Popen(  # noqa: S603 - fixed argument list, no untrusted input
        [sys.executable, "-m", "waitress", *WAITRESS_ARGS], env=env,
        stdout=open(tmp / "server.out", "w"), stderr=open(tmp / "server.err", "w"),
    )

    def call(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        h = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "plans.example.edu"}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body)
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read().decode()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw
        return resp, payload

    resp, body = None, None
    for _ in range(40):
        try:
            resp, body = call("GET", "/api/v1/health/ready")
            if resp.status == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    check("server ready via waitress", resp is not None and resp.status == 200 and body == {"status": "ready"})

    origin = {"Origin": "https://plans.example.edu"}
    resp, body = call("POST", "/api/v1/auth/login", {"username": "smoke_admin", "password": admin_pw}, origin)
    check("login through simulated proxy headers", resp.status == 200, str(body))
    if resp.status != 200:
        raise SystemExit(1)
    cookie_header = resp.getheader("Set-Cookie") or ""
    flags = ("Secure", "HttpOnly", "SameSite=Lax")
    check("cookie is __Host-sid, Secure, HttpOnly, SameSite=Lax, no Domain",
          cookie_header.startswith("__Host-sid=") and all(a in cookie_header for a in flags)
          and "Domain" not in cookie_header, cookie_header[:60])
    check("HSTS and security headers present", bool(resp.getheader("Strict-Transport-Security"))
          and resp.getheader("X-Content-Type-Options") == "nosniff")
    cookie = {"Cookie": cookie_header.split(";")[0]}
    csrf = body["csrf_token"]

    resp, body = call("GET", "/api/v1/auth/me", headers=cookie)
    check("session works (me)", resp.status == 200 and body["user"]["username"] == "smoke_admin")
    resp, body = call("POST", "/api/v1/auth/change-password",
                      {"current_password": admin_pw, "new_password": "x"}, {**cookie, **origin})
    check("write without CSRF token is rejected", resp.status == 403, str(body))
    resp, body = call("POST", "/api/v1/auth/change-password", {"current_password": admin_pw, "new_password": "short"},
                      {**cookie, **origin, "X-CSRF-Token": csrf})
    check("password policy enforced over HTTP (422)", resp.status == 422, str(body))
    resp, body = call("POST", "/api/v1/auth/login", {"username": "smoke_admin", "password": "wrong"},
                      {"Origin": "https://evil.example"})
    check("foreign Origin rejected", resp.status == 403)
    resp, _ = call("POST", "/api/v1/auth/logout", None, {**cookie, **origin, "X-CSRF-Token": csrf})
    check("logout", resp.status == 200)
    resp, _ = call("GET", "/api/v1/auth/me", headers=cookie)
    check("old cookie rejected after logout", resp.status == 401)

    time.sleep(0.5)
    logs = tmp / "logs"
    app_log = (logs / "app.log").read_text() if (logs / "app.log").exists() else ""
    audit_log = (logs / "audit.log").read_text() if (logs / "audit.log").exists() else ""
    lines = [line for line in app_log.splitlines() if line]
    check("app.log is JSON with request ids", bool(lines) and all(json.loads(line)["request_id"] for line in lines))
    check("real client IP (from proxy header) is logged", "ip=203.0.113.9" in app_log)
    check("audit.log has login success + logout",
          '"action": "auth.login"' in audit_log and '"action": "auth.logout"' in audit_log)
    everything = app_log + audit_log + (tmp / "server.out").read_text() + (tmp / "server.err").read_text()
    check("no password or session token in any log", admin_pw not in everything and student_pw not in everything
          and cookie["Cookie"].split("=", 1)[1] not in everything)
    check("secret key not in any log", (tmp / "secrets" / "secret_key").read_text() not in everything)
finally:
    if server:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
