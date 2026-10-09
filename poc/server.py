#!/usr/bin/env python3
"""Standalone proof-of-concept web server for the degree plan tool.

PROOF OF CONCEPT ONLY: NOT FOR DEPLOYMENT. The production system is `backend/` (see docs/). This demo
has in-memory sessions, a single process and a self-signed certificate, and uses its own tiny users table.

* Listens on HTTP (default 80) and HTTPS (default 443).
* Serves only the pages in a fixed route table (no filesystem path lookups).
* GET /           -> login page (always the first page a visitor sees)
* POST /login     -> checks username/password against the SQLite `users` table;
                     valid -> session cookie + landing page, invalid -> login page again
* GET /landing    -> landing page (requires a valid session, otherwise back to login)
* POST /logout    -> ends the session

Python standard library only (the optional TLS cert generator needs `cryptography`).

    python server.py --init-demo                 # demo DB (random passwords shown once) + self-signed cert
    python server.py --http-port 8080 --https-port 8443
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import http.cookies
import logging
import os
import re
import secrets
import sqlite3
import ssl
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import gen_cert

BASE = Path(__file__).resolve().parent
PAGES_DIR = BASE / "pages"
STATIC_DIR = BASE / "static"
DEFAULT_DB = BASE / "instance" / "poc.sqlite3"

COOKIE_NAME = "sid"
MAX_BODY = 4096  # bytes; a login form is tiny
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

# Only these URLs are ever served from the static dir (no user-controlled file paths).
STATIC_ROUTES = {"/static/style.css": ("style.css", "text/css; charset=utf-8")}
LOGIN_PATHS = {"/", "/login", "/index.html"}

log = logging.getLogger("poc")


# --------------------------------------------------------------------------- passwords
# Stored format: "scrypt:<n>:<r>:<p>$<salt>$<hex digest>"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
_MAX_SCRYPT_N = 2**17  # refuse absurd cost params from a tampered DB row


def hash_password(password: str) -> str:
    salt = secrets.token_hex(8)
    digest = hashlib.scrypt(
        password.encode(), salt=salt.encode(), n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        maxmem=132 * SCRYPT_N * SCRYPT_R * SCRYPT_P,
    ).hex()
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}${salt}${digest}"


def verify_password(stored: str | None, password: str) -> bool:
    if not stored:
        return False
    try:
        method, salt, expected = stored.split("$", 2)
        algo, n, r, p = method.split(":")
        n, r, p = int(n), int(r), int(p)
        if algo != "scrypt" or not (2 <= n <= _MAX_SCRYPT_N) or r < 1 or p < 1:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=salt.encode(), n=n, r=r, p=p, maxmem=132 * n * r * p
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")  # same work for unknown users


# --------------------------------------------------------------------------- database
# Usernames and roles only: passwords are generated randomly when the demo database is created.
DEMO_ACCOUNTS = (
    ("admin", "admin"),
    ("teacher1", "teacher"),
    ("alice", "student"),
    ("bob", "student"),
)


def init_demo_db(path: Path) -> dict[str, str]:
    """Create a minimal users table with demo accounts.

    Returns {username: password} for the accounts created now (empty if they already existed). The
    passwords are random and are not stored anywhere in clear text: show them once and discard.
    """
    created: dict[str, str] = {}
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   username TEXT NOT NULL UNIQUE,
                   password_hash TEXT,
                   role TEXT NOT NULL DEFAULT 'student',
                   is_active INTEGER NOT NULL DEFAULT 1)"""
        )
        for username, role in DEMO_ACCOUNTS:
            exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if not exists:
                password = secrets.token_urlsafe(14)
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (username, hash_password(password), role),
                )
                created[username] = password
        conn.commit()
        log.info("Demo database ready at %s", path)
    finally:
        conn.close()
    return created


def authenticate(db_path: Path, username: str, password: str) -> dict | None:
    """Return the user row if the credentials are valid and the account is active."""
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    # Always run a full hash comparison so timing doesn't reveal whether the user exists.
    ok = verify_password(row["password_hash"] if row else _DUMMY_HASH, password)
    if row and ok and row["is_active"]:
        return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return None


# --------------------------------------------------------------------------- sessions / rate limit
class SessionStore:
    """In-memory sessions (a POC: they vanish on restart)."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._data: dict[str, tuple[dict, float]] = {}
        self._lock = threading.Lock()

    def create(self, user: dict) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._data[token] = (user, time.time() + self.ttl)
        return token

    def get(self, token: str) -> dict | None:
        with self._lock:
            entry = self._data.get(token)
            if not entry:
                return None
            if entry[1] < time.time():
                del self._data[token]
                return None
            return entry[0]

    def delete(self, token: str) -> None:
        with self._lock:
            self._data.pop(token, None)

    def _purge(self) -> None:
        now = time.time()
        for token in [t for t, (_, exp) in self._data.items() if exp < now]:
            del self._data[token]


class LoginRateLimiter:
    """Blocks an IP after `max_failures` failed logins within `window` seconds."""

    def __init__(self, max_failures: int, window: int):
        self.max_failures, self.window = max_failures, window
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def retry_after(self, ip: str) -> int:
        with self._lock:
            q = self._prune(ip)
            if len(q) >= self.max_failures:
                return max(1, int(q[0] + self.window - time.time()) + 1)
            return 0

    def record_failure(self, ip: str) -> None:
        with self._lock:
            self._prune(ip).append(time.time())

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def _prune(self, ip: str) -> deque:
        q, cutoff = self._fails[ip], time.time() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q


# --------------------------------------------------------------------------- app + config
@dataclass
class Config:
    bind: str = "localhost"
    http_port: int = 80
    https_port: int = 443
    db_path: Path = DEFAULT_DB
    cert: Path = gen_cert.DEFAULT_CERT
    key: Path = gen_cert.DEFAULT_KEY
    http_mode: str = "redirect"  # "redirect" -> 301 to HTTPS, "serve" -> same pages over plain HTTP
    session_ttl: int = 8 * 3600
    max_failures: int = 5
    fail_window: int = 60


@dataclass
class App:
    config: Config
    sessions: SessionStore = field(init=False)
    limiter: LoginRateLimiter = field(init=False)
    pages: dict[str, str] = field(init=False)
    static: dict[str, tuple[bytes, str]] = field(init=False)

    def __post_init__(self) -> None:
        self.sessions = SessionStore(self.config.session_ttl)
        self.limiter = LoginRateLimiter(self.config.max_failures, self.config.fail_window)
        # Load everything once at startup: requests never touch arbitrary paths.
        self.pages = {p.stem: p.read_text(encoding="utf-8") for p in PAGES_DIR.glob("*.html")}
        for needed in ("login", "landing", "404"):
            if needed not in self.pages:
                raise RuntimeError(f"Missing page: pages/{needed}.html")
        self.static = {
            url: ((STATIC_DIR / fname).read_bytes(), ctype) for url, (fname, ctype) in STATIC_ROUTES.items()
        }


class HttpError(Exception):
    def __init__(self, status: int):
        self.status = status


# --------------------------------------------------------------------------- HTTP handlers
class _BaseHandler(BaseHTTPRequestHandler):
    server_version = "POC"
    sys_version = ""  # don't advertise the Python version
    timeout = 10  # seconds; also bounds slow clients / TLS handshakes
    scheme = "http"

    @property
    def ip(self) -> str:
        return self.client_address[0]

    def setup(self) -> None:
        if isinstance(self.request, ssl.SSLSocket):
            self.request.settimeout(self.timeout)
            self.request.do_handshake()  # in the worker thread, so a slow client can't block accept()
        super().setup()

    def log_request(self, code="-", size="-") -> None:
        level = logging.WARNING if str(code).isdigit() and int(code) >= 400 else logging.INFO
        log.log(level, "[%s] %s %s %s -> %s", self.scheme, self.ip, self.command, urlsplit(self.path).path, code)

    def log_message(self, format, *args) -> None:  # protocol-level problems (bad request lines etc.)
        log.warning("[%s] %s: %s", self.scheme, self.ip, format % args)


class PageHandler(_BaseHandler):
    """Serves the login/landing pages (used on HTTPS, and on HTTP when --http-mode serve)."""

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    # ----- plumbing
    def do_GET(self):
        self._dispatch(self._get)

    do_HEAD = do_GET

    def do_POST(self):
        self._dispatch(self._post)

    def _dispatch(self, fn) -> None:
        try:
            fn()
        except HttpError as exc:
            self._plain(exc.status)
        except (ConnectionError, TimeoutError):
            log.debug("Client %s went away mid-request", self.ip)
        except Exception:
            log.exception("Unhandled error on %s %s", self.command, urlsplit(self.path).path)
            try:
                self._plain(500)
            except Exception:
                pass

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8", extra=()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        self.send_header("Cache-Control", "no-store")
        if self.scheme == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _plain(self, status: int) -> None:
        phrase = HTTPStatus(status).phrase
        self._send(status, f"{status} {phrase}\n".encode(), "text/plain; charset=utf-8")

    def _redirect(self, location: str, extra=()) -> None:
        self._send(303, b"", extra=(("Location", location), *extra))

    def _render(self, name: str, status: int = 200, extra=(), **ctx: str) -> None:
        page = self.app.pages[name]
        for key, value in ctx.items():
            page = page.replace("{{" + key + "}}", value)
        self._send(status, page.encode("utf-8"), extra=extra)

    def _not_found(self) -> None:
        self._render("404", 404)

    # ----- sessions
    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            morsel = http.cookies.SimpleCookie(raw).get(COOKIE_NAME)
        except http.cookies.CookieError:
            return None
        return morsel.value if morsel else None

    def _current_user(self) -> dict | None:
        token = self._session_token()
        return self.app.sessions.get(token) if token else None

    def _cookie(self, value: str, max_age: int) -> tuple[str, str]:
        parts = [f"{COOKIE_NAME}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Strict"]
        if self.scheme == "https":
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    # ----- GET
    def _get(self) -> None:
        path = urlsplit(self.path).path
        if path in LOGIN_PATHS:
            if self._current_user():
                return self._redirect("/landing")
            return self._render("login", error="")
        if path == "/landing":
            user = self._current_user()
            if not user:
                return self._redirect("/")
            return self._render(
                "landing", username=html.escape(user["username"]), role=html.escape(user["role"])
            )
        if path in self.app.static:
            body, ctype = self.app.static[path]
            return self._send(200, body, ctype, extra=(("Cache-Control", "public, max-age=3600"),))
        if path == "/favicon.ico":
            return self._send(204, b"")
        self._not_found()

    # ----- POST
    def _post(self) -> None:
        path = urlsplit(self.path).path
        if path == "/login":
            return self._login()
        if path == "/logout":
            return self._logout()
        self._not_found()

    def _read_form(self) -> dict[str, list[str]]:
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/x-www-form-urlencoded":
            raise HttpError(415)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise HttpError(411) from None
        if length < 0 or length > MAX_BODY:
            raise HttpError(413)
        try:
            return parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True, max_num_fields=10)
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400) from None

    def _login_failed(self, status: int, message: str, extra=()) -> None:
        error = f'<p class="error" role="alert">{html.escape(message)}</p>'
        self._render("login", status, extra=extra, error=error)

    def _login(self) -> None:
        wait = self.app.limiter.retry_after(self.ip)
        if wait:
            log.warning("Login rate limit hit for %s", self.ip)
            return self._login_failed(429, "Too many failed attempts. Try again later.", (("Retry-After", str(wait)),))

        form = self._read_form()
        username = form.get("username", [""])[0][:64].strip()
        password = form.get("password", [""])[0][:256]

        user = authenticate(self.app.config.db_path, username, password) if username and password else None
        if not user:
            self.app.limiter.record_failure(self.ip)
            log.warning("Failed login for username=%r from %s", username, self.ip)  # never log the password
            return self._login_failed(401, "Invalid username or password.")

        self.app.limiter.reset(self.ip)
        token = self.app.sessions.create(user)
        log.info("User %r logged in (role=%s) from %s", user["username"], user["role"], self.ip)
        self._redirect("/landing", extra=(self._cookie(token, self.app.config.session_ttl),))

    def _logout(self) -> None:
        token = self._session_token()
        if token:
            self.app.sessions.delete(token)
        self._redirect("/", extra=(self._cookie("", 0),))


class HttpsPageHandler(PageHandler):
    scheme = "https"  # enables the Secure cookie flag and HSTS header


class RedirectHandler(_BaseHandler):
    """Port 80: send everything to HTTPS so credentials never travel in clear text."""

    def _redirect_to_https(self) -> None:
        host = (self.headers.get("Host") or "localhost").rsplit(":", 1)[0]
        if not HOST_RE.match(host):  # don't echo arbitrary Host header values into Location
            host = "localhost"
        port = self.server.https_port  # type: ignore[attr-defined]
        netloc = host if port == 443 else f"{host}:{port}"
        path = self.path if self.path.startswith("/") else "/"
        status = 301 if self.command in ("GET", "HEAD") else 308
        self.send_response(status)
        self.send_header("Location", f"https://{netloc}{path}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _redirect_to_https


# --------------------------------------------------------------------------- servers
class POCServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process steal the port; keep it off there.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, handler, app: App, ssl_ctx: ssl.SSLContext | None = None):
        self.app = app
        self.ssl_ctx = ssl_ctx
        self.https_port = 0
        super().__init__(address, handler)

    def get_request(self):
        sock, addr = super().get_request()
        if self.ssl_ctx:
            # Handshake happens later in the handler thread (see _BaseHandler.setup).
            sock = self.ssl_ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, addr

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
            log.debug("Connection error from %s: %s", client_address[0], exc)  # scanners, plain HTTP on 443, ...
        else:
            log.exception("Unhandled server error from %s", client_address[0])


def create_servers(cfg: Config) -> tuple[POCServer, POCServer, App]:
    """Bind both listeners (not yet serving). Returns (http_server, https_server, app)."""
    if not cfg.db_path.exists():
        raise SystemExit(f"Database not found: {cfg.db_path}\nRun with --init-demo to create a demo database.")
    gen_cert.ensure_cert(cfg.cert, cfg.key)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg.cert, cfg.key)

    app = App(cfg)
    try:
        https = POCServer((cfg.bind, cfg.https_port), HttpsPageHandler, app, ctx)
    except OSError as exc:
        raise SystemExit(f"Cannot bind HTTPS port {cfg.https_port}: {exc}\nTry --https-port 8443.") from exc
    try:
        handler = PageHandler if cfg.http_mode == "serve" else RedirectHandler
        http_server = POCServer((cfg.bind, cfg.http_port), handler, app)
    except OSError as exc:
        https.server_close()
        raise SystemExit(f"Cannot bind HTTP port {cfg.http_port}: {exc}\nTry --http-port 8080.") from exc
    http_server.https_port = https.server_address[1]
    return http_server, https, app


def setup_logging(level: str, log_file: Path | None) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bind", default="localhost", help='address to listen on (default localhost; "" = all interfaces)')
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--https-port", type=int, default=443)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB with a `users` table")
    ap.add_argument("--init-demo", action="store_true", help="create the DB with demo users if it doesn't exist")
    ap.add_argument("--cert", type=Path, default=gen_cert.DEFAULT_CERT)
    ap.add_argument("--key", type=Path, default=gen_cert.DEFAULT_KEY)
    ap.add_argument("--http-mode", choices=("redirect", "serve"), default="redirect",
                    help="redirect: port 80 -> HTTPS (default, safe). serve: serve pages over plain HTTP (insecure)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    setup_logging(args.log_level.upper(), BASE / "logs" / "poc.log")
    cfg = Config(args.bind, args.http_port, args.https_port, args.db, args.cert, args.key, args.http_mode)
    log.warning("PROOF OF CONCEPT: not for deployment. The production system is in backend/.")
    if args.init_demo and not cfg.db_path.exists():
        demo_passwords = init_demo_db(cfg.db_path)
        # Printed to the terminal only (never logged) and shown exactly once.
        print("Demo accounts created. These passwords are shown ONCE:")
        for username, password in demo_passwords.items():
            print(f"  {username:<10} {password}")
    if cfg.http_mode == "serve":
        log.warning("--http-mode serve: logins over plain HTTP are NOT encrypted. Demo use only.")

    http_server, https_server, _ = create_servers(cfg)
    threads = [
        threading.Thread(target=s.serve_forever, name=name, daemon=True)
        for s, name in ((https_server, "https"), (http_server, "http"))
    ]
    for t in threads:
        t.start()
    log.info(
        "Listening: http://%s:%s (%s) and https://%s:%s  [db=%s]",
        cfg.bind or "*", http_server.server_address[1], cfg.http_mode,
        cfg.bind or "*", https_server.server_address[1], cfg.db_path,
    )
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        for s in (http_server, https_server):
            s.shutdown()
            s.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
